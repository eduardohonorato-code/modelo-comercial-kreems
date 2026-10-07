# Rutina del día 1 de cada mes — comisiones (modelo nuevo)

Desde octubre 2026 las comisiones se calculan con 5 indicadores. Dos de ellos se
miden contra la **foto de cómo quedan los clientes asignados a cada vendedor al
inicio del mes**:

- **Efectividad de cartera** = clientes que compraron ÷ clientes de su cartera.
- **Cobertura de ruta** = visitas hechas ÷ visitas programadas (agendamientos).

Por eso, **el primer día hábil de cada mes** se actualiza la cartera y se fijan las
metas. Si no se hace, el mes se mide contra la cartera y las rutas del mes anterior
(clientes dados de baja, reasignaciones o rutas nuevas quedan mal contadas).

> ⚠️ **Hacerlo solo el día 1.** La app guarda únicamente la cartera *actual*: si se
> recarga a mitad de mes, el mes en curso **y los meses pasados que se vuelvan a
> abrir en la app** se recalculan con la cartera nueva. Ejemplo real: al recargar la
> cartera el 07-10-2026, septiembre de Joaquín pasó de 90 a 98 clientes y su
> efectividad quedó bajo el piso en la app (lo pagado salió del Excel de cierre y no
> cambia). Lo que se paga siempre sale del cierre de cada mes.

---

## Paso a paso (≈ 10 minutos)

### 1. Exportar la cartera de Autoventa
En Autoventa, exportar los dos reportes de clientes (formato CSV):

| Archivo | Qué trae |
|---|---|
| `clientes (N).csv` | Una fila por cliente (casa matriz): **Vendedor exclusivo** (código del vendedor) y **Ruta** |
| `direcciones_despacho (N).csv` | Sucursales de cada cliente (solo para contar sucursales) |

### 2. Cargar la cartera
```bash
python -m etl.cargar_cartera "<ruta>\clientes (N).csv" "<ruta>\direcciones_despacho (N).csv" --reemplazar
```
- `--reemplazar` **borra los clientes que ya no vienen en el reporte** (dados de baja
  o fuera de Autoventa). Sin esa opción seguirían contando en la cartera de su
  vendedor anterior.
- Revisar el resumen que imprime: clientes por código de vendedor. Si aparece
  **"⚠️ SIN MAPEAR en dim_vendedor"** con un código, es un vendedor nuevo o que
  cambió de cuenta: completar su `cod_vendedor_autoventa` en `dim_vendedor` y volver a
  correr (ej. `sql/046`: 35442 = Joaquín Brandt, 35443 = Francisco Arriagada).
- Para probar sin escribir: agregar `--dry-run`.

### 3. Visitas programadas (agendamientos) del mes
La app las **estima sola** con la ruta de cada cliente de la cartera recién cargada
(código de ruta del reporte de clientes):

| Ruta | Ejemplo | Visitas en el mes |
|---|---|---|
| Semanal | `CN15` | 1 por cada semana del mes (4 o 5) |
| Quincenal | `CN12-Q1` | la mitad |
| Mensual | `CN32-M2` | 1 |
| Sin ruta | `CENTRALIZADA` | 0 |

Autoventa agenda por semana; esta estimación quedó a 4% de error promedio contra el
reporte real de septiembre 2026. **Para que sea exacta**, cuando Autoventa ya muestre
el total del mes, cargarlo en **Panel Gerencia → Avance de comisiones → Metas del mes →
Por vendedor → "Visitas programadas"** (0 = usar la estimación).

### 4. Metas del mes
En **Panel Gerencia → Avance de comisiones → Metas del mes**:
- **Por vendedor:** objetivo de venta, clientes nuevos (= objetivo de máquinas) y,
  si se tiene, visitas programadas.
- **Generales:** efectividad (65% abr–oct · 75% nov–mar), cobertura de ruta (92%) y
  SKU por cliente. Se guardan **desde el mes que se está viendo** en adelante; los
  meses anteriores conservan sus metas.
- **Piso de pago** (80%).

### 5. Revisar
Abrir el Panel Gerencia: cada vendedor debe tener cartera, visitas programadas y
objetivo de venta. Si alguno sale con "—", falta su cartera o su objetivo.

---

## Lo que NO hay que hacer a mano

- **Visitas hechas:** se cargan solas cada madrugada desde el GPS de Autoventa
  (`etl/run_visitas_api.py`, dentro de la carga diaria).
- **Ventas y máquinas** (clientes nuevos = máquinas FL-4 facturadas): carga diaria.

## A fin de mes (cierre)

Para **pagar** manda el reporte oficial **Cobertura / Efectividad** de Autoventa:
cargar agendamientos y visitas del mes en **Comisiones → Propuesta → Configuración →
Cobertura de ruta**. El panel y la comisión pasan a usar ese dato en vez de la
estimación.
