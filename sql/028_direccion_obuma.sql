-- ============================================================================
-- Kreems · Sucursales también para Obuma/Acuña (segunda fuente de direcciones)
-- ----------------------------------------------------------------------------
-- sql/027 pobló dim_direccion desde Autoventa, que solo cubre Gran Natural. Pero
-- el export Excel de Obuma trae la dirección del cliente EN CADA LÍNEA:
--     "CLIENTE CODIGO Direccion" + "CLIENTE Direccion"
-- con lo que Acuña —y las notas de crédito, que no tienen pedido— también pueden
-- atribuirse a su sucursal. Sin esto, toda la facturación de Acuña de una cadena
-- con muchos locales queda como un solo número "sin sucursal".
--
-- OJO con los códigos de Obuma: están sucios. El mismo local físico aparece con
-- dos o más códigos distintos, y hay códigos alfanuméricos mezclados con
-- numéricos. Por eso la identidad de una sucursal NO
-- es el código sino (cliente_rut + dirección normalizada) → `dir_norm`, y el id
-- de las direcciones de Obuma es un hash estable y NEGATIVO de esa llave (los
-- positivos son los address_id de Autoventa).
--
-- Si una dirección de Obuma coincide (mismo rut + dir_norm) con una que ya vino
-- de Autoventa, se reusa el id de Autoventa: es el mismo local, no se duplica.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

alter table public.dim_direccion
    add column if not exists origen         text,   -- 'autoventa' | 'obuma'
    add column if not exists codigo_externo text,   -- código tal cual del ERP
    add column if not exists dir_norm       text;   -- dirección normalizada (identidad)

create index if not exists ix_dim_direccion_norm
    on public.dim_direccion(cliente_rut, dir_norm);

-- Las filas ya cargadas vienen todas de Autoventa (sql/027).
update public.dim_direccion set origen = 'autoventa' where origen is null;

-- ============================================================================
-- FIN. Tras ejecutar:
--   python -m etl.run_direcciones_obuma --desde 2025-06 --hasta 2026-07
-- ============================================================================
