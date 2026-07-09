-- ============================================================================
-- Kreems · Propuesta de Comisiones v1.1 — meta de profundidad SKU
-- ----------------------------------------------------------------------------
-- Rediseño acordado con gerencia (2026-07-09):
--   · Efectividad de visita se ELIMINA (se fusiona en Cobertura de cartera).
--   · El 35% restante tras Cuota (50%) y Nuevos+Reactivados (15%) se reparte
--     parejo: Cobertura / Amplitud de categorías / Profundidad SKU (11,67% c/u).
--   · Profundidad SKU = promedio de SKUs distintos por categoría que lleva el
--     cliente (todo el portafolio, excluye Máquinas/Servicios). Meta en la
--     NUEVA columna `meta_skus` (default 4).
--   · Nuevos+Reactivados sigue siendo UN KPI con meta AUTOMÁTICA
--     (2% de cartera + 10% de los dormidos del vendedor); `meta_nuevos_react`
--     pasa a ser el override manual (NULL = automático).
-- Columnas legacy que quedan sin uso: meta_amplitud (era % Galletas NY),
-- meta_visitas (era efectividad). No se borran para no perder historial.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

alter table public.comision_v1_meta
    add column if not exists meta_skus numeric;  -- SKUs x categoría objetivo (ej. 4)

-- ============================================================================
-- FIN.
-- ============================================================================
