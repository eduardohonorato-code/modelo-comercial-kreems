-- ============================================================================
-- Kreems · Comisiones — tramos base máquinas 25-35% para plan Macarena (id=2)
-- ----------------------------------------------------------------------------
-- El 009 agregó estos tramos solo para plan 1. Macarena usa plan 2 y quedó
-- sin los tramos base, por lo que seguía devolviendo $0 con 1 máquina.
-- ============================================================================

insert into public.comision_tramo_maquinas (plan_id, logro_pct, monto) values
  (2, 0.25, 40000),
  (2, 0.30, 40000),
  (2, 0.35, 40000)
on conflict (plan_id, logro_pct) do update set monto = excluded.monto;

-- Verificar:
-- select logro_pct, monto from comision_tramo_maquinas
-- where plan_id = 2 order by logro_pct;
-- → debe aparecer 0.25, 0.30, 0.35 con monto=40000 junto al resto de la escala.
