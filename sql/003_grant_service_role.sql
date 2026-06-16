-- ============================================================================
-- Parche: permisos explícitos para service_role
-- El service_role bypasea RLS pero igualmente necesita GRANT sobre las tablas
-- cuando se accede vía PostgREST (la API REST de Supabase).
-- Ejecutar una sola vez en SQL Editor.
-- ============================================================================

grant usage on schema public to service_role;
grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;

-- Verificación
select grantee, table_name, privilege_type
from information_schema.role_table_grants
where grantee = 'service_role'
  and table_schema = 'public'
order by table_name;
