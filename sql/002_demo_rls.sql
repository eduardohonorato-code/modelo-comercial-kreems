-- ============================================================================
-- DEMOSTRACIÓN DE RLS  (ejecutar DESPUÉS de 001_modelo_datos.sql)
-- ----------------------------------------------------------------------------
-- No necesita login real: simulamos el JWT de cada usuario con
-- set_config('request.jwt.claims', ...). Es la forma estándar de probar RLS
-- en el SQL Editor de Supabase / psql.
--
-- Cada bloque va en su propia transacción y termina en ROLLBACK: no modifica
-- nada, solo cambia el rol/identidad efectivos para la consulta.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- A) Como postgres (service_role / admin): se ve TODO. Es el "control".
-- ----------------------------------------------------------------------------
select '--- A) postgres: ve todos los vendedores ---' as paso;
select vendedor_id, nombre_canonico, anio, mes, fact_nc, n_documentos,
       proyeccion_cierre, pct_cumplimiento, pct_proyeccion, pct_efectividad,
       no_facturado_monto, maquinas_gestionadas, maquinas_entregadas, maquinas_retiros
from public.v_resumen_vendedor_mes
order by nombre_canonico;
-- Esperado: 2 filas (Vendedor Demo y Vendedor Dos).


-- ----------------------------------------------------------------------------
-- B) Como VENDEDOR (vendedor.demo, uuid 1111...): solo SUS filas.
-- ----------------------------------------------------------------------------
begin;
  set local role authenticated;
  select set_config('request.jwt.claims',
    '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

  select '--- B) vendedor.demo: solo se ve a sí mismo ---' as paso;

  -- Vista de resumen: debe traer SOLO "Vendedor Demo".
  select vendedor_id, nombre_canonico, anio, mes, fact_nc, n_documentos,
         proyeccion_cierre, pct_cumplimiento, pct_efectividad,
         no_facturado_monto, maquinas_gestionadas, maquinas_entregadas, maquinas_retiros
  from public.v_resumen_vendedor_mes;

  -- Tablas base: también filtradas.
  select 'fact_ventas visibles para el vendedor' as detalle, count(*) as filas from public.fact_ventas;
  select 'dim_vendedor visibles para el vendedor' as detalle, count(*) as filas from public.dim_vendedor;
  -- Esperado: vista=1 fila, fact_ventas=2 (su factura + su NC), dim_vendedor=1.
rollback;


-- ----------------------------------------------------------------------------
-- C) Como GERENCIA (gerente.demo, uuid 2222...): ve TODOS y puede editar objetivos.
-- ----------------------------------------------------------------------------
begin;
  set local role authenticated;
  select set_config('request.jwt.claims',
    '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}', true);

  select '--- C) gerente.demo: ve todos los vendedores ---' as paso;

  select vendedor_id, nombre_canonico, anio, mes, fact_nc, pct_cumplimiento
  from public.v_resumen_vendedor_mes
  order by nombre_canonico;
  -- Esperado: 2 filas.

  select 'fact_ventas visibles para gerencia' as detalle, count(*) as filas from public.fact_ventas;
  -- Esperado: 3 (todas).

  -- Edición de objetivos permitida para gerencia:
  update public.objetivos_mensuales o
     set obj_visitas = obj_visitas
   from public.dim_vendedor dv
   where dv.id = o.vendedor_id and dv.nombre_canonico = 'Vendedor Demo'
     and o.anio = 2026 and o.mes = 5;
  select '--- C) gerencia pudo ejecutar UPDATE sobre objetivos (OK) ---' as paso;
rollback;


-- ----------------------------------------------------------------------------
-- D) VENDEDOR intentando editar objetivos: RLS lo BLOQUEA (0 filas afectadas).
-- ----------------------------------------------------------------------------
begin;
  set local role authenticated;
  select set_config('request.jwt.claims',
    '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

  with upd as (
    update public.objetivos_mensuales set obj_venta = 999999999
    where anio = 2026 and mes = 5
    returning 1
  )
  select '--- D) vendedor intenta editar objetivos ---' as paso,
         count(*) as filas_afectadas from upd;
  -- Esperado: filas_afectadas = 0  (no tiene permiso de escritura -> RLS lo impide).
rollback;
