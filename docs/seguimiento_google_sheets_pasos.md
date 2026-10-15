# Seguimiento comercial de máquinas en Google Sheets — pasos a seguir

Objetivo: que el reporte semanal (rechazos sin retomar, reingresos sin cerrar y
pedidos sin gestionar) viva en una **hoja nativa de Google Sheets** y que cada
lunes se actualice con **doble clic**, conservando lo que comercial escribió.

Se hace **una sola vez** (≈15 minutos). Después, la rutina es el paso 8.

---

## Cómo funciona (en una línea)

El script descarga la hoja de Google, rescata las columnas amarillas
(comentarios de comercial) por **ID de caso**, arma la versión nueva con los
datos del sistema y la **sube encima de la misma hoja**: mismo link, mismos
permisos. Antes de subirla guarda una copia en `Historial`.

Para entrar a tu Drive sin tu contraseña usa una **cuenta de servicio**: un
«usuario robot» de Google con su propio correo (algo como
`seguimiento@kreems-123.iam.gserviceaccount.com`). El robot solo ve los archivos
que tú compartas con él.

---

## Paso 1 · Crear la hoja de Google a partir del Excel

1. Entra a [drive.google.com](https://drive.google.com) → *Mi unidad → Eduardo →
   Comercial → Control de Maquinas*.
2. Abre **Seguimiento_comercial_maquinas.xlsx** (doble clic).
3. Menú **Archivo → Guardar como Hojas de cálculo de Google**.
4. Se abre la hoja nueva. Cámbiale el nombre a **Seguimiento comercial máquinas**.
5. Copia el **ID** desde la barra de direcciones: es lo que va entre `/d/` y
   `/edit`. Ejemplo:
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xYz`**`/edit` → el ID es `1AbC...xYz`.
   Guárdalo: lo usas en el paso 6.

> Desde ahora **trabajen solo en esta hoja**. El .xlsx queda como respaldo.

## Paso 2 · Crear el proyecto en Google Cloud y habilitar Drive

1. Entra a [console.cloud.google.com](https://console.cloud.google.com) con
   **eduardo.honorato@kreems.cl**.
2. Arriba a la izquierda, en el selector de proyecto → **Proyecto nuevo** →
   nombre `kreems-seguimiento` → **Crear**. (Es gratis; no pide tarjeta.)
3. Con ese proyecto seleccionado, ve a **APIs y servicios → Biblioteca**,
   busca **Google Drive API** → **Habilitar**.

## Paso 3 · Crear la cuenta de servicio (el robot)

1. **APIs y servicios → Credenciales → Crear credenciales → Cuenta de servicio**.
2. Nombre: `seguimiento-maquinas` → **Crear y continuar** → en «rol» no elijas
   nada → **Listo**.
3. Copia el **correo** del robot (termina en `.iam.gserviceaccount.com`).

## Paso 4 · Descargar la llave del robot

1. En la lista de cuentas de servicio, entra a `seguimiento-maquinas`.
2. Pestaña **Claves → Agregar clave → Crear clave nueva → JSON → Crear**.
   Se descarga un archivo `.json`.
3. Crea la carpeta `credenciales` dentro del proyecto y guarda ahí el archivo
   con el nombre **`google_sa.json`**:

   ```
   Modelo_Comercial\credenciales\google_sa.json
   ```

> ⚠️ Esa llave es una contraseña: **no la mandes por correo ni la subas a
> GitHub**. La carpeta `credenciales/` ya está excluida del repositorio
> (`.gitignore`). Si se pierde o se filtra, bórrala en Google Cloud y crea otra.
>
> Si Google te dice que la creación de claves está **bloqueada por una
> política de la organización**, el administrador de Google Workspace de
> Kreems tiene que permitirla (política `iam.disableServiceAccountKeyCreation`).

## Paso 5 · Compartir la hoja con el robot

1. Abre la hoja del paso 1 → botón **Compartir**.
2. Pega el **correo del robot** (paso 3), permiso **Editor**, desmarca
   «Notificar» → **Compartir**.

Aprovecha de compartirla también con el **equipo comercial** (Editor), que es
quien va a trabajar las columnas amarillas.

## Paso 6 · Decirle al script cuál es la hoja

Abre el archivo **`.env`** del proyecto (el mismo que tiene las llaves de
Supabase) y agrega al final:

```
SEGUIMIENTO_SHEET_ID=1AbC...xYz
```

(el ID del paso 1). Si guardaste la llave en otra ruta, agrega también
`GOOGLE_SA_JSON=C:\ruta\a\la\llave.json`.

## Paso 7 · Probar la conexión (no modifica nada)

Abre una terminal en la carpeta del proyecto y corre:

```bash
python reportes/seguimiento_comercial_maquinas.py --probar
```

Debe decir:

```
Conectando con Google como seguimiento-maquinas@....iam.gserviceaccount.com…
Hoja encontrada: «Seguimiento comercial máquinas»
La conexión funciona: el robot puede leer la hoja. No se modificó nada.
```

Si sale un mensaje con `***`, el propio mensaje dice qué paso revisar (ver
también la tabla de problemas al final).

## Paso 8 · Rutina de cada lunes (después de la reunión)

1. Doble clic en **`Actualizar seguimiento maquinas.bat`**.
2. El script muestra los conteos y el link de la hoja:
   ```
     Rechazos sin retomar ....... 40  (38 con más de 7 días)
     Reingresos sin cerrar ...... 16
     Pedidos sin gestionar ...... 10
     Cerrados desde el anterior . 3
   Hoja actualizada: https://docs.google.com/spreadsheets/d/...
   ```
3. Avisar a comercial que la hoja está al día (el link es siempre el mismo).

Con `SEGUIMIENTO_SHEET_ID` en el `.env`, el `.bat` escribe en Google Sheets.
Sin él, sigue escribiendo el Excel en la carpeta de Drive como antes.

---

## Qué hace comercial durante la semana

- Trabaja **solo las columnas amarillas**: *Estado gestión* (lista desplegable),
  *Acción comprometida*, *Responsable comercial*, *Fecha compromiso*,
  *Comentario / situación del cliente*.
- **No** editar las demás columnas ni cambiar el nombre de las pestañas ni de los
  encabezados: el script las usa para encontrar cada caso.
- **No** insertar columnas en medio. Filtrar y ordenar sí se puede.
- Marcar «Descartado» **no** saca el caso de la lista: sale solo cuando el
  sistema lo ve resuelto (entregado, DTE emitido o anulado en el ERP) y pasa a
  «4 · Casos cerrados» con sus comentarios.
- Los **comentarios de Google** (clic derecho → Comentario) se pueden perder al
  actualizar, porque la hoja se reemplaza entera. Lo que se quiera conservar va
  en la columna *Comentario / situación del cliente*.

## Si algo sale mal

| Mensaje | Qué revisar |
|---|---|
| `No encuentro la llave…` | Paso 4: el archivo `credenciales\google_sa.json` |
| `la hoja no existe o NO está compartida con el robot` | Paso 5 (compartir como Editor) y el ID del paso 6 |
| `sin permiso … Drive API has not been used / disabled` | Paso 2: habilitar Google Drive API |
| `no es una hoja nativa de Google Sheets` | El ID es del .xlsx: usa el de la hoja creada en el paso 1 |
| `No se pudo leer la versión actual de la hoja` | Alguien cambió nombres de pestañas o encabezados. No se reemplaza nada; restaura desde *Archivo → Historial de versiones* |

**Volver atrás:** en la hoja, *Archivo → Historial de versiones → Ver historial
de versiones* y restaurar la del día anterior; o abrir la copia
`Historial\Seguimiento_hoja_antes_de_AAAA-MM-DD.xlsx` que deja el script.

## Archivos involucrados

| Archivo | Para qué |
|---|---|
| `Actualizar seguimiento maquinas.bat` | El doble clic de los lunes |
| `reportes/seguimiento_comercial_maquinas.py` | Elige Google Sheets o Excel y arma el reporte |
| `reportes/seguimiento_google_sheets.py` | Conexión con Google (descargar / subir la hoja) |
| `app/export_seguimiento_comercial.py` | Las listas y la regla que conserva los comentarios |
| `credenciales/google_sa.json` | Llave del robot (**no** va al repositorio) |
| `.env` → `SEGUIMIENTO_SHEET_ID` | Cuál hoja actualizar |
