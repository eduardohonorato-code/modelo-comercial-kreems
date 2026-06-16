-- ============================================================================
-- Kreems · Comisiones — bajar piso de máquinas a 25% y agregar tramo base
-- ----------------------------------------------------------------------------
-- Agrega el tramo maquinas 25% = $40.000 (plan 1 Kreems Normal).
-- El piso de comision_ajustar_logro para máquinas baja de 0.40 a 0.25 en la
-- vista, para que logros del 25% al 39% no queden descartados antes de buscar.
--
-- OPCIÓN A (recomendada si 25–39% todos ganan $40k):
--   Agrega tramos en 25%, 30% y 35% = $40.000 cada uno.
-- OPCIÓN B (solo el tramo exacto 25%):
--   Agrega únicamente 25% = $40.000; logros 28–37% siguen en $0.
--
-- Descomenta la opción que aplique. Por defecto viene la Opción A activa.
-- ============================================================================

-- ── OPCIÓN A: rango 25-39% completo paga $40.000 ───────────────────────────
insert into public.comision_tramo_maquinas (plan_id, logro_pct, monto) values
  (1, 0.25, 40000),
  (1, 0.30, 40000),
  (1, 0.35, 40000)
on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- ── OPCIÓN B: solo el tramo 25% (descomenta si prefieres esta) ──────────────
-- insert into public.comision_tramo_maquinas (plan_id, logro_pct, monto) values
--   (1, 0.25, 40000)
-- on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- ── Recrear vista con piso de máquinas = 0.25 ───────────────────────────────
-- (mantiene todo lo demás igual que 008_overrides_tramo.sql)
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
    public.comision_ajustar_logro(
      coalesce(b.pnv_logro_override, b.logro_pnv),       0.05, 0.80, 1.10) as pnv_aj,
    -- piso máquinas: 0.25 (antes 0.40) para incluir el tramo base de $40.000
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
-- Verificar resultado esperado:
--   logro 25% (1 maq / obj 4)  → com_maquinas = 40.000
--   logro 30% (3 maq / obj 10) → com_maquinas = 40.000  (Opción A)
--   logro 40% (2 maq / obj 5)  → com_maquinas = 152.000
--   logro 20% (1 maq / obj 5)  → com_maquinas = 0  (bajo piso)
-- ============================================================================
