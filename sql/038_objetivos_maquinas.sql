-- ============================================================================
-- 038 · Objetivos del control de máquinas (editables por gerencia)
-- ============================================================================
-- Igual que `objetivos_mensuales` fija la vara de cada vendedor, esta tabla fija
-- la vara del comodato. Va por mes: una meta de temporada alta no es la misma
-- que la de julio, y así el histórico conserva contra qué se estaba midiendo en
-- cada momento en vez de reescribirlo cuando cambia la meta.
--
-- Una fila por mes. Si un mes no tiene fila, la app usa la del mes más reciente
-- ya definido, y si no hay ninguna, los valores por defecto del código
-- (22 gestiones semanales, definición de gerencia de agosto 2026).
--
-- Correr en el SQL Editor de Supabase. Es idempotente.
-- ============================================================================

create table if not exists public.objetivos_maquinas (
  anio                    smallint not null,
  mes                     smallint not null,
  -- Volumen
  meta_gestiones_semana   integer,        -- fletes con DTE por semana, equipo
  meta_pedidos_semana     integer,        -- pedidos ingresados por semana (vendedor)
  -- Gestión (logística: emitir el documento)
  meta_pct_concretado     numeric(5,4),   -- pedidos que llegan a DTE
  meta_dias_gestion       integer,        -- mediana de días ingreso → DTE
  meta_cola_vencida       integer,        -- pedidos sin DTE con más de 30 días
  -- Terreno (logística: despachar)
  meta_pct_entregado      numeric(5,4),
  meta_pct_rechazo        numeric(5,4),
  meta_conversion_inst    numeric(5,4),   -- instalaciones confirmadas entregadas
  -- Resultado
  meta_parque_neto        integer,        -- instalaciones − retiros del mes
  actualizado_en          timestamptz default now(),
  primary key (anio, mes)
);

comment on table public.objetivos_maquinas is
  'Metas mensuales del control de máquinas en comodato. Editables desde la '
  'sección Control de Máquinas (rol gerencia).';

drop trigger if exists tg_objetivos_maquinas_upd on public.objetivos_maquinas;
create trigger tg_objetivos_maquinas_upd
  before update on public.objetivos_maquinas
  for each row execute function public.tg_set_actualizado_en();

-- ── RLS: leen todos los autenticados, escribe solo gerencia ────────────────
alter table public.objetivos_maquinas enable row level security;

drop policy if exists objetivos_maquinas_sel on public.objetivos_maquinas;
create policy objetivos_maquinas_sel on public.objetivos_maquinas
  for select to authenticated using (true);

drop policy if exists objetivos_maquinas_upd on public.objetivos_maquinas;
create policy objetivos_maquinas_upd on public.objetivos_maquinas
  for all to authenticated
  using ((select public.es_gerencia()))
  with check ((select public.es_gerencia()));

grant select on public.objetivos_maquinas to authenticated;
grant insert, update, delete on public.objetivos_maquinas to authenticated;
grant all on public.objetivos_maquinas to service_role;

-- ── Semilla: la definición vigente de gerencia (agosto 2026) ───────────────
-- Las 22 gestiones semanales las fijó gerencia; el resto son las metas
-- propuestas sobre la línea base de marzo–agosto 2026, a revisar con un mes de
-- operación encima.
insert into public.objetivos_maquinas (
    anio, mes, meta_gestiones_semana, meta_pct_concretado, meta_dias_gestion,
    meta_cola_vencida, meta_pct_entregado, meta_pct_rechazo,
    meta_conversion_inst, meta_parque_neto)
values (2026, 8, 22, 0.90, 7, 0, 0.85, 0.10, 0.85, 0)
on conflict (anio, mes) do nothing;
