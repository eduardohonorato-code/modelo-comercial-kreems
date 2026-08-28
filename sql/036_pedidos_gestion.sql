-- ============================================================================
-- 036 · Fecha de ingreso del pedido y estado de gestión (fact_pedidos)
-- ============================================================================
-- Por qué: para medir la GESTIÓN del vendedor hay que contar el pedido cuando
-- él lo ingresa, no cuando se factura. Hoy `fact_pedidos.fecha` guarda la fecha
-- del DTE (o la de despacho solicitada, si el pedido aún no factura), y entre
-- una cosa y otra pasan semanas: el pedido 3898 se ingresó el 22-06-2026 y se
-- facturó el 28-08-2026, 67 días después. Con solo esa columna, un pedido
-- "salta" de mes al facturarse y la gestión del vendedor queda contada en el
-- mes equivocado.
--
-- La API de Autoventa sí expone el dato: cada línea (tanto en /requests como en
-- /invoices) trae `created_at` = cuándo se creó el pedido, y `status`.
--
-- Correr en el SQL Editor de Supabase. Es idempotente.
-- ============================================================================

alter table public.fact_pedidos
  add column if not exists fecha_pedido   date,
  add column if not exists estado_pedido  text;

comment on column public.fact_pedidos.fecha_pedido is
  'Fecha en que el vendedor ingresó el pedido en Autoventa (created_at de la '
  'línea). Es la fecha con la que se mide la gestión; `fecha` es la del DTE.';
comment on column public.fact_pedidos.estado_pedido is
  'Estado de la línea en Autoventa (invoiced, pending, …). Complementa a '
  'doc_venta: "Sin DTE" = ingresado y todavía no gestionado.';

-- Se consulta por rango de fecha de ingreso y por producto (los FL de máquina).
create index if not exists ix_pedidos_fecha_pedido
  on public.fact_pedidos (fecha_pedido);
create index if not exists ix_pedidos_prod_fecha_pedido
  on public.fact_pedidos (producto_codigo, fecha_pedido);
