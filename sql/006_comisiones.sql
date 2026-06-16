-- ============================================================================
-- Kreems · Sistema de Seguimiento Comercial
-- SECCIÓN COMISIONES — Sueldos y Comisiones (parte VARIABLE)
-- ----------------------------------------------------------------------------
-- Script IDEMPOTENTE (re-ejecutable sin duplicar):
--   · Tablas        -> CREATE TABLE IF NOT EXISTS
--   · Funciones     -> CREATE OR REPLACE FUNCTION
--   · Vista         -> CREATE OR REPLACE VIEW (security_invoker)
--   · RLS           -> DROP POLICY IF EXISTS + CREATE POLICY
--   · Seed escalas  -> INSERT ... ON CONFLICT DO UPDATE  (fuente de verdad:
--                      data/muestras/tabla_comisiones.xlsx — "PROPUESTA OCT 2024")
--
-- Requiere 001_modelo_datos.sql ya ejecutado (dim_vendedor, calendario_laboral,
-- objetivos_mensuales, v_resumen_vendedor_mes, helpers es_gerencia()/...).
--
-- REGLAS DE NEGOCIO (confirmadas con gerencia, ver CLAUDE.md):
--   Total Comisión = PNV(tramo) + Bono 4% + Máquinas + Efectividad
--   Semana Corrida = Total Comisión / días_trabajados × INAB (días descanso mes)
--   Total Variable = Total Comisión + Semana Corrida
--   Total a Pagar  = Total Variable + Bono Reposición ($15.000 × salas Ganga)
--   · Logros se redondean al tramo MÁS CERCANO (PNV/Máq al 5%, Efect al 10%).
--   · Bajo el piso de cada componente => $0.
--   · Bono 4% = 4% × (Fact-NC − 1,10 × obj_venta), solo si logro ≥ 110%, ilimitado.
--   · Máquinas: solo instalaciones nuevas (FL-4) ENTREGADAS / obj_maquinas.
--   · Efectividad: matriz 2D = N°facturas/obj_visitas × rango de cartera de clientes.
--   · 2 planes: 'kreems_normal' y 'macarena' (esta última, montos propios más altos).
--   · Cálculo CONSOLIDADO (Acuña + Gran Natural juntos), un resultado por vendedor/mes.
--   Escalas estables 2025. Para versionar a futuro: agregar columna vigente_desde
--   a las tablas de tramo y filtrar por período en las funciones de lookup.
-- ============================================================================

-- ============================================================================
-- 1. PLANES Y ESCALAS DE COMISIÓN  (config — fuente de verdad: el Excel)
-- ============================================================================

create table if not exists public.comision_plan (
  id     smallint primary key,
  codigo text not null unique,        -- 'kreems_normal' | 'macarena'
  nombre text not null
);

-- PNV: % de logro de venta -> monto fijo en CLP.
create table if not exists public.comision_tramo_pnv (
  plan_id   smallint not null references public.comision_plan(id) on delete cascade,
  logro_pct numeric(5,4) not null,    -- 0.8000, 0.8500, ... 1.1000
  monto     numeric(18,2) not null,
  primary key (plan_id, logro_pct)
);

-- Máquinas: % de logro (entregadas/obj) -> monto fijo en CLP.
create table if not exists public.comision_tramo_maquinas (
  plan_id   smallint not null references public.comision_plan(id) on delete cascade,
  logro_pct numeric(5,4) not null,    -- 0.4000 ... 1.4000
  monto     numeric(18,2) not null,
  primary key (plan_id, logro_pct)
);

-- Efectividad: matriz 2D = rango de cartera (cartera_min) × % efectividad (30/40/50/60).
-- cartera_min = límite inferior del tramo de nº de clientes asignados (81, 91, ... 141).
-- Solo hay filas para rangos pagables (cartera >= 81). Bajo eso => no hay fila => $0.
create table if not exists public.comision_tramo_efectividad (
  plan_id         smallint not null references public.comision_plan(id) on delete cascade,
  cartera_min     integer not null,           -- 81, 91, 101, 111, 121, 131, 141
  efectividad_pct numeric(5,4) not null,       -- 0.3000, 0.4000, 0.5000, 0.6000
  monto           numeric(18,2) not null,
  primary key (plan_id, cartera_min, efectividad_pct)
);

-- Parámetros globales del cálculo (bono, umbral, reposición).
create table if not exists public.comision_parametro (
  clave text primary key,
  valor numeric(18,4) not null,
  descripcion text
);

-- ============================================================================
-- 2. ENTRADAS EDITABLES POR GERENCIA (por vendedor / mes)
--    Cartera de clientes asignados (define el rango de efectividad) y nº de
--    salas Ganga atendidas (bono reposición). Override manual de efectividad
--    para los casos de criterio del gerente (ver discrepancia documentada).
-- ============================================================================
create table if not exists public.comision_entrada_mensual (
  vendedor_id          bigint   not null references public.dim_vendedor(id) on delete cascade,
  anio                 smallint not null,
  mes                  smallint not null,
  cartera_clientes     integer  default 0,        -- nº de clientes asignados al vendedor
  salas_ganga          integer  default 0,        -- nº de salas Ganga atendidas (bono $15.000 c/u)
  efectividad_override numeric(5,4),              -- si se setea, reemplaza el % calculado (criterio gerencia)
  actualizado_en       timestamptz default now(),
  primary key (vendedor_id, anio, mes)
);

drop trigger if exists trg_comision_entrada_actualizado on public.comision_entrada_mensual;
create trigger trg_comision_entrada_actualizado
  before update on public.comision_entrada_mensual
  for each row execute function public.tg_set_actualizado_en();

-- ============================================================================
-- 3. EXTENSIONES A TABLAS EXISTENTES
-- ============================================================================

-- INAB = nº de días de descanso del mes (base legal de la Semana Corrida).
alter table public.calendario_laboral
  add column if not exists inab smallint;
comment on column public.calendario_laboral.inab is
  'Días de descanso del mes para Semana Corrida (ej. 6 en abril, 7 en mayo 2026).';

-- Plan de comisión por vendedor (default: Kreems normal = 1).
alter table public.dim_vendedor
  add column if not exists plan_comision_id smallint references public.comision_plan(id);

-- ============================================================================
-- 4. FUNCIONES DE CÁLCULO
-- ============================================================================

-- Redondea el logro al tramo más cercano y aplica piso/techo.
--   · Bajo el piso => NULL (no paga; el lookup devuelve 0 vía coalesce).
--   · Sobre el techo => se congela en el techo (ej. máquinas tope 140%).
create or replace function public.comision_ajustar_logro(
  p_logro numeric, p_paso numeric, p_piso numeric, p_techo numeric)
returns numeric language sql immutable as $$
  select case
    when p_logro is null then null
    when round(p_logro / p_paso) * p_paso < p_piso  then null
    when round(p_logro / p_paso) * p_paso > p_techo then p_techo
    else round(round(p_logro / p_paso) * p_paso, 4)
  end;
$$;

create or replace function public.comision_pnv_monto(p_plan smallint, p_logro_aj numeric)
returns numeric language sql stable as $$
  select monto from public.comision_tramo_pnv
  where plan_id = p_plan and logro_pct = p_logro_aj;
$$;

create or replace function public.comision_maq_monto(p_plan smallint, p_logro_aj numeric)
returns numeric language sql stable as $$
  select monto from public.comision_tramo_maquinas
  where plan_id = p_plan and logro_pct = p_logro_aj;
$$;

-- Efectividad: elige la banda de cartera más alta cuyo límite inferior <= cartera.
create or replace function public.comision_efect_monto(
  p_plan smallint, p_cartera integer, p_ef_aj numeric)
returns numeric language sql stable as $$
  select monto from public.comision_tramo_efectividad
  where plan_id = p_plan
    and efectividad_pct = p_ef_aj
    and cartera_min <= p_cartera
  order by cartera_min desc
  limit 1;
$$;

create or replace function public.comision_param(p_clave text)
returns numeric language sql stable as $$
  select valor from public.comision_parametro where clave = p_clave;
$$;

-- ============================================================================
-- 5. VISTA DE CÁLCULO EN VIVO  (gerencia)
--    Se apoya en v_resumen_vendedor_mes (Fact-NC, n_facturas, máquinas,
--    objetivos, días trabajados ya consolidados). Solo gerencia ve filas.
-- ============================================================================
drop view if exists public.v_comision_vendedor_mes;
create or replace view public.v_comision_vendedor_mes
with (security_invoker = true) as
with base as (
  select
    r.vendedor_id,
    r.nombre_canonico,
    r.anio,
    r.mes,
    coalesce(dv.plan_comision_id, 1)::smallint                as plan_id,
    r.fact_nc,
    r.obj_venta,
    r.obj_maquinas,
    r.obj_visitas,
    r.n_facturas,
    r.maquinas_entregadas,
    r.dias_trabajados,
    cal.inab,
    coalesce(e.cartera_clientes, 0)                           as cartera_clientes,
    coalesce(e.salas_ganga, 0)                                as salas_ganga,
    e.efectividad_override,
    -- % de logro REAL de cada componente
    r.pct_cumplimiento                                        as logro_pnv,
    case when r.obj_maquinas > 0
         then round(r.maquinas_entregadas::numeric / r.obj_maquinas, 4) end as logro_maquinas,
    coalesce(e.efectividad_override, r.pct_efectividad)       as logro_efectividad
  from public.v_resumen_vendedor_mes r
  join public.dim_vendedor dv on dv.id = r.vendedor_id
  left join public.calendario_laboral cal
         on cal.anio = r.anio and cal.mes = r.mes
  left join public.comision_entrada_mensual e
         on e.vendedor_id = r.vendedor_id and e.anio = r.anio and e.mes = r.mes
),
ajustes as (
  select b.*,
    public.comision_ajustar_logro(b.logro_pnv,        0.05, 0.80, 1.10) as pnv_aj,
    public.comision_ajustar_logro(b.logro_maquinas,   0.05, 0.40, 1.40) as maq_aj,
    public.comision_ajustar_logro(b.logro_efectividad,0.10, 0.30, 0.60) as efect_aj
  from base b
),
calc as (
  select a.*,
    coalesce(public.comision_pnv_monto(a.plan_id, a.pnv_aj), 0)                       as com_pnv,
    coalesce(public.comision_maq_monto(a.plan_id, a.maq_aj), 0)                       as com_maquinas,
    coalesce(public.comision_efect_monto(a.plan_id, a.cartera_clientes, a.efect_aj),0) as com_efectividad,
    -- Bono 4% sobre el exceso del 110% del objetivo, solo si se alcanza el 110%.
    case when a.logro_pnv >= public.comision_param('bono_umbral')
         then round(public.comision_param('bono_pct')
                    * greatest(0, a.fact_nc - public.comision_param('bono_umbral') * a.obj_venta), 2)
         else 0 end                                                                    as bono_4pct,
    coalesce(a.salas_ganga,0) * public.comision_param('reposicion_monto')              as bono_reposicion
  from ajustes a
)
select
  c.vendedor_id, c.nombre_canonico, c.anio, c.mes,
  c.plan_id, p.nombre as plan_nombre,
  -- Componente PNV
  c.fact_nc, c.obj_venta, c.logro_pnv, c.pnv_aj, c.com_pnv, c.bono_4pct,
  -- Componente Máquinas
  c.obj_maquinas, c.maquinas_entregadas, c.logro_maquinas, c.maq_aj, c.com_maquinas,
  -- Componente Efectividad
  c.obj_visitas, c.n_facturas, c.cartera_clientes, c.logro_efectividad, c.efect_aj,
  c.efectividad_override, c.com_efectividad,
  -- Totales
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)                      as total_comision,
  c.dias_trabajados, c.inab,
  case when c.dias_trabajados > 0 and c.inab is not null
       then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                  / c.dias_trabajados * c.inab, 0) end                                as semana_corrida,
  c.salas_ganga, c.bono_reposicion,
  -- Total Variable = Total Comisión + Semana Corrida
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)                           as total_variable,
  -- Total a Pagar = Total Variable + Bono Reposición (por fuera de la comisión)
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)
    + c.bono_reposicion                                                               as total_a_pagar
from calc c
join public.comision_plan p on p.id = c.plan_id
where public.es_gerencia();   -- solo gerencia/admin ve comisiones

-- ============================================================================
-- 6. HISTORIAL — snapshot mensual congelado (foto del cálculo al cerrar)
--    La app inserta aquí cuando gerencia "cierra" el mes; protege el número.
-- ============================================================================
create table if not exists public.comision_calculo (
  vendedor_id      bigint   not null references public.dim_vendedor(id) on delete cascade,
  anio             smallint not null,
  mes              smallint not null,
  plan_id          smallint references public.comision_plan(id),
  fact_nc          numeric(18,2),
  obj_venta        numeric(18,2),
  logro_pnv        numeric(8,4),
  pnv_aj           numeric(5,4),
  com_pnv          numeric(18,2),
  bono_4pct        numeric(18,2),
  obj_maquinas     integer,
  maquinas_entregadas integer,
  logro_maquinas   numeric(8,4),
  maq_aj           numeric(5,4),
  com_maquinas     numeric(18,2),
  obj_visitas      integer,
  n_facturas       integer,
  cartera_clientes integer,
  logro_efectividad numeric(8,4),
  efect_aj         numeric(5,4),
  com_efectividad  numeric(18,2),
  total_comision   numeric(18,2),
  dias_trabajados  smallint,
  inab             smallint,
  semana_corrida   numeric(18,2),
  salas_ganga      integer,
  bono_reposicion  numeric(18,2),
  total_variable   numeric(18,2),
  total_a_pagar    numeric(18,2),
  cerrado          boolean default true,
  calculado_en     timestamptz default now(),
  primary key (vendedor_id, anio, mes)
);

-- ============================================================================
-- 7. RLS  (sección comisiones = solo gerencia/admin)
-- ============================================================================
alter table public.comision_plan              enable row level security;
alter table public.comision_tramo_pnv         enable row level security;
alter table public.comision_tramo_maquinas    enable row level security;
alter table public.comision_tramo_efectividad enable row level security;
alter table public.comision_parametro         enable row level security;
alter table public.comision_entrada_mensual   enable row level security;
alter table public.comision_calculo           enable row level security;

-- Escalas/planes/parámetros: lectura para autenticados (no son datos sensibles;
-- la vista de cálculo igual filtra por es_gerencia()). El ETL/seed escribe con service_role.
do $$
declare t text;
begin
  foreach t in array array[
    'comision_plan','comision_tramo_pnv','comision_tramo_maquinas',
    'comision_tramo_efectividad','comision_parametro'
  ] loop
    execute format('drop policy if exists %I on public.%I', t||'_sel', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (true)',
      t||'_sel', t);
  end loop;
end $$;

-- Entradas mensuales: gerencia CRUD total; nadie más.
drop policy if exists comision_entrada_sel on public.comision_entrada_mensual;
drop policy if exists comision_entrada_all on public.comision_entrada_mensual;
create policy comision_entrada_sel on public.comision_entrada_mensual
  for select to authenticated using (public.es_gerencia());
create policy comision_entrada_all on public.comision_entrada_mensual
  for all to authenticated using (public.es_gerencia()) with check (public.es_gerencia());

-- Snapshot de cálculo: gerencia CRUD total; nadie más.
drop policy if exists comision_calculo_sel on public.comision_calculo;
drop policy if exists comision_calculo_all on public.comision_calculo;
create policy comision_calculo_sel on public.comision_calculo
  for select to authenticated using (public.es_gerencia());
create policy comision_calculo_all on public.comision_calculo
  for all to authenticated using (public.es_gerencia()) with check (public.es_gerencia());

-- ---- Grants (RLS sigue filtrando por encima) ----
grant select on public.comision_plan, public.comision_tramo_pnv,
                public.comision_tramo_maquinas, public.comision_tramo_efectividad,
                public.comision_parametro to authenticated;
grant select, insert, update, delete on public.comision_entrada_mensual to authenticated;
grant select, insert, update, delete on public.comision_calculo to authenticated;
grant select on public.v_comision_vendedor_mes to authenticated;
grant execute on function
  public.comision_ajustar_logro(numeric,numeric,numeric,numeric),
  public.comision_pnv_monto(smallint,numeric),
  public.comision_maq_monto(smallint,numeric),
  public.comision_efect_monto(smallint,integer,numeric),
  public.comision_param(text)
  to anon, authenticated;

-- ============================================================================
-- 8. SEED — Planes, escalas y parámetros
--    Valores exactos de data/muestras/tabla_comisiones.xlsx (PROPUESTA OCT 2024).
--    Macarena = escala propia (montos más altos). Decimales preservados del Excel.
-- ============================================================================

insert into public.comision_plan (id, codigo, nombre) values
  (1, 'kreems_normal', 'Kreems normal'),
  (2, 'macarena',      'Nueva escala Macarena')
on conflict (id) do update set codigo = excluded.codigo, nombre = excluded.nombre;

-- 8.1 PNV (plan_id, logro, monto)
insert into public.comision_tramo_pnv (plan_id, logro_pct, monto) values
  (1, 0.80,  61250),    (1, 0.85, 113750),    (1, 0.90, 166250),
  (1, 0.95, 218750),    (1, 1.00, 271250),    (1, 1.05, 323750),
  (1, 1.10, 376250),
  (2, 0.80,  81572.58), (2, 0.85, 151491.94), (2, 0.90, 221411.29),
  (2, 0.95, 291330.65), (2, 1.00, 361250),    (2, 1.05, 431169.35),
  (2, 1.10, 501088.71)
on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- 8.2 Máquinas (plan_id, logro, monto)
insert into public.comision_tramo_maquinas (plan_id, logro_pct, monto) values
  (1, 0.40,  40000), (1, 0.45,  54000), (1, 0.50,  68000), (1, 0.55,  82000),
  (1, 0.60,  96000), (1, 0.65, 110000), (1, 0.70, 124000), (1, 0.75, 138000),
  (1, 0.80, 152000), (1, 0.85, 166000), (1, 0.90, 180000), (1, 0.95, 194000),
  (1, 1.00, 216750), (1, 1.05, 238250), (1, 1.10, 259750), (1, 1.15, 281250),
  (1, 1.20, 302750), (1, 1.25, 324250), (1, 1.30, 345750), (1, 1.35, 357750),
  (1, 1.40, 369750),
  (2, 0.40,  42768.17), (2, 0.45,  57737.02), (2, 0.50,  72705.88), (2, 0.55,  87674.74),
  (2, 0.60, 102643.60), (2, 0.65, 117612.46), (2, 0.70, 132581.31), (2, 0.75, 147550.17),
  (2, 0.80, 162519.03), (2, 0.85, 177487.89), (2, 0.90, 192456.75), (2, 0.95, 207425.61),
  (2, 1.00, 231750),    (2, 1.05, 254737.89), (2, 1.10, 277725.78), (2, 1.15, 300713.67),
  (2, 1.20, 323701.56), (2, 1.25, 346689.45), (2, 1.30, 369677.34), (2, 1.35, 382507.79),
  (2, 1.40, 395338.24)
on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- 8.3 Efectividad (plan_id, cartera_min, efectividad, monto)  — matriz 2D
--     Kreems normal (plan 1)
insert into public.comision_tramo_efectividad (plan_id, cartera_min, efectividad_pct, monto) values
  (1,  81, 0.30,  20000), (1,  81, 0.40,  50000), (1,  81, 0.50,  80000), (1,  81, 0.60, 110000),
  (1,  91, 0.30,  40000), (1,  91, 0.40,  70000), (1,  91, 0.50, 100000), (1,  91, 0.60, 130000),
  (1, 101, 0.30,  60000), (1, 101, 0.40,  90000), (1, 101, 0.50, 120000), (1, 101, 0.60, 150000),
  (1, 111, 0.30,  80000), (1, 111, 0.40, 110000), (1, 111, 0.50, 140000), (1, 111, 0.60, 170000),
  (1, 121, 0.30, 100000), (1, 121, 0.40, 130000), (1, 121, 0.50, 160000), (1, 121, 0.60, 190000),
  (1, 131, 0.30, 120000), (1, 131, 0.40, 150000), (1, 131, 0.50, 180000), (1, 131, 0.60, 210000),
  (1, 141, 0.30, 140000), (1, 141, 0.40, 170000), (1, 141, 0.50, 200000), (1, 141, 0.60, 230000),
--     Macarena (plan 2)
  (2,  81, 0.30,  21875),   (2,  81, 0.40,  54687.50), (2,  81, 0.50,  87500),    (2,  81, 0.60, 120312.50),
  (2,  91, 0.30,  43750),   (2,  91, 0.40,  76562.50), (2,  91, 0.50, 109375),    (2,  91, 0.60, 142187.50),
  (2, 101, 0.30,  65625),   (2, 101, 0.40,  98437.50), (2, 101, 0.50, 131250),    (2, 101, 0.60, 164062.50),
  (2, 111, 0.30,  87500),   (2, 111, 0.40, 120312.50), (2, 111, 0.50, 153125),    (2, 111, 0.60, 185937.50),
  (2, 121, 0.30, 109375),   (2, 121, 0.40, 142187.50), (2, 121, 0.50, 175000),    (2, 121, 0.60, 207812.50),
  (2, 131, 0.30, 131250),   (2, 131, 0.40, 164062.50), (2, 131, 0.50, 196875),    (2, 131, 0.60, 229687.50),
  (2, 141, 0.30, 153125),   (2, 141, 0.40, 185937.50), (2, 141, 0.50, 218750),    (2, 141, 0.60, 251562.50)
on conflict (plan_id, cartera_min, efectividad_pct) do update set monto = excluded.monto;

-- 8.4 Parámetros
insert into public.comision_parametro (clave, valor, descripcion) values
  ('bono_pct',         0.04,  'Bono PNV: 4% sobre el exceso del 110% del objetivo'),
  ('bono_umbral',      1.10,  'Umbral de logro PNV para gatillar el bono (110%)'),
  ('reposicion_monto', 15000, 'Bono reposición: $ por sala Ganga atendida')
on conflict (clave) do update set valor = excluded.valor, descripcion = excluded.descripcion;

-- 8.5 Plan por vendedor: Macarena -> plan 2; el resto queda en plan 1 (default).
update public.dim_vendedor set plan_comision_id = 1 where plan_comision_id is null;
update public.dim_vendedor set plan_comision_id = 2 where nombre_canonico ilike '%macarena%';

-- 8.6 INAB (días de descanso) — completar por mes según calendario real.
--     Mayo 2026 = 7 (dato confirmado por gerencia). Abril 2026 = 6.
update public.calendario_laboral set inab = 7 where anio = 2026 and mes = 5;
-- Si abril 2026 aún no existe en calendario_laboral, créalo (ajustar días reales):
insert into public.calendario_laboral (anio, mes, dias_totales, dias_trabajados, inab) values
  (2026, 4, 30, 21, 6)
on conflict (anio, mes) do update set
  dias_totales = excluded.dias_totales,
  dias_trabajados = excluded.dias_trabajados,
  inab = excluded.inab;

-- ============================================================================
-- FIN. Verificación rápida tras ejecutar (como gerencia):
--   select * from public.v_comision_vendedor_mes where anio=2026 and mes=5
--   order by total_a_pagar desc;
-- ============================================================================
