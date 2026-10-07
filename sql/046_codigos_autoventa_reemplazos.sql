-- ============================================================================
-- 046 — Códigos de Autoventa de Joaquín Brandt y Francisco Arriagada
-- ============================================================================
-- Desde fines de sep-2026 los dos usan cuenta propia en Autoventa. Sus códigos
-- ("Vendedor exclusivo" del reporte de clientes y salesman_code del GPS):
--   35442 = Joaquín Brandt      (rutas CN2x, ex cartera de Diego/Carlos)
--   35443 = Francisco Arriagada (rutas CN4x, ex cartera de Maicol)
-- Verificado 2026-10-07 con la API de visitas (salesman_code ↔ salesman_name).
-- Sin esto, la recarga de cartera deja esos clientes sin vendedor.
-- Diego (33224) y Maicol (32303) conservan sus códigos viejos a propósito.
-- Idempotente.
-- ============================================================================
update public.dim_vendedor set cod_vendedor_autoventa = '35442'
 where nombre_canonico = 'Joaquín Brandt'
   and coalesce(cod_vendedor_autoventa, '') <> '35442';
update public.dim_vendedor set cod_vendedor_autoventa = '35443'
 where nombre_canonico = 'Francisco Arriagada'
   and coalesce(cod_vendedor_autoventa, '') <> '35443';
