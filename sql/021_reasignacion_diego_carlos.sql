-- ============================================================================
-- 021 — Separar historia de Diego + reasignación por fecha a Carlos
-- ============================================================================
-- Corrige sql/020 (que había fusionado ambos en un registro): ahora Diego
-- conserva su historia (feb-jun) y Carlos toma julio en adelante.
--
--   · id 9 vuelve a ser "Diego Andres Guerstein Droguett" (INACTIVO: ya no trabaja)
--     → su historia feb-jun queda a su nombre, intacta.
--   · "Carlos Matabenitez" pasa a ser un registro NUEVO (id propio).
--   · Regla de reasignación POR FECHA: lo que el ETL mapea a Diego (id 9) con
--     fecha >= 2026-07-01 se reasigna a Carlos. El ETL la respeta en cada carga
--     (por eso julio, aunque llegue como "Diego", queda en Carlos; junio, no).
--   · Se mueve lo de julio YA cargado bajo id 9 → Carlos.
--   · Se elimina el alias por nombre de sql/020 (ya no se usa: id 9 es Diego).
--
-- Correr una vez en el SQL Editor de Supabase. Requiere haber corrido antes el
-- 020 (crea vendedor_alias). El código del ETL ya soporta la reasignación.
-- ============================================================================

-- 1. Tabla de reasignación por fecha (id origen → id destino desde una fecha)
create table if not exists public.vendedor_reasignacion (
  origen_id   bigint not null references public.dim_vendedor(id),
  destino_id  bigint not null references public.dim_vendedor(id),
  desde       date   not null,
  nota        text,
  primary key (origen_id, desde)
);
grant select on public.vendedor_reasignacion to authenticated;
grant all    on public.vendedor_reasignacion to service_role;

-- 2. id 9 vuelve a ser Diego (inactivo).
update public.dim_vendedor
   set nombre_canonico = 'Diego Andres Guerstein Droguett', activo = false
 where id = 9;

-- 3. Carlos como registro NUEVO (solo si no existe ya).
insert into public.dim_vendedor (nombre_canonico, activo)
select 'Carlos Matabenitez', true
where not exists (select 1 from public.dim_vendedor
                  where nombre_canonico = 'Carlos Matabenitez');

-- 4. Regla: lo de Diego (9) desde jul-2026 → Carlos.
insert into public.vendedor_reasignacion (origen_id, destino_id, desde, nota)
select 9,
       (select id from public.dim_vendedor
         where nombre_canonico = 'Carlos Matabenitez' order by id desc limit 1),
       date '2026-07-01',
       'Carlos reemplaza a Diego desde jul-2026 (factura bajo el nombre de Diego)'
on conflict (origen_id, desde) do update set destino_id = excluded.destino_id;

-- 5. Mover lo de JULIO ya cargado de Diego (9) → Carlos.
update public.fact_ventas
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and fecha >= date '2026-07-01';

update public.fact_pedidos
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and fecha >= date '2026-07-01';

update public.fact_maquinas
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and fecha >= date '2026-07-01';

update public.fact_despachos
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and fecha_ruta >= date '2026-07-01';

update public.objetivos_mensuales
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and anio = 2026 and mes >= 7;

update public.comision_entrada_mensual
   set vendedor_id = (select destino_id from public.vendedor_reasignacion where origen_id = 9)
 where vendedor_id = 9 and anio = 2026 and mes >= 7;

-- 6. Quitar el alias por nombre de Diego (de sql/020): ya no aplica.
delete from public.vendedor_alias where alias ilike '%guerstein%';

-- Verificación:
-- select id, nombre_canonico, activo from public.dim_vendedor where id = 9
--   or nombre_canonico = 'Carlos Matabenitez';
-- select * from public.vendedor_reasignacion;
