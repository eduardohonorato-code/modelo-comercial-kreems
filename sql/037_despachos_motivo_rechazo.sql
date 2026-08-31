-- ============================================================================
-- 037 · Motivo de rechazo y comentario de entrega (fact_despachos)
-- ============================================================================
-- Por qué: el 19% de los despachos de máquina vuelve rechazado y hoy no sabemos
-- por qué, porque el detalle de despachos trae dos columnas que no estábamos
-- guardando.
--
-- OJO con cuál sirve. En el export de agosto 2026, "Motivo rechazo" viene
-- **vacío en los 56 rechazos**: el campo estructurado del ERP no se está
-- llenando. El motivo real está escrito a mano en "Comentrario entrega", que sí
-- viene completo ("sin dinero", "local cerrado", "máquina aún en uso"...).
-- Por eso se guardan las dos: la estructurada para cuando logística empiece a
-- usarla, y el texto libre, que es lo único que hoy explica el rechazo.
--
-- El informe clasifica ese texto por palabras clave; es una heurística sobre
-- texto libre, no un dato del ERP, y así está rotulado en el Excel.
--
-- Correr en el SQL Editor de Supabase. Es idempotente.
-- ============================================================================

alter table public.fact_despachos
  add column if not exists motivo_rechazo     text,
  add column if not exists comentario_entrega text;

comment on column public.fact_despachos.motivo_rechazo is
  'Campo "Motivo rechazo" del detalle de despachos de Autoventa. A ago-2026 '
  'llega siempre vacío: el ERP no lo está llenando.';
comment on column public.fact_despachos.comentario_entrega is
  'Campo "Comentrario entrega" (así, con el typo del ERP) del detalle de '
  'despachos: texto libre del repartidor. Hoy es la única fuente real del '
  'motivo de rechazo.';
