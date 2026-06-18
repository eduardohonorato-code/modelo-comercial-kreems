-- ============================================================================
-- 006 — Proyección a cierre con días hábiles DINÁMICOS para el mes en curso
-- ============================================================================
-- Problema: la proyección usaba calendario_laboral.dias_trabajados, un valor
-- ESTÁTICO. Para junio 2026 quedó en 3 (se cargó el 3-jun) y nunca se actualizó,
-- así que la proyección dividía por 3 días (× ~7) e inflaba el % de proyección
-- (ej. Carlos Sanhueza: 48,75% cumplimiento -> 341% proyección).
--
-- Solución: para el MES EN CURSO, contar los días hábiles (Lun-Vie) transcurridos
-- del 1° hasta hoy, capado a dias_totales. Para meses pasados se usa el valor
-- guardado (que ya considera feriados). Se autocorrige a diario sin tocar datos.
--
-- Nota: el conteo dinámico no descuenta feriados intra-mes (usa Lun-Vie). Es una
-- aproximación muy superior a la anterior; los meses cerrados conservan su valor
-- exacto con feriados. Si se requiere precisión de feriados en el mes en curso,
-- agregar una tabla de feriados y restarlos aquí.
-- ============================================================================

drop view if exists public.v_resumen_vendedor_mes;
create or replace view public.v_resumen_vendedor_mes
with (security_invoker = true) as
with ventas as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         sum(neto) as fact_nc,                                  -- NC ya viene negativo
         count(distinct case when upper(tipo_dcto) like 'FACTURA%' then n_dcto end) as n_docs,
         count(distinct case when upper(tipo_dcto) like 'FACTURA%' then n_dcto end) as n_facturas,
         count(distinct case when upper(tipo_dcto) like 'NOTA%'    then n_dcto end) as n_notas_credito,
         sum(case when upper(tipo_dcto) like 'FACTURA%' then neto else 0 end)       as monto_facturas,
         sum(case when upper(tipo_dcto) like 'NOTA%'    then neto else 0 end)       as monto_notas_credito
  from public.fact_ventas
  group by 1,2,3
),
maquinas as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         count(*) filter (where tipo_mov = 'nueva')                          as maq_gestionadas,
         count(*) filter (where tipo_mov = 'nueva' and estado = 'entregada') as maq_entregadas,
         count(*) filter (where tipo_mov = 'retiro')                         as maq_retiros
  from public.fact_maquinas
  group by 1,2,3
),
pedidos as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         sum(case when not facturado then neto else 0 end) as no_facturado_monto,
         count(*) filter (where not facturado)             as no_facturado_docs
  from public.fact_pedidos
  group by 1,2,3
),
periodos as (
  select vendedor_id, anio, mes from ventas
  union select vendedor_id, anio, mes from maquinas
  union select vendedor_id, anio, mes from pedidos
  union select vendedor_id, anio, mes from public.objetivos_mensuales
)
select
  p.vendedor_id,
  dv.nombre_canonico,
  p.anio,
  p.mes,
  -- Fact-NC = facturas - notas de crédito
  coalesce(ve.fact_nc, 0)                                            as fact_nc,
  coalesce(ve.n_docs, 0)                                             as n_documentos,
  coalesce(ve.n_facturas, 0)                                         as n_facturas,
  coalesce(ve.n_notas_credito, 0)                                    as n_notas_credito,
  coalesce(ve.monto_facturas, 0)                                     as monto_facturas,
  coalesce(ve.monto_notas_credito, 0)                                as monto_notas_credito,
  -- Días hábiles efectivos: dinámicos para el mes en curso (ver dt), guardados si no.
  dt.dias_trab_efectivo                                              as dias_trabajados,
  cal.dias_totales,
  -- Proyección lineal a cierre = (Fact-NC / días_trabajados) * días_totales
  case when dt.dias_trab_efectivo > 0
       then round(coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * cal.dias_totales, 2)
  end                                                               as proyeccion_cierre,
  o.obj_venta,
  o.obj_maquinas,
  o.obj_visitas,
  -- % Cumplimiento = Fact-NC / objetivo de venta
  case when o.obj_venta > 0
       then round(coalesce(ve.fact_nc,0) / o.obj_venta, 4)
  end                                                               as pct_cumplimiento,
  -- % Proyección = Proyección / objetivo de venta
  case when o.obj_venta > 0 and dt.dias_trab_efectivo > 0
       then round((coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * cal.dias_totales) / o.obj_venta, 4)
  end                                                               as pct_proyeccion,
  -- % Efectividad = N° de facturas / objetivo de visitas
  case when o.obj_visitas > 0
       then round(coalesce(ve.n_docs,0)::numeric / o.obj_visitas, 4)
  end                                                               as pct_efectividad,
  coalesce(pe.no_facturado_monto, 0)                                as no_facturado_monto,
  coalesce(pe.no_facturado_docs, 0)                                 as no_facturado_docs,
  coalesce(ma.maq_gestionadas, 0)                                   as maquinas_gestionadas,
  coalesce(ma.maq_entregadas, 0)                                    as maquinas_entregadas,
  coalesce(ma.maq_retiros, 0)                                       as maquinas_retiros,
  -- Conversión gestionada -> entregada
  case when coalesce(ma.maq_gestionadas,0) > 0
       then round(coalesce(ma.maq_entregadas,0)::numeric / ma.maq_gestionadas, 4)
  end                                                               as conversion_gestionada_entregada
from periodos p
join      public.dim_vendedor       dv  on dv.id = p.vendedor_id
left join ventas                    ve  on ve.vendedor_id = p.vendedor_id and ve.anio = p.anio and ve.mes = p.mes
left join maquinas                  ma  on ma.vendedor_id = p.vendedor_id and ma.anio = p.anio and ma.mes = p.mes
left join pedidos                   pe  on pe.vendedor_id = p.vendedor_id and pe.anio = p.anio and pe.mes = p.mes
left join public.objetivos_mensuales o  on o.vendedor_id  = p.vendedor_id and o.anio  = p.anio and o.mes  = p.mes
left join public.calendario_laboral cal on cal.anio = p.anio and cal.mes = p.mes
-- Días hábiles efectivos: si (anio,mes) es el mes actual, cuenta Lun-Vie del 1°
-- a hoy (capado a dias_totales); si es un mes pasado, usa el valor guardado.
cross join lateral (
  select case
    when p.anio = extract(year  from current_date)::int
     and p.mes  = extract(month from current_date)::int
    then least(
           (select count(*)::smallint
              from generate_series(date_trunc('month', current_date)::date,
                                   current_date, interval '1 day') g
             where extract(isodow from g) < 6),
           cal.dias_totales)
    else cal.dias_trabajados
  end as dias_trab_efectivo
) dt;
