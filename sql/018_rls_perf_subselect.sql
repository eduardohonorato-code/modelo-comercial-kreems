-- 018 · Optimización de RLS: evaluar las funciones de auth UNA sola vez por query
-- ---------------------------------------------------------------------------
-- Problema (2026-07-02): tras cargar el histórico 2025 de Acuña, fact_ventas
-- pasó de ~27K a ~64K filas. La vista v_resumen_vendedor_mes es
-- security_invoker=true, así que aplica las políticas RLS de las tablas base
-- con el rol del usuario. Las políticas llamaban a public.es_gerencia() y
-- public.mi_vendedor_id() SIN envolver → Postgres las evaluaba POR FILA
-- (una consulta a perfil_usuario/dim_vendedor por cada fila). Con 64K filas ×
-- 3 tablas de hechos, la vista superó el statement_timeout del rol
-- authenticated (~8s) → Error 57014 "canceling statement due to statement
-- timeout" en la página de Inicio (get_resumen).
--
-- Fix estándar de Supabase: envolver las llamadas en un subselect escalar
-- `(select public.fn())`. Así el planner las evalúa como InitPlan UNA vez y
-- reusa el resultado para todas las filas. No cambia la semántica de la
-- política (mismos permisos), solo el rendimiento. Idempotente.
-- ---------------------------------------------------------------------------

-- Hechos: vendedor ve solo sus filas; gerencia/admin ven todo.
do $$
declare t text;
begin
  foreach t in array array[
    'fact_ventas','fact_pedidos','fact_despachos','fact_maquinas'
  ] loop
    execute format('drop policy if exists %I on public.%I', t||'_sel', t);
    execute format(
      'create policy %I on public.%I for select to authenticated
         using ((select public.es_gerencia())
                or vendedor_id = (select public.mi_vendedor_id()))',
      t||'_sel', t);
  end loop;
end $$;

-- dim_vendedor: vendedor ve su fila; gerencia/admin ven todas.
drop policy if exists dim_vendedor_sel on public.dim_vendedor;
create policy dim_vendedor_sel on public.dim_vendedor
  for select to authenticated
  using ((select public.es_gerencia()) or id = (select public.mi_vendedor_id()));

-- objetivos_mensuales: vendedor lee lo suyo; gerencia/admin CRUD total.
drop policy if exists objetivos_sel       on public.objetivos_mensuales;
drop policy if exists objetivos_admin_all on public.objetivos_mensuales;

create policy objetivos_sel on public.objetivos_mensuales
  for select to authenticated
  using ((select public.es_gerencia())
         or vendedor_id = (select public.mi_vendedor_id()));

create policy objetivos_admin_all on public.objetivos_mensuales
  for all to authenticated
  using ((select public.es_gerencia()))
  with check ((select public.es_gerencia()));

-- perfil_usuario: cada uno ve el suyo; admin ve/gestiona todos.
drop policy if exists perfil_sel       on public.perfil_usuario;
drop policy if exists perfil_admin_all on public.perfil_usuario;

create policy perfil_sel on public.perfil_usuario
  for select to authenticated
  using (user_id = (select auth.uid()) or (select public.mi_rol()) = 'admin');

create policy perfil_admin_all on public.perfil_usuario
  for all to authenticated
  using ((select public.mi_rol()) = 'admin')
  with check ((select public.mi_rol()) = 'admin');
