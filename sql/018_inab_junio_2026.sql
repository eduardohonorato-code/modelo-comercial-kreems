-- ============================================================================
-- 018 — INAB de junio 2026 (Semana Corrida)
-- ============================================================================
-- La Semana Corrida = total_comision / dias_trabajados × INAB. Si INAB es NULL,
-- la fórmula devuelve 0. Junio 2026 había quedado sin INAB → semana corrida $0.
--
-- Regla del INAB (reproduce los valores confirmados por gerencia):
--   INAB = nº de domingos del mes + feriados que NO caen en domingo.
--   Abril 2026: 4 domingos + 2 feriados (3 y 4) = 6  ✓
--   Mayo  2026: 5 domingos + 2 feriados (1 y 21) = 7  ✓
--   Junio 2026: 4 domingos + 1 feriado (29; el 21 cae domingo) = 5
-- ============================================================================

update public.calendario_laboral set inab = 5 where anio = 2026 and mes = 6;

-- Nota: enero/febrero/marzo 2026 también tienen INAB NULL. Si se calcularán
-- comisiones de esos meses, cargar su INAB con la misma regla.
