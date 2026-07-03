"""
Carga de ventas Obuma · Gran Natural · vía API REST (Fase 4).

Pulea las ventas de Gran Natural desde la API de Obuma y hace upsert idempotente
en fact_ventas / dim_cliente / dim_producto, reutilizando exactamente la misma
lógica y llaves naturales que el ETL de Excel.

Uso:
    python -m etl.run_obuma_api --periodo 2026-06
    python -m etl.run_obuma_api --periodo 2026-06 --dry-run   # no escribe, solo valida

Acuña NO se carga aquí (otra empresa en Obuma → otra API key); sigue por Excel
con `python -m etl.run_etl`.

Requisitos en .env:
    OBUMA_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import calendar
from datetime import date

import pandas as pd

from etl.db import get_client, cargar_alias
from etl.cleaners import construir_mapeo_vendedor, agregar_alias
from etl.upsert import upsert_tabla
from etl.loaders.obuma_api import cargar_obuma_api
from etl.maquinas import (derivar_maquinas_obuma, aplicar_estado_despachos,
                          aplicar_override_vendedor)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))],
)
logger = logging.getLogger(__name__)


def _asegurar_vendedor_sin_asignar(client) -> int:
    """Igual que en run_etl: bucket 'Sin asignar' para ventas sin vendedor."""
    resp = (client.table("dim_vendedor")
            .select("id").eq("nombre_canonico", "Sin asignar").execute())
    if resp.data:
        return resp.data[0]["id"]
    ins = (client.table("dim_vendedor")
           .insert({"nombre_canonico": "Sin asignar", "activo": True}).execute())
    return ins.data[0]["id"]


def _leer_despachos_periodo(client, periodo: tuple) -> pd.DataFrame:
    """
    Lee fact_despachos del período desde Supabase para cruzar el estado de
    entrega de las máquinas. Si aún no hay despachos del mes (se cargan por
    Autoventa), las máquinas quedan en 'gestionada'.
    """
    anio, mes = periodo
    f_desde = date(anio, mes, 1).isoformat()
    f_hasta = date(anio, mes, calendar.monthrange(anio, mes)[1]).isoformat()
    rows, start = [], 0
    while True:
        b = (client.table("fact_despachos")
             .select("documento,estado")
             .eq("sociedad_id", 2)
             .gte("fecha_ruta", f_desde).lte("fecha_ruta", f_hasta)
             .range(start, start + 999).execute().data)
        if not b:
            break
        rows += b
        start += 1000
        if len(b) < 1000:
            break
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["documento", "estado"])


def _leer_overrides_maquina(client) -> pd.DataFrame:
    """Lee maquina_vendedor_override (vacío si la tabla no existe)."""
    try:
        r = (client.table("maquina_vendedor_override")
             .select("sociedad_id,documento,vendedor_id").range(0, 9999).execute())
        return pd.DataFrame(r.data) if r.data else pd.DataFrame(
            columns=["sociedad_id", "documento", "vendedor_id"])
    except Exception:
        return pd.DataFrame(columns=["sociedad_id", "documento", "vendedor_id"])


def _parse_periodo(valor: str) -> tuple:
    try:
        anio, mes = valor.split("-")
        return (int(anio), int(mes))
    except (ValueError, AttributeError):
        raise SystemExit(f"--periodo inválido: '{valor}'. Formato AAAA-MM (ej. 2026-06).")


def run(periodo: tuple, dry_run: bool = False):
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("Carga API Obuma · Gran Natural · %d-%02d %s",
                *periodo, "(DRY-RUN, no escribe)" if dry_run else "")
    logger.info("=" * 60)

    client = get_client()
    logger.info("Conexión Supabase OK")

    # Mapeo de vendedores nombre→id (igual que el ETL Excel)
    resp = client.table("dim_vendedor").select("id, nombre_canonico").execute()
    mapeo_vendedor = agregar_alias(construir_mapeo_vendedor(resp.data or []),
                                   cargar_alias(client))
    fallback_id = _asegurar_vendedor_sin_asignar(client)
    logger.info("Vendedores en dim_vendedor: %d | fallback 'Sin asignar' id=%s",
                len(mapeo_vendedor), fallback_id)

    log_no_mapeados: list = []
    obuma = cargar_obuma_api(periodo, mapeo_vendedor, log_no_mapeados,
                             fallback_vendedor_id=fallback_id)

    fact_ventas = obuma["fact_ventas"]
    if fact_ventas.empty:
        logger.warning("Sin ventas para %d-%02d. Nada que cargar.", *periodo)
        return

    # dim_cliente: solo columnas seguras (NO pisar comuna/tipo del Excel con
    # códigos/nulos de la API). region ya viene como nombre.
    dim_cliente = (obuma["dim_cliente"]
                   .rename(columns={"cliente_rut": "rut"})
                   [["rut", "razon_social", "region", "sociedad_id", "es_maquina"]])

    # Máquinas: derivar de las líneas FL-x de Obuma (gestionada/cambio/retiro) y
    # cruzar el estado de entrega con los despachos del mes que ya estén en
    # Supabase. Si junio aún no tiene despachos (vienen por Autoventa), quedan
    # 'gestionada' y se completan al re-correr tras cargar despachos.
    fact_maquinas = derivar_maquinas_obuma(fact_ventas)
    fact_maquinas = aplicar_estado_despachos(
        fact_maquinas, _leer_despachos_periodo(client, periodo))
    fact_maquinas = aplicar_override_vendedor(
        fact_maquinas, _leer_overrides_maquina(client))

    if dry_run:
        logger.info("\n-- DRY-RUN: resumen sin escribir --")
        logger.info("  fact_ventas:  %d filas | Fact-NC neto = %.0f",
                    len(fact_ventas), fact_ventas["neto"].astype(float).sum())
        logger.info("  dim_cliente:  %d | dim_producto: %d",
                    len(dim_cliente), len(obuma["dim_producto"]))
        logger.info("  fact_maquinas: %d movimientos", len(fact_maquinas))
        logger.info("  stats: %s", obuma["stats"])
        if log_no_mapeados:
            unicos = sorted({r["nombre_original"] for r in log_no_mapeados})
            logger.warning("  Vendedores no mapeados (%d): %s", len(unicos), unicos)
        logger.info("DRY-RUN completado. No se escribió en Supabase.")
        return

    logger.info("\n-- Upserts a Supabase --")
    upsert_tabla(client, "dim_cliente", dim_cliente, on_conflict="rut")
    upsert_tabla(client, "dim_producto", obuma["dim_producto"], on_conflict="codigo")
    upsert_tabla(client, "fact_ventas", fact_ventas,
                 on_conflict="sociedad_id,tipo_dcto,n_dcto,producto_codigo,linea")
    if not fact_maquinas.empty:
        upsert_tabla(client, "fact_maquinas", fact_maquinas,
                     on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")

    if log_no_mapeados:
        unicos = sorted({r["nombre_original"] for r in log_no_mapeados})
        logger.warning("Vendedores no mapeados (%d únicos): %s", len(unicos), unicos)

    fin = datetime.now()
    logger.info("\n%s", "=" * 60)
    logger.info("Carga API completada en %.1f s | stats: %s",
                (fin - inicio).total_seconds(), obuma["stats"])
    logger.info("%s", "=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Carga ventas Obuma Gran Natural vía API REST.")
    ap.add_argument("--periodo", required=True, metavar="AAAA-MM",
                    help="Mes a cargar (ej. 2026-06).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Valida y muestra el resumen sin escribir en Supabase.")
    args = ap.parse_args()
    run(_parse_periodo(args.periodo), dry_run=args.dry_run)
