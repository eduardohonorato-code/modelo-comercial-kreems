# -*- coding: utf-8 -*-
"""
Conexión con la hoja de Google Sheets (nativa) del seguimiento comercial.

El reporte se arma igual que el Excel (app/export_seguimiento_comercial.py).
Lo que cambia es de dónde se lee la versión anterior y dónde se escribe:

  1. EXPORTAR la hoja de Google como .xlsx (API de Drive). Ese archivo es la
     «versión anterior»: de ahí salen los comentarios de comercial.
  2. Guardar una COPIA de respaldo de ese .xlsx (carpeta Historial).
  3. Armar el libro nuevo, conservando las columnas amarillas por ID de caso.
  4. SUBIR el libro nuevo ENCIMA de la misma hoja (misma ID, mismo link).
     Drive lo convierte a Google Sheets; el historial de versiones de Google
     queda disponible en Archivo → Historial de versiones.

Autenticación: una «cuenta de servicio» de Google Cloud (un usuario robot).
Su llave .json vive en `credenciales/` (fuera del repo) y la hoja se comparte
con su correo como Editor. Pasos en docs/seguimiento_google_sheets_pasos.md.

Variables en .env:
    SEGUIMIENTO_SHEET_ID   = el ID de la hoja (lo que va entre /d/ y /edit en el link)
    GOOGLE_SA_JSON         = ruta a la llave (por defecto credenciales/google_sa.json)
"""
import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_HOJA_GOOGLE = "application/vnd.google-apps.spreadsheet"
_API = "https://www.googleapis.com/drive/v3/files"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class ErrorGoogle(RuntimeError):
    """Un problema de configuración o de permisos, explicado en español."""


def _ruta_llave() -> Path:
    p = Path(os.environ.get("GOOGLE_SA_JSON", "credenciales/google_sa.json"))
    return p if p.is_absolute() else RAIZ / p


def sesion():
    """Sesión autenticada con la cuenta de servicio."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    llave = _ruta_llave()
    if not llave.exists():
        raise ErrorGoogle(
            f"No encuentro la llave de la cuenta de servicio en {llave}.\n"
            "    Descárgala de Google Cloud (paso 4 de la guía) y guárdala ahí, "
            "o indica la ruta en GOOGLE_SA_JSON dentro del .env.")
    cred = service_account.Credentials.from_service_account_file(
        str(llave), scopes=_SCOPES)
    return AuthorizedSession(cred)


def correo_robot() -> str:
    """El correo de la cuenta de servicio: con ese se comparte la hoja."""
    try:
        return json.loads(_ruta_llave().read_text(encoding="utf-8"))["client_email"]
    except Exception:
        return "(no se pudo leer la llave)"


def _revisar(r, que: str):
    if r.status_code < 300:
        return
    detalle = ""
    try:
        detalle = r.json().get("error", {}).get("message", "")
    except Exception:
        detalle = r.text[:300]
    if r.status_code == 404:
        raise ErrorGoogle(
            f"{que}: la hoja no existe o NO está compartida con el robot.\n"
            f"    Compártela como Editor con: {correo_robot()}\n"
            "    y revisa que SEGUIMIENTO_SHEET_ID sea el ID correcto.")
    if r.status_code == 403:
        raise ErrorGoogle(
            f"{que}: sin permiso ({detalle}).\n"
            "    Revisa que la API de Google Drive esté HABILITADA en el proyecto "
            "(paso 2) y que el robot sea Editor (no Lector) de la hoja.")
    raise ErrorGoogle(f"{que}: error {r.status_code} de Google — {detalle}")


def info(s, sheet_id: str) -> dict:
    """Nombre y tipo de la hoja: sirve para probar la conexión sin tocar nada."""
    r = s.get(f"{_API}/{sheet_id}",
              params={"fields": "id,name,mimeType,modifiedTime,webViewLink",
                      "supportsAllDrives": "true"})
    _revisar(r, "Leer la hoja")
    d = r.json()
    if d.get("mimeType") != _HOJA_GOOGLE:
        raise ErrorGoogle(
            f"El archivo «{d.get('name')}» no es una hoja nativa de Google "
            f"Sheets (es {d.get('mimeType')}). Usa el ID de la hoja de Google.")
    return d


def exportar_xlsx(s, sheet_id: str) -> bytes:
    """La hoja de Google, tal como está ahora, en formato .xlsx."""
    r = s.get(f"{_API}/{sheet_id}/export",
              params={"mimeType": _XLSX, "supportsAllDrives": "true"})
    _revisar(r, "Descargar la hoja")
    return r.content


def reemplazar_contenido(s, sheet_id: str, xlsx: bytes) -> None:
    """
    Sube el .xlsx encima de la hoja de Google: mismo archivo, mismo link,
    mismos permisos. Drive convierte el contenido a Google Sheets.
    """
    r = s.patch(f"{_UPLOAD}/{sheet_id}",
                params={"uploadType": "media", "supportsAllDrives": "true"},
                data=xlsx, headers={"Content-Type": _XLSX})
    _revisar(r, "Subir la versión nueva")
