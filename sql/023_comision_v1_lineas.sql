-- ============================================================================
-- Kreems · Propuesta de Comisiones v1 — separar Amplitud de Penetración NY
-- ----------------------------------------------------------------------------
-- El bloque de portafolio (15%) se divide en DOS KPIs:
--   · Penetración Galletas NY (10% → 0,50% s/venta): meta en `meta_amplitud`
--     (fracción, ej. 0.30 = 30% de clientes con la línea nueva).
--   · Amplitud de portafolio  ( 5% → 0,25% s/venta): promedio de líneas
--     distintas por cliente; meta en la NUEVA columna `meta_lineas` (ej. 2).
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

alter table public.comision_v1_meta
    add column if not exists meta_lineas numeric;  -- líneas x cliente objetivo (ej. 2)

-- ============================================================================
-- FIN.
-- ============================================================================
