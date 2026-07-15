-- 021 · Estado ERP del cliente (activo/inactivo) desde la lista de Autoventa
-- ---------------------------------------------------------------------------
-- La lista de clientes del ERP (Autoventa) trae una columna 'cliente_activo'
-- (1/0) que NO viene por la API ni por los reportes de venta. Se sube aparte
-- (Excel 'lista_clientes.xlsx', columna AW) y se guarda aquí para cruzarla con
-- el análisis de máquinas/cartera en la app. Es dato de referencia, de baja
-- cardinalidad; se actualiza por upsert (rut = llave).
-- ---------------------------------------------------------------------------
create table if not exists public.cliente_estado_erp (
  rut            text primary key,
  activo         boolean not null,
  razon_social   text,
  actualizado_at timestamptz not null default now()
);

comment on table public.cliente_estado_erp is
  'Flag activo/inactivo del cliente según el ERP Autoventa (lista_clientes.xlsx, col cliente_activo). Se sube desde la app (página Carga).';

alter table public.cliente_estado_erp enable row level security;

-- Dato de referencia: lectura para cualquier autenticado (como dim_cliente).
drop policy if exists cliente_estado_erp_sel on public.cliente_estado_erp;
create policy cliente_estado_erp_sel on public.cliente_estado_erp
  for select to authenticated using (true);

grant select on public.cliente_estado_erp to authenticated;
-- La escritura la hace el ETL/carga con la service_role key. En este proyecto los
-- grants a service_role NO son automáticos para tablas nuevas → concederlos explícito.
grant select, insert, update, delete on public.cliente_estado_erp to service_role;
