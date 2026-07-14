"""
Atribuye la sucursal de las ventas que vienen del Excel de Obuma (Acuña y NC).

Complementa `run_direcciones` (que cubre Gran Natural vía Autoventa): lee los
exports de ventas de Obuma, saca la dirección del cliente de cada documento y
escribe fact_ventas.direccion_id, creando en dim_direccion las sucursales que
falten (origen='obuma').

Uso:
    python -m etl.run_direcciones_obuma --archivos "data/mensual/acuna/*.xls*"
    python -m etl.run_direcciones_obuma --archivos "data/mensual/acuna/*.xls*" --dry-run

Idempotente: re-ejecutable sin duplicar (la identidad de la sucursal es
cliente_rut + dirección normalizada, no el código de Obuma).

Requiere sql/027 y sql/028 corridos en Supabase.
"""
import argparse
import glob
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from etl.config import SOCIEDAD_ID
from etl.db import get_client
from etl.direcciones import ruts_dim_cliente
from etl.upsert import upsert_tabla
from etl.direcciones_obuma import (leer_direcciones_excel, construir,
                                   actualizar_fact_ventas, ids_existentes)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))],
)
logger = logging.getLogger(__name__)


def run(patrones: list[str], sociedad: str = "acuna", dry_run: bool = False):
    inicio = datetime.now()
    archivos = sorted({p for pat in patrones for p in glob.glob(pat)})
    if not archivos:
        raise SystemExit(f"No se encontraron archivos con: {patrones}")

    logger.info("=" * 60)
    logger.info("Sucursales desde Excel de Obuma · %s · %d archivo(s) %s",
                sociedad, len(archivos), "(DRY-RUN)" if dry_run else "")
    logger.info("=" * 60)

    client = get_client()
    ids = ids_existentes(client)
    logger.info("Direcciones ya conocidas: %d", len(ids))

    lineas = []
    for a in archivos:
        try:
            df = leer_direcciones_excel(Path(a), sociedad)
            logger.info("  %s → %d líneas, %d documentos, %d direcciones",
                        Path(a).name, len(df), df["n_dcto"].nunique(),
                        df["dir_norm"].nunique())
            lineas.append(df)
        except Exception as exc:
            logger.error("  %s: NO se pudo leer (%s)", Path(a).name, exc)
    if not lineas:
        raise SystemExit("Ningún archivo pudo leerse.")

    df = pd.concat(lineas, ignore_index=True)
    dim, mapa = construir(df, ids, ruts_validos=ruts_dim_cliente(client))
    logger.info("Direcciones nuevas para dim_direccion: %d | documentos mapeados: %d",
                len(dim), len(mapa))

    if dry_run:
        if not dim.empty:
            logger.info("\n%s", dim[["id", "cliente_rut", "direccion", "comuna"]]
                        .head(10).to_string(index=False))
        logger.info("DRY-RUN: no se escribió nada.")
        return

    if not dim.empty:
        upsert_tabla(client, "dim_direccion", dim, on_conflict="id")
    filas = actualizar_fact_ventas(client, mapa, SOCIEDAD_ID[sociedad])
    logger.info("fact_ventas: %d líneas con sucursal asignada", filas)

    logger.info("=" * 60)
    logger.info("Listo en %.1f s", (datetime.now() - inicio).total_seconds())
    logger.info("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Atribuye sucursal a las ventas del Excel de Obuma (Acuña/NC).")
    ap.add_argument("--archivos", nargs="+", required=True,
                    help='Glob(s) de exports de Obuma, ej. "data/mensual/acuna/*.xls*"')
    ap.add_argument("--sociedad", default="acuna", choices=list(SOCIEDAD_ID),
                    help="Sociedad de esos archivos (default: acuna).")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.archivos, args.sociedad, args.dry_run)
