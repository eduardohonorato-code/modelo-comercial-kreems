# Flujo de actualización de datos (manual, carpetas `/data/mensual`)

Guía operativa para alimentar la base de datos (Supabase) mes a mes mientras la
ingesta es manual, antes de tener las APIs de Obuma/Autoventa.

---

## 1. Las 4 fuentes y para qué sirve cada una

| Archivo | ERP | Carpeta | Nivel | Alimenta | Métricas que habilita |
|---|---|---|---|---|---|
| `obuma_ventas_acuña_AAAA_MM.xls` | Obuma | `data/mensual/acuña/` | línea de producto | `fact_ventas` (Acuña), `dim_cliente`, `dim_producto`, **`fact_maquinas`** | Fact-NC, margen, proyección, %Cumplimiento, N° docs, máquinas |
| `obuma_ventas_grannatural_AAAA_MM.xls` | Obuma | `data/mensual/gran_natural/` | línea de producto | `fact_ventas` (Gran Natural) + dims + **`fact_maquinas`** | igual que arriba, sociedad Gran Natural |
| `pedidos_detalle_productos_<mes>.csv` | Autoventa | `data/mensual/autoventa/` | línea de producto (CSV `;`) | `fact_pedidos` | N° de pedidos, $ pedido vs facturado, **No facturado** (Sin DTE), cruce pedido↔factura |
| `detalle_despachos_<mes>.xlsx` | Autoventa | `data/mensual/autoventa/` | documento/entrega | `fact_despachos` | entrega de máquinas (entregada/rechazada), efectividad logística, devoluciones, peso/cajas, transportista |

> **Obuma es la columna vertebral.** Trae la facturación real (lo que cuenta para
> la meta del vendedor) y las máquinas. Autoventa aporta el "antes" (pedido) y el
> "después" (entrega) de esa venta.

### ¿De dónde sale cada cosa?
- **Ventas / Fact-NC / margen / %Cumplimiento** → Obuma (`fact_ventas`).
- **Máquinas** → Obuma, categoría `Maquinas`, códigos FL:
  `FL-4`=nueva · `FL-2`=retiro · `FL-1`/`FL-3`/`FL-5`=cambio.
  Estado *entregada* → se completa cruzando con despachos.
- **Pedidos y No facturado** → Autoventa pedidos detalle.
- **Entregas / rechazos / logística** → Autoventa despachos.

---

## 2. Cómo se cruzan (llaves)

```
                 RUT cliente (normalizado)
   Obuma  ───────────────┬───────────────  Autoventa
                         │
   N° DCTO  ═════════════╪═════════════  Num documento (pedidos)
            ═════════════╪═════════════  Documento     (despachos)
                         │
   VENDEDOR (texto) ──→ dim_vendedor (mapeo nombre→id)
```

- **`Obuma.N° DCTO` = `Autoventa.Num documento` = `Despachos.Documento`** → une la
  venta facturada con su pedido y con su entrega. Es lo que permite marcar una
  máquina como *entregada* y medir pedido↔factura.
- **RUT** → consolida el cliente entre ambos ERP (`dim_cliente`).
- **Vendedor** → es texto en los dos ERP; se mapea a `dim_vendedor` por nombre.
  Nombres nuevos quedan en `etl_historico_no_mapeados.csv` (no se pierden datos:
  van a "Sin asignar" hasta que se registren).

---

## 3. Rutina de actualización (semanal / mensual)

1. **Exportar** desde cada ERP el período en curso y **guardar en su carpeta** con
   el nombre correcto:
   - Obuma Acuña → `data/mensual/acuña/obuma_ventas_acuña_AAAA_MM.xls`
   - Obuma Gran Natural → `data/mensual/gran_natural/obuma_ventas_grannatural_AAAA_MM.xls`
   - Autoventa pedidos → `data/mensual/autoventa/pedidos_detalle_productos_<mes>.csv`
   - Autoventa despachos → `data/mensual/autoventa/detalle_despachos_<mes>.xlsx`

   > Si actualizas un mes que ya cargaste, **sobrescribe el archivo** con el export
   > más reciente (trae los días nuevos). El ETL es idempotente: vuelve a leer todo
   > y actualiza sin duplicar.

2. **Cargar.** Dos modos:
   - Solo el mes en curso (rápido, ideal para la actualización semanal):
     ```
     python -m etl.run_historico --mes 2026-06
     ```
   - Todo lo que haya en las carpetas (recarga completa):
     ```
     python -m etl.run_historico
     ```

3. **Revisar el log** (`etl_historico.log`):
   - `% match Obuma↔despachos` por mes (ver sección 4).
   - `Vendedores no mapeados`: si aparece un nombre nuevo, agrégalo a
     `dim_vendedor` y **vuelve a correr** (reasigna sus filas, sin duplicar).

4. **Objetivos.** Las metas (`obj_venta`, `obj_maquinas`, `obj_visitas`) se editan
   desde la app (rol gerencia), no vienen en estos archivos. El %Cumplimiento se
   recalcula solo al actualizar las ventas.

---

## 4. ¿Qué es el "% match Obuma↔despachos" y afecta algo?

Es un **chequeo de integridad**, no un número de negocio. Mide, por mes:

```
% match = documentos de despacho que SÍ encuentran su factura en Obuma
          ──────────────────────────────────────────────────────────
                    total de documentos de despacho del mes
```

- **No cambia** los montos guardados (ventas, pedidos, despachos se cargan igual).
- **Sí indica** qué tan confiable es el cruce que marca **máquinas entregadas**:
  ese estado se obtiene uniendo el documento de la máquina (Obuma) con el despacho.
  Un match alto (90-100%) = casi todos los despachos enlazan con su venta.
- **Acuña aparece en ~0% y es normal:** Autoventa/despachos son solo de Gran
  Natural, así que no hay despachos de Acuña que cruzar. No es un error.
- Un match < 100% en Gran Natural suele deberse a **desfase temporal**: el despacho
  cae en un mes y la factura en otro. Se corrige solo al cargar el mes siguiente.
  Cargar el mes en curso cada semana mantiene el match alto.

**Conclusión:** úsalo como semáforo de calidad. Si baja mucho (p.ej. <70% en Gran
Natural), revisa que cargaste los archivos correctos del período.

---

## 5. Garantías técnicas

- **Idempotente:** cada tabla tiene llave natural (`UNIQUE`) y se hace `upsert`.
  Re-cargar un mes, varios o todo el histórico da el mismo resultado, sin duplicar.
- **Agnóstico a la cadencia:** la frecuencia (semanal/mensual) es solo *cuándo*
  corres el script; el código no cambia.
- **Fuente única de máquinas:** `etl/maquinas.py` lo usan tanto `run_etl.py`
  (mensual) como `run_historico.py` (carpetas), así ambos producen lo mismo.
- **`/data` es solo lectura** para el ETL: nunca se modifica ni borra un archivo.

---

## 6. Convención de nombres (estándar y compatibilidad)

Originalmente cada ERP se nombró distinto (Obuma con número `AAAA_MM`, Autoventa
con la palabra del mes `feb`/`febrero`). **No hay razón técnica**: era solo cómo
venían nombrados los exports. El sistema acepta ambas, pero el **estándar
recomendado** (escalable y a prueba de varios años) es numérico para todos:

```
obuma_ventas_acuña_AAAA_MM.xls
obuma_ventas_grannatural_AAAA_MM.xls
pedidos_detalle_productos_AAAA_MM.csv
detalle_despachos_AAAA_MM.xlsx
```

El detector de archivos reconoce `AAAA_MM`, `AAAA-MM` y también la palabra del mes
(compatibilidad con los archivos antiguos). Al subir desde la webapp, los archivos
se guardan automáticamente con este nombre estándar.

## 7. Subir Excel desde la webapp ✅ IMPLEMENTADO

Apartado **"Carga de archivos"** (solo rol gerencia/admin) en la app:
`app/pages/carga.py`. Subes los exports del mes, eliges el período y se cargan a
Supabase con un clic. Usa **el mismo núcleo** que el ETL de carpetas
(`etl/run_historico.procesar_carga`), así que el resultado es idéntico. Muestra el
reporte (N° docs, líneas, % match) y los vendedores no mapeados. Idempotente:
volver a subir el mismo mes actualiza, no duplica. La service-role key se usa solo
en el servidor (nunca llega al navegador) y la página está restringida por rol.
