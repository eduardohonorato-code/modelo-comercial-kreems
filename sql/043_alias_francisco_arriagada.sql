-- ============================================================================
-- 043 — Francisco Arriagada ya factura con su propio nombre en los ERP
-- ============================================================================
-- Mismo caso que Joaquín (sql/042). Desde el 1-sep-2026 Francisco (id 41,
-- sql/041) trabaja la ruta con la cuenta de "Maicol Sebastian Gutierrez
-- Sanhueza" (id 17) — la regla 17→41 desde sep lo cubre y NO cambia. El 30-sep
-- empezó a pedir/facturar TAMBIÉN con su cuenta propia "Francisco Javier
-- Arriagada Wall", que no calza con el nombre_canonico "Francisco Arriagada" y
-- cayó en "Sin asignar":
--   · Obuma: 8 facturas, $787.580 (folios 5786-5788, 5804-5808)
--   · Autoventa: 8 pedidos, $787.580 (6029, 6043, 6049, 6091, 6101, 6104, 6114, 6115)
--   · Despachos: 8, los mismos folios
--   · Máquinas: ninguna
-- El resto de "Sin asignar" de sep ($2.849.945) son documentos SIN vendedor en
-- Obuma y NO se tocan: los updates filtran por los folios/pedidos exactos.
--
-- 1) Alias → las próximas cargas (diaria y subida de despachos) lo asignan solas.
-- 2) Se mueven de una vez las filas ya cargadas, para no esperar la carga.
-- Idempotente. Solo toca Gran Natural (sociedad 2) y filas en "Sin asignar".
-- ============================================================================

insert into public.vendedor_alias (alias, vendedor_id, nota)
select 'Francisco Javier Arriagada Wall',
       (select id from public.dim_vendedor where nombre_canonico = 'Francisco Arriagada'),
       'Nombre con el que Francisco Arriagada factura en Obuma/Autoventa (desde 30-sep-2026)'
on conflict (alias) do update set vendedor_id = excluded.vendedor_id,
                                  nota        = excluded.nota;

update public.fact_ventas
   set vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Francisco Arriagada')
 where sociedad_id = 2
   and vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Sin asignar')
   and fecha = date '2026-09-30'
   and n_dcto in ('5786','5787','5788','5804','5805','5806','5807','5808');

update public.fact_pedidos
   set vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Francisco Arriagada')
 where sociedad_id = 2
   and vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Sin asignar')
   and fecha >= date '2026-09-01'
   and n_pedido::text in ('6029','6043','6049','6091','6101','6104','6114','6115');

update public.fact_despachos
   set vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Francisco Arriagada')
 where sociedad_id = 2
   and vendedor_id = (select id from public.dim_vendedor where nombre_canonico = 'Sin asignar')
   and fecha_ruta >= date '2026-09-01'
   and documento in ('5786','5787','5788','5804','5805','5806','5807','5808');

-- Verificación (esperado: fact_ventas 36 filas / $2.849.945 = solo docs sin
-- vendedor; fact_pedidos y fact_despachos 0):
-- select 'ventas' t, count(*), sum(neto) from public.fact_ventas
--  where vendedor_id = 26 and fecha >= date '2026-09-01'
-- union all
-- select 'pedidos', count(*), sum(neto) from public.fact_pedidos
--  where vendedor_id = 26 and fecha >= date '2026-09-01'
-- union all
-- select 'despachos', count(*), null from public.fact_despachos
--  where vendedor_id = 26 and fecha_ruta >= date '2026-09-01';
