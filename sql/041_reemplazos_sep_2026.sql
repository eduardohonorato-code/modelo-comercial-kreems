-- ============================================================================
-- 041 — Reemplazos sep-2026: Carlos → Joaquín Brandt y Maicol → Francisco Arriagada
-- ============================================================================
-- Mismo mecanismo que el caso Diego → Carlos (sql/021): NO se renombra a nadie,
-- cada saliente conserva su historia a su nombre y el entrante es un registro
-- NUEVO que recibe la facturación desde una fecha de corte.
--
--   · Carlos Matabenitez (id 39) ya no está; lo reemplaza Joaquín Brandt.
--     Carlos nunca tuvo cuenta propia en el ERP: su facturación llega a nombre
--     de "Diego Andres Guerstein Droguett" (id 9) y la regla 9→39 desde
--     jul-2026 la desviaba a él. Ahora se agrega 9→Joaquín desde sep-2026, que
--     manda por ser la regla más reciente. Julio y agosto siguen en Carlos.
--   · Maicol Gutierrez (id 17) sí factura con su propio nombre (cod Autoventa
--     32303). Lo reemplaza Francisco Arriagada: regla 17→Francisco desde
--     sep-2026. Feb–ago quedan en Maicol.
--
-- Los códigos de Autoventa y los nombres del ERP se dejan DONDE ESTÁN a
-- propósito: el ETL mapea nombre/código → vendedor saliente y recién después
-- aplica la reasignación por fecha. Si se movieran, una recarga de un mes
-- anterior relabelaría el histórico.
--
-- Fecha de corte: 2026-09-01 (definida por gerencia). Los objetivos de
-- septiembre ya cargados y lo facturado del 1 al 8 de sep se mueven al entrante.
--
-- Idempotente: se puede correr más de una vez. Correr en el SQL Editor de
-- Supabase. Requiere el 021 (crea vendedor_reasignacion).
-- ============================================================================

-- ── 1. Los dos vendedores nuevos ────────────────────────────────────────────
insert into public.dim_vendedor (nombre_canonico, activo)
select 'Joaquín Brandt', true
where not exists (select 1 from public.dim_vendedor
                  where nombre_canonico = 'Joaquín Brandt');

insert into public.dim_vendedor (nombre_canonico, activo)
select 'Francisco Arriagada', true
where not exists (select 1 from public.dim_vendedor
                  where nombre_canonico = 'Francisco Arriagada');

-- ── 2. Reglas de reasignación desde el 1-sep-2026 ───────────────────────────
-- 2a. Lo que llega como "Diego" (id 9) desde sep → Joaquín. Convive con la
--     regla 9→39 (jul) del sql/021: para cada fila manda la de `desde` más
--     reciente que ya empezó, así jul/ago quedan en Carlos y sep+ en Joaquín.
insert into public.vendedor_reasignacion (origen_id, destino_id, desde, nota)
select 9,
       (select id from public.dim_vendedor where nombre_canonico = 'Joaquín Brandt'),
       date '2026-09-01',
       'Joaquín Brandt reemplaza a Carlos desde sep-2026 (la facturación sigue llegando bajo el nombre de Diego)'
on conflict (origen_id, desde) do update set destino_id = excluded.destino_id,
                                             nota       = excluded.nota;

-- 2b. Red de seguridad: si algo llegara a mapear directo a Carlos (id 39) desde
--     sep, también va a Joaquín.
insert into public.vendedor_reasignacion (origen_id, destino_id, desde, nota)
select 39,
       (select id from public.dim_vendedor where nombre_canonico = 'Joaquín Brandt'),
       date '2026-09-01',
       'Joaquín Brandt reemplaza a Carlos Matabenitez desde sep-2026'
on conflict (origen_id, desde) do update set destino_id = excluded.destino_id,
                                             nota       = excluded.nota;

-- 2c. Maicol → Francisco.
insert into public.vendedor_reasignacion (origen_id, destino_id, desde, nota)
select 17,
       (select id from public.dim_vendedor where nombre_canonico = 'Francisco Arriagada'),
       date '2026-09-01',
       'Francisco Arriagada reemplaza a Maicol Gutierrez desde sep-2026 (factura bajo el nombre/código de Maicol)'
on conflict (origen_id, desde) do update set destino_id = excluded.destino_id,
                                             nota       = excluded.nota;

-- ── 3. Los salientes quedan inactivos ───────────────────────────────────────
-- Conservan su historia; salen del editor de objetivos y de los listados de
-- vendedores activos.
update public.dim_vendedor set activo = false where id in (17, 39);

-- ── 4. Mover lo de septiembre YA cargado bajo los salientes ─────────────────
-- (de aquí en adelante el ETL lo reasigna solo; esto arregla lo que entró antes
--  de crear las reglas)
update public.fact_ventas    f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and f.vendedor_id = r.origen_id
   and f.fecha >= date '2026-09-01';

update public.fact_pedidos   f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and f.vendedor_id = r.origen_id
   and f.fecha >= date '2026-09-01';

update public.fact_maquinas  f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and f.vendedor_id = r.origen_id
   and f.fecha >= date '2026-09-01';

update public.fact_despachos f set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and f.vendedor_id = r.origen_id
   and f.fecha_ruta >= date '2026-09-01';

-- ── 5. Objetivos y entradas de comisión de sep-2026 en adelante ─────────────
update public.objetivos_mensuales o set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and o.vendedor_id = r.origen_id
   and (o.anio > 2026 or (o.anio = 2026 and o.mes >= 9))
   and not exists (select 1 from public.objetivos_mensuales o2
                    where o2.vendedor_id = r.destino_id
                      and o2.anio = o.anio and o2.mes = o.mes);

update public.comision_entrada_mensual c set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and c.vendedor_id = r.origen_id
   and (c.anio > 2026 or (c.anio = 2026 and c.mes >= 9))
   and not exists (select 1 from public.comision_entrada_mensual c2
                    where c2.vendedor_id = r.destino_id
                      and c2.anio = c.anio and c2.mes = c.mes);

-- ── 6. La ruta se hereda: cartera y valores fijos de comisión ───────────────
-- Los clientes de la cartera pasan al entrante (misma ruta física). El ETL de
-- cartera (etl/cargar_cartera.py) ya aplica la reasignación vigente, así que
-- una recarga del reporte de Autoventa no los devuelve al saliente.
update public.cartera_cliente cc set vendedor_id = r.destino_id
  from public.vendedor_reasignacion r
 where r.desde = date '2026-09-01' and cc.vendedor_id = r.origen_id;

-- Cartera y salas Ganga fijas (sql/039): se copian del saliente al entrante, si
-- no la comisión de efectividad del mes saldría con cartera 0.
insert into public.comision_valor_fijo (vendedor_id, cartera_clientes, salas_ganga, nota)
select r.destino_id, v.cartera_clientes, v.salas_ganga,
       'Heredado del vendedor saliente (reemplazo sep-2026)'
  from public.vendedor_reasignacion r
  join public.comision_valor_fijo v on v.vendedor_id = r.origen_id
 where r.desde = date '2026-09-01'
on conflict (vendedor_id) do nothing;

-- Verificación:
-- select id, nombre_canonico, activo from public.dim_vendedor
--  where id in (9,17,39) or nombre_canonico in ('Joaquín Brandt','Francisco Arriagada');
-- select * from public.vendedor_reasignacion order by origen_id, desde;
-- select vendedor_id, sum(neto) from public.fact_ventas
--  where fecha >= date '2026-09-01' group by 1 order by 1;
