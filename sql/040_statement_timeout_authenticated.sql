-- ============================================================================
-- 040 — Subir el statement_timeout del rol `authenticated` (8s → 20s)
-- ============================================================================
-- CONTEXTO: `v_resumen_vendedor_mes` agrega TODO el histórico de fact_ventas en
-- cada llamada (el filtro anio/mes no se puede empujar a un índice porque son
-- columnas calculadas). Con 266.000 líneas ya tarda ~1,6 s en caliente y ~4-8 s
-- en frío, medido con service_role. `v_comision_vendedor_mes` se construye
-- encima, así que siempre es más cara. Cuando la base está exigida, cruza el
-- statement_timeout de 8 s que Supabase le pone por defecto al rol
-- `authenticated` y devuelve 57014 → en Streamlit Cloud se ve como un
-- "APIError" con el mensaje censurado.
--
-- Esto es un COLCHÓN, no la cura. La causa de fondo del último episodio era la
-- pestaña "Propuesta de Comisiones v1", que traía 180.000 filas de histórico
-- (67 s) en CADA render de la página de Comisiones — arreglado en la app. Si
-- más adelante la vista sigue creciendo, el arreglo estructural es reescribir
-- v_resumen_vendedor_mes con una sola pasada (union all + group by) para que el
-- filtro de período llegue hasta las tablas de hechos.
--
-- Idempotente. Correr en el SQL Editor de Supabase.
-- Para revertir:  alter role authenticated reset statement_timeout;
-- ============================================================================

alter role authenticated set statement_timeout = '20s';

-- PostgREST cachea la config de conexión: avisarle que recargue.
notify pgrst, 'reload config';

-- ============================================================================
-- Verificar (el valor nuevo aplica a conexiones nuevas):
--   select rolname, rolconfig from pg_roles where rolname = 'authenticated';
--   -- debe listar {statement_timeout=20s}
-- ============================================================================
