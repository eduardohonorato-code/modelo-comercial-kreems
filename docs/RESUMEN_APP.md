# 🍦 Kreems — Dashboard Comercial

Resumen ejecutivo de la aplicación de seguimiento comercial de Kreems.

---

## ¿Qué es?

Una **aplicación web propia** que reemplaza el antiguo reporte de Power BI. Centraliza
las ventas, máquinas (comodato), pedidos y comisiones del canal tradicional, y entrega
a cada vendedor y a gerencia una vista clara de cómo van contra sus objetivos, en
tiempo casi real.

## ¿Qué problema resuelve?

- **Antes:** un Power BI estático, dependiente de exportaciones manuales, sin control de
  acceso por persona y difícil de adaptar.
- **Ahora:** una app a medida donde **cada vendedor ve solo lo suyo**, gerencia ve todo,
  los datos se actualizan por API + carga web, y las métricas se calculan de forma
  trazable y consistente. Se acabó el "¿de dónde sale este número?".

Resuelve en concreto:
- Seguimiento de **cumplimiento de metas** por vendedor y proyección a fin de mes.
- Control del **parque de máquinas** (instalaciones, cambios, retiros y entregas).
- Visibilidad de **qué se pidió vs. qué se facturó** (lo "no facturado").
- **Análisis de productos** (paletas, potes, bachas, galletas…) por SKU y categoría.
- Cálculo de **comisiones** según reglas del negocio.

---

## Funciones principales (por pantalla)

| Pantalla | Para quién | Qué muestra |
|---|---|---|
| **🏠 Inicio** | Todos | Resumen del mes: venta real, meta, % cumplimiento, **% y monto de proyección**, días hábiles, brecha a meta. Para gerencia: ranking y alertas del equipo. |
| **📊 Panel Gerencia** | Gerencia | Tabla de todos los vendedores: Fact‑NC, % cumplimiento, pedidos, no facturado, máquinas, efectividad y **cartera de clientes**. Ranking y edición de objetivos. |
| **👤 Panel Vendedor** | Vendedor | Sus propios KPIs: objetivo, Fact‑NC, proyección, máquinas, efectividad. |
| **📈 Análisis** | Todos | 3 vistas: **Ventas** (productos, geografía, sucursales), **Máquinas** (estado y evolución nuevas vs retiros), **Productos a fondo** (drill‑down por categoría y mejores SKUs). |
| **💰 Comisiones** | Gerencia | Cálculo de comisiones (efectividad, PNV, máquinas) y carga de la cartera del período. |
| **📤 Carga** | Gerencia | Sube los archivos del mes (Acuña + despachos) a la base de datos, sin tocar la línea de comandos. |

---

## Métricas clave (definiciones)

- **Fact‑NC** = facturas − notas de crédito (la venta real neta).
- **% Cumplimiento** = Fact‑NC ÷ objetivo de venta.
- **Proyección a cierre** = `Fact‑NC ÷ días hábiles transcurridos × días hábiles del mes`
  (los días descuentan fines de semana **y feriados** chilenos; se autoajusta cada día).
- **% Efectividad** = nº de facturas ÷ objetivo de visitas.
- **Máquinas:** nuevas (FL‑4), cambios (FL‑1/3/5), retiros (FL‑2); el estado
  *entregada/rechazada* sale de los despachos.
- **No facturado** = pedidos sin documento emitido (Sin DTE).

---

## De dónde vienen los datos

Dos ERP, consolidados por dos sociedades (**Acuña** y **Gran Natural**):

| Fuente | Qué aporta | Cómo entra |
|---|---|---|
| **Obuma · Gran Natural** | ventas, márgenes, máquinas | API (automático) |
| **Obuma · Acuña** | ventas, máquinas | Excel (página Carga) |
| **Autoventa · Pedidos** | pedidos, no facturado | API (automático) |
| **Autoventa · Despachos** | entregas/estado de máquinas | Excel (página Carga) |
| **Objetivos / Cartera** | metas y clientes asignados | Editable en la app |

> El cruce entre sistemas es por número de documento (`Obuma N° DCTO = Autoventa Num documento = Despachos Documento`).
> La carga es **idempotente**: volver a subir un mes lo actualiza, no lo duplica.

---

## Seguridad

- **Login** con Supabase Auth (acepta email o nombre de usuario).
- **Row Level Security**: cada vendedor solo accede a sus propias filas; gerencia/admin
  ven todo y editan objetivos, cartera y usuarios.

---

## Stack tecnológico

- **Frontend:** Streamlit (desplegado en Streamlit Cloud, móvil‑first).
- **Backend / datos:** Supabase (PostgreSQL + Auth + RLS). Las métricas se calculan en
  vistas de la base de datos, no en el front.
- **Integración:** Python (pandas) para el ETL e ingesta por API, idempotente.

---

## Estado actual

- ✅ En producción y uso diario por el equipo comercial.
- ✅ Ventas y máquinas de Gran Natural por API; Acuña y despachos por carga web.
- ✅ Análisis de productos, máquinas y comisiones operativos.
- 🔜 Pendiente con IT: API de Acuña y estado de despachos por API (hoy manuales).
