# Sistema de Seguimiento Comercial — Kreems

Reemplaza el reporte Power BI por una app web propia alimentada por Obuma y Autoventa.

## Estructura

```
/
├── sql/          Scripts SQL: tablas, vistas, RLS (Supabase/Postgres)
├── etl/          Pipeline Python de ingesta (idempotente, upsert por llave)
│   └── db.py     Conexión Supabase con service_role key
├── app/          App Streamlit (panel vendedor + panel gerencia)
│   └── supabase_client.py  Conexión Supabase con anon key (RLS activo)
├── data/         ⚠ SOLO LECTURA — exports ERP (en .gitignore, no se sube)
│   └── muestras/ Archivos de muestra que definen el formato de cada fuente
├── .env.example  Variables de entorno requeridas (copiar como .env)
├── requirements.txt
└── CLAUDE.md     Especificación completa del sistema
```

## Primeros pasos

### 1. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 2. Configurar credenciales
```bash
cp .env.example .env
# Editar .env con tus claves reales de Supabase
```

### 3. Flujo de actualización de datos
1. Exportar archivos desde Obuma y Autoventa → copiar a `/data/muestras/`.
2. Ejecutar el ETL **indicando el mes** (recomendado):
   ```bash
   python -m etl.run_etl --periodo 2026-05
   ```
   Sin `--periodo` carga todo lo que traigan los archivos (cualquier mes).
3. La app Streamlit lee desde Supabase: `python -m streamlit run app/main.py`.

#### Reconocimiento de archivos
El ETL identifica cada fuente por **palabra clave sobre el nombre, sin acentos**
(da igual `acuna`, `Acuña` o `ACUÑA`). Solo necesitas que el nombre contenga la
sociedad: incluir `acuna` **o** `gran natural` (los de Autoventa se reconocen por
`pedidos` / `despacho`). El archivo de Obuma **no trae la sociedad adentro**, por
eso la etiqueta en el nombre es obligatoria.

- `--periodo AAAA-MM` filtra las ventas Obuma a ese mes y, si hay varios archivos
  de una sociedad en la carpeta, **elige el que tiene datos de ese mes** (evita
  cargar por error un export viejo, p.ej. de 2025). Si un archivo no aporta filas
  del período, el ETL se detiene con un mensaje claro en vez de cargar mal.
- Conviene dejar **un solo archivo por sociedad y período** en la carpeta. Nombre
  sugerido: `obuma_acuna_2026-05.xls`, `obuma_grannatural_2026-05.xls`.

#### Ventas sin vendedor ("Sin asignar")
Obuma a veces emite documentos **sin vendedor** (campo `VENDEDOR` vacío). El ETL
los asigna a un vendedor especial **"Sin asignar"** (sin usuario ligado → por RLS
solo lo ve gerencia/admin). Así suman al total de gerencia y nada se pierde. Un
nombre que **sí** viene pero no mapea a `dim_vendedor` se registra en
`etl_no_mapeados.csv` para que lo agregues.

#### Datos demo
El seed demo (login de prueba, RLS) está en `sql/seed_demo.sql` y **no debe correrse
en producción**: inserta ventas ficticias `DEMO-*` que inflan los totales. Si por
error se cargaron, purgar con `sql/cleanup_demo_prod.sql`.

## Validación / cuadre con Power BI

El sistema fue validado contra las bases reales que alimenta Power BI (mayo 2026):

| Métrica | App (Supabase) | Archivos fuente PBI | ¿Cuadra? |
|---------|---------------:|--------------------:|:--------:|
| Facturas (Subtotal Neto) | 70.188.332 | 70.188.332 | ✅ |
| Notas de crédito | −8.519.478 | −8.519.478 | ✅ |
| Fact-NC | 61.668.854 | 61.668.854 | ✅ |
| N° facturas | 673 | 673 | ✅ |

La app reproduce **exactamente** la columna `Subtotal Neto` de los exports de Obuma.
La métrica de negocio es: **Fact-NC = Σ facturas − Σ notas de crédito** (las NC
entran con signo negativo).

Se verificó contra las **dos** tablas de Obuma y ambas dan idéntico (70.188.332):
- `Reporte Ventas Por Sucursal (ITEM)` — nivel línea, **es la que usa el ETL**
  (trae producto/categoría/fabricante/margen para los análisis).
- `Reporte Venta Por Sucursal` — nivel documento, la que usa Power BI. Su columna
  `NETO` suma lo mismo que el `Subtotal Neto` del detalle por ítem.

La medida DAX de Power BI es **equivalente** a nuestra vista:
`Fact Total (Facturas) = CALCULATE([Fact-Nc], TIPO DCTO="FACTURA ELECTRONICA")`,
donde `[Fact-Nc] = Σ NETO` (facturas + NC con signo). Aplicada sobre los archivos
reales da 70.188.332, no 70.434.512.

Notas:
- El *tile* de Power BI en pantalla mostró 70.434.512 (≈246.180 más). Esa cifra
  **no sale de ninguna tabla de Obuma** (ni ítem ni documento) → corresponde a un
  *refresh anterior* del informe PBI, no a una diferencia de datos.
- El tile PBI "N° Pedidos" (641) es conteo de **pedidos Autoventa**, no de facturas.
- Las **bonificaciones** (devoluciones por producto en mal estado, repuesto a neto 0)
  se contabilizan a su neto real, igual que en Power BI.

## Fases del proyecto

| Fase | Contenido | Estado |
|------|-----------|--------|
| 0 | Esqueleto, estructura, conexión Supabase | ✅ |
| 1 | Capa de datos: tablas SQL, vista de métricas, RLS, seed | ✅ |
| 2 | ETL idempotente (Python) | ✅ |
| 3 | App Streamlit completa + UI/UX polish | ✅ |
| 4 | API Obuma → Edge Function (tiempo real) | Pendiente |

### Detalle Fase 3 — App Streamlit

Páginas implementadas:
- **Inicio** — saludo, cards de acceso rápido, resumen KPIs del período
- **Panel Gerencia** — KPI strip (días/última factura) + grid 3×2 + tabla de 13 columnas (% Cumpl, sin margen) + ranking + editor de objetivos
- **Panel Vendedor** — KPI destacado (% Cumplimiento), máquinas, barra de avance, gauge. La proyección solo aparece si el mes está activo (`dias_trabajados < dias_totales`)
- **Análisis** — Producto/categoría (venta neta + unidades), Región/comuna (N° facturas en vez de margen), Ciclo de máquinas

Sidebar: navegación por botones con estado activo, avatar con iniciales, secciones Navegación / Período / Usuario.

## Fuentes de datos (archivos en `/data/muestras/`)

| Patrón en el nombre | Sistema | Contenido |
|---------|---------|-----------|
| `*acuna*.xls` | Obuma | Ventas sociedad Acuña |
| `*gran natural*.xls` | Obuma | Ventas sociedad Gran Natural |
| `*pedidos*.csv` | Autoventa | Pedidos detalle (delimitador `;`) |
| `*despacho*.xlsx` | Autoventa | Detalle despachos |
| `*objetivo*.xlsx` | Manual | Objetivos mensuales por vendedor |

## Llave de cruce confirmada
`Obuma."N° DCTO"` == `Autoventa."Num documento"` (documento de venta).
