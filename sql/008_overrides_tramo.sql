-- ============================================================================
-- Kreems · Comisiones — override de tramo PNV y Máquinas por gerencia
-- ----------------------------------------------------------------------------
-- Agrega pnv_logro_override y maq_logro_override a comision_entrada_mensual.
-- Cuando están seteados, la vista usa ese % en lugar del logro calculado
-- para elegir el tramo de la escala (misma lógica que efectividad_override).
-- El logro_pnv / logro_maquinas reales se siguen mostrando intactos.
-- El bono 4% sigue usando el logro REAL (no el override).
-- Idempotente (ADD COLUMN IF NOT EXISTS + recreación de vista).
-- ============================================================================

-- 1. Nuevas columnas en la tabla de entradas
alter table public.comision_entrada_mensual
  add column if not exists pnv_logro_override numeric(5,4),  -- ej. 1.00 = forzar tramo 100%
  add column if not exists maq_logro_override numeric(5,4);  -- ej. 0.80 = forzar tramo 80%

-- 2. Recrear la vista incluyendo los overrides
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
    -- % de logro REAL (siempre el calculado; los overrides no lo ocultan)
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
    -- Si hay override, se usa ese % como entrada al redondeo de tramo.
    -- Si gerencia escribe exactamente 1.00, comision_ajustar_logro(1.00,...) = 1.00.
    public.comision_ajustar_logro(
      coalesce(b.pnv_logro_override, b.logro_pnv),       0.05, 0.80, 1.10) as pnv_aj,
    public.comision_ajustar_logro(
      coalesce(b.maq_logro_override, b.logro_maquinas),  0.05, 0.40, 1.40) as maq_aj,
    public.comision_ajustar_logro(b.logro_efectividad,   0.10, 0.30, 0.60) as efect_aj
  from base b
),
calc as (
  select a.*,
    coalesce(public.comision_pnv_monto(a.plan_id, a.pnv_aj), 0)                        as com_pnv,
    coalesce(public.comision_maq_monto(a.plan_id, a.maq_aj), 0)                        as com_maquinas,
    coalesce(public.comision_efect_monto(a.plan_id, a.cartera_clientes, a.efect_aj),0) as com_efectividad,
    -- Bono 4%: usa el logro REAL (no el override); si realmente llegó al 110%, paga.
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
  -- Componente PNV
  c.fact_nc, c.obj_venta, c.logro_pnv, c.pnv_aj,
  c.pnv_logro_override,                          -- expuesto para indicador en UI
  c.com_pnv, c.bono_4pct,
  -- Componente Máquinas
  c.obj_maquinas, c.maquinas_entregadas, c.logro_maquinas, c.maq_aj,
  c.maq_logro_override,                          -- expuesto para indicador en UI
  c.com_maquinas,
  -- Componente Efectividad
  c.obj_visitas, c.n_facturas, c.cartera_clientes, c.logro_efectividad, c.efect_aj,
  c.efectividad_override, c.com_efectividad,
  -- Totales
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)                       as total_comision,
  c.dias_trabajados, c.inab,
  case when c.dias_trabajados > 0 and c.inab is not null
       then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                  / c.dias_trabajados * c.inab, 0) end                                 as semana_corrida,
  c.salas_ganga, c.bono_reposicion,
  -- Total Variable = Total Comisión + Semana Corrida
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)                            as total_variable,
  -- Total a Pagar = Total Variable + Bono Reposición
  (c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
    + coalesce(case when c.dias_trabajados > 0 and c.inab is not null
        then round((c.com_pnv + c.bono_4pct + c.com_maquinas + c.com_efectividad)
                   / c.dias_trabajados * c.inab, 0) end, 0)
    + c.bono_reposicion                                                                as total_a_pagar
from calc c
join public.comision_plan p on p.id = c.plan_id
where public.es_gerencia();   -- solo gerencia/admin ve comisiones

-- 3. El grant sobre la vista sigue igual (el nombre no cambia)
grant select on public.v_comision_vendedor_mes to authenticated;

-- ============================================================================
-- FIN. Verificar que el override funcione:
--   update comision_entrada_mensual
--     set pnv_logro_override = 1.00
--   where vendedor_id = <id_diego> and anio = 2026 and mes = 5;
--   -- Debería mostrar com_pnv = 271.250 aunque logro_pnv sea 1.04.
-- ============================================================================
