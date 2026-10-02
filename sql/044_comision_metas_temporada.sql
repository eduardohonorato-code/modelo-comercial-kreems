-- ============================================================================
-- Kreems · Comisiones — metas generales en % (efectividad por temporada y ruta)
-- ----------------------------------------------------------------------------
-- Efectividad de cartera: la meta deja de ser el 100% de la cartera y pasa a
-- ser un % que depende de la temporada del helado:
--     verano  (oct–mar): 65% de la cartera comprando en el mes
--     invierno (abr–sep): 50%
--   Calibrado con dato real feb–sep 2026 (nivel que ya alcanza el mejor tercio).
-- Cobertura de ruta: meta = 90% de los agendamientos (estándar de la industria
--   para cumplimiento de ruta: sobre 90%).
-- El piso de pago (umbral_*) se aplica sobre el logro contra estas metas.
--
-- Editables desde Comisiones → Propuesta → Configuración → Metas generales.
-- Idempotente: no pisa valores ya editados. Proyecto kfxdtjkendmaguzckxqs.
-- ============================================================================

insert into public.comision_v1_parametro (clave, valor, descripcion) values
    ('meta_efec_verano',   0.65, 'Meta efectividad de cartera, verano oct–mar (fracción de la cartera)'),
    ('meta_efec_invierno', 0.50, 'Meta efectividad de cartera, invierno abr–sep (fracción de la cartera)'),
    ('meta_ruta',          0.90, 'Meta cobertura de ruta (fracción de los agendamientos)')
on conflict (clave) do nothing;

-- Verificar: select clave, valor from public.comision_v1_parametro order by clave;
