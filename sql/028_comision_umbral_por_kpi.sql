-- ============================================================================
-- Kreems · Comisiones — umbral de acceso configurable por KPI
-- ----------------------------------------------------------------------------
-- Regla acordada con gerencia (2026-07-31):
--   El umbral es una PUERTA DE ENTRADA, no una reescala.
--     · logro < umbral  → ese KPI paga 0
--     · logro ≥ umbral  → paga proporcional al logro real (tope 100%)
--   Ejemplo con umbral 80% y cuota (máx 1,50%):
--     logro 79% → 0,00% · logro 80% → 1,20% · logro 90% → 1,35% · 100% → 1,50%
--   Umbral 0 = sin umbral (paga proporcional desde el primer punto).
--
-- Cada KPI tiene su propio umbral, editable desde la pestaña de Comisiones.
-- Idempotente. Correr en el SQL Editor de Supabase (proyecto kfxdtjkendmaguzckxqs).
-- ============================================================================

-- Falta el umbral del KPI nuevo (Cobertura de ruta); los otros cuatro ya existen.
insert into public.comision_v1_parametro (clave, valor, descripcion) values
    ('umbral_ruta', 0.80, 'Umbral de acceso de Cobertura de ruta (fracción de la meta)')
on conflict (clave) do nothing;

-- Descripciones al día con los nombres del modelo vigente.
update public.comision_v1_parametro set descripcion = 'Umbral de acceso de Cuota de venta (fracción de la meta)'          where clave = 'umbral_cuota';
update public.comision_v1_parametro set descripcion = 'Umbral de acceso de Clientes nuevos válidos'                      where clave = 'umbral_nuevos';
update public.comision_v1_parametro set descripcion = 'Umbral de acceso de Efectividad de cartera'                       where clave = 'umbral_cobertura';
update public.comision_v1_parametro set descripcion = 'Umbral de acceso de Amplitud de portafolio'                       where clave = 'umbral_amplitud';

-- Parámetros que quedaron obsoletos: el KPI de SKU salió del modelo y la regla
-- ya no es un modo binario, sino un umbral por indicador.
delete from public.comision_v1_parametro
 where clave in ('umbral_sku', 'modo_pago', 'umbral_pago');

-- ============================================================================
-- FIN. Verificar:  select clave, valor from public.comision_v1_parametro order by clave;
-- Deben quedar 5 filas: umbral_amplitud, umbral_cobertura, umbral_cuota,
-- umbral_nuevos, umbral_ruta.
-- ============================================================================
