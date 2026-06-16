"""
Carga de pedidos Autoventa · Gran Natural · vía API REST (Fase 4).

Pulea los pedidos desde la API de Autoventa y hace upsert idempotente en
fact_pedidos, con la misma llave natural que el ETL de Excel.

Uso:
    python -m etl.run_autoventa_api --periodo 2026-06
    python -m etl.run_autoventa_api --periodo 2026-06 --dry-run

Validado contra mayo 2026: 1.978 filas / 672 pedidos / neto $69,4M / 89 Sin DTE,
idéntico al Excel (dif $11 por redondeo de centavos de la API).

Gap conocido: neto_nc queda en 0 (las NC se emiten en Obuma y esta API no las
expone; en mayo eran $29.080 en total).

Los despachos (estado Entregada/Rechazada) NO vienen por esta API — siguen por
el Excel de despachos hasta resolver la lectura de DispatchInvoice con IT.

Requisitos en .env:
    AUTOVENTA_API_KEY_ADMIN, AUTOVENTA_EMPRESA_ID,
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
from etl.cleaners import construir_mapeo_vendedor
from etl.upsert import upsert_tabla
from etl.loaders.autoventa_api import cargar_autoventa_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))],
)
logger = logging.getLogger(__name__)


def _asegurar_vendedor_sin_asignar(client) -> int:
    resp = (client.table("dim_vendedor")
            .select("id").eq("nombre_canonico", "Sin asignar").execute())
    if resp.data:
        return resp.data[0]["id"]
    ins = (client.table("dim_vendedor")
           .insert({"nombre_canonico": "Sin asignar", "activo": True}).execute())
    return ins.data[0]["id"]


def _parse_periodo(valor: str) -> tuple:
    try:
        anio, mes = valor.split("-")
        return (int(anio), int(mes))
    except (ValueError, AttributeError):
        raise SystemExit(f"--periodo inválido: '{valor}'. Formato AAAA-MM (ej. 2026-06).")


def run(periodo: tuple, dry_run: bool = False):
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("Carga API Autoventa · Pedidos GN · %d-%02d %s",
                *periodo, "(DRY-RUN, no escribe)" if dry_run else "")
    logger.info("=" * 60)

    client = get_client()
    logger.info("Conexión Supabase OK")

    resp = client.table("dim_vendedor").select("id, nombre_canonico").execute()
    mapeo_vendedor = construir_mapeo_vendedor(resp.data or [])
    fallback_id = _asegurar_vendedor_sin_asignar(client)
    logger.info("Vendedores en dim_vendedor: %d | fallback 'Sin asignar' id=%s",
                len(mapeo_vendedor), fallback_id)

    log_no_mapeados: list = []
    av = cargar_autoventa_api(periodo, mapeo_vendedor, log_no_mapeados,
                              fallback_vendedor_id=fallback_id)

    fact_pedidos = av["fact_pedidos"]
    if fact_pedidos.empty:
        logger.warning("Sin pedidos para %d-%02d. Nada que cargar.", *periodo)
        return

    dim_cliente = av["dim_cliente"].rename(columns={"cliente_rut": "rut"})

    if dry_run:
        logger.info("\n-- DRY-RUN: resumen sin escribir --")
        logger.info("  fact_pedidos: %d filas | neto = %.0f",
                    len(fact_pedidos), fact_pedidos["neto"].astype(float).sum())
        logger.info("  dim_cliente:  %d", len(dim_cliente))
        logger.info("  stats: %s", av["stats"])
        if log_no_mapeados:
            unicos = sorted({r["nombre_original"] for r in log_no_mapeados})
            logger.warning("  Vendedores no mapeados (%d): %s", len(unicos), unicos)
        logger.info("DRY-RUN completado. No se escribió en Supabase.")
        return

    logger.info("\n-- Upserts a Supabase --")
    upsert_tabla(client, "dim_cliente", dim_cliente, on_conflict="rut")
    upsert_tabla(client, "fact_pedidos", fact_pedidos,
                 on_conflict="sociedad_id,n_pedido,producto_codigo,linea")

    if log_no_mapeados:
        unicos = sorted({r["nombre_original"] for r in log_no_mapeados})
        logger.warning("Vendedores no mapeados (%d únicos): %s", len(unicos), unicos)

    fin = datetime.now()
    logger.info("\n%s", "=" * 60)
    logger.info("Carga API Autoventa completada en %.1f s | stats: %s",
                (fin - inicio).total_seconds(), av["stats"])
    logger.info("%s", "=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Carga pedidos Autoventa GN vía API REST.")
    ap.add_argument("--periodo", required=True, metavar="AAAA-MM",
                    help="Mes a cargar (ej. 2026-06).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Valida y muestra el resumen sin escribir en Supabase.")
    args = ap.parse_args()
    run(_parse_periodo(args.periodo), dry_run=args.dry_run)
