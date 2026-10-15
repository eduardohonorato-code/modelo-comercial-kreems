# Control de Máquinas, reporte comercial y Control de Entregas

> Documento de replicación (oct-2026). Explica **qué** se construyó, **por qué**
> cada decisión, y **cómo** llevarlo a otro proyecto con otros ERP. Está escrito
> para que alguien que no vio el desarrollo pueda rehacerlo sin repetir los
> errores que ya se cometieron aquí.

---

## 1. El problema que resuelve

Una empresa que entrega máquinas en comodato y vende producto en ruta necesita
responder, cada semana, dos cosas distintas:

| Pregunta | Quién la responde | Dónde vive |
|---|---|---|
| ¿Se movieron las máquinas? ¿Qué pasó con las que volvieron? | Gerencia comercial | **Control de Máquinas** |
| ¿Cuánto se demora un pedido en llegar? ¿Cuánto vuelve y qué pasa después? | Logística / operaciones | **Control de Entregas** |
| ¿Quién tiene que hacer qué con cada caso pendiente? | Área comercial (vendedores) | **Reporte semanal en Drive** |

Se separaron a propósito: cuando todo estaba en una sola página, gerencia se
enredaba con indicadores que tenían denominadores distintos.

---

## 2. Fuentes de datos mínimas

Para replicar hacen falta **tres tablas de hechos** (en Kreems: Supabase/Postgres).

| Tabla | Una fila por | Columnas que se usan | Origen en Kreems |
|---|---|---|---|
| `fact_pedidos` | línea de pedido | `n_pedido`, `fecha_pedido` (ingreso), `fecha` (DTE), `num_documento`, `doc_venta` ('Sin DTE'), `estado_pedido`, `producto_codigo`, `cliente_rut`, `vendedor_id`, `neto` | API Autoventa (diario) |
| `fact_despachos` | documento × cliente despachado | `documento`, `cliente_rut`, `fecha_ruta`, `estado` (Entregada/Rechazada/Pendiente), `transportista`, `comentario_entrega`, `motivo_rechazo` | **Excel manual** de Autoventa |
| `fact_ventas` | línea de factura/NC | `n_dcto`, `tipo_dcto`, `fecha`, `cliente_rut`, `producto_codigo`, `neto` | API Obuma (diario) |

Y una derivada: `fact_maquinas` (movimientos de máquina = líneas con código de
flete `FL-x` de las ventas: FL-4 instalación, FL-1/3/5 cambio, FL-2 retiro).

Más: `dim_cliente` (razón social, comuna), `dim_vendedor`, `feriados`
(para días hábiles) y `objetivos_maquinas` (metas editables).

### Las llaves y sus trampas (no repetir)

1. **Documento ↔ despacho se cruza por `(documento, cliente_rut)`, nunca solo
   por número.** Las series de folios se repiten (las NC tienen serie propia):
   una NC de flete calzaba con la factura de otro cliente con el mismo número y
   salía "entregada".
2. **El número de documento se reutiliza entre meses.** Al cruzar pedido ↔
   despacho se exige que la ruta caiga entre −30 y +60 días del DTE; en las
   llaves de casos se usa `documento + fecha`.
3. **El ERP de despachos deja una fila por documento** (el último intento pisa
   al anterior). Si hay varias, prioridad Entregada > Rechazada > Pendiente.
4. **"Sin DTE" no siempre es pendiente**: el pedido puede estar facturado en la
   otra serie (estado API `invoiced`) o ser un fantasma (anulado o reingresado
   con otro número: estado nulo o `no_existe_en_api`). Ambos se excluyen.
5. **Logística factura con la fecha de ENTREGA**: hay DTE fechados en el
   futuro. Las vistas por semana llegan hasta el último día del período, no
   hasta hoy, y la carga trae 14 días hacia adelante.
6. **La ruta ficticia "Nulas mes en curso"** es una anulación de logística,
   no un rechazo en terreno: se aparta en todas partes.
7. **PostgREST devuelve 1.000 filas por request**: toda lectura de una tabla
   grande se pagina con `.range()` **y** `.order("id")` (sin orden, repite o
   salta filas entre páginas).

---

## 3. Control de Máquinas (`app/pages/control_maquinas.py`)

### La regla que evita el enredo: UN solo grupo
Todo se cuenta sobre las **gestiones de la semana** = fletes de máquina con DTE
emitido en esas fechas. Entregas, rechazos y "sin confirmar" son esas mismas
gestiones. Cuadre: `entregadas + sin confirmar + rechazadas = gestiones`.
El cálculo vive en `kpis_maquinas.conteo_semana` y lo usan la página y el Excel.

### Seguimiento de rechazos (`kpis_maquinas.seguimiento_rechazos`)
Ningún sistema guarda el número de serie de la máquina, así que el reintento de
un rechazo se reconoce por **mismo cliente + mismo tipo de movimiento + fecha
posterior al rechazo**, en este orden:

1. Otro documento de flete que cumpla eso → su estado de entrega es el estado
   post-rechazo (Reingresado y entregado / va en camino / se volvió a rechazar).
2. Un pedido FL ingresado después y aún sin DTE → "Pedido reingresado, esperando DTE".
3. Cualquier otro movimiento del cliente → "Otro movimiento del cliente".
4. Nada → **"Sin reintento"** (el único estado que cuenta como abierto para el vendedor).

Estados agrupados en la página:
- **Sin retomar** = Sin reintento (40 al 15-10-2026).
- **Reingresados sin cerrar** = todo lo reingresado que no terminó entregado
  ni anulado (16 al 15-10-2026).
- **Ya entregados** y **Anuladas por logística**.

Un caso encadenado (reintento que también se rechazó) se cuenta **una vez**: el
segundo documento tiene su propia fila.

### Pedidos sin gestionar (re-agregado en oct-2026)
Pedidos de flete ingresados que todavía no tienen DTE (sin fantasmas ni
facturados en otra serie). **No son gestiones**: cuentan cuando se emita el
documento. Salen todos los abiertos, sin importar el período. La lista sale de
`export_seguimiento_comercial.casos_abiertos` (la misma del reporte de Drive).

### El % de entrega por transportista
Se mudó a Control de Entregas (ver §5.4).

---

## 4. Reporte semanal para el área comercial (Drive)

### Para qué
Después de la reunión de gerencia de los lunes, derivar a comercial los casos
pendientes y **poder ver la semana siguiente qué se hizo con cada uno**.

### Archivos
| Archivo | Rol |
|---|---|
| `app/export_seguimiento_comercial.py` | Arma el libro (lógica única) |
| `reportes/seguimiento_comercial_maquinas.py` | Script que escribe en Drive |
| `Actualizar seguimiento maquinas.bat` | Doble clic para el script |
| Página Control de Máquinas → «📤 Reporte semanal para el área comercial» | Alternativa desde cualquier PC (sube versión anterior, descarga nueva) |
| `tests/test_seguimiento_comercial.py` | Cuida que los comentarios sobrevivan |

Destino: `G:\Mi unidad\Eduardo\Comercial\Control de Maquinas\Seguimiento_comercial_maquinas.xlsx`
(Google Drive para escritorio). Antes de pisarlo se guarda una copia en
`Historial\Seguimiento_comercial_maquinas_antes_de_AAAA-MM-DD.xlsx`.

### Hojas
1. **Resumen** — conteos, tabla de casos abiertos **por vendedor** (la que se reenvía), instrucciones.
2. **1 · Rechazos sin retomar** — responsable: vendedor. Incluye la "lectura de la venta" (si el cliente sigue comprando, probablemente se renegoció).
3. **2 · Reingresos sin cerrar** — "Responsable" dice si la pelota es de logística (en ruta, esperando DTE) o del vendedor (se volvió a rechazar).
4. **3 · Pedidos sin gestionar** — responsable: logística (emitir DTE). Marca si el pedido es reingreso de un rechazo.
5. **4 · Casos cerrados** — lo que estaba abierto en el reporte anterior y ya no lo está, con **cómo se cerró** (lo dice el sistema) y los comentarios de comercial. Se acumula.
6. **Historial semanal** — una fila por reporte con los conteos (si no bajan, los casos no se mueven).

### El mecanismo clave: columnas manuales que sobreviven
- Cada caso tiene un **ID estable**: `R-<documento>-<AAAAMMDD>` (rechazo; con la
  fecha porque el folio se reutiliza) o `P-<n° pedido>`.
- Columnas **amarillas** (las llena comercial): `Estado gestión` (lista:
  Pendiente/En gestión/Reingresado/Descartado/Cerrado), `Acción comprometida`,
  `Responsable comercial`, `Fecha compromiso`, `Comentario / situación del cliente`.
- Al regenerar, `leer_previo` lee el archivo anterior, arma `{ID: columnas
  manuales}` y las copia a la fila del mismo ID **aunque haya cambiado de hoja**
  (un rechazo reingresado pasa de la 1 a la 2 con sus comentarios).
- `En el reporte desde` se conserva → `Semanas en el reporte` mide antigüedad.
- Lo que estaba abierto y ya no aparece → «Casos cerrados» con `como_se_cerro()`.
- Si el archivo anterior **no se puede leer**, el script **no lo reemplaza**
  (se perderían los comentarios) y lo avisa.
- Regenerar el mismo día no duplica la fila del historial.

### Rutina del lunes
1. Reunión de gerencia con la página Control de Máquinas.
2. Doble clic en `Actualizar seguimiento maquinas.bat` (cierra el Excel antes).
3. Avisar a comercial que está actualizado (el archivo es el mismo link de siempre).
4. Comercial trabaja las columnas amarillas durante la semana.

### Variante: hoja nativa de Google Sheets
Si el `.env` tiene `SEGUIMIENTO_SHEET_ID`, el mismo `.bat` escribe en una hoja
**nativa** de Google Sheets en vez del .xlsx: exporta la hoja como xlsx (esa es
la «versión anterior»), arma el libro nuevo y lo sube **encima de la misma
hoja** por la API de Drive (mismo link y permisos). Se autentica con una cuenta
de servicio de Google Cloud. Código en `reportes/seguimiento_google_sheets.py`;
configuración paso a paso en **`docs/seguimiento_google_sheets_pasos.md`**.

> ⚠️ En modo Excel, si comercial abre el xlsx en Google Sheets, debe editarlo
> **en modo Office** (Drive lo guarda como .xlsx). Si alguien hace "Guardar como Hojas de
> cálculo de Google" crea OTRO archivo y el script no lo verá.
> Marcar «Descartado» NO saca un caso de la lista: el sistema lo cierra cuando
> el movimiento se resuelve o se anula en el ERP.

---

## 5. Control de Entregas (`app/pages/control_entregas.py`)

Lógica en `app/kpis_entregas.py`. Cuatro vistas (selector `st.radio`, no
`st.tabs`: las pestañas renderizan todo siempre y en esta app provocaron
timeouts).

### 5.1 Definiciones
- **Grupo**: pedidos **ingresados** en el período (`fecha_pedido`). Tiempo, %
  a tiempo, rechazo y abiertos son esos mismos pedidos.
- **Tres fechas**: Ingreso (Autoventa `created_at`) → DTE (`fact_pedidos.fecha`
  con documento) → Ruta (`fact_despachos.fecha_ruta`). No hay hora de entrega
  (la columna del Excel viene vacía): **el día de la ruta es el día de entrega**.
- **Días hábiles** (lun–vie, sin `feriados`), con `numpy.busday_count`. Un
  pedido del viernes entregado el lunes = 1 día. Se muestran también los
  corridos en el detalle.
- **Estados**: Entregado, Rechazado (cierran) · En ruta, Facturado sin ruta,
  Sin DTE (abiertos) · fuera de indicadores: Anulado por logística, Facturado
  en otra serie, Sin información (mes sin despachos cargados).
- **Plazo comprometido (SLA)**: editable en la página; por defecto **2 días
  hábiles helados** y **5 máquinas**. «% a tiempo» = entregados con
  `Días a cierre ≤ plazo` / entregados.
- **% de rechazo** = rechazados / cerrados (en cantidad y en monto).

Línea base feb–oct 2026 (Gran Natural): helados mediana **2 días**, 78% en
≤2 días, rechazo 8,1% (6,3% del monto); máquinas mediana **7,5 días**, casi
todo en Ingreso→DTE (6 días): el cuello de botella es emitir el documento.

### 5.2 Vistas 🍦 Helados y 🧊 Máquinas
Tarjetas (pedidos, mediana, % a tiempo, % rechazo) → «Dónde se va el tiempo»
(mediana por etapa + abiertos) → distribución por días + tendencia 8 semanas
(% a tiempo y % rechazo) → tabla por vendedor / transportista / comuna (y por
movimiento en máquinas) → pedidos abiertos con días → detalle + CSV.
Máquinas cuenta **pedidos ingresados**; Control de Máquinas cuenta **gestiones
con DTE**: no cuadran entre sí a propósito y la página lo dice.

### 5.3 Vista ↩️ Rechazos de helados — qué pasa después (tiempo)
Por cada pedido rechazado (`seguimiento_rechazos_productos`):
- **Nota de crédito**: primera NC del cliente **por el mismo monto** (±$2)
  desde 2 días antes del rechazo; si no hay exacta, la primera NC del cliente.
  → `Días a la NC`. Sin NC por más de **10 días** = alerta (el cliente debe una
  factura que no recibió). Base: 94% se acredita, mediana 7 días.
- **Siguiente entrega**: primera ruta *Entregada* al mismo cliente después del
  rechazo → `Días a la siguiente entrega` (85% vuelve, mediana 13 días). Si no
  vuelve, fue venta perdida.
- Motivos clasificados por palabras clave sobre el comentario del repartidor
  (`export_maquinas.clasificar_motivo`): el campo "Motivo rechazo" del ERP
  llega vacío.

### 5.4 Vista 🚛 Por transportista
% de entrega **por fecha de ruta** (definición oficial): helados en **pesos**,
máquinas en **unidades** (el flete se factura a $1). Las NC **no** se restan
del denominador (inflarían el %). En helados las anulaciones quedan en el
total; en máquinas se excluyen. Las ventas se piden con 45 días de margen
hacia atrás para valorizar documentos facturados antes de salir a ruta.

---

## 6. Cómo replicarlo en otro proyecto (checklist)

1. **Modelo**: crear las tres tablas de hechos de §2 con `fecha_pedido` en
   pedidos (la fecha de ingreso es lo que más cuesta conseguir: en Autoventa es
   el `created_at` de la línea).
2. **Llaves**: cruzar siempre por `(documento, cliente)` + ventana de fechas.
3. **Copiar módulos** (no dependen de Streamlit salvo las páginas):
   `kpis_maquinas.py`, `kpis_entregas.py`, `export_seguimiento_comercial.py`,
   `export_maquinas.py` (`preparar_movimientos`, `_prep_pedidos`,
   `clasificar_motivo`), `export_entregas.py`.
4. **Ajustar constantes**: `PRIMER_DESPACHO`, códigos FL→movimiento
   (`FL_A_MOV`), `_RUTA_ANULACION`, camión de máquinas (`_CAMION_MAQUINAS`),
   reglas de `clasificar_motivo`, `SLA_*`, `DIAS_PARA_REINTENTAR`,
   `DIAS_ALERTA_NC`, ruta de Drive del script.
5. **Páginas**: `control_maquinas.py` y `control_entregas.py`; registrar en la
   navegación con el `try/except ImportError` (en Streamlit Cloud un módulo
   viejo en memoria tumbaba la app entera).
6. **Pruebas**: `python -m unittest discover tests` (RUT ficticios: el repo es público).
7. **Operación**: programar la carga de despachos (en Kreems es manual: sin
   ese Excel, los estados quedan en "Facturado sin ruta"; la página muestra
   hasta qué fecha hay despachos cargados).

---

## 7. Pendientes y decisiones abiertas

- **Plazos (SLA)**: 2 días helados / 5 máquinas son propuestos; confirmar con
  gerencia y, si se fijan, guardarlos en una tabla editable como `objetivos_maquinas`.
- **Despachos manuales**: mientras el estado de entrega venga de un Excel, los
  tiempos de las últimas semanas dependen de cuándo se subió.
- **Ciclo completo de una máquina rechazada** (primer ingreso → entrega final
  tras el reintento): hoy se ve en el seguimiento caso a caso, no como KPI.
- **NC de flete** en `fact_maquinas`: Control de Máquinas las excluye; el resto
  del sistema todavía las cuenta (afecta comisiones; decisión pendiente).
