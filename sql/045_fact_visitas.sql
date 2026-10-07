-- ============================================================================
-- Kreems · Visitas de los vendedores (GPS de Autoventa)
-- ----------------------------------------------------------------------------
-- Fuente: API v2 de Autoventa `visit-reports/visits-details` (una fila por
-- check-in del vendedor en un cliente, con o sin pedido). La carga es diaria y
-- automática (etl/run_visitas_api.py, dentro de etl.run_diario).
--
-- Uso: Panel Gerencia → "Avance de comisiones del mes", indicador Cobertura de
-- ruta (visitas ÷ visitas programadas). El dato OFICIAL sigue siendo el reporte
-- "Cobertura / Efectividad" de Autoventa que se carga a fin de mes en
-- Comisiones; mientras no esté, el panel estima con esta tabla contando UNA
-- visita por cliente por semana (así agenda Autoventa). Validado contra el
-- reporte: jul-2026 8,6% de error, sep-2026 3,5%.
--
-- Idempotente. Correr en el SQL Editor de Supabase (proyecto kfxdtjkendmaguzckxqs).
-- ============================================================================

create table if not exists public.fact_visitas (
    id             text primary key,          -- md5(fecha_hora|salesman_id|client_id|request_id)
    fecha          date not null,             -- día local (Chile) de la visita
    fecha_hora     timestamptz not null,
    vendedor_id    integer references public.dim_vendedor(id),
    salesman_code  text,                      -- código Autoventa del usuario que visitó
    salesman_name  text,
    cliente_rut    text,                      -- normalizado XX.XXX.XXX-X
    client_id      bigint,                    -- id del cliente en Autoventa
    con_pedido     boolean not null default false,
    request_id     text,                      -- pedido asociado, si la visita vendió
    comentario     text,
    cargado_en     timestamptz not null default now()
);

create index if not exists fact_visitas_fecha_idx
    on public.fact_visitas (fecha);
create index if not exists fact_visitas_vendedor_fecha_idx
    on public.fact_visitas (vendedor_id, fecha);

alter table public.fact_visitas enable row level security;

-- Lectura: gerencia ve todo; un vendedor solo sus visitas (mismo patrón fact_*).
drop policy if exists fact_visitas_select on public.fact_visitas;
create policy fact_visitas_select on public.fact_visitas
    for select to authenticated
    using (
        (select public.es_gerencia())
        or vendedor_id in (select id from public.dim_vendedor
                           where user_id = (select auth.uid()))
    );

grant select on public.fact_visitas to authenticated;
grant select, insert, update, delete on public.fact_visitas to service_role;

-- ============================================================================
-- FIN. Para cargar el mes en curso sin esperar la corrida diaria:
--   python -m etl.run_visitas_api --periodo 2026-10
-- ============================================================================
