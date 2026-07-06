-- ============================================================================
-- Kreems · Propuesta de Comisiones v1 — metas por vendedor/mes
-- ----------------------------------------------------------------------------
-- Modelo NUEVO (convive con el actual, no lo reemplaza): scorecard de 5 KPIs
-- ponderados → tasa efectiva 0–5% aplicada sobre la venta REAL (Fact-NC).
--   1. Cuota de venta            50%  (2,50% s/venta)
--   2. Clientes nuevos + react.  15%  (0,75%)
--   3. Cobertura de cartera      15%  (0,75%)
--   4. Amplitud portafolio (NY)  15%  (0,75%)
--   5. Efectividad de visita      5%  (0,25%)
-- Regla de pago: cada KPI paga proporcional desde el 80% de su meta; tope 5%.
--
-- El cálculo se hace en la app (pandas), reusando el detalle de fact_ventas.
-- Aquí SOLO persistimos las METAS editables por vendedor/mes. Las metas que
-- ya existen en otras tablas (obj_venta, obj_visitas, cartera) se usan como
-- default cuando la columna respectiva queda NULL.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

create table if not exists public.comision_v1_meta (
    vendedor_id        integer not null references public.dim_vendedor(id) on delete cascade,
    anio               integer not null,
    mes                integer not null check (mes between 1 and 12),
    meta_venta         numeric,   -- NULL → usa obj_venta de objetivos_mensuales
    meta_nuevos_react  integer,   -- clientes nuevos + reactivados objetivo del mes
    meta_cobertura     integer,   -- nº clientes a cubrir; NULL → usa cartera_clientes
    meta_amplitud      numeric,   -- líneas distintas por cliente objetivo (ej. 4)
    meta_visitas       integer,   -- NULL → usa obj_visitas de objetivos_mensuales
    updated_at         timestamptz not null default now(),
    primary key (vendedor_id, anio, mes)
);

alter table public.comision_v1_meta enable row level security;

-- Solo gerencia/admin lee y escribe (toda la sección Comisiones es gerencia-only).
drop policy if exists comision_v1_meta_admin on public.comision_v1_meta;
create policy comision_v1_meta_admin on public.comision_v1_meta
    for all to authenticated
    using (public.es_gerencia()) with check (public.es_gerencia());

grant select, insert, update, delete on public.comision_v1_meta to authenticated;

-- ============================================================================
-- FIN. Tras ejecutar, la pestaña "Propuesta de Comisiones v1" puede guardar metas.
-- ============================================================================
