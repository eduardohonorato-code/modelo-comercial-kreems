-- ============================================================================
-- 032 — Calendario laboral julio–diciembre 2026
-- ============================================================================
-- PROBLEMA: calendario_laboral llegaba hasta junio 2026. Sin la fila del mes:
--   · v_resumen_vendedor_mes deja dias_trabajados y proyeccion_cierre en NULL
--   · v_comision_vendedor_mes deja SEMANA CORRIDA en NULL para TODOS los
--     vendedores (la fórmula divide por dias_trabajados)
-- Se detectó al calcular las comisiones de julio 2026.
--
-- dias_trabajados = días de lunes a viernes del mes − feriados (tabla feriados),
-- misma convención que los meses ya cargados (junio 2026 = 21).
-- El INAB lo completa solo el trigger trg_inab_auto (sql/019):
--   INAB = domingos + feriados que no caen domingo.
--   jul=5 · ago=6 · sep=6 · oct=6 · nov=5 · dic=6
--
-- Correr una vez en el SQL Editor de Supabase.
-- ============================================================================

insert into public.calendario_laboral (anio, mes, dias_totales, dias_trabajados) values
  (2026,  7, 22, 22),
  (2026,  8, 21, 21),
  (2026,  9, 21, 21),
  (2026, 10, 21, 21),
  (2026, 11, 21, 21),
  (2026, 12, 21, 21)
on conflict (anio, mes) do update set
  dias_totales    = excluded.dias_totales,
  dias_trabajados = excluded.dias_trabajados;

-- El ON CONFLICT ... DO UPDATE no dispara el trigger BEFORE INSERT, así que
-- para los meses que ya existieran se completa el INAB explícitamente.
update public.calendario_laboral
   set inab = public.inab_calculado(anio, mes)
 where anio = 2026 and mes between 7 and 12 and inab is null;

-- Verificación:
-- select anio, mes, dias_totales, dias_trabajados, inab
--   from public.calendario_laboral where anio = 2026 order by mes;
