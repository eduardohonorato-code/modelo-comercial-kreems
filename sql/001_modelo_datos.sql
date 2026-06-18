-- ============================================================================
-- Kreems · Sistema de Seguimiento Comercial
-- FASE 1 — Capa de datos (Supabase / Postgres 15+)
-- ----------------------------------------------------------------------------
-- Script IDEMPOTENTE: se puede ejecutar varias veces sin error ni duplicar.
--   · Tablas        -> CREATE TABLE IF NOT EXISTS
--   · Vista         -> CREATE OR REPLACE VIEW (con security_invoker)
--   · Funciones     -> CREATE OR REPLACE FUNCTION
--   · Políticas RLS -> DROP POLICY IF EXISTS + CREATE POLICY
--   · Seed          -> INSERT ... ON CONFLICT DO NOTHING/UPDATE
--
-- Nota de diseño: el ETL escribe con la SERVICE_ROLE key, que IGNORA RLS.
-- Las políticas de abajo protegen el acceso desde la app (anon/authenticated).
-- ============================================================================

-- pgcrypto: necesario para crear los usuarios de prueba (hash de password).
create extension if not exists pgcrypto with schema extensions;

-- ============================================================================
-- 1. DIMENSIONES
-- ============================================================================

create table if not exists public.dim_sociedad (
  id      smallint primary key,
  nombre  text not null unique
);

create table if not exists public.dim_vendedor (
  id                    bigint generated always as identity primary key,
  nombre_canonico       text not null unique,
  cod_vendedor_autoventa text,
  agrupacion            text,                         -- zona / equipo / sucursal (opcional)
  activo                boolean not null default true,
  user_id               uuid unique references auth.users(id) on delete set null
);
comment on column public.dim_vendedor.user_id is 'Liga el vendedor a su usuario de Supabase Auth (para RLS).';

create table if not exists public.dim_cliente (
  rut          text primary key,
  razon_social text,
  comuna       text,
  region       text,
  tipo         text,
  es_maquina   boolean default false,
  sociedad_id  smallint references public.dim_sociedad(id)
);

create table if not exists public.dim_producto (
  codigo        text primary key,
  nombre        text,
  categoria     text,
  subcategoria  text,
  fabricante    text,
  unidad_medida text
);

create table if not exists public.dim_fecha (
  fecha       date primary key,
  anio        smallint not null,
  mes         smallint not null,
  dia         smallint not null,
  dia_semana  smallint not null   -- 1=lunes ... 7=domingo
);

create table if not exists public.calendario_laboral (
  anio            smallint not null,
  mes             smallint not null,
  dias_totales    smallint not null,
  dias_trabajados smallint not null,
  primary key (anio, mes)
);
comment on table public.calendario_laboral is 'Base de la proyección lineal a cierre (sección 3).';

-- ============================================================================
-- 2. HECHOS
--    Cada tabla lleva una llave natural (constraint UNIQUE) para que el ETL
--    haga UPSERT idempotente con ON CONFLICT, sin importar cuántos períodos
--    traiga cada carga.
-- ============================================================================

-- Ventas (Obuma, nivel línea de producto). NC entran con SIGNO NEGATIVO.
create table if not exists public.fact_ventas (
  id              bigint generated always as identity primary key,
  fecha           date not null,
  tipo_dcto       text not null,                 -- 'FACTURA' | 'NOTA DE CREDITO'
  n_dcto          text not null,                 -- llave de cruce con Autoventa.num_documento
  linea           smallint not null default 1,   -- nº de línea dentro del documento
  vendedor_id     bigint   references public.dim_vendedor(id),  -- NULL si el nombre no se mapeó (no descartar)
  cliente_rut     text     references public.dim_cliente(rut),
  producto_codigo text     references public.dim_producto(codigo),
  sociedad_id     smallint references public.dim_sociedad(id),
  sucursal        text,
  cantidad        numeric(18,4),
  neto            numeric(18,2),   -- NC negativo
  total           numeric(18,2),   -- NC negativo
  costo           numeric(18,2),
  margen          numeric(18,2),
  constraint uq_fact_ventas unique (sociedad_id, tipo_dcto, n_dcto, producto_codigo, linea)
);

-- Pedidos (Autoventa). Necesario para la métrica "No facturado" (Doc. venta = 'Sin DTE')
-- y para el cruce pedido<->factura. (Complemento del modelo sección 4, requerido por sección 3.)
create table if not exists public.fact_pedidos (
  id              bigint generated always as identity primary key,
  n_pedido        text,
  num_documento   text,            -- = fact_ventas.n_dcto cuando se facturó
  doc_venta       text,            -- 'Sin DTE' => no facturado
  fecha           date,
  vendedor_id     bigint   references public.dim_vendedor(id),
  cliente_rut     text     references public.dim_cliente(rut),
  producto_codigo text     references public.dim_producto(codigo),
  sociedad_id     smallint references public.dim_sociedad(id),
  neto            numeric(18,2),
  neto_nc         numeric(18,2),
  linea           smallint not null default 1,
  facturado boolean generated always as
    (coalesce(doc_venta,'') <> 'Sin DTE' and coalesce(num_documento,'') <> '') stored,
  constraint uq_fact_pedidos unique (sociedad_id, n_pedido, producto_codigo, linea)
);

-- Despachos / logística (Autoventa).
create table if not exists public.fact_despachos (
  id            bigint generated always as identity primary key,
  documento     text not null,
  fecha_ruta    date,
  vendedor_id   bigint   references public.dim_vendedor(id),
  cliente_rut   text     references public.dim_cliente(rut),
  estado        text,             -- Entregada | Pendiente | Rechazada
  devolucion    boolean default false,
  peso          numeric(18,3),
  es_maquina    boolean default false,
  transportista text,
  sociedad_id   smallint references public.dim_sociedad(id),
  constraint uq_fact_despachos unique (sociedad_id, documento, cliente_rut)
);

-- Máquinas (derivado de pedidos Autoventa categoría MAQUINAS_POP + cruce despachos).
create table if not exists public.fact_maquinas (
  id          bigint generated always as identity primary key,
  documento   text not null,
  fecha       date,
  vendedor_id bigint   references public.dim_vendedor(id),
  cliente_rut text     references public.dim_cliente(rut),
  tipo_mov    text not null,   -- nueva(FL-4) | cambio(FL-1) | retiro(FL-2)
  estado      text,            -- gestionada | entregada | rechazada
  sociedad_id smallint references public.dim_sociedad(id),
  constraint chk_maq_tipo   check (tipo_mov in ('nueva','cambio','retiro')),
  constraint chk_maq_estado check (estado is null or estado in ('gestionada','entregada','rechazada')),
  constraint uq_fact_maquinas unique (sociedad_id, documento, cliente_rut, tipo_mov)
);

-- ============================================================================
-- 3. INPUT EDITABLE — Objetivos mensuales (rol gerencia)
-- ============================================================================
create table if not exists public.objetivos_mensuales (
  vendedor_id   bigint not null references public.dim_vendedor(id) on delete cascade,
  anio          smallint not null,
  mes           smallint not null,
  obj_venta     numeric(18,2) default 0,
  obj_maquinas  integer       default 0,
  obj_visitas   integer       default 0,
  actualizado_en timestamptz  default now(),
  primary key (vendedor_id, anio, mes)
);

create or replace function public.tg_set_actualizado_en()
returns trigger language plpgsql as $$
begin
  new.actualizado_en := now();
  return new;
end;
$$;

drop trigger if exists trg_objetivos_actualizado on public.objetivos_mensuales;
create trigger trg_objetivos_actualizado
  before update on public.objetivos_mensuales
  for each row execute function public.tg_set_actualizado_en();

-- Índices de apoyo para las agregaciones por vendedor/mes.
create index if not exists ix_ventas_vend_fecha   on public.fact_ventas   (vendedor_id, fecha);
create index if not exists ix_maquinas_vend_fecha on public.fact_maquinas (vendedor_id, fecha);
create index if not exists ix_pedidos_vend_fecha  on public.fact_pedidos  (vendedor_id, fecha);
create index if not exists ix_despachos_vend      on public.fact_despachos(vendedor_id, fecha_ruta);

-- ============================================================================
-- 4. VISTA DE MÉTRICAS  (cálculo en Postgres; el front solo lee)
--    security_invoker = true  ->  la vista respeta el RLS del usuario que
--    consulta (Postgres 15+). Sin esto, un vendedor vería filas de todos.
-- ============================================================================
drop view if exists public.v_resumen_vendedor_mes;
create or replace view public.v_resumen_vendedor_mes
with (security_invoker = true) as
with ventas as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         sum(neto) as fact_nc,                                  -- NC ya viene negativo
         count(distinct case when upper(tipo_dcto) like 'FACTURA%' then n_dcto end) as n_docs,
         count(distinct case when upper(tipo_dcto) like 'FACTURA%' then n_dcto end) as n_facturas,
         count(distinct case when upper(tipo_dcto) like 'NOTA%'    then n_dcto end) as n_notas_credito,
         sum(case when upper(tipo_dcto) like 'FACTURA%' then neto else 0 end)       as monto_facturas,
         sum(case when upper(tipo_dcto) like 'NOTA%'    then neto else 0 end)       as monto_notas_credito
  from public.fact_ventas
  group by 1,2,3
),
maquinas as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         count(*) filter (where tipo_mov = 'nueva')                          as maq_gestionadas,
         count(*) filter (where tipo_mov = 'nueva' and estado = 'entregada') as maq_entregadas,
         count(*) filter (where tipo_mov = 'retiro')                         as maq_retiros
  from public.fact_maquinas
  group by 1,2,3
),
pedidos as (
  select vendedor_id,
         extract(year  from fecha)::smallint as anio,
         extract(month from fecha)::smallint as mes,
         sum(case when not facturado then neto else 0 end) as no_facturado_monto,
         count(*) filter (where not facturado)             as no_facturado_docs
  from public.fact_pedidos
  group by 1,2,3
),
periodos as (
  select vendedor_id, anio, mes from ventas
  union select vendedor_id, anio, mes from maquinas
  union select vendedor_id, anio, mes from pedidos
  union select vendedor_id, anio, mes from public.objetivos_mensuales
)
select
  p.vendedor_id,
  dv.nombre_canonico,
  p.anio,
  p.mes,
  -- Fact-NC = facturas - notas de crédito
  coalesce(ve.fact_nc, 0)                                            as fact_nc,
  coalesce(ve.n_docs, 0)                                             as n_documentos,
  coalesce(ve.n_facturas, 0)                                         as n_facturas,
  coalesce(ve.n_notas_credito, 0)                                    as n_notas_credito,
  coalesce(ve.monto_facturas, 0)                                     as monto_facturas,
  coalesce(ve.monto_notas_credito, 0)                                as monto_notas_credito,
  -- Días hábiles efectivos: dinámicos para el mes en curso (ver dt), guardados si no.
  dt.dias_trab_efectivo                                              as dias_trabajados,
  cal.dias_totales,
  -- Proyección lineal a cierre = (Fact-NC / días_trabajados) * días_totales
  case when dt.dias_trab_efectivo > 0
       then round(coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * cal.dias_totales, 2)
  end                                                               as proyeccion_cierre,
  o.obj_venta,
  o.obj_maquinas,
  o.obj_visitas,
  -- % Cumplimiento = Fact-NC / objetivo de venta
  case when o.obj_venta > 0
       then round(coalesce(ve.fact_nc,0) / o.obj_venta, 4)
  end                                                               as pct_cumplimiento,
  -- % Proyección = Proyección / objetivo de venta
  case when o.obj_venta > 0 and dt.dias_trab_efectivo > 0
       then round((coalesce(ve.fact_nc,0) / dt.dias_trab_efectivo * cal.dias_totales) / o.obj_venta, 4)
  end                                                               as pct_proyeccion,
  -- % Efectividad = N° de facturas / objetivo de visitas
  case when o.obj_visitas > 0
       then round(coalesce(ve.n_docs,0)::numeric / o.obj_visitas, 4)
  end                                                               as pct_efectividad,
  coalesce(pe.no_facturado_monto, 0)                                as no_facturado_monto,
  coalesce(pe.no_facturado_docs, 0)                                 as no_facturado_docs,
  coalesce(ma.maq_gestionadas, 0)                                   as maquinas_gestionadas,
  coalesce(ma.maq_entregadas, 0)                                    as maquinas_entregadas,
  coalesce(ma.maq_retiros, 0)                                       as maquinas_retiros,
  -- Conversión gestionada -> entregada
  case when coalesce(ma.maq_gestionadas,0) > 0
       then round(coalesce(ma.maq_entregadas,0)::numeric / ma.maq_gestionadas, 4)
  end                                                               as conversion_gestionada_entregada
from periodos p
join      public.dim_vendedor       dv  on dv.id = p.vendedor_id
left join ventas                    ve  on ve.vendedor_id = p.vendedor_id and ve.anio = p.anio and ve.mes = p.mes
left join maquinas                  ma  on ma.vendedor_id = p.vendedor_id and ma.anio = p.anio and ma.mes = p.mes
left join pedidos                   pe  on pe.vendedor_id = p.vendedor_id and pe.anio = p.anio and pe.mes = p.mes
left join public.objetivos_mensuales o  on o.vendedor_id  = p.vendedor_id and o.anio  = p.anio and o.mes  = p.mes
left join public.calendario_laboral cal on cal.anio = p.anio and cal.mes = p.mes
-- Días hábiles efectivos: si (anio,mes) es el mes actual, cuenta Lun-Vie del 1°
-- a hoy (capado a dias_totales); si es un mes pasado, usa el valor guardado.
cross join lateral (
  select case
    when p.anio = extract(year  from current_date)::int
     and p.mes  = extract(month from current_date)::int
    then least(
           (select count(*)::smallint
              from generate_series(date_trunc('month', current_date)::date,
                                   current_date, interval '1 day') g
             where extract(isodow from g) < 6),
           cal.dias_totales)
    else cal.dias_trabajados
  end as dias_trab_efectivo
) dt;

-- ============================================================================
-- 5. SEGURIDAD — Roles, perfiles y RLS  (sección 5)
-- ============================================================================

-- Perfil de usuario: define el rol de cada usuario de Auth.
create table if not exists public.perfil_usuario (
  user_id  uuid primary key references auth.users(id) on delete cascade,
  rol      text not null default 'vendedor' check (rol in ('vendedor','gerencia','admin')),
  creado_en timestamptz default now()
);

-- Helpers SECURITY DEFINER: leen perfil/dim_vendedor sin disparar RLS
-- (evita recursión en las políticas).
create or replace function public.mi_rol()
returns text language sql stable security definer set search_path = public as $$
  select rol from public.perfil_usuario where user_id = auth.uid();
$$;

create or replace function public.es_gerencia()
returns boolean language sql stable security definer set search_path = public as $$
  select coalesce(
    (select rol in ('gerencia','admin') from public.perfil_usuario where user_id = auth.uid()),
    false);
$$;

create or replace function public.mi_vendedor_id()
returns bigint language sql stable security definer set search_path = public as $$
  select id from public.dim_vendedor where user_id = auth.uid();
$$;

-- Activar RLS en todas las tablas.
alter table public.dim_sociedad        enable row level security;
alter table public.dim_vendedor        enable row level security;
alter table public.dim_cliente         enable row level security;
alter table public.dim_producto        enable row level security;
alter table public.dim_fecha           enable row level security;
alter table public.calendario_laboral  enable row level security;
alter table public.fact_ventas         enable row level security;
alter table public.fact_pedidos        enable row level security;
alter table public.fact_despachos      enable row level security;
alter table public.fact_maquinas       enable row level security;
alter table public.objetivos_mensuales enable row level security;
alter table public.perfil_usuario      enable row level security;

-- ---- Reference data (dimensiones + calendario): lectura para autenticados ----
do $$
declare t text;
begin
  foreach t in array array[
    'dim_sociedad','dim_cliente','dim_producto','dim_fecha','calendario_laboral'
  ] loop
    execute format('drop policy if exists %I on public.%I', t||'_sel', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (true)',
      t||'_sel', t);
  end loop;
end $$;

-- ---- dim_vendedor: vendedor ve su fila; gerencia/admin ven todas ----
drop policy if exists dim_vendedor_sel on public.dim_vendedor;
create policy dim_vendedor_sel on public.dim_vendedor
  for select to authenticated
  using (public.es_gerencia() or id = public.mi_vendedor_id());

-- ---- Hechos: vendedor ve solo sus filas; gerencia/admin ven todo ----
do $$
declare t text;
begin
  foreach t in array array[
    'fact_ventas','fact_pedidos','fact_despachos','fact_maquinas'
  ] loop
    execute format('drop policy if exists %I on public.%I', t||'_sel', t);
    execute format(
      'create policy %I on public.%I for select to authenticated
         using (public.es_gerencia() or vendedor_id = public.mi_vendedor_id())',
      t||'_sel', t);
  end loop;
end $$;

-- ---- objetivos_mensuales: vendedor lee lo suyo; gerencia/admin CRUD total ----
drop policy if exists objetivos_sel        on public.objetivos_mensuales;
drop policy if exists objetivos_admin_all  on public.objetivos_mensuales;

create policy objetivos_sel on public.objetivos_mensuales
  for select to authenticated
  using (public.es_gerencia() or vendedor_id = public.mi_vendedor_id());

create policy objetivos_admin_all on public.objetivos_mensuales
  for all to authenticated
  using (public.es_gerencia())
  with check (public.es_gerencia());

-- ---- perfil_usuario: cada uno ve el suyo; admin ve/gestiona todos ----
drop policy if exists perfil_sel       on public.perfil_usuario;
drop policy if exists perfil_admin_all on public.perfil_usuario;

create policy perfil_sel on public.perfil_usuario
  for select to authenticated
  using (user_id = auth.uid() or public.mi_rol() = 'admin');

create policy perfil_admin_all on public.perfil_usuario
  for all to authenticated
  using (public.mi_rol() = 'admin')
  with check (public.mi_rol() = 'admin');

-- ---- Grants (RLS sigue filtrando por encima de estos permisos) ----
grant usage on schema public to anon, authenticated;
grant select on all tables in schema public to authenticated;
grant insert, update, delete on public.objetivos_mensuales to authenticated;
grant select on public.v_resumen_vendedor_mes to authenticated;
grant execute on function public.mi_rol(), public.es_gerencia(), public.mi_vendedor_id()
  to anon, authenticated;

-- ============================================================================
-- 6. SEED MÍNIMO
-- ============================================================================

-- 6.1 Sociedades
insert into public.dim_sociedad (id, nombre) values
  (1, 'Acuña'),
  (2, 'Gran Natural SPA')
on conflict (id) do nothing;

-- 6.2 Usuarios de prueba (Supabase Auth)
--     vendedor.demo@kreems.cl / Demo1234!   (rol vendedor)
--     gerente.demo@kreems.cl  / Demo1234!   (rol gerencia)
insert into auth.users
  (instance_id, id, aud, role, email, encrypted_password,
   email_confirmed_at, created_at, updated_at,
   raw_app_meta_data, raw_user_meta_data,
   confirmation_token, recovery_token, email_change_token_new, email_change)
values
  ('00000000-0000-0000-0000-000000000000',
   '11111111-1111-1111-1111-111111111111',
   'authenticated','authenticated','vendedor.demo@kreems.cl',
   extensions.crypt('Demo1234!', extensions.gen_salt('bf')),
   now(), now(), now(),
   '{"provider":"email","providers":["email"]}','{}','','','',''),
  ('00000000-0000-0000-0000-000000000000',
   '22222222-2222-2222-2222-222222222222',
   'authenticated','authenticated','gerente.demo@kreems.cl',
   extensions.crypt('Demo1234!', extensions.gen_salt('bf')),
   now(), now(), now(),
   '{"provider":"email","providers":["email"]}','{}','','','','')
on conflict (id) do nothing;

-- Identidad email (para que el login real funcione en GoTrue). Tolerante a fallos.
do $$
begin
  insert into auth.identities
    (id, user_id, identity_data, provider, provider_id, last_sign_in_at, created_at, updated_at)
  values
    (gen_random_uuid(),'11111111-1111-1111-1111-111111111111',
     '{"sub":"11111111-1111-1111-1111-111111111111","email":"vendedor.demo@kreems.cl"}',
     'email','11111111-1111-1111-1111-111111111111', now(), now(), now()),
    (gen_random_uuid(),'22222222-2222-2222-2222-222222222222',
     '{"sub":"22222222-2222-2222-2222-222222222222","email":"gerente.demo@kreems.cl"}',
     'email','22222222-2222-2222-2222-222222222222', now(), now(), now())
  on conflict do nothing;
exception when others then
  raise notice 'auth.identities no insertado (%). El login real puede requerir crear los usuarios desde el panel; la demo de RLS por claims funciona igual.', sqlerrm;
end $$;

-- 6.3 Perfiles (rol de cada usuario)
insert into public.perfil_usuario (user_id, rol) values
  ('11111111-1111-1111-1111-111111111111','vendedor'),
  ('22222222-2222-2222-2222-222222222222','gerencia')
on conflict (user_id) do update set rol = excluded.rol;

-- 6.4 Vendedores (uno ligado al usuario de prueba; otro sin usuario)
insert into public.dim_vendedor (nombre_canonico, cod_vendedor_autoventa, user_id, activo)
values ('Vendedor Demo','V001','11111111-1111-1111-1111-111111111111', true)
on conflict (nombre_canonico) do update set user_id = excluded.user_id;

insert into public.dim_vendedor (nombre_canonico, cod_vendedor_autoventa, activo)
values ('Vendedor Dos','V002', true)
on conflict (nombre_canonico) do nothing;

-- 6.5 Calendario laboral (DATO REAL: base de la proyección lineal a cierre).
--     Ajustar dias_trabajados según los días hábiles efectivos del mes.
insert into public.calendario_laboral (anio, mes, dias_totales, dias_trabajados) values
  (2026, 5, 31, 20)
on conflict (anio, mes) do nothing;

-- ⚠ Los datos demo (clientes/productos/objetivos/ventas/NC/máquinas con llave
--   "DEMO-*") se movieron a sql/seed_demo.sql. NO se cargan en producción
--   porque inflan los totales y rompen el cuadre con Power BI.
--   · Para una base de prueba:        ejecutar sql/seed_demo.sql
--   · Para limpiar una base real:     ejecutar sql/cleanup_demo_prod.sql

-- ============================================================================
-- FIN. Ejecutar 002_demo_rls.sql para comprobar que el RLS filtra por usuario.
--      (002 espera los datos demo; correr antes sql/seed_demo.sql en pruebas.)
-- ============================================================================
