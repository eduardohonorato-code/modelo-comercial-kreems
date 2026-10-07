-- ============================================================================
-- 047 — El vendedor puede LEER lo necesario para ver su avance de comisiones
-- ============================================================================
-- Desde oct-2026 cada vendedor ve en su panel la ficha "Mi avance del mes".
-- Para calcularla con SU usuario necesita leer:
--   · comision_v1_parametro  → pisos y metas generales (iguales para todos, no
--                              son datos sensibles).
--   · comision_ruta_mensual  → SOLO su fila (agendamientos/visitas del reporte).
--   · comision_v1_meta       → SOLO sus metas manuales, si las hay.
-- La escritura sigue siendo solo de gerencia (políticas *_admin de 022/026/027).
-- Las políticas se suman (OR) a las existentes. Idempotente.
-- ============================================================================

drop policy if exists comision_v1_parametro_lectura on public.comision_v1_parametro;
create policy comision_v1_parametro_lectura on public.comision_v1_parametro
    for select to authenticated
    using (true);

drop policy if exists comision_ruta_mensual_propia on public.comision_ruta_mensual;
create policy comision_ruta_mensual_propia on public.comision_ruta_mensual
    for select to authenticated
    using ((select public.es_gerencia()) or vendedor_id = (select public.mi_vendedor_id()));

drop policy if exists comision_v1_meta_propia on public.comision_v1_meta;
create policy comision_v1_meta_propia on public.comision_v1_meta
    for select to authenticated
    using ((select public.es_gerencia()) or vendedor_id = (select public.mi_vendedor_id()));
