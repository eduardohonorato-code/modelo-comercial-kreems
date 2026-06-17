"""
Organizador de descargas — Kreems (carpetas-bandeja).

Los exports de Obuma/Autoventa se descargan con nombres aleatorios y SIN el nombre
de la empresa, así que no se pueden clasificar por el nombre. En vez de eso, se usa
una bandeja rotulada por fuente: tú sueltas cada descarga (con el nombre que tenga)
en su carpeta, y este script la archiva con el nombre canónico del período.

    inbox/                          data/mensual/
      acuna/      <archivo>   →       acuna/        acuna_AAAA-MM.xls
      despachos/  <archivo>   →       despachos/    despachos_AAAA-MM.xlsx
      pedidos/    <archivo>   →       pedidos/      pedidos_AAAA-MM.csv        (opcional)
      gran_natural/ <archivo> →       gran_natural/ gran_natural_AAAA-MM.xls   (opcional, respaldo API)
      objetivos/  <archivo>   →       objetivos/    objetivos_AAAA-MM.xlsx     (opcional)

La fuente la define la BANDEJA (no el nombre). El período lo das tú con --periodo
(no se deduce del nombre: la fecha del archivo Obuma es la de descarga, no la de
los datos). El original se mueve a inbox/_procesados/<periodo>/ como respaldo.

Uso típico (cada vez que cargas un mes):
    python -m etl.organizar --periodo 2026-06     # archiva lo que haya en inbox/
    python -m etl.organizar --periodo 2026-06 --dry-run
    python -m etl.organizar --init                # crea las carpetas inbox/ vacías

Después:
    python -m etl.run_etl --periodo 2026-06
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.config import MENSUAL_DIR, FUENTES

INBOX_DIR = ROOT / "inbox"


def _parse_periodo(valor: str) -> tuple[int, int]:
    try:
        anio, mes = valor.split("-")
        return int(anio), int(mes)
    except (ValueError, AttributeError):
        raise SystemExit(f"--periodo inválido: '{valor}'. Formato AAAA-MM (ej. 2026-06).")


def _crear_bandejas(inbox: Path) -> None:
    """Crea inbox/<fuente>/ para cada fuente (idempotente)."""
    for clave in FUENTES:
        (inbox / FUENTES[clave]["carpeta"]).mkdir(parents=True, exist_ok=True)
    print(f"Bandejas listas en: {inbox}")
    for clave in FUENTES:
        print(f"  inbox/{FUENTES[clave]['carpeta']}/   ({clave}, {FUENTES[clave]['ext']})")


def _periodo_en_contenido(path: Path, ext: str) -> str | None:
    """
    Best-effort: detecta el mes dominante de los datos para avisar si no coincide
    con --periodo (evita el error típico de archivar el mes equivocado). Nunca
    bloquea: si no puede determinarlo, devuelve None en silencio.
    """
    try:
        import pandas as pd, io, warnings
        warnings.simplefilter("ignore")
        if ext == ".xls":          # Obuma: HTML disfrazado de .xls
            html = path.read_text(encoding="latin-1", errors="ignore")
            df = pd.read_html(io.StringIO(html), header=0, flavor="lxml")[0]
        elif ext == ".xlsx":
            df = pd.read_excel(path, engine="openpyxl")
        elif ext == ".csv":
            df = pd.read_csv(path, sep=";", dtype=str, on_bad_lines="skip")
        else:
            return None
        col = next((c for c in df.columns if "FECHA" in str(c).upper()), None)
        if not col:
            return None
        fechas = pd.to_datetime(df[col], errors="coerce", dayfirst=True).dropna()
        if fechas.empty:
            return None
        top = fechas.dt.to_period("M").value_counts().idxmax()
        return f"{top.year:04d}-{top.month:02d}"
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Archiva las descargas desde inbox/ al esquema mensual.")
    ap.add_argument("--periodo", metavar="AAAA-MM",
                    help="Mes al que pertenecen los archivos (ej. 2026-06).")
    ap.add_argument("--inbox", type=Path, default=INBOX_DIR,
                    help=f"Carpeta de bandejas (por defecto: {INBOX_DIR}).")
    ap.add_argument("--init", action="store_true",
                    help="Crea las carpetas-bandeja vacías y termina.")
    ap.add_argument("--dry-run", action="store_true", help="Muestra el plan sin mover nada.")
    args = ap.parse_args()

    inbox = args.inbox.resolve()

    if args.init:
        _crear_bandejas(inbox)
        return 0

    if not args.periodo:
        raise SystemExit("Falta --periodo AAAA-MM (o usa --init para crear las bandejas).")
    anio, mes = _parse_periodo(args.periodo)
    sufijo = f"{anio:04d}-{mes:02d}"

    if not inbox.is_dir():
        raise SystemExit(f"No existe la carpeta de bandejas: {inbox}\n"
                         f"Créala con:  python -m etl.organizar --init")

    print(f"Inbox:   {inbox}")
    print(f"Período: {sufijo}\n")

    procesados = inbox / "_procesados" / sufijo
    archivados = 0
    vacias = []

    for clave in FUENTES:
        carpeta_in = inbox / FUENTES[clave]["carpeta"]
        ext = FUENTES[clave]["ext"]
        if not carpeta_in.is_dir():
            vacias.append(clave)
            continue
        # Archivos reales (ignora subcarpetas y ocultos)
        files = [p for p in carpeta_in.iterdir() if p.is_file() and not p.name.startswith(".")]
        if not files:
            vacias.append(clave)
            continue
        if len(files) > 1:
            print(f"[!] {clave}: hay {len(files)} archivos en inbox/{FUENTES[clave]['carpeta']}/ "
                  f"({[f.name for f in files]}). Deja solo uno. Omitido.")
            continue

        origen = files[0]
        if origen.suffix.lower() != ext:
            print(f"[!] {clave}: '{origen.name}' tiene extensión {origen.suffix} "
                  f"pero se esperaba {ext}. ¿Bandeja equivocada? Omitido.")
            continue

        # Aviso si el contenido parece de otro mes (no bloquea)
        det = _periodo_en_contenido(origen, ext)
        if det and det != sufijo:
            print(f"[!] {clave}: el contenido parece de {det}, no de {sufijo}. "
                  f"Revisa el período. (Se archiva igual.)")

        destino = MENSUAL_DIR / FUENTES[clave]["carpeta"] / f"{FUENTES[clave]['carpeta']}_{sufijo}{ext}"
        rel = destino.relative_to(ROOT)
        if args.dry_run:
            print(f"[plan] inbox/{FUENTES[clave]['carpeta']}/{origen.name}  ->  {rel}")
            continue

        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)               # escribe el archivo canónico
        procesados.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(procesados / origen.name))  # vacía la bandeja
        print(f"[ok]   inbox/{FUENTES[clave]['carpeta']}/{origen.name}  ->  {rel}")
        archivados += 1

    if vacias:
        print(f"\n(Sin archivo en bandeja: {', '.join(vacias)})")

    if not args.dry_run:
        print(f"\nArchivados {archivados} archivo(s). Originales en inbox/_procesados/{sufijo}/.")
        if archivados:
            print(f"Ahora corre:\n    python -m etl.run_etl --periodo {sufijo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
