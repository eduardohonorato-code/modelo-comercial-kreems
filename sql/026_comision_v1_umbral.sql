-- ============================================================================
-- Kreems · Propuesta de Comisiones v1.1 — umbrales de pago editables por KPI
-- ----------------------------------------------------------------------------
-- El umbral de pago (hasta ahora fijo en 80%) pasa a ser configurable por KPI
-- desde la app. Regla: logro < umbral → $0; entre umbral y 100% sube lineal;
-- ≥100% paga completo. Ej: umbral 60% y logro 80% → paga la mitad.
-- La app lee estos valores fail-soft (si la tabla no existe usa 80%).
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

create table if not exists public.comision_v1_parametro (
    clave       text primary key,
    valor       numeric not null,
    descripcion text,
    updated_at  timestamptz not null default now()
);

alter table public.comision_v1_parametro enable row level security;

drop policy if exists comision_v1_parametro_admin on public.comision_v1_parametro;
create policy comision_v1_parametro_admin on public.comision_v1_parametro
    for all to authenticated
    using (public.es_gerencia()) with check (public.es_gerencia());

grant select, insert, update, delete on public.comision_v1_parametro to authenticated;
grant select, insert, update, delete on public.comision_v1_parametro to service_role;

-- Seed: umbral 80% para los 5 KPIs (fracción 0-1). Editables desde la app.
insert into public.comision_v1_parametro (clave, valor, descripcion) values
    ('umbral_cuota',     0.80, 'Umbral de pago de Cuota de venta (fracción de la meta)'),
    ('umbral_nuevos',    0.80, 'Umbral de pago de Nuevos + reactivados'),
    ('umbral_cobertura', 0.80, 'Umbral de pago de Cobertura de cartera'),
    ('umbral_amplitud',  0.80, 'Umbral de pago de Amplitud de categorías'),
    ('umbral_sku',       0.80, 'Umbral de pago de Profundidad SKU')
on conflict (clave) do nothing;

-- ============================================================================
-- FIN. Tras ejecutar, la pestaña v1 muestra la sección "Umbrales de pago".
-- ============================================================================
