"""
Carga de sucursales/direcciones de cliente y atribución de la facturación.

Puebla dim_direccion (una llamada a /clients) y escribe direccion_id en
fact_ventas y fact_pedidos de los períodos indicados, cruzando por la API de
Autoventa (ver etl/direcciones.py para la cadena de atribución).

Uso:
    python -m etl.run_direcciones --periodo 2026-07
    python -m etl.run_direcciones --desde 2026-02 --hasta 2026-07     # backfill
    python -m etl.run_direcciones --desde 2026-06 --hasta 2026-06 --dry-run

Idempotente: re-ejecutable sin duplicar. Solo aplica a Gran Natural (Acuña no
pasa por Autoventa); las notas de crédito quedan sin dirección.

Requisitos en .env: AUTOVENTA_API_KEY_ADMIN, AUTOVENTA_EMPRESA_ID,
SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from etl.db import get_client
from etl.upsert import upsert_tabla
from etl.direcciones import (cargar_dim_direccion, mapas_periodo,
                             actualizar_direcciones, direcciones_faltantes,
                             ruts_dim_cliente)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))],
)
logger = logging.getLogger(__name__)


def _parse_periodo(valor: str) -> tuple:
    try:
        anio, mes = valor.split("-")
        return (int(anio), int(mes))
    except (ValueError, AttributeError):
        raise SystemExit(f"Período inválido: '{valor}'. Formato AAAA-MM (ej. 2026-06).")


def _rango(desde: tuple, hasta: tuple) -> list[tuple]:
    if desde > hasta:
        raise SystemExit("--desde no puede ser posterior a --hasta.")
    out, (a, m) = [], desde
    while (a, m) <= hasta:
        out.append((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def run(periodos: list[tuple], dry_run: bool = False):
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("Direcciones/sucursales · períodos: %s %s",
                ", ".join(f"{a}-{m:02d}" for a, m in periodos),
                "(DRY-RUN, no escribe)" if dry_run else "")
    logger.info("=" * 60)

    client = get_client()

    # 1) dim_direccion (no depende del período)
    ruts = ruts_dim_cliente(client)
    logger.info("RUT en dim_cliente: %d", len(ruts))
    dim = cargar_dim_direccion(ruts_validos=ruts)
    if dim.empty:
        raise SystemExit("La API no devolvió direcciones. Abortando.")
    if not dry_run:
        upsert_tabla(client, "dim_direccion", dim, on_conflict="id")

    multi = dim.groupby("cliente_rut").size()
    logger.info("Clientes con más de una dirección: %d", int((multi > 1).sum()))
    conocidas = set(dim["id"].astype(int))

    # 2) Atribución por período
    for periodo in periodos:
        doc_dir, pedido_dir, stats, vistas = mapas_periodo(periodo)

        # Direcciones despachadas que el catálogo no trae → registrarlas antes de
        # que fact_ventas.direccion_id las referencie (FK).
        extra = direcciones_faltantes(vistas, conocidas, ruts)
        if not extra.empty:
            if not dry_run:
                upsert_tabla(client, "dim_direccion", extra, on_conflict="id")
            conocidas |= set(extra["id"].astype(int))

        res = actualizar_direcciones(client, periodo, doc_dir, pedido_dir,
                                     dry_run=dry_run)
        logger.info("  [dir] %d-%02d actualizado → %s | %s", *periodo, res, stats)

    logger.info("=" * 60)
    logger.info("Listo en %.1f s%s", (datetime.now() - inicio).total_seconds(),
                " (DRY-RUN: no se escribió nada)" if dry_run else "")
    logger.info("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Carga dim_direccion y atribuye facturación/pedidos por sucursal.")
    ap.add_argument("--periodo", metavar="AAAA-MM", help="Un solo mes (ej. 2026-07).")
    ap.add_argument("--desde", metavar="AAAA-MM", help="Primer mes del rango.")
    ap.add_argument("--hasta", metavar="AAAA-MM", help="Último mes del rango.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Muestra la cobertura sin escribir en Supabase.")
    args = ap.parse_args()

    if args.periodo:
        periodos = [_parse_periodo(args.periodo)]
    elif args.desde and args.hasta:
        periodos = _rango(_parse_periodo(args.desde), _parse_periodo(args.hasta))
    else:
        raise SystemExit("Indica --periodo AAAA-MM o --desde AAAA-MM --hasta AAAA-MM.")

    run(periodos, dry_run=args.dry_run)
