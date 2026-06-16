-- ============================================================================
-- Calendario laboral 2026 — días hábiles reales (Chile)
-- Feriados considerados: 1 mayo, 21 mayo, y los de cada mes.
-- dias_trabajados = días hábiles TRANSCURRIDOS en el mes (para proyección).
-- Para meses completos = total días hábiles del mes.
-- Para el mes en curso = días hábiles transcurridos hasta hoy.
-- ============================================================================

insert into public.calendario_laboral (anio, mes, dias_totales, dias_trabajados)
values
-- Mayo 2026: feriados 1-may (Trabajo) y 21-may (Glorias Navales)
-- Días hábiles: 4-8/5 + 11-15/5 + 18-20/5 + 22/5 + 25-30/5 = 19 días
(2026, 5,  19, 19),
-- Abril 2026: sin feriados (Semana Santa variable - aquí sin feriados fijos)
(2026, 4,  22, 22),
-- Marzo 2026
(2026, 3,  22, 22),
-- Febrero 2026 (28 días)
(2026, 2,  20, 20),
-- Enero 2026 (1 enero feriado)
(2026, 1,  21, 21),
-- Junio 2026 (mes en curso al 3-jun: solo 3 días hábiles transcurridos)
(2026, 6,  21, 3)
on conflict (anio, mes) do update
  set dias_totales    = excluded.dias_totales,
      dias_trabajados = excluded.dias_trabajados;

-- Verificar
select anio, mes, dias_totales, dias_trabajados,
       round(dias_trabajados::numeric/dias_totales*100,1) as pct_mes_transcurrido
from public.calendario_laboral
where anio = 2026
order by mes;
