-- 031: Reparación one-shot del bug ×100 en fact_ventas.cantidad
--
-- Causa raíz: los exports .xls de Obuma traen números en formato chileno
-- (decimal ','). pd.read_html usaba thousands=',' por defecto y convertía
-- la Cantidad '1,00' en 100 al leer el archivo. Corregido en el loader
-- (etl/loaders/obuma.py: thousands='.', decimal=','), pero las filas ya
-- cargadas por esa vía quedaron con cantidad multiplicada por 100.
--
-- Alcance de la corrección (solo cargas vía export Excel):
--   · sociedad 1 (Acuña): todas las filas históricas hasta jul-2026.
--   · sociedad 2 (Gran Natural): solo hasta may-2026; desde jun-2026 GN se
--     carga por API y la cantidad viene correcta (NO tocar).
--
-- ⚠️ ORDEN: correr este script ANTES de re-ejecutar el ETL con el fix para
-- cualquier período antiguo. El script tiene un centinela que lo vuelve
-- re-ejecutable sin dividir dos veces: solo aplica si una fila conocida
-- del bug sigue leyendo el valor corrupto.

DO $$
DECLARE
  filas bigint;
BEGIN
  -- Centinela: factura 82724 (Acuña, 02-ene-2026, CR-6x20) se cargó con
  -- cantidad = 200 (real: 2 cajas). Si ya no está en 200, la reparación
  -- ya se aplicó (o el período se recargó con el ETL corregido) → no-op.
  IF NOT EXISTS (
      SELECT 1 FROM fact_ventas
      WHERE n_dcto = '82724' AND producto_codigo = 'CR-6x20'
        AND sociedad_id = 1 AND fecha = '2026-01-02' AND cantidad = 200
  ) THEN
    RAISE NOTICE 'fix ×100: centinela no encontrado en estado corrupto — nada que hacer.';
    RETURN;
  END IF;

  UPDATE fact_ventas
     SET cantidad = cantidad / 100.0
   WHERE (sociedad_id = 1 AND fecha <= DATE '2026-07-31')
      OR (sociedad_id = 2 AND fecha <  DATE '2026-06-01');

  GET DIAGNOSTICS filas = ROW_COUNT;
  RAISE NOTICE 'fix ×100 aplicado: % filas corregidas.', filas;
END $$;
