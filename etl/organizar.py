"""
Organizador de descargas — Kreems.

Toma los Excel/CSV que descargaste (por defecto desde tu carpeta Descargas) y los
ARCHIVA en la estructura mensual con el nombre parametrizado:

    data/mensual/<fuente>/<fuente>_AAAA-MM.<ext>

Reconoce la fuente por palabras clave en el nombre original (mismas reglas que el
ETL legacy) y le pone el período que tú indicas. Copia (no borra el original) y
sobrescribe el destino si ya existe (idempotente: re-descargar el mismo mes solo
reemplaza el archivo, no acumula basura).

Uso:
    # Archiva lo que haya en Descargas para junio 2026
    python -m etl.organizar --periodo 2026-06

    # Desde otra carpeta de entrada
    python -m etl.organizar --periodo 2026-06 --inbox "D:/descargas_kreems"

    # Solo Acuña y despachos (las fuentes manuales típicas)
    python -m etl.organizar --periodo 2026-06 --solo obuma_acuna autoventa_despachos

    # Ver qué haría sin mover nada
    python -m etl.organizar --periodo 2026-06 --dry-run

Después de archivar, corre el ETL:
    python -m etl.run_etl --periodo 2026-06
"""
import argparse
import os
import shutil
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.config import MENSUAL_DIR, FUENTES, FILE_MATCH


def _norm(nombre: str) -> str:
    """Sin acentos y en MAYÚSCULAS (igual criterio que run_etl)."""
    nfkd = unicodedata.normalize("NFKD", nombre)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _inbox_por_defecto() -> Path:
    """Carpeta Descargas del usuario en Windows."""
    return Path(os.path.expanduser("~")) / "Downloads"


def _clasificar(p: Path) -> str | None:
    """Devuelve la clave de fuente que coincide con el archivo, o None."""
    nombre = _norm(p.name)
    for clave, regla in FILE_MATCH.items():
        if p.suffix.lower() != regla["ext"]:
            continue
        if all(k in nombre for k in regla["incluye"]) and not any(
            k in nombre for k in regla["excluye"]
        ):
            return clave
    return None


def _parse_periodo(valor: str) -> tuple[int, int]:
    try:
        anio, mes = valor.split("-")
        return int(anio), int(mes)
    except (ValueError, AttributeError):
        raise SystemExit(f"--periodo inválido: '{valor}'. Formato AAAA-MM (ej. 2026-06).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Archiva descargas en data/mensual/ con nombre parametrizado.")
    ap.add_argument("--periodo", required=True, metavar="AAAA-MM",
                    help="Mes al que pertenecen los archivos (ej. 2026-06).")
    ap.add_argument("--inbox", type=Path, default=None,
                    help="Carpeta de entrada (por defecto: ~/Downloads).")
    ap.add_argument("--solo", nargs="+", choices=list(FUENTES),
                    help="Limitar a estas fuentes (por defecto: todas las que aparezcan).")
    ap.add_argument("--dry-run", action="store_true", help="Muestra el plan sin mover archivos.")
    args = ap.parse_args()

    anio, mes = _parse_periodo(args.periodo)
    inbox = (args.inbox or _inbox_por_defecto()).resolve()
    if not inbox.is_dir():
        raise SystemExit(f"No existe la carpeta de entrada: {inbox}")

    permitidas = set(args.solo) if args.solo else set(FUENTES)
    sufijo = f"{anio:04d}-{mes:02d}"

    print(f"Inbox:   {inbox}")
    print(f"Período: {sufijo}")
    print(f"Fuentes: {', '.join(sorted(permitidas))}\n")

    encontrados: dict[str, Path] = {}
    for p in inbox.iterdir():
        if not p.is_file():
            continue
        clave = _clasificar(p)
        if clave and clave in permitidas:
            # Si hay varios para la misma fuente, gana el más reciente
            if clave not in encontrados or p.stat().st_mtime > encontrados[clave].stat().st_mtime:
                encontrados[clave] = p

    if not encontrados:
        print("No se encontró ningún archivo reconocible en la carpeta de entrada.")
        return 1

    movidos = 0
    for clave in sorted(encontrados):
        origen = encontrados[clave]
        carpeta = MENSUAL_DIR / FUENTES[clave]["carpeta"]
        destino = carpeta / f"{FUENTES[clave]['carpeta']}_{sufijo}{FUENTES[clave]['ext']}"
        flecha = f"{origen.name}  ->  {destino.relative_to(ROOT)}"
        if args.dry_run:
            print(f"[plan] {flecha}")
            continue
        carpeta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)   # copia (conserva el original en Descargas)
        print(f"[ok]   {flecha}")
        movidos += 1

    faltan = permitidas - set(encontrados)
    if faltan:
        print(f"\n[!] Sin archivo en la entrada para: {', '.join(sorted(faltan))}")

    if not args.dry_run:
        print(f"\nArchivados {movidos} archivo(s). Ahora corre:")
        print(f"    python -m etl.run_etl --periodo {sufijo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
