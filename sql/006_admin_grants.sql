-- ============================================================================
-- 006 — Grants y políticas para la página de Administración de Usuarios
-- ============================================================================
-- La página Admin usa la SERVICE_ROLE key (bypassa RLS por completo).
-- Estos grants son adicionales para que el rol authenticated + admin también
-- pueda operar si en el futuro se llama desde un cliente autenticado.
-- ============================================================================

-- ── perfil_usuario: grants de escritura (la política admin_all ya existe) ────
grant insert, update, delete on public.perfil_usuario to authenticated;

-- ── dim_vendedor: política de UPDATE para admin (asignar/quitar user_id) ─────
drop policy if exists dim_vendedor_admin_write on public.dim_vendedor;
create policy dim_vendedor_admin_write on public.dim_vendedor
  for update to authenticated
  using  (public.mi_rol() = 'admin')
  with check (public.mi_rol() = 'admin');

grant update (user_id, activo, nombre_canonico, cod_vendedor_autoventa, agrupacion)
  on public.dim_vendedor to authenticated;

-- ── gerencia también puede actualizar activo de dim_vendedor ─────────────────
drop policy if exists dim_vendedor_gerencia_write on public.dim_vendedor;
create policy dim_vendedor_gerencia_write on public.dim_vendedor
  for update to authenticated
  using  (public.es_gerencia())
  with check (public.es_gerencia());
