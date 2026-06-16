-- ============================================================================
-- 007 — Login dual: por email O por nombre corto (username)
-- ============================================================================
-- • username es OPCIONAL: se asigna después del registro inicial.
--   Mientras no tenga username el usuario entra solo con email.
--   Una vez asignado puede entrar con cualquiera de los dos.
-- • La columna acepta NULL (varios usuarios sin username = OK),
--   pero si tiene valor debe ser ÚNICO (no dos usuarios con el mismo ID).
-- • La función get_email_by_username() es SECURITY DEFINER: la anon key
--   puede llamarla sin necesitar la service_role key.
-- ============================================================================

-- ── 1. Columna username (nullable) ───────────────────────────────────────────
alter table public.perfil_usuario
  add column if not exists username text;

-- Unicidad solo sobre valores no nulos (en Postgres, NULL != NULL,
-- así que múltiples NULLs conviven sin violar UNIQUE).
-- ADD CONSTRAINT no soporta IF NOT EXISTS → usamos bloque DO para idempotencia.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'uq_perfil_username'
      and conrelid = 'public.perfil_usuario'::regclass
  ) then
    alter table public.perfil_usuario
      add constraint uq_perfil_username unique (username);
  end if;
end $$;

-- Índice para la búsqueda case-insensitive en el login.
-- WHERE username IS NOT NULL evita indexar los nulos.
create index if not exists ix_perfil_username_lower
  on public.perfil_usuario (lower(username))
  where username is not null;

-- ── 2. Función de lookup username → email ────────────────────────────────────
-- SECURITY DEFINER: corre con privilegios del owner (postgres),
-- puede leer auth.users sin RLS y sin exponer la service_role key.
create or replace function public.get_email_by_username(p_username text)
returns text
language sql
stable
security definer
set search_path = public, auth
as $$
  select u.email
  from   auth.users            u
  join   public.perfil_usuario p on p.user_id = u.id
  where  lower(p.username) = lower(p_username)
  limit  1;
$$;

-- Accesible desde la anon key (solo resuelve un email, no expone datos sensibles).
grant execute on function public.get_email_by_username(text) to anon;
grant execute on function public.get_email_by_username(text) to authenticated;

-- ============================================================================
-- Verificación rápida (descomentar en el SQL Editor para probar):
-- 1. Asignar un username de prueba:
--    UPDATE perfil_usuario SET username = 'jperez'
--    WHERE user_id = '11111111-1111-1111-1111-111111111111';
-- 2. Resolver:
--    SELECT public.get_email_by_username('jperez');
--    → debe retornar el email del usuario de prueba
-- ============================================================================
