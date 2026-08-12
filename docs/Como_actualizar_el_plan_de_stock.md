# Cómo actualizar el plan de stock (1 vez al mes)

## ¿Qué es un archivo .bat?

Es un **atajo**. Un archivo que, al hacerle doble clic, ejecuta por ti una serie de
pasos que si no tendrías que escribir a mano en la consola. No es un programa que
haya que instalar ni una macro de Excel: es un archivo de texto con instrucciones,
y Windows sabe cómo ejecutarlo.

En el proyecto ya existe otro (`Cargar mes.bat`) que es de la operación diaria.
El nuevo funciona igual, pero sirve para otra cosa: **las proyecciones**.

Al hacer doble clic se abre una **ventana negra** (la consola de Windows). Eso es
normal: ahí se va viendo el avance. No la cierres hasta que diga LISTO.

---

## Dónde están los archivos

Los dos .bat están en la **carpeta del proyecto**:

```
C:\Users\Evelyn Novoa\OneDrive\Escritorio\Modelo_Comercial\
```

Ahí adentro vas a ver, entre otras cosas:

- `Actualizar plan de stock.bat`  ← **el que te interesa aquí**: actualiza las proyecciones
- `Cargar mes.bat`  ← operación diaria (despachos, máquinas). No hace falta para este archivo.

---

## Este archivo es solo de PROYECCIONES

Importante para no confundirlo con la operación diaria:

- Este plan **solo lee ventas** (`fact_ventas` y el catálogo de productos).
  No lee despachos, ni pedidos, ni máquinas.
- **No escribe nada en la base.** Es de solo lectura: genera un Excel y nada más.
- **No modifica ni depende del ETL diario.** Es otra pata, para tener proyecciones.

Las ventas llegan **solas por API** (Gran Natural). Acuña dejó de facturar con la
transición a Gran Natural — su última venta real es del 13-jul-2026 — y el script la
tiene registrada como *sociedad de baja*, así que no espera datos suyos. Si alguna vez
volviera a facturar, avisa en pantalla.

**Conclusión práctica: para actualizar las proyecciones no tienes que cargar nada.**
Basta con el paso 2.

(La carga manual de despachos y el `Cargar mes.bat` son parte de la operación diaria
y sirven para otros análisis —máquinas, logística—, no para este archivo.)

---

## El proceso mensual, en 2 pasos

Los primeros días de cada mes, cuando ya cerró el mes anterior:

### Paso 1 — Cerrar el Excel del plan de stock
Si tienes abierto `plan_stock_temporada_2026_2027.xlsx`, ciérralo.
Windows no deja reescribir un archivo que está abierto.

> Si se te olvida no pasa nada grave: el proceso te avisa y guarda una copia
> aparte en la carpeta `reportes` del proyecto. Pero es más limpio cerrarlo antes.

### Paso 2 — Actualizar el plan
Doble clic en **`Actualizar plan de stock.bat`**.

Está en la carpeta del proyecto:
`C:\Users\Evelyn Novoa\OneDrive\Escritorio\Modelo_Comercial\`

Te va a pedir que presiones una tecla para empezar. Después trabaja solo:

```
[1/2] Recalculando demanda y stock desde Supabase...
[2/2] Recalculando formulas con Excel...
LISTO.
```

Puede demorar **1 o 2 minutos**. Durante el paso 2 vas a ver que Excel se abre y
se cierra solo: es el propio proceso recalculando las fórmulas. Déjalo hacer.

Cuando termina, el archivo ya quedó actualizado **directo en Drive**:
`G:\Mi unidad\Reportes Financieros\Entregables Valorizacion\Entregables Due Diligence\Proyecciones\`

No hay que copiar ni mover nada.

---

## ¿Qué cambia cada vez que lo corres?

El archivo distingue entre **meses reales** (ya facturados) y **meses proyectados**.
Ese corte **se mueve solo**: el programa mira cuál es el último mes completo que hay
en la base y lo usa como frontera. No hay que configurar nada.

Cuando abras el archivo, en cada hoja vas a ver:

- Una fila arriba que dice **REAL** o **PROY** sobre cada mes.
- Las columnas **REALES pintadas de verde**.
- En la hoja **Parametros**, arriba, la frase:
  *"DATOS AL 12-08-2026 · Meses REALES: enero a Jul-26 · Proyectados: Ago-26 en adelante"*

Ese es el chequeo rápido: si dice el mes que esperas, quedó bien.

Además, al cerrar un mes nuevo:
- Ese mes pasa de proyectado a real (con la venta efectiva, no la estimada).
- Los meses que quedan se recalculan con la venta acumulada real.
- Los stocks mínimo e ideal se ajustan solos, y también las tres hojas por CD.

---

## ¿Y si algo sale mal?

**Dice "No se pudo escribir en Drive"**
El Excel estaba abierto. Ciérralo y vuelve a correr el .bat.

**Dice "Fallo la generacion"**
Casi siempre es que no hay internet o la base no respondió. Reintenta; si sigue,
avísame con lo que diga la ventana.

**La ventana negra se cerró de inmediato**
Se hizo doble clic sobre el archivo equivocado, o el .bat no está en la carpeta del
proyecto (necesita estar junto a la carpeta `reportes`).

**El mes que cerró sigue apareciendo como PROY**
El mes todavía no está completo en la base (la API carga día a día). Revisa en la
pantalla hasta qué fecha hay datos.

**Dice "CORTE LIMITADO POR: ..."**
Significa que una fuente que sí opera no está cargada hasta fin de mes, así que el
plan **no** toma ese mes como real (si lo tomara, subestimaría la venta del mes y le
exigiría de más a los meses siguientes). La pantalla te dice cuál es y hasta qué día
tiene datos. Con Acuña de baja y Gran Natural entrando por API, este aviso no debería
aparecer; si sale, avísame.

**Dice "FACTURÓ DESPUÉS DE LA BAJA"**
Acuña volvió a emitir ventas después de haber quedado sin operación. No es un error
del proceso, es información: avísame para actualizar el registro en el script.

---

## Dos cosas que conviene mirar cada mes

En la hoja **Parametros**:

1. **"Año implícito al RITMO REAL"** — es a cuánto cerraría el año si el negocio
   sigue al ritmo que va. Si se aleja mucho de la META, conviene bajar la META en
   vez de exigirle a los meses que quedan una recuperación que no está pasando.

2. **El precio por caja** — las proyecciones en pesos usan el precio promedio de los
   meses ya cerrados. Si hubo promociones fuertes, ese promedio queda castigado y el
   $ proyectado sale bajo (o al revés). Las **cajas** no se ven afectadas: solo la
   conversión a pesos.

---

## Resumen para pegar en la pared

| Cuándo | Qué | Cómo |
|---|---|---|
| Cierra el mes | Cerrar el Excel del plan si está abierto | — |
| Después | Actualizar el plan de stock | Doble clic en `Actualizar plan de stock.bat` |
| Al final | Verificar el corte | Hoja Parametros: "Meses REALES: enero a ___" |

Nada más: las ventas llegan solas por API. `Cargar mes.bat` es de la operación
diaria (despachos, máquinas), no de este archivo.
