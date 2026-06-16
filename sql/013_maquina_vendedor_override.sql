-- ============================================================================
-- Kreems · Máquinas — override manual de vendedor + diagnóstico "Sin asignar"
-- ----------------------------------------------------------------------------
-- Algunas máquinas (FL-x) llegan SIN vendedor en la factura Obuma, o con un
-- vendedor que gerencia quiere corregir. Como la atribución oficial es por el
-- campo VENDEDOR de Obuma, estas excepciones se manejan acá, NO tocando el ETL
-- ni los archivos de /data.
--
-- · maquina_vendedor_override: (sociedad_id, documento) → vendedor_id.
--   El ETL la consulta DESPUÉS de derivar de Obuma y reasigna esos documentos.
--   Vacía por ahora → no cambia nada. Idempotente: re-correr el ETL respeta
--   estos overrides en vez de volver a "Sin asignar".
-- · v_maquinas_sin_asignar: vista de diagnóstico (solo gerencia) que lista las
--   máquinas cuyo vendedor quedó en "Sin asignar", para revisarlas y, si se
--   quiere, cargarlas en la tabla de override.
-- Idempotente.
-- ============================================================================

-- 1. Tabla de override (vacía por defecto) ───────────────────────────────────
create table if not exists public.maquina_vendedor_override (
  sociedad_id smallint not null references public.dim_sociedad(id),
  documento   text     not null,
  vendedor_id bigint   not null references public.dim_vendedor(id),
  nota        text,                       -- por qué se reasigna (auditoría)
  creado_at   timestamptz not null default now(),
  primary key (sociedad_id, documento)
);

comment on table public.maquina_vendedor_override is
  'Reasignación manual de vendedor para máquinas (FL-x) por documento. '
  'El ETL la aplica tras derivar de Obuma. Vacía = sin cambios.';

-- 2. RLS: solo gerencia/admin lee y escribe ──────────────────────────────────
alter table public.maquina_vendedor_override enable row level security;

drop policy if exists maquina_override_select on public.maquina_vendedor_override;
create policy maquina_override_select on public.maquina_vendedor_override
  for select to authenticated using (public.es_gerencia());

drop policy if exists maquina_override_admin on public.maquina_vendedor_override;
create policy maquina_override_admin on public.maquina_vendedor_override
  for all to authenticated
  using (public.es_gerencia()) with check (public.es_gerencia());

-- El ETL usa service_role (bypassa RLS); estos grants son para la app (gerencia).
grant select, insert, update, delete
  on public.maquina_vendedor_override to authenticated;

-- service_role bypassa RLS pero igual necesita GRANT vía PostgREST (el ETL la lee).
grant all on public.maquina_vendedor_override to service_role;

-- 3. Vista de diagnóstico: máquinas que quedaron en "Sin asignar" ─────────────
drop view if exists public.v_maquinas_sin_asignar;
create or replace view public.v_maquinas_sin_asignar
with (security_invoker = true) as
select
  fm.sociedad_id,
  s.nombre        as sociedad,
  fm.documento,
  fm.fecha,
  fm.tipo_mov,                 -- nueva / cambio / retiro
  fm.estado,                   -- gestionada / entregada / rechazada
  fm.cliente_rut,
  cl.razon_social as cliente,
  (ov.vendedor_id is not null) as tiene_override
from public.fact_maquinas fm
join public.dim_vendedor dv on dv.id = fm.vendedor_id
left join public.dim_sociedad s  on s.id  = fm.sociedad_id
left join public.dim_cliente  cl on cl.rut = fm.cliente_rut
left join public.maquina_vendedor_override ov
       on ov.sociedad_id = fm.sociedad_id and ov.documento = fm.documento
where dv.nombre_canonico = 'Sin asignar'
  and public.es_gerencia()
order by fm.fecha desc, fm.documento;

grant select on public.v_maquinas_sin_asignar to authenticated;

-- ============================================================================
-- Uso futuro (cuando gerencia decida atribuir una máquina):
--   insert into maquina_vendedor_override (sociedad_id, documento, vendedor_id, nota)
--   values (2, '3131', <id_vendedor>, 'SUPERMERCADO VIDA — vendedor vacío en Obuma');
--   luego re-correr el ETL del período → la máquina deja de ser "Sin asignar".
--
-- Ver pendientes:
--   select * from v_maquinas_sin_asignar;
-- ============================================================================
