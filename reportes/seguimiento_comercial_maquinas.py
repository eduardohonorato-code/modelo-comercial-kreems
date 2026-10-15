# -*- coding: utf-8 -*-
"""
Actualiza en Drive el reporte de seguimiento comercial de máquinas.

Se corre cada lunes después de la reunión de gerencia (doble clic en
`Actualizar seguimiento maquinas.bat`). Lee la base, toma el archivo que ya está
en Drive para CONSERVAR lo que comercial escribió en las columnas amarillas, y
lo reemplaza con la versión nueva. Antes de pisarlo deja una copia del anterior
en la subcarpeta «Historial», por si alguien desordenó el archivo.

    python reportes/seguimiento_comercial_maquinas.py
    python reportes/seguimiento_comercial_maquinas.py --sin-drive   (solo local)
    python reportes/seguimiento_comercial_maquinas.py --salida=ruta.xlsx

Solo LEE de Supabase. La lógica vive en app/export_seguimiento_comercial.py
(la misma que usa el botón de la página Control de Máquinas).
"""
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.db import get_client  # noqa: E402
from app.export_seguimiento_comercial import (  # noqa: E402
    datos_seguimiento, libro_seguimiento)

NOMBRE = "Seguimiento_comercial_maquinas.xlsx"
DRIVE = Path(r"G:\Mi unidad\Eduardo\Comercial\Control de Maquinas")
LOCAL = Path(__file__).resolve().parent / NOMBRE


def main(argv: list[str]) -> int:
    # La consola de Windows (cp850) no tiene todos los caracteres.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    hoy = date.today()
    destino = DRIVE / NOMBRE
    if "--sin-drive" in argv or not DRIVE.parent.exists():
        destino = LOCAL
    for a in argv:
        if a.startswith("--salida="):
            destino = Path(a.split("=", 1)[1])

    previo = destino if destino.exists() else None
    print(f"Leyendo la base ({hoy:%d/%m/%Y})…")
    d = datos_seguimiento(get_client(), hoy)
    xlsx, res = libro_seguimiento(d["seg"], d["ped"], previo=previo,
                                  ventas_post=d["ventas_post"],
                                  vendedores=d["vendedores"],
                                  clientes=d["clientes"], hoy=hoy)
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

    print()
    print(f"  Rechazos sin retomar ....... {res['sin_retomar']}"
          f"  ({res['sin_retomar_vencidos']} con más de 7 días)")
    print(f"  Reingresos sin cerrar ...... {res['sin_cerrar']}")
    print(f"  Pedidos sin gestionar ...... {res['pedidos_sin_gestionar']}")
    print(f"  Cerrados desde el anterior . {res['cerrados_semana']}")
    print()
    print(f"Archivo: {destino}")
    print(f"##ARCHIVO##{destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
