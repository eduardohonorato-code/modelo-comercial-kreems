# Fase 4 — Webhook Obuma → Supabase Edge Function

Actualización casi en vivo: cuando Obuma emite un documento (factura o nota de crédito),
llama automáticamente a nuestra Edge Function, que hace upsert idempotente en Supabase.
Autoventa sigue con el ETL manual hasta que habiliten su API.

---

## 1. Prerrequisitos

- Supabase CLI instalado: `npm i -g supabase`
- SQL `005_webhook_log.sql` ejecutado en Supabase (crea la tabla `webhook_log`)
- La función SQL `001_modelo_datos.sql` ya estaba ejecutada (Fase 1)

---

## 2. Configurar el secret

El secret es la contraseña compartida entre Obuma y Supabase.
**Nunca lo pongas en el código ni en el repo.**

```bash
# Generar un secret seguro (guardarlo en un gestor de contraseñas)
openssl rand -hex 32

# Registrarlo en Supabase (reemplaza <tu-secret> con el valor generado)
supabase secrets set OBUMA_WEBHOOK_SECRET=<tu-secret> --project-ref <project-ref>

# Verificar que quedó registrado
supabase secrets list --project-ref <project-ref>
```

`SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` los inyecta Supabase automáticamente —
no hay que configurarlos.

---

## 3. Desplegar la Edge Function

```bash
# Desde la raíz del proyecto
supabase functions deploy obuma-webhook --project-ref <project-ref>
```

La URL pública de la función quedará en:
```
https://<project-ref>.supabase.co/functions/v1/obuma-webhook
```

Guarda esa URL: la necesitas en el paso siguiente.

---

## 4. Registrar el webhook en Obuma

> **Nota:** El flujo exacto puede variar según la versión de Obuma que uses.
> Las pantallas de referencia son de la sección *Integraciones → Webhooks* del panel de administración.

**Pasos:**

1. Ir a **Configuración → Integraciones → Webhooks** (o *API → Webhooks*).
2. Crear un nuevo webhook con:
   - **URL:** `https://<project-ref>.supabase.co/functions/v1/obuma-webhook`
   - **Evento:** `documento.emitido` (o el equivalente en tu versión: "Nueva Factura", "Emisión DTE", etc.)
   - **Sociedades:** activar tanto **Acuña** como **Gran Natural SPA** si el panel lo permite;
     si no, crear un webhook por sociedad con la misma URL.
3. En el campo de **autenticación / cabecera personalizada**, agregar:
   - **Header:** `Authorization`
   - **Valor:** `Bearer <tu-secret>` (el mismo valor que registraste en `OBUMA_WEBHOOK_SECRET`)
   
   Si Obuma no soporta header `Authorization`, usar `X-Webhook-Secret: <tu-secret>` —
   la función acepta ambas variantes.
4. Guardar y activar el webhook.

---

## 5. Formato esperado del payload

La función acepta este JSON (Obuma debería enviarlo automáticamente):

```json
{
  "evento": "documento.emitido",
  "sociedad": "Gran Natural SPA",
  "documento": {
    "tipo": "FACTURA ELECTRONICA",
    "numero": "123456",
    "fecha": "04-06-2026",
    "sucursal": "SUCURSAL CENTRAL",
    "vendedor": "JUAN PEREZ GARCIA",
    "cliente_rut": "12.345.678-9",
    "cliente_razon_social": "Comercial Ejemplo Ltda.",
    "cliente_comuna": "PROVIDENCIA",
    "cliente_region": "METROPOLITANA",
    "cliente_tipo": "MINORISTA",
    "items": [
      {
        "codigo": "HEL001",
        "nombre": "Helado Vainilla 1L",
        "categoria": "HELADOS",
        "subcategoria": "PALETAS",
        "fabricante": "KREEMS",
        "unidad_medida": "UN",
        "cantidad": 10,
        "subtotal_neto": 50000,
        "total": 59500,
        "costo": 30000,
        "margen": 20000
      }
    ]
  }
}
```

**Notas sobre el mapeo:**
- `sociedad`: acepta "Gran Natural SPA", "Gran Natural", "GRANNATURAL", "Acuña", "ACUNA", etc.
- `fecha`: acepta `DD-MM-YYYY` (formato Obuma) o `YYYY-MM-DD` (ISO).
- `vendedor`: se normaliza (sin acentos, mayúsculas) y busca en `dim_vendedor`.
  Si no se encuentra, va al bucket "Sin asignar" (igual que el ETL).
- NC: los montos se guardan con signo negativo automáticamente según el tipo de documento.

---

## 6. Probar el webhook manualmente

### 6.1 Prueba rápida con curl (sin secret)

Útil durante el desarrollo inicial con `OBUMA_WEBHOOK_SECRET` no configurado aún:

```bash
curl -X POST \
  https://<project-ref>.supabase.co/functions/v1/obuma-webhook \
  -H "Content-Type: application/json" \
  -d '{
    "evento": "documento.emitido",
    "sociedad": "Gran Natural SPA",
    "documento": {
      "tipo": "FACTURA ELECTRONICA",
      "numero": "TEST-001",
      "fecha": "04-06-2026",
      "sucursal": "TEST",
      "vendedor": "Sin asignar",
      "cliente_rut": "12.345.678-9",
      "cliente_razon_social": "Cliente Test",
      "items": [
        {
          "codigo": "TEST-PROD",
          "nombre": "Producto Test",
          "cantidad": 1,
          "subtotal_neto": 1000,
          "total": 1190
        }
      ]
    }
  }'
```

Respuesta esperada: `{"ok":true,"status":"ok","detalle":"FACTURA ELECTRONICA N°TEST-001 | 1 línea(s) | ..."}` (HTTP 200)

### 6.2 Prueba con secret configurado

```bash
curl -X POST \
  https://<project-ref>.supabase.co/functions/v1/obuma-webhook \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <tu-secret>" \
  -d '{ ... mismo payload ... }'
```

### 6.3 Verificar en Supabase

```sql
-- Ver los últimos webhooks recibidos
SELECT recibido_at, evento, n_dcto, sociedad, status, detalle
FROM webhook_log
ORDER BY recibido_at DESC
LIMIT 20;

-- Ver si el documento llegó a fact_ventas
SELECT * FROM fact_ventas WHERE n_dcto = 'TEST-001';
```

---

## 7. Monitoreo en producción

```bash
# Ver logs en vivo de la función (requiere Supabase CLI)
supabase functions logs obuma-webhook --project-ref <project-ref> --tail

# Ver los últimos errores
# (ejecutar en Supabase SQL Editor)
SELECT recibido_at, n_dcto, detalle
FROM webhook_log
WHERE status = 'error'
ORDER BY recibido_at DESC
LIMIT 10;
```

---

## 8. Idempotencia

La función usa las mismas claves de `ON CONFLICT` que el ETL Python:

| Tabla          | Clave ON CONFLICT                                          |
|----------------|------------------------------------------------------------|
| `dim_cliente`  | `rut`                                                      |
| `dim_producto` | `codigo`                                                   |
| `fact_ventas`  | `sociedad_id, tipo_dcto, n_dcto, producto_codigo, linea`   |

Si Obuma reenvía el mismo documento (retry, doble-click), el resultado en Supabase
es idéntico — no hay duplicados.

---

## 9. Limitaciones actuales (pendiente)

- **Autoventa** (pedidos, despachos, máquinas) sigue via ETL manual con
  `python -m etl.run_etl --periodo AAAA-MM`. No hay API de Autoventa aún.
- El webhook solo procesa **documentos Obuma**. Las máquinas (FL-4/FL-1/FL-2)
  solo se actualizan con el ETL completo.
- Si Obuma cambia el formato del payload, ajustar los tipos en `index.ts`
  (sección `ObumaPayload` / `ObumaItem`) sin tocar la lógica de negocio.
