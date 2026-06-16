-- ============================================================================
-- Kreems · Seed de vendedores REALES
-- Ejecutar DESPUÉS de 001_modelo_datos.sql y ANTES de la primera carga ETL.
-- Script idempotente: ON CONFLICT actualiza cod_vendedor_autoventa sin borrar
-- el user_id que ya tuviera asignado.
-- ============================================================================
-- TAREA PENDIENTE para gerencia: asignar user_id a cada vendedor desde
-- Authentication → Users del panel de Supabase, luego actualizar con:
--   UPDATE dim_vendedor SET user_id = '<uuid>' WHERE nombre_canonico = '...';
-- ============================================================================

insert into public.dim_vendedor
  (nombre_canonico, cod_vendedor_autoventa, activo)
values
  -- Vendedores con código Autoventa conocido
  ('Ana Maria Concha Gonzalez',            null,    true),
  ('Carlos Eduardo Sanhueza Quezada',      '32287', true),
  ('Claudio Ignacio Carreno Torres',       null,    true),
  ('Daniel Eduardo Nunez Carrasco',        null,    true),  -- Obuma sin acento; Autoventa usa "Nuñez"
  ('Diego Andres Guerstein Droguett',      '33224', true),
  ('Edith Isabel Cordero Caceres',         null,    true),
  ('Fernando Omar Astorga Gonzalez',       '32293', true),
  ('Ignacio Humberto Ibanez Arevalo',      null,    true),
  ('Jorge Alfredo Jara Bravo',             '32300', true),
  ('Jorge Marcos Avila Pinto',             null,    true),
  ('Luis Marcelo Pinto Guerrero',          null,    true),
  ('Macarena Nicole Garrido Mulchi',       '32302', true),
  ('Maicol Sebastian Gutierrez Sanhueza',  '32303', true),
  ('Manuel Alejandro Pina Valenzuela',     null,    true),
  ('Marcela Andrea Sanhueza Carvajal',     '33226', true),
  ('Mauricio Andres Figueroa Holtmann',    '32304', true),
  ('Nicolas Eduardo Campos Saa',           null,    true),
  ('Rigo Antonio Lara Diaz',              '32312', true),
  ('Servando Miguel Aguayo Mas',           null,    true),
  ('Tomas Benjamin Vallejo Parada',        null,    true)
on conflict (nombre_canonico)
  do update set
    cod_vendedor_autoventa = excluded.cod_vendedor_autoventa,
    activo                 = excluded.activo;

-- Verificación inmediata
select id, nombre_canonico, cod_vendedor_autoventa, activo, user_id
from public.dim_vendedor
order by nombre_canonico;
