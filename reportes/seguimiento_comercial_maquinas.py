# -*- coding: utf-8 -*-
"""
Actualiza el reporte de seguimiento comercial de máquinas.

Se corre cada lunes después de la reunión de gerencia (doble clic en
`Actualizar seguimiento maquinas.bat`). Lee la base, toma la versión que ya
existe para CONSERVAR lo que comercial escribió en las columnas amarillas, y la
reemplaza con la versión nueva. Antes de pisarla deja una copia en la carpeta
«Historial», por si alguien desordenó el archivo.

Dos destinos posibles:

  · GOOGLE SHEETS (nativo) — si el .env tiene SEGUIMIENTO_SHEET_ID. Escribe
    directo en la hoja de Google, mismo link de siempre. Configuración en
    docs/seguimiento_google_sheets_pasos.md.
  · EXCEL en la carpeta de Drive — si no hay SEGUIMIENTO_SHEET_ID (o con
    --excel). El .xlsx también se puede abrir y editar en Google Sheets.

    python reportes/seguimiento_comercial_maquinas.py
    python reportes/seguimiento_comercial_maquinas.py --probar     (solo prueba la conexión con Google)
    python reportes/seguimiento_comercial_maquinas.py --excel      (fuerza el modo Excel)
    python reportes/seguimiento_comercial_maquinas.py --sin-drive  (Excel solo local)
    python reportes/seguimiento_comercial_maquinas.py --salida=ruta.xlsx

Solo LEE de Supabase. La lógica vive en app/export_seguimiento_comercial.py
(la misma que usa el botón de la página Control de Máquinas).
"""
import os
import shutil
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

NOMBRE = "Seguimiento_comercial_maquinas.xlsx"
DRIVE = Path(r"G:\Mi unidad\Eduardo\Comercial\Control de Maquinas")
LOCAL = Path(__file__).resolve().parent / NOMBRE


def _armar(previo, hoy):
    from etl.db import get_client
    from app.export_seguimiento_comercial import (datos_seguimiento,
                                                  libro_seguimiento)
    print(f"Leyendo la base ({hoy:%d/%m/%Y})…")
    d = datos_seguimiento(get_client(), hoy)
    return libro_seguimiento(d["seg"], d["ped"], previo=previo,
                             ventas_post=d["ventas_post"],
                             vendedores=d["vendedores"],
                             clientes=d["clientes"], hoy=hoy)


def _carpeta_historial() -> Path:
    h = (DRIVE if DRIVE.parent.exists() else LOCAL.parent) / "Historial"
    h.mkdir(parents=True, exist_ok=True)
    return h


def _resumen(res: dict, donde: str) -> None:
    print()
    print(f"  Rechazos sin retomar ....... {res['sin_retomar']}"
          f"  ({res['sin_retomar_vencidos']} con más de 7 días)")
    print(f"  Reingresos sin cerrar ...... {res['sin_cerrar']}")
    print(f"  Pedidos sin gestionar ...... {res['pedidos_sin_gestionar']}")
    print(f"  Cerrados desde el anterior . {res['cerrados_semana']}")
    print()
    print(donde)


# ── Google Sheets ────────────────────────────────────────────────────────────

def modo_google(sheet_id: str, hoy: date, solo_probar: bool) -> int:
    from reportes.seguimiento_google_sheets import (
        ErrorGoogle, correo_robot, exportar_xlsx, info, reemplazar_contenido,
        sesion)
    try:
        s = sesion()
        print(f"Conectando con Google como {correo_robot()}…")
        meta = info(s, sheet_id)
        print(f"Hoja encontrada: «{meta['name']}»")
        previo = exportar_xlsx(s, sheet_id)
    except ErrorGoogle as e:
        print(f"*** {e}")
        return 1
    except Exception as e:
        # La librería de Google lanza sus propios errores cuando la llave no
        # sirve (borrada en Google Cloud, archivo dañado, reloj del PC
        # desfasado) o no hay internet.
        print(f"*** Google rechazó la conexión: {type(e).__name__}: {e}")
        print("    Revisa la conexión a internet y que la llave del paso 4 "
              "siga vigente (si se borró en Google Cloud, crea otra).")
        return 1
    if solo_probar:
        print("La conexión funciona: el robot puede leer la hoja. No se modificó nada.")
        return 0

    xlsx, res = _armar(previo, hoy)
    if not res["previo_leido"]:
        print("*** No se pudo leer la versión actual de la hoja: los "
              "comentarios de comercial NO se conservarían. No se reemplaza.")
        return 1
    copia = _carpeta_historial() / f"Seguimiento_hoja_antes_de_{hoy:%Y-%m-%d}.xlsx"
    copia.write_bytes(previo)
    print(f"Copia de respaldo de la hoja: {copia}")
    try:
        reemplazar_contenido(s, sheet_id, xlsx)
    except ErrorGoogle as e:
        print(f"*** {e}")
        return 1
    _resumen(res, f"Hoja actualizada: {meta.get('webViewLink', sheet_id)}")
    return 0


# ── Excel en la carpeta de Drive ─────────────────────────────────────────────

def modo_excel(argv: list[str], hoy: date) -> int:
    destino = DRIVE / NOMBRE
    if "--sin-drive" in argv or not DRIVE.parent.exists():
        destino = LOCAL
    for a in argv:
        if a.startswith("--salida="):
            destino = Path(a.split("=", 1)[1])

    previo = destino if destino.exists() else None
    xlsx, res = _armar(previo, hoy)
    if previo is not None and not res["previo_leido"]:
        print(f"*** No se pudo leer el archivo anterior ({previo}).")
        print("    Los comentarios de comercial NO se conservarían: no se "
              "reemplaza. Revisa que no esté abierto o dañado.")
        return 1

    destino.parent.mkdir(parents=True, exist_ok=True)
    if previo is not None:
        hist = destino.parent / "Historial"
        hist.mkdir(exist_ok=True)
        copia = hist / f"{destino.stem}_antes_de_{hoy:%Y-%m-%d}.xlsx"
        shutil.copy2(previo, copia)
        print(f"Copia del anterior: {copia}")
    try:
        destino.write_bytes(xlsx)
    except PermissionError:
        alt = LOCAL.with_name(f"{LOCAL.stem}_{hoy:%Y-%m-%d}.xlsx")
        alt.write_bytes(xlsx)
        print(f"*** {destino} está abierto (Excel/Drive). Se guardó en {alt}:")
        print("    ciérralo y vuelve a correr para actualizar Drive.")
        print(f"##ARCHIVO##{alt}")
        return 2
    _resumen(res, f"Archivo: {destino}")
    print(f"##ARCHIVO##{destino}")
    return 0


def main(argv: list[str]) -> int:
    # La consola de Windows (cp850) no tiene todos los caracteres.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
    hoy = date.today()
    sheet_id = os.environ.get("SEGUIMIENTO_SHEET_ID", "").strip()
    if sheet_id and "--excel" not in argv and "--salida" not in " ".join(argv):
        return modo_google(sheet_id, hoy, "--probar" in argv)
    if "--probar" in argv:
        print("*** Falta SEGUIMIENTO_SHEET_ID en el .env (paso 6 de la guía).")
        return 1
    return modo_excel(argv, hoy)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
