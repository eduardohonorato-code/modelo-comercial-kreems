# 🍦 Kreems — Dashboard Comercial

Resumen ejecutivo de la aplicación de seguimiento comercial de Kreems.
Actualizado al 6-oct-2026. El detalle de cada cálculo está en la especificación
funcional (tarea 6.1 de la integración con el SaaS).

---

## ¿Qué es?

Una **aplicación web propia** que reemplaza el antiguo reporte de Power BI. Centraliza
las ventas, máquinas (comodato), pedidos y comisiones del canal tradicional, y entrega
a cada vendedor y a gerencia una vista clara de cómo van contra sus objetivos, con
datos del día anterior.

## ¿Qué problema resuelve?

- **Antes:** un Power BI estático, dependiente de exportaciones manuales, sin control de
  acceso por persona y difícil de adaptar.
- **Ahora:** una app a medida donde **cada vendedor ve solo lo suyo**, gerencia ve todo,
  los datos se actualizan por API + carga web, y las métricas se calculan de forma
  trazable y consistente.

Resuelve en concreto:
- Seguimiento de **cumplimiento de metas** por vendedor y proyección a fin de mes.
- Control semanal de **máquinas** (gestiones, entregas, rechazos y su seguimiento).
- Visibilidad de **qué se pidió vs. qué se facturó** (lo "no facturado").
- **Análisis de productos** (paletas, potes, bachas, galletas…) por SKU, categoría,
  región, sucursal y centro de distribución.
- **Cartera de clientes**: estado, segmentación y alertas por cliente y por sucursal.
- **Presupuesto** anual contra la venta real, con un objetivo sugerido para el mes siguiente.
- Cálculo de **comisiones** (la versión que se paga y una propuesta nueva en paralelo).

---

## Funciones principales (por pantalla)

| Pantalla | Para quién | Qué muestra |
|---|---|---|
| **🏠 Inicio** | Todos | Resumen del mes: venta real, meta, % cumplimiento, proyección, ritmo y brecha. Para gerencia: tabla del equipo, ranking, evolución diaria, top 3, vendedores en riesgo y frases de resumen. |
| **📊 Panel Gerencia** | Gerencia | Tabla de todos los vendedores: Fact‑NC, % cumplimiento, proyección, pedidos, pedidos facturados, no facturado, NC, máquinas ingresadas y entregadas, N° de documentos y efectividad. Aviso de pedidos sin facturar, export PNG/CSV y edición de objetivos. |
| **👤 Panel Vendedor** | Vendedor («Mi Panel») y gerencia | Sus KPIs, avance contra el ritmo del mes, máquinas, detalle por documento y su lista de **clientes dormidos** para recuperar. |
| **📈 Análisis** | Todos | 5 pestañas: **Ventas** (productos, geografía, sucursales), **Máquinas**, **Productos a fondo** (por categoría), **Cajas por CD** (Santiago, Concepción, Temuco) e **Informe Excel**. Filtros de fechas, sociedad y categoría. |
| **🧾 Clientes** | Todos | CRM en 6 pestañas: **Resumen** (estados de cartera y concentración), **Segmentación** (ABC y RFM), **Alertas**, **Ranking**, **Sucursales** (por dirección de despacho) y **Ficha** por cliente con su salud comercial. Cada vendedor ve solo sus clientes. |
| **🧊 Control Máquinas** | Gerencia | Gestiones de la semana contra la meta (22 por semana, 85 % entregado), estado de cada una, motivos de rechazo, qué pasó con cada rechazo y si el cliente siguió comprando, tendencia de 8 semanas, entregas por transportista e informes Excel. |
| **🎯 Presupuesto** | Gerencia | Presupuesto vs real por mes, comparación con años anteriores, estacionalidad y objetivo sugerido para el mes siguiente con su reparto por vendedor. |
| **💰 Comisiones** | Gerencia | **Versión original** (la que se paga): venta, máquinas, efectividad, bono 4 %, semana corrida y reposición, con ajustes y cierre de mes. **Propuesta v1**: scorecard de 5 indicadores sobre la venta, con parámetros todavía en ajuste. |
| **📤 Carga de archivos** | Gerencia | Sube Acuña, despachos y la lista de clientes del ERP, sin tocar la línea de comandos. Respaldo de Gran Natural y pedidos si la API falló. |
| **⚙️ Usuarios** | Admin | Crear, editar, desactivar y eliminar usuarios; rol y vendedor vinculado. |

---

## Métricas clave (definiciones)

- **Fact‑NC** = facturas − notas de crédito (la venta real neta, sin IVA). Cuenta solo
  DTE reales: factura, factura exenta y nota de crédito.
- **% Cumplimiento** = Fact‑NC ÷ objetivo de venta.
- **Proyección a cierre** = `Fact‑NC ÷ días hábiles transcurridos × días hábiles del mes`
  (los días descuentan fines de semana **y feriados** chilenos; se autoajusta cada día).
- **% Efectividad** = nº de facturas ÷ objetivo de visitas.
- **No facturado** = pedidos de Autoventa sin documento emitido (Sin DTE).
- **Máquinas:** nuevas (FL‑4), cambios (FL‑1/3/5) y retiros (FL‑2), derivadas de los
  fletes facturados en Obuma. El estado sale de los despachos, cruzando documento y RUT.
  En Control de Máquinas una **gestión** es un flete con DTE (sin las NC de flete) y tiene
  5 estados: entregada, rechazada, en ruta, sin despacho y sin información.
- **Clientes (CRM):** estado por **recencia** de compra: *activo* (0–1 mes), *en riesgo*
  (2 meses), *perdido* (≥3 meses), *nuevo* (1ª compra el mes) y *recuperado* (vuelve tras
  ≥3 meses). **ABC** por facturación acumulada (A 80 % / B 95 % / C resto), **RFM** y
  **salud comercial** 0–100 (recencia 40 % · frecuencia 30 % · tendencia 30 %).
- **Dormido:** cliente sin comprar hace 3 meses o más. Pertenece al vendedor de la
  **cartera oficial**.

---

## De dónde vienen los datos

Dos ERP, consolidados por dos sociedades (**Acuña** y **Gran Natural**):

| Fuente | Qué aporta | Cómo entra |
|---|---|---|
| **Obuma · Gran Natural** | ventas, máquinas, clientes, productos | API (automático, diario) |
| **Obuma · Acuña** | ventas y máquinas (histórico; Acuña ya no factura) | Excel (página Carga) |
| **Autoventa · Pedidos** | pedidos, no facturado | API (automático, diario) |
| **Autoventa · Despachos** | estado de entrega de helados y máquinas | Excel (página Carga) |
| **Autoventa · Reporte de clientes** | cartera oficial por vendedor y direcciones | Excel (script de carga) |
| **Autoventa · Lista de clientes** | cliente activo / inactivo en el ERP | Excel (página Carga) |
| **Gerencia** | objetivos, metas de máquinas, presupuesto, escalas de comisión, feriados | Editable en la app |

> El cruce entre sistemas es por número de documento y RUT
> (`Obuma N° DCTO = Autoventa Num documento = Despachos Documento`).
> La carga es **idempotente**: volver a subir un mes lo actualiza, no lo duplica.
> La carga diaria toma el mes en curso y, los primeros 5 días, también el anterior.

---

## Seguridad

- **Login** con Supabase Auth (acepta email o nombre de usuario).
- **Row Level Security**: cada vendedor solo accede a sus propias filas; gerencia/admin
  ven todo y editan objetivos, cartera, metas y usuarios. Las comisiones solo las ve gerencia.

---

## Stack tecnológico

- **Frontend:** Streamlit (desplegado en Streamlit Cloud, móvil‑first).
- **Backend / datos:** Supabase (PostgreSQL + Auth + RLS). Las métricas principales se
  calculan en vistas de la base de datos.
- **Integración:** Python (pandas) para el ETL e ingesta por API, idempotente, con una
  tarea programada diaria.

---

## Estado actual

- ✅ En producción y uso diario por el equipo comercial.
- ✅ Ventas y máquinas de Gran Natural y pedidos de Autoventa por API; Acuña y despachos
  por carga web.
- ✅ Control de Máquinas semanal con seguimiento de rechazos.
- ✅ Análisis de productos, cajas por centro de distribución, CRM de clientes y sucursales.
- ✅ Comisiones versión original en uso; propuesta v1 en calibración con gerencia.
- 🔜 Despachos y cobertura de ruta siguen por archivo (Autoventa no los entrega por API).
- 🔜 La app se está replicando en el SaaS de Kreems (integración en curso, oct–dic 2026).
