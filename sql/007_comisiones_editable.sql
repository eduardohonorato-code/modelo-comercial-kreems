-- ============================================================================
-- Kreems · Comisiones — habilitar EDICIÓN de escalas y parámetros desde la app
-- ----------------------------------------------------------------------------
-- Las tablas de config (planes, tramos, parámetros) se crearon en 006 con solo
-- política de SELECT. Aquí se agrega permiso de escritura SOLO para gerencia,
-- para poder editar las tablas de comisión desde el panel.
-- Idempotente (DROP POLICY IF EXISTS + grants repetibles).
-- ============================================================================

do $$
declare t text;
begin
  foreach t in array array[
    'comision_plan','comision_tramo_pnv','comision_tramo_maquinas',
    'comision_tramo_efectividad','comision_parametro'
  ] loop
    -- Política de escritura (insert/update/delete) solo gerencia/admin.
    execute format('drop policy if exists %I on public.%I', t||'_admin', t);
    execute format(
      'create policy %I on public.%I for all to authenticated
         using (public.es_gerencia()) with check (public.es_gerencia())',
      t||'_admin', t);
  end loop;
end $$;

grant insert, update, delete on
  public.comision_plan, public.comision_tramo_pnv,
  public.comision_tramo_maquinas, public.comision_tramo_efectividad,
  public.comision_parametro
  to authenticated;

-- ============================================================================
-- FIN. Tras ejecutar, la pestaña "Escalas y parámetros" puede guardar cambios.
-- ============================================================================
