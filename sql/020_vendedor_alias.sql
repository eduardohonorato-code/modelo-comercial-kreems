-- ============================================================================
-- 020 — Alias de vendedor + reemplazo momentáneo Diego → Carlos
-- ============================================================================
-- Problema: Diego Guerstein ya no trabaja (desde julio 2026). Lo reemplaza Carlos
-- Matabenítez, que aún NO tiene cuenta en el ERP, así que sus facturas de julio
-- llegan a nombre de "Diego Guerstein". Queremos que aparezcan como Carlos.
--
-- Solución momentánea:
--   1. Tabla vendedor_alias (nombre ERP → vendedor_id): el ETL la respeta y
--      REDIRIGE la facturación de un nombre a otro vendedor.
--   2. Se RENOMBRA el registro de Diego a "Carlos Matabenitez" (mismo id → todo
--      lo que ya mapeaba a Diego pasa a mostrar Carlos, sin mover datos).
--   3. Se agrega alias del nombre de Diego → ese id, para que el ETL siga
--      mapeando las facturas que llegan como "Diego" a ese registro (ahora Carlos).
--
-- Cuando Carlos tenga su cuenta y facture con su nombre real, "Carlos Matabenitez"
-- ya mapea al mismo registro (es su nombre_canonico) → transición sin cortes.
-- OJO: al ser el mismo registro, el historial de Diego (feb-jun) pasa a mostrarse
-- como Carlos. Es reversible (renombrar de vuelta) si se necesita separar.
-- Correr una vez en el SQL Editor de Supabase.
-- ============================================================================

-- 1. Tabla de alias
create table if not exists public.vendedor_alias (
  alias        text primary key,          -- nombre tal como llega del ERP
  vendedor_id  bigint not null references public.dim_vendedor(id) on delete cascade,
  nota         text,
  creado_en    timestamptz default now()
);
grant select on public.vendedor_alias to authenticated;
grant all    on public.vendedor_alias to service_role;

-- 2. Alias del nombre ACTUAL de Diego → su id (se guarda antes de renombrar).
insert into public.vendedor_alias (alias, vendedor_id, nota)
select dv.nombre_canonico, dv.id,
       'Reemplazo jul-2026: Carlos Matabenitez factura bajo el nombre de Diego'
from public.dim_vendedor dv
where dv.nombre_canonico ilike '%guerstein%'
on conflict (alias) do update set vendedor_id = excluded.vendedor_id;

-- 3. Renombrar el registro de Diego a Carlos (mismo id).
update public.dim_vendedor
   set nombre_canonico = 'Carlos Matabenitez', activo = true
 where nombre_canonico ilike '%guerstein%';

-- Verificación:
-- select id, nombre_canonico, activo from public.dim_vendedor where id in
--   (select vendedor_id from public.vendedor_alias);
-- select * from public.vendedor_alias;
