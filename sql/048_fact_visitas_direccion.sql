-- ============================================================================
-- Kreems · Visitas: dirección (sucursal) visitada
-- ----------------------------------------------------------------------------
-- Autoventa agenda y cuenta las visitas POR DIRECCIÓN: una cadena con varias
-- sucursales bajo el mismo RUT tiene una agenda por local. El panel contaba
-- una visita por RUT por semana y juntaba las sucursales (oct-2026: Mauricio
-- 113 en la app vs 129 en el reporte "Cobertura / Efectividad").
--
--   direccion      calle del local visitado (address_street de la API)
--   direccion_id   llave del local: coordenadas de la dirección en Autoventa
--                  (las fichas duplicadas de un mismo local cuentan una vez);
--                  sin coordenadas, id del cliente + calle
--
-- Contando una visita por local por semana: sep-2026 2,0% de error contra el
-- reporte (antes 3,7%), oct-2026 al día 9 3,5% (antes 5,0%; Mauricio 131 vs 129).
--
-- Idempotente. Correr en el SQL Editor de Supabase (proyecto kfxdtjkendmaguzckxqs)
-- y después recargar los meses:  python -m etl.run_visitas_api --periodo 2026-10
-- ============================================================================

alter table public.fact_visitas add column if not exists direccion    text;
alter table public.fact_visitas add column if not exists direccion_id text;

-- Para que PostgREST vea las columnas nuevas sin esperar
notify pgrst, 'reload schema';
