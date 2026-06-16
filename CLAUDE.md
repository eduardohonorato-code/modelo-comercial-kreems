# PROMPT MAESTRO — Sistema de Seguimiento Comercial (Kreems)
> Este archivo es la especificación completa. Colócalo en el repo como `CLAUDE.md`.
> Claude Code lo lee en cada sesión, así los prompts de cada fase se mantienen cortos.

Eres un ingeniero de datos full-stack. Vas a construir, desde cero, un sistema que reemplaza un
reporte de Power BI por una aplicación web propia, alimentada por los ERP de la empresa.
Trabaja por fases, valida con las muestras reales y no avances de fase sin dejar la anterior funcionando.

---

## 0. Reglas de la carpeta /data (LEER PRIMERO)
- Existe una carpeta **`/data/muestras/`** con archivos que coloca el usuario. Son **muestras** que
  definen el formato exacto de cada fuente (columnas, delimitador, cómo se cruzan). Sirven para
  desarrollar y validar el pipeline. NO son "datos de prueba" en un formato distinto: el formato es el real.
- **`/data` es de SOLO LECTURA para el agente**: nunca crear, modificar, sobrescribir ni borrar
  archivos ahí. El ETL solo lee de `/data` y escribe en Supabase.
- Agregar `/data/` al `.gitignore` (esos exports traen RUT y datos de clientes; no deben subirse al repo).
- **Ingesta: la cadencia está POR DEFINIR** (mensual, por trimestre, histórico completo). El ETL debe ser
  **idempotente (upsert por llave)** y **agnóstico a cuántos períodos traiga cada carga**: cargar un mes,
  varios meses o todo el histórico de una sola vez debe dar el mismo resultado sin duplicar. La cadencia
  será solo una decisión de cuándo se corre el script, no un cambio de código.
- Nombres de archivo esperados en `/data/muestras/` (fijar y referenciar de forma explícita en el ETL):
  `obuma_ventas_acuna.xlsx`, `obuma_ventas_grannatural.xlsx`, `autoventa_pedidos.csv`,
  `autoventa_despachos.xlsx`, `objetivos.xlsx`. (Ajustar a los nombres reales que use el usuario.)

## 1. Contexto de negocio
- Kreems vende helados en **canal tradicional**. Cada vendedor tiene puntos/clientes asignados en varias regiones.
- Parte del trabajo del vendedor es **colocar máquinas**: la empresa invierte en la máquina y la entrega en
  **comodato** a cambio de que el cliente compre. Cada máquina nueva = cliente nuevo.
- Hay **dos sociedades**: **Acuña** y **Gran Natural SPA** (Gran Natural concentra la mayor parte de las ventas).
  Los datos llegan en archivos separados por sociedad y deben consolidarse.
- ~10–14 vendedores (más algunos roles de oficina/gerencia). El diseño no debe limitar el nº de usuarios.

## 2. Fuentes de datos (hoy: export Excel/CSV manual; mañana: API de Obuma)
Obuma (ventas, facturación, NC, región, márgenes):
- `Reporte Ventas por Sucursal (ITEM)` — nivel línea de producto. Columnas clave: `FECHA DCTO`, `TIPO DCTO`
  (FACTURA / NOTA DE CRÉDITO), `N° DCTO`, `SUCURSAL`, `VENDEDOR`, `CLIENTE Rut/Razón Social/Comuna/Región/Tipo`,
  `TOTAL`, `Subtotal Neto`, `Cantidad`, `Costo Neto Subtotal`, `Utilidad (Margen)`, `Categoria`, `SubCategoria`, `Fabricante`.
- (Existe export de Pedidos en Obuma — usar si está disponible.)

Autoventa (pedidos y logística de máquinas):
- `Pedidos detalle productos` (CSV, delimitador `;`). Columnas clave: `Cod. Prod.`, `Nombre prod.`, `Categoría`,
  `RUT Cliente`, `Cod. Cliente`, `Vendedor`, `Cod. Vendedor`, `N° pedido`, `Doc. venta`, `Num documento`,
  `Fecha doc.`, `Neto`, `Vta total neta`, `Neto Nota de Crédito`, `Cant. NC`.
- `Detalle despachos` (XLSX). Columnas clave: `Documento`, `Fecha ruta`, `Transportista`, `Vendedor`, `Rut`,
  `Cliente`, `Estado` (Entregada/Pendiente/Rechazada), `Devolución`, `Peso (Kgs)`, `Cod. Cliente`.

Gerencia comercial (input manual):
- Objetivos mensuales por vendedor: objetivo de venta, objetivo de máquinas, objetivo de visitas.
  Hoy en Excel; deben pasar a una tabla editable desde la app por el rol gerencia.

### 2.1 Llave de cruce (CONFIRMADA con datos reales)
- **`Obuma."N° DCTO"` == `Autoventa."Num documento"`** (documento de venta). Esa es la unión ventas↔logística/máquinas.
- Cliente: `RUT` (`CLIENTE Rut` en Obuma, `RUT Cliente` en Autoventa). Normalizar formato del RUT antes de unir.
- Vendedor: es **texto/nombre** en ambos ERP, NO un código compartido. Crear dimensión Vendedor con id propio y
  una tabla de mapeo nombre→id que tolere variaciones de escritura. Autoventa además trae `Cod. Vendedor`.

## 3. Reglas de negocio / definiciones de métricas (CONFIRMADAS)
- **Fact-NC** = suma de facturas − suma de notas de crédito (NC entran con signo negativo).
- **Proyección a cierre** (lineal) = `(Fact-NC / días_trabajados) × días_totales_del_mes`.
- **% Cumplimiento** = `Fact-NC / Objetivo de venta`. (Mostrar también % Proyección = Proyección / Objetivo.)
- **N° de documentos** = nº de facturas distintas del vendedor en el período.
- **% Efectividad** = `N° de facturas / Objetivo de visitas`.
- **No facturado** = pedidos sin documento emitido → en Autoventa `Doc. venta = "Sin DTE"` (o `Num documento` vacío).
- **Máquinas** (categoría `MAQUINAS_POP` en Pedidos Autoventa; el código indica el movimiento):
  - `FL-4` = Instalación cliente nuevo → **máquina gestionada / nueva**.
  - `FL-1` = Cambio de máquina.
  - `FL-2` = Retiro por término → **retiro** (incluir análisis de retiros).
  - **Máquina gestionada** = pedido con FL-4 generado (el vendedor cumplió el acuerdo).
  - **Máquina entregada** = en `Detalle despachos`, documento de máquina con `Estado = Entregada`
    (un "Entregada" puede ser entrega o retiro exitoso → distinguir por el código FL-x del pedido cruzado).
  - Gestionadas no siempre terminan entregadas (el cliente puede echarse atrás): medir ambas y la conversión.

## 4. Modelo de datos (Supabase / Postgres — esquema estrella)
Dimensiones:
- `dim_vendedor(id, nombre_canonico, cod_vendedor_autoventa, agrupacion, activo, user_id)`
  — `user_id` liga al usuario de Auth; `agrupacion` = zona/equipo/sucursal (opcional, configurable).
- `dim_cliente(rut, razon_social, comuna, region, tipo, es_maquina, sociedad)`.
- `dim_producto(codigo, nombre, categoria, subcategoria, fabricante, unidad_medida)`.
- `dim_fecha(fecha, año, mes, dia, dia_semana)` + `calendario_laboral(año, mes, dias_totales, dias_trabajados)`.
- `dim_sociedad(id, nombre)` → Acuña / Gran Natural.

Hechos:
- `fact_ventas(fecha, tipo_dcto, n_dcto, vendedor_id, cliente_rut, producto_codigo, sociedad_id, sucursal,
  cantidad, neto, total, costo, margen)` — desde Obuma; NC con signo negativo.
- `fact_despachos(documento, fecha_ruta, vendedor_id, cliente_rut, estado, devolucion, peso, es_maquina, sociedad_id)`.
- `fact_maquinas(documento, fecha, vendedor_id, cliente_rut, tipo_mov, estado, sociedad_id)`
  — `tipo_mov` ∈ {nueva(FL-4), cambio(FL-1), retiro(FL-2)}; `estado` ∈ {gestionada, entregada, rechazada}.

Input editable:
- `objetivos_mensuales(vendedor_id, año, mes, obj_venta, obj_maquinas, obj_visitas)` — editable por rol gerencia.

Vistas (cálculo en Postgres, no en el front):
- `v_resumen_vendedor_mes`: por vendedor/mes → Fact-NC, Proyección, %Cumpl, %Proy, N° docs, no_facturado,
  máquinas gestionadas/entregadas/retiros, %Efectividad.

## 5. Seguridad (clave del proyecto)
- **Supabase Auth** para login.
- **Row Level Security**:
  - rol `vendedor` → solo ve filas donde `vendedor_id` corresponde a su `user_id`.
  - rol `gerencia`/`admin` → ve todos los vendedores y edita `objetivos_mensuales`.
- Gerencia decide, desde la app, qué puede ver cada usuario.

## 6. ETL (Fase 1 de operación — carga idempotente)
Script Python idempotente (corre en Colab o programado):
1. Lee los archivos de `/data/muestras` (Obuma ventas Acuña, Obuma ventas Gran Natural, Autoventa pedidos CSV `;`,
   Autoventa despachos, Excel objetivos) usando los nombres fijados en la sección 0.
2. Limpia y tipa (fechas, montos, RUT normalizado).
3. Normaliza `VENDEDOR` (nombre→id) y registra en un log los nombres/RUT no mapeados (no descartar en silencio).
4. Une las dos sociedades agregando `sociedad_id`.
5. Cruza Obuma↔Autoventa por `N° DCTO = Num documento`; reporta el % de match por carga.
6. Deriva máquinas (FL-4/FL-1/FL-2) y marca `No facturado` (`Doc. venta = "Sin DTE"`).
7. **Upsert** a Supabase: re-ejecutable sin duplicar, agnóstico a cuántos períodos traiga el archivo
   (un mes, varios o el histórico completo dan el mismo resultado).

## 7. Front-end (Streamlit + Supabase, móvil-first)
- Login (Supabase Auth). Tras login, detecta rol.
- **Panel Vendedor**: tarjetas KPI (Objetivo, Fact-NC, Proyección, %Cumpl), tabla mensual, máquinas
  (gestionadas/entregadas/retiros), %Efectividad. Filtros: mes, día, sociedad.
- **Panel Gerencia**: tabla de TODOS los vendedores (réplica mejorada del reporte actual), ranking,
  edición de `objetivos_mensuales`, comparativos.
- **Análisis** (aprovechar la riqueza de Obuma): ventas y margen por producto/categoría/fabricante,
  por región/comuna, por sociedad; ciclo de máquinas (nuevas vs retiros, conversión gestionada→entregada).
- Diseño limpio, legible en celular (los vendedores lo usan en terreno).
- Leer las métricas desde las vistas, no recalcular en el front.

## 8. Fase 2 de operación — Webhook Obuma ✅ IMPLEMENTADO (2026-06-04)
- Edge Function desplegada: `supabase/functions/obuma-webhook/index.ts` (Deno/TypeScript).
- Webhook registrado en Obuma: evento `venta.created`, estado Activo.
- Secret configurado en Supabase: `OBUMA_WEBHOOK_SECRET` (nunca en código).
- Autenticación via query param `?secret=` (Obuma no soporta headers custom en webhooks).
- Lógica idempotente: upsert en `dim_cliente`, `dim_producto`, `fact_ventas` con los mismos
  ON CONFLICT que el ETL Python.
- Auditoría: tabla `webhook_log` registra cada evento (ok / error / ignorado) con payload crudo.
- Autoventa sigue vía ETL manual (`python -m etl.run_etl --periodo AAAA-MM`) hasta tener su API.
- Para monitorear llegada de webhooks:
  `SELECT * FROM webhook_log ORDER BY recibido_at DESC LIMIT 10;`
- Ver guía completa de operación en `docs/fase4_webhook.md`.

## 9. Stack y entregables
- Backend/datos: **Supabase** (Postgres + Auth + RLS + API). Empieza en plan gratis; subir a Pro (~US$25/mes)
  solo cuando crezca el histórico o se requieran backups/always-on.
- ETL: **Python** (pandas) idempotente.
- Front: **Streamlit** desplegado, conectado a Supabase.
- Entregar: scripts SQL de creación de tablas + RLS, script ETL, app Streamlit, y un README con el flujo de actualización.

## 10. Reglas de calidad
- No descartar datos sin registrar (vendedores/RUT no mapeados → log).
- NC siempre con signo correcto; no doble-contar líneas de un mismo documento.
- Todo cálculo de métrica reproducible y trazable a las definiciones de la sección 3.
- Validar el cruce de documentos en cada carga (reportar % de match, como el ~80% observado en las muestras).
- Respetar la sección 0: `/data` solo lectura, ETL idempotente y agnóstico a la cadencia.
