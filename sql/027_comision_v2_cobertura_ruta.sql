-- ============================================================================
-- Kreems · Comisiones v2 (modelo de gerencia, julio 2026)
-- ----------------------------------------------------------------------------
-- Cambios respecto de v1.1:
--   · Pesos nuevos: Cuota 30% · Clientes nuevos 20% · Amplitud 15% ·
--     Cobertura de ruta 15% (NUEVO) · Efectividad de cartera 20%.
--   · Se elimina Profundidad SKU del cálculo.
--   · Regla de pago conmutable: proporcional (default) o umbral 80%.
--
-- Cobertura de ruta = Visitas ÷ Agendamientos, del reporte "Cobertura /
-- Efectividad" de la web de Autoventa. Ese reporte NO está publicado en la API
-- (verificado sobre los 94 endpoints documentados), así que los dos números se
-- cargan a mano una vez al mes desde la pestaña de Comisiones.
-- Idempotente. Correr en el SQL Editor de Supabase.
-- ============================================================================

create table if not exists public.comision_ruta_mensual (
    vendedor_id   integer not null references public.dim_vendedor(id) on delete cascade,
    anio          integer not null,
    mes           integer not null check (mes between 1 and 12),
    agendamientos integer not null default 0,
    visitas       integer not null default 0,
    pedidos       integer,               -- opcional: permite auditar la efectividad
    updated_at    timestamptz not null default now(),
    primary key (vendedor_id, anio, mes)
);

comment on table public.comision_ruta_mensual is
    'Cobertura de ruta: agendamientos y visitas del reporte Cobertura/Efectividad de Autoventa (carga manual mensual).';

alter table public.comision_ruta_mensual enable row level security;

drop policy if exists comision_ruta_mensual_admin on public.comision_ruta_mensual;
create policy comision_ruta_mensual_admin on public.comision_ruta_mensual
    for all to authenticated
    using (public.es_gerencia()) with check (public.es_gerencia());

grant select, insert, update, delete on public.comision_ruta_mensual to authenticated;
grant select, insert, update, delete on public.comision_ruta_mensual to service_role;

-- ── Parámetro global: regla de pago ─────────────────────────────────────────
-- La tabla comision_v1_parametro ya existe (sql/026). Se reutiliza para
-- guardar la regla vigente, de modo que gerencia pueda comparar ambos modelos.
--   modo_pago = 0  → proporcional (cobra el mismo % que cumplió)
--   modo_pago = 1  → umbral: no paga bajo el 80% de la meta
insert into public.comision_v1_parametro (clave, valor, descripcion) values
    ('modo_pago',   0,    'Regla de pago: 0 = proporcional, 1 = con umbral'),
    ('umbral_pago', 0.80, 'Umbral de pago cuando modo_pago = 1 (fracción de la meta)')
on conflict (clave) do nothing;

-- ============================================================================
-- FIN. Tras ejecutar, la pestaña v1 permite cargar cobertura de ruta y
-- conmutar la regla de pago.
-- ============================================================================
