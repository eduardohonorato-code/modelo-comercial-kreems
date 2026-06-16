-- Fase 4: tabla de auditoría de webhooks entrantes de Obuma.
-- Guarda el payload crudo y el resultado para facilitar el debugging.
-- Ejecutar en Supabase SQL Editor (una sola vez).

CREATE TABLE IF NOT EXISTS webhook_log (
  id           BIGSERIAL     PRIMARY KEY,
  recibido_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  evento       TEXT,                          -- "documento.emitido", etc.
  n_dcto       TEXT,                          -- N° de documento procesado
  sociedad     TEXT,                          -- "Acuña" | "Gran Natural SPA"
  status       TEXT          NOT NULL,        -- 'ok' | 'error' | 'ignorado'
  detalle      TEXT,                          -- resumen de filas upsertadas o mensaje de error
  payload_raw  JSONB                          -- payload completo (para re-procesar si falla)
);

-- Solo gerencia/admin puede leer el log; la función usa service_role y lo bypasa.
ALTER TABLE webhook_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "solo_gerencia_lee_webhook_log"
  ON webhook_log FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM auth.users u
      WHERE  u.id = auth.uid()
        AND  (u.raw_user_meta_data->>'rol')::text IN ('gerencia', 'admin')
    )
  );

-- Índices para consultas frecuentes de debugging
CREATE INDEX IF NOT EXISTS idx_webhook_log_recibido  ON webhook_log (recibido_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_log_n_dcto    ON webhook_log (n_dcto);
CREATE INDEX IF NOT EXISTS idx_webhook_log_status    ON webhook_log (status);
