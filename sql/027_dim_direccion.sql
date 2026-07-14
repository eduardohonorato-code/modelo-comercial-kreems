-- ============================================================================
-- Kreems · Sucursales / direcciones de cliente (facturación por punto de venta)
-- ----------------------------------------------------------------------------
-- Un mismo RUT puede comprar en varias direcciones: una casa matriz y sus locales
-- (cadenas, concesionarios de casinos, empresas con varias sucursales).
-- Hasta ahora toda la app agregaba por RUT y esas sucursales quedaban sumadas en
-- un solo número.
--
-- Fuente (verificado 2026-07-14 contra la API):
--   · dim_direccion   ← GET /clients?expand[]=r_client_addresses&expand[]=address_detail
--                       (Autoventa: id de dirección estable, nombre, ruta, GPS).
--   · direccion_id de un PEDIDO   ← /requests.dispatch_address_id (100% poblado).
--   · direccion_id de una FACTURA ← su línea trae request_id → dispatch_address_id
--                       del pedido. En junio 2026: 518/528 facturas resueltas
--                       (100% del neto) y NINGUNA factura mezcla dos direcciones,
--                       así que la atribución documento→sucursal es 1 a 1.
--
-- Cobertura: solo GRAN NATURAL (sus pedidos pasan por Autoventa). Acuña se carga
-- del Excel de Obuma, que no trae dirección → direccion_id queda NULL. Las notas
-- de crédito se emiten en Obuma sin pedido asociado → también NULL. Por eso la
-- columna es nullable y la app debe tratar NULL como "sin sucursal asignada".
--
-- Se puebla con `python -m etl.run_direcciones --desde 2026-02 --hasta 2026-07`
-- (backfill) y, de ahí en adelante, en cada corrida de run_autoventa_api.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

create table if not exists public.dim_direccion (
    id            bigint primary key,            -- address_id de Autoventa (estable)
    cliente_rut   text references public.dim_cliente(rut),
    nombre        text,                          -- nombre del local, o "Dirección principal"
    direccion     text,
    comuna        text,                          -- locality en Autoventa
    ciudad        text,                          -- city (viene con la región)
    ruta          text,                          -- código de ruta (RM23-Q2, CN14, ...)
    latitud       numeric(12,9),
    longitud      numeric(12,9),
    es_principal  boolean not null default false, -- main = casa matriz
    activa        boolean not null default true,
    updated_at    timestamptz not null default now()
);

create index if not exists ix_dim_direccion_rut on public.dim_direccion(cliente_rut);

-- Columna de sucursal en los hechos. Nullable: ver nota de cobertura arriba.
alter table public.fact_ventas
    add column if not exists direccion_id bigint references public.dim_direccion(id);
alter table public.fact_pedidos
    add column if not exists direccion_id bigint references public.dim_direccion(id);

create index if not exists ix_fact_ventas_direccion  on public.fact_ventas(direccion_id);
create index if not exists ix_fact_pedidos_direccion on public.fact_pedidos(direccion_id);

-- RLS: es una dimensión de referencia, mismo trato que dim_cliente (lectura para
-- autenticados). El filtro por vendedor lo sigue aplicando fact_ventas.
alter table public.dim_direccion enable row level security;

drop policy if exists dim_direccion_sel on public.dim_direccion;
create policy dim_direccion_sel on public.dim_direccion
    for select to authenticated using (true);

grant select on public.dim_direccion to authenticated;
grant select, insert, update, delete on public.dim_direccion to service_role;

-- ============================================================================
-- FIN. Tras ejecutar:
--   python -m etl.run_direcciones --desde 2026-02 --hasta 2026-07
-- ============================================================================
