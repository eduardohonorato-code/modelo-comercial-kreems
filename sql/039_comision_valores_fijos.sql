-- ============================================================================
-- 039 — Valores FIJOS por vendedor: rango de cartera y salas Ganga
-- ============================================================================
-- PROBLEMA: `cartera_clientes` y `salas_ganga` viven en comision_entrada_mensual,
-- o sea POR MES. Como en la práctica casi nunca cambian (la cartera de un
-- vendedor es la misma en julio que en agosto, y las salas Ganga que repone
-- tampoco se mueven), gerencia tenía que volver a cargarlas vendedor por
-- vendedor cada mes. Si se olvidaba, el mes salía con cartera 0 → sin comisión
-- de efectividad, y con reposición $0.
--
-- SOLUCIÓN: una tabla de valores fijos por vendedor (`comision_valor_fijo`) que
-- la vista usa como RESPALDO. La regla queda:
--
--     valor efectivo = valor del mes (si está cargado)
--                      → si no, valor fijo del vendedor
--                      → si no, 0
--
-- Lo del mes SIGUE mandando: si un mes puntual necesita otro número, se carga
-- ahí y pisa al fijo. La diferencia es que ahora "no cargar nada" (NULL) ya no
-- significa 0, significa "usa el fijo".
--
-- ⚠️ NO cambia ningún mes ya cargado. Las filas que hoy existen en
-- comision_entrada_mensual tienen valores NO NULOS (la app siempre escribía un
-- entero, aunque fuera 0), así que siguen mandando ellas. El fijo solo aparece
-- en los meses donde no hay fila, o donde gerencia deje el campo en "usar el
-- fijo" desde la app.
--
-- Idempotente y AUTOSUFICIENTE: la sección 0 se asegura de las columnas y
-- funciones que agregaron los scripts 008 / 014 / 033 / 034, porque no todos se
-- habían corrido en la base (la app es fail-soft y sigue andando sin ellos, así
-- que la falta no se nota hasta que algo recrea la vista completa).
-- Correr en el SQL Editor de Supabase.
-- ============================================================================

-- ── 0. Requisitos: columnas y funciones que la vista da por sentadas ─────────
-- Este script recrea v_comision_vendedor_mes completa, así que necesita todo lo
-- que le agregaron los scripts anteriores. Como no siempre se corrieron todos
-- (la app es fail-soft y sigue andando sin ellos, así que la falta no se nota
-- hasta acá), se aseguran aquí. Todo con `if not exists`: si ya estaban, no
-- pasa nada.

--   008 — criterio manual de tramo
alter table public.comision_entrada_mensual
  add column if not exists pnv_logro_override numeric(5,4),
  add column if not exists maq_logro_override numeric(5,4);

--   033 — días trabajados / INAB del vendedor en meses parciales
alter table public.comision_entrada_mensual
  add column if not exists dias_trabajados_override smallint,
  add column if not exists inab_override            smallint;

alter table public.comision_entrada_mensual
  drop constraint if exists comision_entrada_dias_chk;
alter table public.comision_entrada_mensual
  add constraint comision_entrada_dias_chk check (
    (dias_trabajados_override is null or dias_trabajados_override between 1 and 31)
    and (inab_override is null or inab_override between 0 and 15)
  );

--   034 — ajuste manual de monto libre, con motivo obligatorio
alter table public.comision_entrada_mensual
  add column if not exists ajuste_monto  numeric(18,2),
  add column if not exists ajuste_motivo text;

alter table public.comision_entrada_mensual
  drop constraint if exists comision_entrada_ajuste_chk;
alter table public.comision_entrada_mensual
  add constraint comision_entrada_ajuste_chk check (
    coalesce(ajuste_monto, 0) = 0
    or (ajuste_motivo is not null and length(btrim(ajuste_motivo)) >= 3)
  );

--   034 — el snapshot de cierre también guarda el ajuste
alter table public.comision_calculo
  add column if not exists ajuste_monto  numeric(18,2),
  add column if not exists ajuste_motivo text;

--   014 — redondeo del PNV hacia abajo (piso del tramo)
create or replace function public.comision_ajustar_logro_piso(
  p_logro numeric, p_paso numeric, p_piso numeric, p_techo numeric)
returns numeric language sql immutable as $$
  select case
    when p_logro is null then null
    when floor(p_logro / p_paso) * p_paso < p_piso  then null
    when floor(p_logro / p_paso) * p_paso > p_techo then p_techo
    else round(floor(p_logro / p_paso) * p_paso, 4)
  end;
$$;

grant execute on function
  public.comision_ajustar_logro_piso(numeric,numeric,numeric,numeric)
  to authenticated;

-- Aviso si falta algo de 006 (las escalas y sus funciones): sin eso la vista se
-- crea pero el cálculo sale en $0, y conviene enterarse ahora y no al pagar.
do $$
declare faltan text := ''; n bigint;
begin
  if to_regprocedure('public.comision_param(text)') is null then
    faltan := faltan || ' comision_param()';
  end if;
  if to_regclass('public.comision_tramo_pnv') is null then
    faltan := faltan || ' comision_tramo_pnv';
  else
    execute 'select count(*) from public.comision_tramo_pnv' into n;
    if n = 0 then faltan := faltan || ' comision_tramo_pnv(vacía)'; end if;
  end if;
  if to_regclass('public.comision_tramo_efectividad') is null then
    faltan := faltan || ' comision_tramo_efectividad';
  else
    execute 'select count(*) from public.comision_tramo_efectividad' into n;
    if n = 0 then faltan := faltan || ' comision_tramo_efectividad(vacía)'; end if;
  end if;
  if faltan <> '' then
    raise warning 'Falta correr sql/006_comisiones.sql —%', faltan;
  end if;

  -- La vista redondea máquinas con piso 25% (sql/009). Si los tramos 25/30/35
  -- no están sembrados, un logro bajo el 40% se queda sin fila y paga $0 sin
  -- avisar: es un error de plata silencioso, mejor enterarse ahora.
  if to_regclass('public.comision_tramo_maquinas') is not null then
    execute 'select count(*) from public.comision_tramo_maquinas where logro_pct < 0.40'
      into n;
    if n = 0 then
      raise warning 'Falta correr sql/009_maquinas_piso_25pct.sql: sin los tramos '
                    '25/30/35%% de máquinas, esos logros pagan cero.';
    end if;
  end if;
end $$;

-- ── 1. Tabla de valores fijos ───────────────────────────────────────────────
create table if not exists public.comision_valor_fijo (
  vendedor_id      bigint primary key
                     references public.dim_vendedor(id) on delete cascade,
  cartera_clientes integer,      -- límite inferior del rango (81, 91, … 141). NULL = sin fijo
  salas_ganga      integer,      -- nº de salas Ganga que repone habitualmente. NULL = sin fijo
  nota             text,
  actualizado_en   timestamptz default now()
);

comment on table public.comision_valor_fijo is
  'Cartera y salas Ganga habituales de cada vendedor. Respaldo de '
  'comision_entrada_mensual: se usan cuando el mes no trae valor propio.';
comment on column public.comision_valor_fijo.cartera_clientes is
  'Límite inferior del rango de cartera (Rango 9 = 81, Rango 15 = 141+). '
  'Define qué fila de la tabla de efectividad se aplica.';

drop trigger if exists trg_comision_valor_fijo_actualizado on public.comision_valor_fijo;
create trigger trg_comision_valor_fijo_actualizado
  before update on public.comision_valor_fijo
  for each row execute function public.tg_set_actualizado_en();

-- ── 2. RLS: leen los autenticados (la vista igual filtra), escribe gerencia ──
alter table public.comision_valor_fijo enable row level security;

drop policy if exists comision_valor_fijo_sel on public.comision_valor_fijo;
create policy comision_valor_fijo_sel on public.comision_valor_fijo
  for select to authenticated using (true);

drop policy if exists comision_valor_fijo_all on public.comision_valor_fijo;
create policy comision_valor_fijo_all on public.comision_valor_fijo
  for all to authenticated
  using ((select public.es_gerencia()))
  with check ((select public.es_gerencia()));

grant select                       on public.comision_valor_fijo to authenticated;
grant insert, update, delete       on public.comision_valor_fijo to authenticated;
grant all                          on public.comision_valor_fijo to service_role;

-- El service_role no tenía grant sobre las tablas de comisiones, así que los
-- scripts de mantención/verificación no podían ni leerlas (error 42501).
grant all on public.comision_plan              to service_role;
grant all on public.comision_tramo_pnv         to service_role;
grant all on public.comision_tramo_maquinas    to service_role;
grant all on public.comision_tramo_efectividad to service_role;
grant all on public.comision_parametro         to service_role;
grant all on public.comision_entrada_mensual   to service_role;
grant all on public.comision_calculo           to service_role;

-- ── 3. Seed: lo que definió gerencia (septiembre 2026) ───────────────────────
-- Cartera: el número es el RANGO de la app; acá se guarda su límite inferior
-- (Rango N → (N-1)*10 + 1). Rango 15 = 141+, Rango 13 = 121-130, etc.
with fijos(nombre, rango, salas) as (
  values
    ('Rigo Antonio Lara Diaz',              15, null::int),
    ('Jorge Alfredo Jara Bravo',            13, null),
    ('Mauricio Andres Figueroa Holtmann',   15, 4),
    ('Carlos Eduardo Sanhueza Quezada',     10, 2),
    ('Marcela Andrea Sanhueza Carvajal',    15, null),
    ('Maicol Sebastian Gutierrez Sanhueza', 14, 3),
    ('Macarena Nicole Garrido Mulchi',      15, null),
    ('Carlos Matabenitez',                  15, 4)
)
insert into public.comision_valor_fijo (vendedor_id, cartera_clientes, salas_ganga, nota)
select dv.id, (f.rango - 1) * 10 + 1, f.salas,
       'Definido por gerencia, septiembre 2026'
  from fijos f
  join public.dim_vendedor dv on dv.nombre_canonico = f.nombre
on conflict (vendedor_id) do update
  set cartera_clientes = excluded.cartera_clientes,
      salas_ganga      = excluded.salas_ganga,
      nota             = excluded.nota;

-- Aviso si algún nombre del seed no calzó con dim_vendedor (se habría perdido
-- en silencio). Deben quedar 8 filas.
do $$
declare n int;
begin
  select count(*) into n from public.comision_valor_fijo;
  raise notice 'comision_valor_fijo: % filas cargadas (se esperaban 8)', n;
end $$;

-- ── 4. Recrear la vista: el fijo entra como respaldo ────────────────────────
-- Igual que la 034, con dos cambios:
--   · cartera_clientes / salas_ganga ahora son EFECTIVOS (mes → fijo → 0).
--   · se exponen los valores crudos (`*_mes`, `*_fijo`) para que el editor
--     pueda mostrar de dónde salió cada número, igual que hace la 033 con
--     dias_trabajados_base / _override.
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
    r.dias_trabajados                                         as dias_trabajados_base,
    cal.inab                                                  as inab_base,
    e.dias_trabajados_override,
    e.inab_override,
    coalesce(e.dias_trabajados_override, r.dias_trabajados)   as dias_trabajados,
    coalesce(e.inab_override, cal.inab)                       as inab,
    -- Cartera y salas: mes → fijo del vendedor → 0  ← cambio del 039
    coalesce(e.cartera_clientes, f.cartera_clientes, 0)       as cartera_clientes,
    coalesce(e.salas_ganga,      f.salas_ganga,      0)       as salas_ganga,
    e.cartera_clientes                                        as cartera_clientes_mes,
    e.salas_ganga                                             as salas_ganga_mes,
    f.cartera_clientes                                        as cartera_clientes_fija,
    f.salas_ganga                                             as salas_ganga_fijas,
    coalesce(e.ajuste_monto, 0)                               as ajuste_monto,
    e.ajuste_motivo,
    e.efectividad_override,
    e.pnv_logro_override,
    e.maq_logro_override,
    r.pct_cumplimiento                                        as logro_pnv,
    case when r.obj_maquinas > 0
         then round(r.maquinas_entregadas::numeric / r.obj_maquinas, 4) end
                                                              as logro_maquinas,
    coalesce(e.efectividad_override, r.pct_efectividad)       as logro_efectividad
  from public.v_resumen_vendedor_mes r
  join public.dim_vendedor dv on dv.id = r.vendedor_id
  left join public.calendario_laboral cal
         on cal.anio = r.anio and cal.mes = r.mes
  left join public.comision_entrada_mensual e
         on e.vendedor_id = r.vendedor_id and e.anio = r.anio and e.mes = r.mes
  left join public.comision_valor_fijo f
         on f.vendedor_id = r.vendedor_id
),
ajustes as (
  select b.*,
    public.comision_ajustar_logro_piso(
      coalesce(b.pnv_logro_override, b.logro_pnv),       0.05, 0.80, 1.10) as pnv_aj,
    public.comision_ajustar_logro(
      coalesce(b.maq_logro_override, b.logro_maquinas),  0.05, 0.25, 1.40) as maq_aj,
    public.comision_ajustar_logro(b.logro_efectividad,   0.10, 0.30, 0.60) as efect_aj
  from base b
),
calc as (
  select a.*,
    coalesce(public.comision_pnv_monto(a.plan_id, a.pnv_aj), 0)                        as com_pnv,
    coalesce(public.comision_maq_monto(a.plan_id, a.maq_aj), 0)                        as com_maquinas,
    coalesce(public.comision_efect_monto(a.plan_id, a.cartera_clientes, a.efect_aj),0) as com_efectividad,
    case when a.logro_pnv >= public.comision_param('bono_umbral')
         then round(public.comision_param('bono_pct')
                    * greatest(0, a.fact_nc - public.comision_param('bono_umbral') * a.obj_venta), 2)
         else 0 end                                                                     as bono_4pct,
    coalesce(a.salas_ganga,0) * public.comision_param('reposicion_monto')               as bono_reposicion
  from ajustes a
),
tot as (
  select c.*,
    (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad + c.ajuste_monto)
                                                                                        as total_comision
  from calc c
),
sc as (
  select t.*,
    case when t.dias_trabajados > 0 and t.inab is not null
         then round(t.total_comision / t.dias_trabajados * t.inab, 0) end               as semana_corrida
  from tot t
)
select
  s.vendedor_id, s.nombre_canonico, s.anio, s.mes,
  s.plan_id, p.nombre as plan_nombre,
  s.fact_nc, s.obj_venta, s.logro_pnv, s.pnv_aj,
  s.pnv_logro_override, s.com_pnv, s.bono_4pct,
  s.obj_maquinas, s.maquinas_entregadas, s.logro_maquinas, s.maq_aj,
  s.maq_logro_override, s.com_maquinas,
  s.obj_visitas, s.n_facturas, s.cartera_clientes, s.logro_efectividad, s.efect_aj,
  s.efectividad_override, s.com_efectividad,
  s.ajuste_monto, s.ajuste_motivo,
  s.total_comision,
  s.dias_trabajados, s.inab,
  s.dias_trabajados_base, s.inab_base,
  s.dias_trabajados_override, s.inab_override,
  s.semana_corrida,
  s.salas_ganga, s.bono_reposicion,
  -- Crudos: de dónde salió cada número (mes propio vs fijo del vendedor)
  s.cartera_clientes_mes, s.cartera_clientes_fija,
  s.salas_ganga_mes,      s.salas_ganga_fijas,
  s.total_comision + coalesce(s.semana_corrida, 0)                                     as total_variable,
  s.total_comision + coalesce(s.semana_corrida, 0) + s.bono_reposicion                 as total_a_pagar
from sc s
join public.comision_plan p on p.id = s.plan_id
where public.es_gerencia();

grant select on public.v_comision_vendedor_mes to authenticated;

-- ============================================================================
-- Verificar (desde la app, con usuario gerencia — en el SQL Editor la vista
-- devuelve 0 filas porque filtra por es_gerencia()):
--
--   select nombre_canonico, cartera_clientes_mes, cartera_clientes_fija,
--          cartera_clientes, salas_ganga_mes, salas_ganga_fijas, salas_ganga
--     from public.v_comision_vendedor_mes where anio = 2026 and mes = 8;
--
-- Los meses ya cargados no cambian: cartera_clientes_mes manda. Para que un mes
-- pase a usar el fijo hay que dejarlo en "usar el fijo" desde el editor (NULL).
-- ============================================================================
