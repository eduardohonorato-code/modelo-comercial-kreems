-- ============================================================================
-- Kreems · DATOS DEMO (solo para entornos de prueba / desarrollo)
-- ----------------------------------------------------------------------------
-- ⚠ NO EJECUTAR EN PRODUCCIÓN. Estas filas usan llaves "DEMO-*" e insertan
--   ventas, NC, pedidos y máquinas ficticias en las MISMAS tablas de hechos
--   que usa el ETL. Si se ejecutan contra la base real, inflan los totales
--   (Fact-NC, N° de facturas, etc.) y dejan de cuadrar con Power BI.
--
-- Sirven para: probar el login demo, el RLS y que las vistas calculan bien
--   SIN tener que cargar datos reales.
--
-- Para purgar estos datos de una base donde ya se ejecutaron por error, usar
--   sql/cleanup_demo_prod.sql.
-- ============================================================================

-- Clientes demo
insert into public.dim_cliente (rut, razon_social, comuna, region, tipo, es_maquina, sociedad_id) values
  ('11111111-1','Almacén Demo Uno','Santiago','Metropolitana','Tradicional', true, 2),
  ('22222222-2','Almacén Demo Dos','Valparaíso','Valparaíso','Tradicional', false, 2)
on conflict (rut) do nothing;

-- Productos demo
insert into public.dim_producto (codigo, nombre, categoria, subcategoria, fabricante, unidad_medida) values
  ('DEMO-001','Helado Demo 1L','HELADOS','POTE','Kreems','UN'),
  ('DEMO-MAQ','Máquina POP','MAQUINAS_POP','FREEZER','Kreems','UN')
on conflict (codigo) do nothing;

-- Objetivos demo (para los vendedores demo creados en 001_modelo_datos.sql)
insert into public.objetivos_mensuales (vendedor_id, anio, mes, obj_venta, obj_maquinas, obj_visitas)
select id, 2026, 5, 2000000, 5, 50 from public.dim_vendedor where nombre_canonico = 'Vendedor Demo'
on conflict (vendedor_id, anio, mes) do update
  set obj_venta = excluded.obj_venta, obj_maquinas = excluded.obj_maquinas, obj_visitas = excluded.obj_visitas;

insert into public.objetivos_mensuales (vendedor_id, anio, mes, obj_venta, obj_maquinas, obj_visitas)
select id, 2026, 5, 1000000, 3, 40 from public.dim_vendedor where nombre_canonico = 'Vendedor Dos'
on conflict (vendedor_id, anio, mes) do update
  set obj_venta = excluded.obj_venta, obj_maquinas = excluded.obj_maquinas, obj_visitas = excluded.obj_visitas;

-- Ventas demo (factura + NC para Vendedor Demo; factura para Vendedor Dos)
insert into public.fact_ventas
  (fecha, tipo_dcto, n_dcto, linea, vendedor_id, cliente_rut, producto_codigo, sociedad_id, sucursal, cantidad, neto, total, costo, margen)
select '2026-05-10','FACTURA','DEMO-F1',1, dv.id,'11111111-1','DEMO-001',2,'Centro', 100, 1000000, 1190000, 600000, 400000
from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_ventas do nothing;

insert into public.fact_ventas
  (fecha, tipo_dcto, n_dcto, linea, vendedor_id, cliente_rut, producto_codigo, sociedad_id, sucursal, cantidad, neto, total, costo, margen)
select '2026-05-15','NOTA DE CREDITO','DEMO-NC1',1, dv.id,'11111111-1','DEMO-001',2,'Centro', -10, -100000, -119000, -60000, -40000
from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_ventas do nothing;

insert into public.fact_ventas
  (fecha, tipo_dcto, n_dcto, linea, vendedor_id, cliente_rut, producto_codigo, sociedad_id, sucursal, cantidad, neto, total, costo, margen)
select '2026-05-12','FACTURA','DEMO-F2',1, dv.id,'22222222-2','DEMO-001',2,'Costa', 50, 500000, 595000, 300000, 200000
from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Dos'
on conflict on constraint uq_fact_ventas do nothing;

-- Pedido no facturado (Sin DTE) para Vendedor Demo
insert into public.fact_pedidos
  (n_pedido, num_documento, doc_venta, fecha, vendedor_id, cliente_rut, producto_codigo, sociedad_id, neto, linea)
select 'DEMO-P1', null, 'Sin DTE','2026-05-09', dv.id,'11111111-1','DEMO-001',2, 50000, 1
from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_pedidos do nothing;

-- Máquinas para Vendedor Demo: 2 gestionadas (1 entregada) + 1 retiro
insert into public.fact_maquinas (documento, fecha, vendedor_id, cliente_rut, tipo_mov, estado, sociedad_id)
select 'DEMO-M1','2026-05-08', dv.id,'11111111-1','nueva','gestionada',2 from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_maquinas do nothing;
insert into public.fact_maquinas (documento, fecha, vendedor_id, cliente_rut, tipo_mov, estado, sociedad_id)
select 'DEMO-M2','2026-05-20', dv.id,'11111111-1','nueva','entregada',2 from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_maquinas do nothing;
insert into public.fact_maquinas (documento, fecha, vendedor_id, cliente_rut, tipo_mov, estado, sociedad_id)
select 'DEMO-M3','2026-05-22', dv.id,'22222222-2','retiro','entregada',2 from public.dim_vendedor dv where dv.nombre_canonico='Vendedor Demo'
on conflict on constraint uq_fact_maquinas do nothing;
