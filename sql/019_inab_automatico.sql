-- ============================================================================
-- 019 — INAB automático (Semana Corrida)
-- ============================================================================
-- El INAB (días de descanso del mes) se cargaba a mano y, si quedaba NULL, la
-- Semana Corrida salía $0 (pasó con junio 2026). Esto lo automatiza:
--   · función inab_calculado(anio, mes) = domingos + feriados que NO caen domingo
--   · backfill de los meses con INAB NULL (respeta los valores manuales ya puestos)
--   · trigger que completa el INAB al crear un mes nuevo en calendario_laboral
--
-- Regla validada: abril=6, mayo=7, junio=5 (coincide con lo confirmado por gerencia).
-- Correr una vez en el SQL Editor de Supabase.
-- ============================================================================

-- 1. Función: INAB = domingos del mes + feriados que no caen en domingo.
create or replace function public.inab_calculado(p_anio int, p_mes int)
returns smallint
language sql stable
set search_path = public
as $$
  with rango as (
    select make_date(p_anio, p_mes, 1) as ini,
           (make_date(p_anio, p_mes, 1) + interval '1 month - 1 day')::date as fin
  ),
  domingos as (
    select count(*)::int n
    from rango, generate_series(ini, fin, interval '1 day') g
    where extract(dow from g) = 0            -- 0 = domingo
  ),
  fer as (
    select count(*)::int n
    from public.feriados f, rango
    where f.fecha between ini and fin
      and extract(dow from f.fecha) <> 0     -- feriados que NO caen domingo
  )
  select (domingos.n + fer.n)::smallint from domingos, fer;
$$;

-- 2. Backfill: completar solo los meses que tengan INAB NULL (no pisa manuales).
update public.calendario_laboral
   set inab = public.inab_calculado(anio, mes)
 where inab is null;

-- 3. Trigger: al insertar un mes nuevo sin INAB, calcularlo automáticamente.
create or replace function public.tg_inab_auto()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.inab is null then
    new.inab := public.inab_calculado(new.anio, new.mes);
  end if;
  return new;
end;
$$;

drop trigger if exists trg_inab_auto on public.calendario_laboral;
create trigger trg_inab_auto
  before insert on public.calendario_laboral
  for each row execute function public.tg_inab_auto();

-- Verificación (opcional): ver el INAB por mes de 2026.
-- select anio, mes, inab, public.inab_calculado(anio, mes) as inab_calc
--   from public.calendario_laboral where anio = 2026 order by mes;
