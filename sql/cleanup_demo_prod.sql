-- ============================================================================
-- Kreems · LIMPIEZA de datos demo en PRODUCCIÓN
-- ----------------------------------------------------------------------------
-- Ejecutar UNA vez en Supabase (SQL Editor) para purgar las filas "DEMO-*"
-- que el seed original (sección 6.5 de 001_modelo_datos.sql) insertó en las
-- tablas de hechos y que estaban inflando los totales:
--   · Fact-NC sobraba 1.500.000 en facturas y 100.000 en notas de crédito
--   · N° de facturas sobraba 2
--
-- Es seguro y re-ejecutable: solo borra filas cuya llave empieza con 'DEMO'
-- o los clientes demo conocidos. No toca datos reales del ETL.
-- ============================================================================

delete from public.fact_ventas   where n_dcto    like 'DEMO%';
delete from public.fact_pedidos  where n_pedido  like 'DEMO%';
delete from public.fact_maquinas where documento like 'DEMO%';

-- Objetivos demo (de los vendedores de prueba)
delete from public.objetivos_mensuales o
using public.dim_vendedor dv
where o.vendedor_id = dv.id
  and dv.nombre_canonico in ('Vendedor Demo','Vendedor Dos');

-- Dimensiones demo (después de los hechos, por las FK)
delete from public.dim_producto where codigo like 'DEMO%';
delete from public.dim_cliente  where rut in ('11111111-1','22222222-2');

-- Verificación: ambos conteos deben dar 0
-- select count(*) as ventas_demo   from public.fact_ventas   where n_dcto like 'DEMO%';
-- select count(*) as clientes_demo from public.dim_cliente   where rut in ('11111111-1','22222222-2');
