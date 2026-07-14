"""
Sucursal de las ventas de Gran Natural usando la API de Obuma (sin Excel).

Gran Natural ya no se exporta a Excel. La API de Obuma trae la dirección de
despacho de cada documento en `venta_observacion`, lo que cubre lo que Autoventa
no puede: las NOTAS DE CRÉDITO (se emiten sin pedido) y las facturas que no
cruzaron con un pedido.

Uso:
    python -m etl.run_direcciones_gn --periodo 2026-07
    python -m etl.run_direcciones_gn --desde 2026-02 --hasta 2026-07   # backfill

Idempotente. En la carga diaria esto ya lo hace run_obuma_api; este runner es para
rellenar los meses cargados antes de que existiera el paso.
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
from etl.run_obuma_api import _atribuir_sucursales_api

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
        raise SystemExit(f"Período inválido: '{valor}'. Formato AAAA-MM.")


def _rango(desde: tuple, hasta: tuple) -> list[tuple]:
    out, (a, m) = [], desde
    while (a, m) <= hasta:
        out.append((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Sucursal de las ventas GN vía API de Obuma (incluye NC).")
    ap.add_argument("--periodo", metavar="AAAA-MM")
    ap.add_argument("--desde", metavar="AAAA-MM")
    ap.add_argument("--hasta", metavar="AAAA-MM")
    args = ap.parse_args()

    if args.periodo:
        periodos = [_parse_periodo(args.periodo)]
    elif args.desde and args.hasta:
        periodos = _rango(_parse_periodo(args.desde), _parse_periodo(args.hasta))
    else:
        raise SystemExit("Indica --periodo AAAA-MM o --desde/--hasta.")

    inicio = datetime.now()
    client = get_client()
    for p in periodos:
        logger.info("── %d-%02d ─────────────────────────────", *p)
        _atribuir_sucursales_api(client, p)
    logger.info("Listo en %.1f s", (datetime.now() - inicio).total_seconds())
