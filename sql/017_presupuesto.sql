-- ============================================================================
-- 017 — Sección "Presupuesto de Venta" (gerencia/admin)
-- ============================================================================
-- Dos tablas LIVIANAS (solo el número mensual, sin detalle de facturación):
--   · presupuesto_venta   : presupuesto mensual de la empresa (el plan de abril).
--   · ventas_historicas   : venta total mensual de años anteriores (2024, 2025)
--                           para estacionalidad y comparación interanual.
-- Correr en Supabase: SQL Editor → pegar todo → Run.
-- ============================================================================

-- 1. Presupuesto mensual (total empresa)
create table if not exists public.presupuesto_venta (
  anio           smallint not null,
  mes            smallint not null check (mes between 1 and 12),
  monto          numeric(18,2) not null default 0,
  actualizado_en timestamptz default now(),
  primary key (anio, mes)
);

-- 2. Ventas históricas (total empresa por mes, años cerrados)
create table if not exists public.ventas_historicas (
  anio           smallint not null,
  mes            smallint not null check (mes between 1 and 12),
  monto          numeric(18,2) not null default 0,
  actualizado_en timestamptz default now(),
  primary key (anio, mes)
);

-- Trigger de actualizado_en (reutiliza la función de 001)
drop trigger if exists trg_presupuesto_actualizado on public.presupuesto_venta;
create trigger trg_presupuesto_actualizado
  before update on public.presupuesto_venta
  for each row execute function public.tg_set_actualizado_en();

drop trigger if exists trg_vhist_actualizado on public.ventas_historicas;
create trigger trg_vhist_actualizado
  before update on public.ventas_historicas
  for each row execute function public.tg_set_actualizado_en();

-- 3. RLS: SOLO gerencia/admin lee y escribe (la sección es de gerencia)
alter table public.presupuesto_venta enable row level security;
alter table public.ventas_historicas enable row level security;

drop policy if exists presupuesto_gerencia_all on public.presupuesto_venta;
create policy presupuesto_gerencia_all on public.presupuesto_venta
  for all to authenticated
  using (public.es_gerencia())
  with check (public.es_gerencia());

drop policy if exists vhist_gerencia_all on public.ventas_historicas;
create policy vhist_gerencia_all on public.ventas_historicas
  for all to authenticated
  using (public.es_gerencia())
  with check (public.es_gerencia());

-- 4. Grants (RLS sigue filtrando por encima)
grant select, insert, update, delete on public.presupuesto_venta to authenticated;
grant select, insert, update, delete on public.ventas_historicas to authenticated;
grant all on public.presupuesto_venta to service_role;
grant all on public.ventas_historicas to service_role;
