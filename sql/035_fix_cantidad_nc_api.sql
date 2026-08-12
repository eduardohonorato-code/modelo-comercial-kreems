-- 035: Reparación del signo de cantidad en notas de crédito cargadas por API
--
-- Causa raíz: etl/loaders/obuma_api.py invertía el signo solo de neto y costo,
-- no de cantidad. La API de Obuma entrega la cantidad de una NC en POSITIVO
-- (el export Excel ya la trae negativa), así que cada devolución SUMABA cajas
-- en vez de restarlas y las cajas netas quedaban infladas.
-- Corregido en el loader; este script repara lo ya cargado.
--
-- Alcance: solo Gran Natural (sociedad 2) desde jun-2026, que es cuando esa
-- sociedad pasó a cargarse por API. Acuña (sociedad 1) y GN hasta may-2026
-- vinieron por Excel con la cantidad ya negativa: NO se tocan.
--
-- Impacto medido al 12-ago-2026: jun +197, jul +161, ago +89 cajas de más
-- (~15% de sobreestimación de cajas netas en julio).
--
-- Idempotente: solo actúa sobre filas de NC que todavía tengan cantidad > 0.
-- Re-ejecutarlo no vuelve a invertir nada.

DO $$
DECLARE
  filas bigint;
BEGIN
  UPDATE fact_ventas
     SET cantidad = -cantidad
   WHERE tipo_dcto ILIKE '%CREDITO%'
     AND cantidad > 0
     AND sociedad_id = 2
     AND fecha >= DATE '2026-06-01';

  GET DIAGNOSTICS filas = ROW_COUNT;
  RAISE NOTICE 'fix signo cantidad NC (API): % filas corregidas.', filas;
END $$;

-- Verificación: no deben quedar NC con cantidad positiva en el rango.
-- SELECT count(*) FROM fact_ventas
--  WHERE tipo_dcto ILIKE '%CREDITO%' AND cantidad > 0
--    AND sociedad_id = 2 AND fecha >= DATE '2026-06-01';
