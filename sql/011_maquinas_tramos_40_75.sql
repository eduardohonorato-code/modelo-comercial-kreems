-- ============================================================================
-- Kreems · Comisiones — tramos máquinas 40%-75% para ambos planes
-- ----------------------------------------------------------------------------
-- El seed original (006) solo tenía tramos desde 80%.
-- Con el piso bajado a 0.25 (009), logros entre 40-75% buscan esos tramos
-- y no los encuentran → $0. Este script los agrega para plan 1 y plan 2.
-- Fuente: tabla adjuntada por gerencia (col. "Kreems normal" y "Nueva escala Macarena").
-- ============================================================================

insert into public.comision_tramo_maquinas (plan_id, logro_pct, monto) values
  -- ── Plan 1: Kreems normal ──────────────────────────────────────────────
  (1, 0.40,  40000),
  (1, 0.45,  54000),
  (1, 0.50,  68000),
  (1, 0.55,  82000),
  (1, 0.60,  96000),
  (1, 0.65, 110000),
  (1, 0.70, 124000),
  (1, 0.75, 138000),
  -- ── Plan 2: Macarena ──────────────────────────────────────────────────
  -- Tramos base 25/30/35% (mismos que plan 1; tabla no los detalla)
  (2, 0.25,  40000),
  (2, 0.30,  40000),
  (2, 0.35,  40000),
  -- Escala propia desde 40%
  (2, 0.40,  42768),
  (2, 0.45,  57737),
  (2, 0.50,  72706),
  (2, 0.55,  87675),
  (2, 0.60, 102644),
  (2, 0.65, 117612),
  (2, 0.70, 132581),
  (2, 0.75, 147550)
on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- ============================================================================
-- Verificar escala completa (debe ir de 0.25 a 1.40 sin huecos de 5% en 5%):
--   select plan_id, logro_pct, monto
--   from comision_tramo_maquinas
--   order by plan_id, logro_pct;
-- ============================================================================
