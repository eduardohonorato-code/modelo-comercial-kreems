-- ============================================================================
-- 034 — Ajuste manual de comisión (monto libre, con motivo obligatorio)
-- ============================================================================
-- PROBLEMA: los overrides existentes (pnv_logro_override, maq_logro_override,
-- efectividad_override) solo permiten elegir un PELDAÑO de la escala. Cuando
-- gerencia decide un monto que no está en ninguna tabla — un caso de criterio,
-- una corrección, un acuerdo puntual — no hay dónde escribirlo, y se termina
-- pagando por planilla un número que el sistema no puede reproducir.
--
-- SOLUCIÓN: `ajuste_monto` (positivo o negativo) + `ajuste_motivo` OBLIGATORIO.
-- Entra a Total Comisión, así que forma parte de la base de la Semana Corrida
-- (decisión de gerencia 2026-08-06: es comisión, no un bono aparte como la
-- reposición, que sigue por fuera).
--
--   Total Comisión = PNV + Bono 4% + Máquinas + Efectividad + AJUSTE
--   Semana Corrida = Total Comisión / días trabajados × INAB
--   Total a Pagar  = Total Comisión + Semana Corrida + Bono Reposición
--
-- El motivo es obligatorio por CHECK: sin él la fila no se guarda. La idea es
-- que dentro de seis meses se pueda decir por qué se pagó ese número.
--
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

alter table public.comision_entrada_mensual
  add column if not exists ajuste_monto  numeric(18,2),
  add column if not exists ajuste_motivo text;

comment on column public.comision_entrada_mensual.ajuste_monto is
  'Ajuste manual de comisión, + o −. Entra a Total Comisión y genera Semana Corrida.';
comment on column public.comision_entrada_mensual.ajuste_motivo is
  'Por qué se aplicó el ajuste. Obligatorio si ajuste_monto <> 0.';

-- Sin motivo no hay ajuste.
alter table public.comision_entrada_mensual
  drop constraint if exists comision_entrada_ajuste_chk;
alter table public.comision_entrada_mensual
  add constraint comision_entrada_ajuste_chk check (
    coalesce(ajuste_monto, 0) = 0
    or (ajuste_motivo is not null and length(btrim(ajuste_motivo)) >= 3)
  );

-- ── Recrear la vista ────────────────────────────────────────────────────────
-- Igual que la 033, más el ajuste. Se aprovecha de introducir el CTE `tot`:
-- antes la suma de los componentes y la semana corrida se repetían literalmente
-- cuatro veces, y cada cambio obligaba a tocar las cuatro.
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
    coalesce(e.cartera_clientes, 0)                           as cartera_clientes,
    coalesce(e.salas_ganga, 0)                                as salas_ganga,
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
  s.total_comision + coalesce(s.semana_corrida, 0)                                     as total_variable,
  s.total_comision + coalesce(s.semana_corrida, 0) + s.bono_reposicion                 as total_a_pagar
from sc s
join public.comision_plan p on p.id = s.plan_id
where public.es_gerencia();

grant select on public.v_comision_vendedor_mes to authenticated;

-- El snapshot de cierre también guarda el ajuste.
alter table public.comision_calculo
  add column if not exists ajuste_monto  numeric(18,2),
  add column if not exists ajuste_motivo text;

-- ============================================================================
-- Verificar (como gerencia, desde la app):
--   sin ajustes cargados, total_comision y semana_corrida dan exactamente lo
--   mismo que antes de este script (ajuste_monto = 0 por el coalesce).
-- ============================================================================
