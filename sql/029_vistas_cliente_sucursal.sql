-- ============================================================================
-- Kreems · Vistas de agregación mensual: cliente × mes y sucursal × mes
-- ----------------------------------------------------------------------------
-- Problema: las páginas de Clientes y Sucursales se traían fact_ventas ENTERA
-- desde PostgREST (paginando de 1000 en 1000) y agregaban en pandas. Con 64K
-- líneas eso ya costaba ~8,3 s por consulta; al cargar el histórico 2023-2024
-- (~200K líneas) pasaría a ~25 s y Streamlit Cloud cortaría antes.
--
-- Estas vistas hacen el GROUP BY en Postgres: la app pasa a leer ~10K filas ya
-- agregadas en vez de 200K crudas, y el tamaño del histórico deja de importar.
-- Es lo que pide el CLAUDE.md (§7): "Leer las métricas desde las vistas, no
-- recalcular en el front".
--
-- Grano: (cliente/dirección, sociedad, mes). La sociedad va EN EL GRANO para que
-- el filtro "Acuña / Gran Natural / Ambas" de la app siga funcionando: la app
-- suma las filas que correspondan.
--
-- Definiciones (idénticas a las que hacía pandas, ver §3 del CLAUDE.md):
--   fact_nc    = SUM(neto)                        · las NC ya vienen negativas
--   n_facturas = COUNT(DISTINCT n_dcto) de FACTURAS (las NC no son visitas)
--
-- RLS: security_invoker = true → se aplican las políticas de fact_ventas (un
-- vendedor ve solo sus líneas, gerencia todo), y esas políticas ya envuelven las
-- funciones de auth en un subselect (sql/018) para que se evalúen una vez por
-- query y no por fila. Sin eso, esta agregación volvería a cruzar el
-- statement_timeout, que es exactamente lo que pasó al cargar el histórico 2025.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

-- ── Cliente × mes ───────────────────────────────────────────────────────────
create or replace view public.v_cliente_mes
with (security_invoker = true) as
select
    v.cliente_rut,
    v.sociedad_id,
    to_char(v.fecha, 'YYYY-MM')                                  as ym,
    sum(v.neto)                                                  as fact_nc,
    count(distinct v.n_dcto) filter (
        where v.tipo_dcto ilike '%factura%')                     as n_facturas
from public.fact_ventas v
where v.cliente_rut is not null
group by v.cliente_rut, v.sociedad_id, to_char(v.fecha, 'YYYY-MM');

-- ── Sucursal × mes ──────────────────────────────────────────────────────────
-- Solo las líneas con sucursal identificada. Lo no atribuible (NC de anulación,
-- ventas sin dirección en el ERP) se expone aparte en v_sin_sucursal_mes: se
-- declara, no se reparte.
create or replace view public.v_sucursal_mes
with (security_invoker = true) as
select
    v.direccion_id,
    v.cliente_rut,
    v.sociedad_id,
    to_char(v.fecha, 'YYYY-MM')                                  as ym,
    sum(v.neto)                                                  as fact_nc,
    count(distinct v.n_dcto) filter (
        where v.tipo_dcto ilike '%factura%')                     as n_facturas
from public.fact_ventas v
where v.direccion_id is not null
group by v.direccion_id, v.cliente_rut, v.sociedad_id,
         to_char(v.fecha, 'YYYY-MM');

create or replace view public.v_sin_sucursal_mes
with (security_invoker = true) as
select
    v.cliente_rut,
    v.sociedad_id,
    to_char(v.fecha, 'YYYY-MM')                                  as ym,
    sum(v.neto)                                                  as fact_nc
from public.fact_ventas v
where v.direccion_id is null and v.cliente_rut is not null
group by v.cliente_rut, v.sociedad_id, to_char(v.fecha, 'YYYY-MM');

grant select on public.v_cliente_mes      to authenticated;
grant select on public.v_sucursal_mes     to authenticated;
grant select on public.v_sin_sucursal_mes to authenticated;

-- Índices que sostienen el GROUP BY cuando crezca el histórico.
create index if not exists ix_fact_ventas_cliente_fecha
    on public.fact_ventas(cliente_rut, fecha);
create index if not exists ix_fact_ventas_fecha
    on public.fact_ventas(fecha);

-- ============================================================================
-- FIN. Tras ejecutar, la app usa estas vistas automáticamente (app/data.py).
-- ============================================================================
