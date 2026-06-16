-- ============================================================================
-- Kreems · Comisiones — PNV redondea HACIA ABAJO (piso del tramo)
-- ----------------------------------------------------------------------------
-- Cambio de criterio (solo PNV): el logro ya NO se redondea al tramo más
-- cercano, sino al tramo más alto que se alcanzó o superó (piso).
--   · 100% – 104.99%  → tramo 100%
--   · 105% – 109.99%  → tramo 105%
--   · ≥ 110%          → tramo 110%   (debe ser igual o mayor)
--   · < 80% (piso)    → no paga
-- Máquinas y Efectividad SIGUEN con redondeo al más cercano (sin cambios).
-- El bono 4% no cambia: se gatilla con el logro REAL ≥ 110%.
-- Idempotente.
-- ============================================================================

-- 1. Función de ajuste con PISO (floor). numeric en Postgres es exacto, así que
--    floor no sufre el problema de coma flotante.
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

-- 2. Recrear la vista — idéntica a la 009, salvo la línea de pnv_aj.
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
    -- PNV: redondeo HACIA ABAJO (piso del tramo) ← cambio del 014
    public.comision_ajustar_logro_piso(
      coalesce(b.pnv_logro_override, b.logro_pnv),       0.05, 0.80, 1.10) as pnv_aj,
    -- Máquinas: al más cercano (sin cambios), piso 0.25
    public.comision_ajustar_logro(
      coalesce(b.maq_logro_override, b.logro_maquinas),  0.05, 0.25, 1.40) as maq_aj,
    -- Efectividad: al más cercano (sin cambios)
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
)
select
  c.vendedor_id, c.nombre_canonico, c.anio, c.mes,
  c.plan_id, p.nombre as plan_nombre,
  c.fact_nc, c.obj_venta, c.logro_pnv, c.pnv_aj,
  c.pnv_logro_override, c.com_pnv, c.bono_4pct,
  c.obj_maquinas, c.maquinas_entregadas, c.logro_maquinas, c.maq_aj,
  c.maq_logro_override, c.com_maquinas,
  c.obj_visitas, c.n_facturas, c.cartera_clientes, c.logro_efectividad, c.efect_aj,
  c.efectividad_override, c.com_efectividad,
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)                       as total_comision,
  c.dias_trabajados, c.inab,
  case when c.dias_trabajados > 0 and c.inab is not null
       then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                  / c.dias_trabajados * c.inab, 0) end                                 as semana_corrida,
  c.salas_ganga, c.bono_reposicion,
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)                            as total_variable,
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)
    + c.bono_reposicion                                                                as total_a_pagar
from calc c
join public.comision_plan p on p.id = c.plan_id
where public.es_gerencia();

grant select on public.v_comision_vendedor_mes to authenticated;

-- ============================================================================
-- Verificar (PNV con piso):
--   logro 104% → pnv_aj = 1.00  (antes 1.05)
--   logro 105% → pnv_aj = 1.05
--   logro 109% → pnv_aj = 1.05
--   logro 110% → pnv_aj = 1.10
--   logro  79% → pnv_aj = NULL  (no paga)
-- Diego (104%) debe pasar a com_pnv del tramo 100% = 271.250.
-- ============================================================================
