# ETL en la nube (GitHub Actions)

El ETL de Gran Natural (ventas Obuma + pedidos Autoventa) corre **solo, en los
servidores de GitHub**, sin depender del PC de la oficina. Así los datos quedan
frescos temprano en la mañana para el reporte de las 9 AM.

Archivo: `.github/workflows/etl-diario.yml`.

## Horario
- Dos corridas diarias: **10:00 y 11:00 UTC** (redundancia por si GitHub se
  atrasa). En Chile eso es ~06:00–07:00 (invierno) / 07:00–08:00 (verano).
- Ambas terminan bastante antes de las 9 AM.
- El ETL es **idempotente**: correr dos veces no duplica nada.

## Configuración inicial (una sola vez) — agregar los Secrets
En GitHub, en el repo `modelo-comercial-kreems`:
1. **Settings** → (menú izquierdo) **Secrets and variables** → **Actions**.
2. Botón **New repository secret** y crear estos 4 (copiar el valor desde el `.env`
   del PC):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `OBUMA_API_KEY`
   - `AUTOVENTA_API_KEY_ADMIN`
   - (Opcional) `AUTOVENTA_EMPRESA_ID` — si no se agrega, usa `548` por defecto.

> Los Secrets quedan cifrados; no se ven en el código ni en los logs.

## Cómo probar / correr a mano
- Pestaña **Actions** → workflow **"ETL diario Kreems (nube)"** → botón
  **Run workflow**. Corre al instante.

## Cómo revisar que corrió bien
- En **Actions** cada corrida aparece con ✓ (ok) o ✗ (falló).
- Si algo falla, entrar a la corrida → se ve el log en pantalla, y además queda el
  archivo `etl_auto.log` como *artifact* descargable (14 días).

## Qué sigue siendo manual (no cambia)
- **Acuña** y **despachos**: se suben por Excel desde la página **Carga** de la app
  (independiente del PC; se puede hacer desde cualquier lado). No son diarios.

## Relación con la tarea del PC
- La tarea de Windows ("Kreems ETL diario", 08:30) queda **redundante**: como el PC
  está apagado a esa hora, no corría a tiempo. Conviene **desactivarla** para que no
  se dispare al prender el PC a las 9 (mientras se saca el pantallazo):
  `Disable-ScheduledTask -TaskName "Kreems ETL diario"` (PowerShell).
