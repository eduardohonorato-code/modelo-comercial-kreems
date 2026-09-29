-- ============================================================================
-- 042 — Joaquín Brandt ya factura con su propio nombre en los ERP
-- ============================================================================
-- Desde el 1-sep-2026 Joaquín Brandt (id 40, sql/041) trabaja la ruta usando la
-- cuenta de "Diego Andres Guerstein Droguett" (id 9) — la regla 9→40 desde sep
-- lo cubre. El 29-sep empezó a pedir/facturar TAMBIÉN con su cuenta propia
-- "Joaquin Ignacio Brandt Acuña", que no calza con el nombre_canonico y caía en
-- "Sin asignar" (Obuma: 6 facturas $617.847; Autoventa: 8 pedidos $760.512;
-- 6 despachos). Se agrega el alias → 40. Ese nombre no existe antes del 29-sep,
-- así que el alias (date-blind) no relabela histórico.
-- Lo demás de "Sin asignar" son documentos SIN vendedor en Obuma y NO se toca.
--
-- Además se aplican las reglas de vendedor_reasignacion a despachos y máquinas
-- que quedaron en el vendedor saliente (la carga web de despachos y la
-- reatribución de máquinas desde Autoventa no las aplicaban):
--   · Diego (9): jul/ago → Carlos (39); sep+ → Joaquín (40)
--   · Maicol (17): sep+ → Francisco (41)
--
-- Idempotente. Después de correrlo, la carga diaria (o run_obuma_api /
-- run_autoventa_api --periodo 2026-09) saca lo de Joaquín de "Sin asignar".
-- ============================================================================

insert into public.vendedor_alias (alias, vendedor_id, nota)
select 'Joaquin Ignacio Brandt Acuña',
       (select id from public.dim_vendedor where nombre_canonico = 'Joaquín Brandt'),
       'Nombre con el que Joaquín Brandt factura en Obuma/Autoventa (desde sep-2026)'
on conflict (alias) do update set vendedor_id = excluded.vendedor_id,
                                  nota        = excluded.nota;

-- Despachos y máquinas que quedaron en Diego: se aplica la regla vigente según
-- la fecha (la de `desde` más reciente que ya empezó).
update public.fact_despachos f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where f.vendedor_id = r.origen_id and f.fecha_ruta >= r.desde
   and not exists (select 1 from public.vendedor_reasignacion r2
                    where r2.origen_id = r.origen_id and r2.desde > r.desde
                      and f.fecha_ruta >= r2.desde);

update public.fact_maquinas f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where f.vendedor_id = r.origen_id and f.fecha >= r.desde
   and not exists (select 1 from public.vendedor_reasignacion r2
                    where r2.origen_id = r.origen_id and r2.desde > r.desde
                      and f.fecha >= r2.desde);

-- Verificación:
-- select * from public.vendedor_alias;
-- select to_char(fecha_ruta,'YYYY-MM') mes, vendedor_id, count(*)
--   from public.fact_despachos where fecha_ruta >= date '2026-07-01'
--    and vendedor_id in (9,26,39,40) group by 1,2 order by 1,2;
