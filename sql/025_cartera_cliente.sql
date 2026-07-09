-- ============================================================================
-- Kreems · Cartera oficial de clientes por vendedor
-- ----------------------------------------------------------------------------
-- Fuente: reporte de Autoventa "clientes.xlsx" (casas matrices) +
-- "clientes_direcciones.xlsx" (sucursales). La columna "Vend. exclusivo" trae
-- el código de vendedor (= dim_vendedor.cod_vendedor_autoventa: 32304 Mauricio,
-- 33226 Marcela, 32312 Rigo, ...). La API de Autoventa NO expone este campo
-- (verificado exhaustivo 2026-07-09), así que se carga desde el reporte con
-- `python -m etl.cargar_cartera <clientes.xlsx> [<direcciones.xlsx>]`.
--
-- Usos: meta real del KPI de cobertura (Comisiones v1), atribución de clientes
-- dormidos al vendedor DUEÑO de la cartera (no solo al último que facturó), y
-- resolver los ~370 dormidos huérfanos de ex-vendedores.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

create table if not exists public.cartera_cliente (
    cliente_rut     text primary key,          -- normalizado XX.XXX.XXX-X
    vendedor_id     integer references public.dim_vendedor(id),  -- NULL = código sin mapear
    cod_vendedor    text,                      -- "Vend. exclusivo" crudo del reporte
    codigo_cliente  text,                      -- Código cliente Autoventa
    nombre          text,
    ruta            text,                      -- código de ruta (RM11-Q2, VL14, ...)
    n_sucursales    integer not null default 0,
    updated_at      timestamptz not null default now()
);

alter table public.cartera_cliente enable row level security;

-- Lectura: gerencia ve todo; un vendedor solo su cartera (mismo patrón fact_*).
drop policy if exists cartera_cliente_select on public.cartera_cliente;
create policy cartera_cliente_select on public.cartera_cliente
    for select to authenticated
    using (
        (select public.es_gerencia())
        or vendedor_id in (select id from public.dim_vendedor
                           where user_id = (select auth.uid()))
    );

-- Escritura desde la app: solo gerencia (el ETL usa service_role).
drop policy if exists cartera_cliente_admin on public.cartera_cliente;
create policy cartera_cliente_admin on public.cartera_cliente
    for all to authenticated
    using (public.es_gerencia()) with check (public.es_gerencia());

grant select on public.cartera_cliente to authenticated;
grant insert, update, delete on public.cartera_cliente to authenticated;
grant select, insert, update, delete on public.cartera_cliente to service_role;

-- ============================================================================
-- FIN. Tras ejecutar: python -m etl.cargar_cartera "<ruta clientes.xlsx>" "<ruta direcciones.xlsx>"
-- ============================================================================
