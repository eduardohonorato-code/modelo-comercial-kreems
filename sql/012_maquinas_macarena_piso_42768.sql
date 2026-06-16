-- Corrige el piso de máquinas de Macarena (25/30/35%) de $40.000 a $42.768
update public.comision_tramo_maquinas
set monto = 42768
where plan_id = 2 and logro_pct in (0.25, 0.30, 0.35);
