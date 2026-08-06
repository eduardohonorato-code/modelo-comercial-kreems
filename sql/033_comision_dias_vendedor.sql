-- ============================================================================
-- 033 — Días trabajados por VENDEDOR (meses parciales) para la Semana Corrida
-- ============================================================================
-- PROBLEMA: la Semana Corrida = Total Comisión / dias_trabajados × INAB, y ambos
-- venían SOLO de calendario_laboral, que es global del mes. Un vendedor que
-- trabajó medio mes (ingreso, salida, licencia, reemplazo) cobraba la semana
-- corrida como si hubiera trabajado el mes completo.
--
-- Ej. real (Jorge Jara, jun-2026: trabajó del 1 al 19, 15 de 21 hábiles):
--   mes completo → 369.750 / 21 × 5 = 88.036
--   días reales  → 369.750 / 15 × 2 = 49.300     ← criterio de gerencia
--
-- SOLUCIÓN: dos overrides opcionales por vendedor/mes en comision_entrada_mensual.
--   · NULL (lo normal) → se usa el calendario del mes, igual que hoy.
--   · Con valor        → manda el override.
-- Se llenan desde la app (Comisiones → Editar entradas del período); NO se
-- muestran en la tabla de comisiones.
--
-- La vista sigue exponiendo `dias_trabajados` e `inab` como los valores
-- EFECTIVOS (ya con override aplicado), así que el snapshot de cierre, los
-- exports y el PDF no cambian. Los valores del calendario quedan disponibles
-- aparte en `dias_trabajados_base` / `inab_base`.
--
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

alter table public.comision_entrada_mensual
  add column if not exists dias_trabajados_override smallint,
  add column if not exists inab_override            smallint;

comment on column public.comision_entrada_mensual.dias_trabajados_override is
  'Días trabajados REALES del vendedor en el mes (solo meses parciales). NULL = usar calendario_laboral.';
comment on column public.comision_entrada_mensual.inab_override is
  'Días de descanso (INAB) del tramo efectivamente trabajado. NULL = usar el INAB del mes.';

-- Rangos sanos: nadie trabaja 0 o 40 días hábiles en un mes.
alter table public.comision_entrada_mensual
  drop constraint if exists comision_entrada_dias_chk;
alter table public.comision_entrada_mensual
  add constraint comision_entrada_dias_chk check (
    (dias_trabajados_override is null or dias_trabajados_override between 1 and 31)
    and (inab_override is null or inab_override between 0 and 15)
  );

-- ── Recrear la vista: idéntica a la 014, salvo dias_trabajados / inab ───────
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
    -- Base del calendario (lo que se usaba antes) — se conserva visible.
    r.dias_trabajados                                         as dias_trabajados_base,
    cal.inab                                                  as inab_base,
    e.dias_trabajados_override,
    e.inab_override,
    -- Valores EFECTIVOS para la semana corrida ← cambio del 033
    coalesce(e.dias_trabajados_override, r.dias_trabajados)   as dias_trabajados,
    coalesce(e.inab_override, cal.inab)                       as inab,
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
    -- PNV: redondeo HACIA ABAJO (piso del tramo) ← 014
    public.comision_ajustar_logro_piso(
      coalesce(b.pnv_logro_override, b.logro_pnv),       0.05, 0.80, 1.10) as pnv_aj,
    -- Máquinas: al más cercano, piso 0.25 ← 009
    public.comision_ajustar_logro(
      coalesce(b.maq_logro_override, b.logro_maquinas),  0.05, 0.25, 1.40) as maq_aj,
    -- Efectividad: al más cercano
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
  -- dias_trabajados / inab = EFECTIVOS (override si existe); base = calendario.
  c.dias_trabajados, c.inab,
  c.dias_trabajados_base, c.inab_base,
  c.dias_trabajados_override, c.inab_override,
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
-- Verificar:
--   select nombre_canonico, dias_trabajados_base, dias_trabajados_override,
--          dias_trabajados, inab_base, inab_override, inab, semana_corrida
--     from public.v_comision_vendedor_mes where anio = 2026 and mes = 7;
-- Sin overrides cargados, dias_trabajados = dias_trabajados_base y la semana
-- corrida da exactamente lo mismo que antes de este script.
-- ============================================================================
