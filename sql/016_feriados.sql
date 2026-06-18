-- ============================================================================
-- 016 — Feriados (calendario chileno) y proyección que los descuenta
-- ============================================================================
-- Mejora sobre 015: el conteo de días hábiles del MES EN CURSO ahora descuenta
-- feriados, no solo sábados/domingos. Tanto los días transcurridos como el total
-- del mes se calculan dinámicamente (Lun-Vie y NOT IN feriados), así que la
-- proyección y la semana corrida quedan correctas en meses como abril (Viernes
-- Santo) o septiembre (18/19), sin tocar datos.
--
-- Importante: solo se calcula dinámicamente el MES EN CURSO. Los meses cerrados
-- conservan su calendario_laboral guardado (no se alteran comisiones ya hechas).
-- ============================================================================

-- 1. Tabla de feriados ------------------------------------------------------
create table if not exists public.feriados (
  fecha         date    primary key,
  nombre        text    not null,
  irrenunciable boolean not null default false
);
comment on table public.feriados is
  'Feriados legales de Chile. Usados para el conteo de días hábiles del mes en curso.';

alter table public.feriados enable row level security;
drop policy if exists feriados_sel on public.feriados;
create policy feriados_sel on public.feriados
  for select to authenticated using (true);

-- 2. Feriados nacionales 2026 (calendario chileno oficial) ------------------
insert into public.feriados (fecha, nombre, irrenunciable) values
  ('2026-01-01', 'Año Nuevo',                              true),
  ('2026-04-03', 'Viernes Santo',                          false),
  ('2026-04-04', 'Sábado Santo',                           false),
  ('2026-05-01', 'Día del Trabajo',                        true),
  ('2026-05-21', 'Día de las Glorias Navales',             false),
  ('2026-06-21', 'Día Nacional de los Pueblos Indígenas',  false),
  ('2026-06-29', 'San Pedro y San Pablo',                  false),
  ('2026-07-16', 'Día de la Virgen del Carmen',            false),
  ('2026-08-15', 'Asunción de la Virgen',                  false),
  ('2026-09-18', 'Independencia Nacional',                 true),
  ('2026-09-19', 'Día de las Glorias del Ejército',        true),
  ('2026-10-12', 'Encuentro de Dos Mundos',                false),
  ('2026-10-31', 'Día de las Iglesias Evangélicas',        false),
  ('2026-11-01', 'Día de Todos los Santos',                false),
  ('2026-12-08', 'Inmaculada Concepción',                  false),
  ('2026-12-25', 'Navidad',                                true)
on conflict (fecha) do update
  set nombre = excluded.nombre, irrenunciable = excluded.irrenunciable;

-- 3. Corregir el valor guardado de junio 2026 -------------------------------
--    Estaba en dias_trabajados=3 (se cargó el 3-jun). Para cuando junio sea un
--    mes cerrado debe valer sus días hábiles reales (22 Lun-Vie − 1 feriado
--    hábil, el 29-jun lunes = 21). dias_totales ya estaba en 21.
update public.calendario_laboral
   set dias_trabajados = 21
 where anio = 2026 and mes = 6;

-- 4. Vista con días hábiles dinámicos que descuentan feriados ---------------
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
  coalesce(ve.fact_nc, 0)                                            as fact_nc,
  coalesce(ve.n_docs, 0)                                             as n_documentos,
  coalesce(ve.n_facturas, 0)                                         as n_facturas,
  coalesce(ve.n_notas_credito, 0)                                    as n_notas_credito,
  coalesce(ve.monto_facturas, 0)                                     as monto_facturas,
  coalesce(ve.monto_notas_credito, 0)                                as monto_notas_credito,
  dt.dias_trab_efectivo::smallint                                    as dias_trabajados,
  dt.dias_tot_efectivo::smallint                                     as dias_totales,
  -- Proyección lineal a cierre = (Fact-NC / días_trabajados) * días_totales
  case when dt.dias_trab_efectivo > 0
       then round(coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * dt.dias_tot_efectivo, 2)
  end                                                               as proyeccion_cierre,
  o.obj_venta,
  o.obj_maquinas,
  o.obj_visitas,
  case when o.obj_venta > 0
       then round(coalesce(ve.fact_nc,0) / o.obj_venta, 4)
  end                                                               as pct_cumplimiento,
  case when o.obj_venta > 0 and dt.dias_trab_efectivo > 0
       then round((coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * dt.dias_tot_efectivo) / o.obj_venta, 4)
  end                                                               as pct_proyeccion,
  case when o.obj_visitas > 0
       then round(coalesce(ve.n_docs,0)::numeric / o.obj_visitas, 4)
  end                                                               as pct_efectividad,
  coalesce(pe.no_facturado_monto, 0)                                as no_facturado_monto,
  coalesce(pe.no_facturado_docs, 0)                                 as no_facturado_docs,
  coalesce(ma.maq_gestionadas, 0)                                   as maquinas_gestionadas,
  coalesce(ma.maq_entregadas, 0)                                    as maquinas_entregadas,
  coalesce(ma.maq_retiros, 0)                                       as maquinas_retiros,
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
-- Días hábiles efectivos. Mes EN CURSO: Lun-Vie del 1° a hoy (y total del mes),
-- descontando feriados. Mes pasado/cerrado: valores guardados (no se recalculan,
-- para no alterar comisiones ya hechas).
cross join lateral (
  select
    case
      when p.anio = extract(year from current_date)::int
       and p.mes  = extract(month from current_date)::int
      then (select count(*)
              from generate_series(date_trunc('month', current_date)::date,
                                   current_date, interval '1 day') g
             where extract(isodow from g) < 6
               and g::date not in (select f.fecha from public.feriados f))
      else cal.dias_trabajados
    end as dias_trab_efectivo,
    case
      when p.anio = extract(year from current_date)::int
       and p.mes  = extract(month from current_date)::int
      then (select count(*)
              from generate_series(date_trunc('month', current_date)::date,
                                   (date_trunc('month', current_date) + interval '1 month' - interval '1 day')::date,
                                   interval '1 day') g
             where extract(isodow from g) < 6
               and g::date not in (select f.fecha from public.feriados f))
      else cal.dias_totales
    end as dias_tot_efectivo
) dt;
