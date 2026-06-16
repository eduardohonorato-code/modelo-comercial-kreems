# /sql — Capa de datos (Fase 1)

| Archivo | Qué hace |
|---------|----------|
| `001_modelo_datos.sql` | Crea **todo**: dimensiones, hechos, vista de métricas, roles/RLS y seed mínimo. Idempotente. |
| `002_demo_rls.sql` | Demuestra que el RLS filtra por usuario. No modifica datos (cada bloque hace `ROLLBACK`). |

## Cómo aplicar

### Opción A — SQL Editor de Supabase (recomendado para empezar)
1. Panel de Supabase → **SQL Editor** → **New query**.
2. Pega el contenido de `001_modelo_datos.sql` y pulsa **Run**.
3. (Opcional) Abre otra query, pega `002_demo_rls.sql` y **Run** para ver la prueba de RLS.

### Opción B — psql / CLI
```bash
# Cadena de conexión: Supabase → Settings → Database → Connection string (URI)
psql "postgresql://postgres:[PASSWORD]@db.<ref>.supabase.co:5432/postgres" -f sql/001_modelo_datos.sql
psql "postgresql://postgres:[PASSWORD]@db.<ref>.supabase.co:5432/postgres" -f sql/002_demo_rls.sql
```

> Es idempotente: volver a correr `001` no duplica nada ni da error.

## Usuarios de prueba que crea el seed
| Email | Password | Rol |
|-------|----------|-----|
| `vendedor.demo@kreems.cl` | `Demo1234!` | vendedor |
| `gerente.demo@kreems.cl` | `Demo1234!` | gerencia |

Si tu versión de GoTrue no permite el login con estos usuarios creados por SQL,
créalos desde **Authentication → Users → Add user** con esos mismos emails y luego
re-ejecuta `001` (el `ON CONFLICT` solo ajustará los perfiles). La **demo de RLS**
(`002`) funciona igual, porque simula el JWT y no requiere login real.

## Qué demuestra `002_demo_rls.sql`
- **A) postgres** → ve los 2 vendedores (control).
- **B) vendedor.demo** → la vista trae **1 fila** (solo él); `fact_ventas` = 2 (su factura + su NC); `dim_vendedor` = 1.
- **C) gerente.demo** → ve **2 filas**; `fact_ventas` = 3; puede **UPDATE** objetivos.
- **D) vendedor** intentando editar objetivos → **0 filas afectadas** (RLS bloquea la escritura).

## Valores esperados de la vista (mes 2026-05, Vendedor Demo)
Sirven para validar las definiciones de la sección 3 de `CLAUDE.md`:

| Métrica | Valor | Fórmula |
|---------|-------|---------|
| Fact-NC | 900.000 | 1.000.000 − 100.000 |
| N° documentos | 1 | facturas distintas (la NC no cuenta) |
| Proyección cierre | 1.395.000 | 900.000 / 20 × 31 |
| % Cumplimiento | 0,45 | 900.000 / 2.000.000 |
| % Proyección | 0,6975 | 1.395.000 / 2.000.000 |
| % Efectividad | 0,02 | 1 / 50 |
| No facturado (monto) | 50.000 | pedido `Sin DTE` |
| Máquinas gestionadas | 2 | FL-4 (`tipo_mov='nueva'`) |
| Máquinas entregadas | 1 | nueva + `estado='entregada'` |
| Máquinas retiros | 1 | FL-2 (`tipo_mov='retiro'`) |
| Conversión gestionada→entregada | 0,5 | 1 / 2 |
