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
import calendar
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from etl.db import get_client, cargar_alias
from etl.cleaners import construir_mapeo_vendedor, agregar_alias
from etl.config import SOCIEDAD_ID
from etl.upsert import upsert_tabla
from etl.loaders.autoventa_api import cargar_autoventa_api
from etl.maquinas import reatribuir_vendedor_autoventa, aplicar_override_vendedor

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


def _mapa_vendedor_doc_obuma(client, periodo: tuple, fallback_id: int) -> dict:
    """
    Mapa folio (n_dcto, str) → vendedor_id del DOCUMENTO en Obuma para el período,
    sociedad Gran Natural. Se usa para reatribuir los pedidos facturados de
    Autoventa al vendedor del DTE (ver memoria 'atribucion-vendedor-linea-doc').

    Obuma atribuye el vendedor por documento, así que normalmente es uniforme por
    n_dcto; por robustez se toma el vendedor con MAYOR monto del documento.
    Requiere que Obuma ya esté cargado del período (run_diario corre Obuma antes).
    """
    anio, mes = periodo
    desde = f"{anio}-{mes:02d}-01"
    hasta = f"{anio}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}"
    soc = SOCIEDAD_ID["grannatural"]
    filas, off = [], 0
    while True:
        r = (client.table("fact_ventas")
             .select("n_dcto,vendedor_id,neto")
             .eq("sociedad_id", soc)
             .gte("fecha", desde).lte("fecha", hasta)
             .order("id").range(off, off + 999).execute())
        filas += r.data or []
        if not r.data or len(r.data) < 1000:
            break
        off += 1000
    if not filas:
        return {}
    d = pd.DataFrame(filas)
    d["neto"] = d["neto"].astype(float)
    d["n_dcto"] = d["n_dcto"].astype(str).str.strip()
    # vendedor del documento = el de mayor monto sumado dentro del n_dcto
    g = (d.groupby(["n_dcto", "vendedor_id"])["neto"].sum().reset_index()
         .sort_values("neto").drop_duplicates("n_dcto", keep="last"))
    return dict(zip(g["n_dcto"], g["vendedor_id"].astype(int)))


def _leer_maquinas_periodo(client, periodo: tuple) -> pd.DataFrame:
    """
    Lee fact_maquinas de Gran Natural del período (ya cargada por Obuma en
    run_diario) para reatribuir su vendedor al de Autoventa.
    """
    anio, mes = periodo
    desde = f"{anio}-{mes:02d}-01"
    hasta = f"{anio}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}"
    cols = "documento,fecha,vendedor_id,cliente_rut,tipo_mov,estado,sociedad_id"
    rows, off = [], 0
    while True:
        b = (client.table("fact_maquinas").select(cols)
             .eq("sociedad_id", SOCIEDAD_ID["grannatural"])
             .gte("fecha", desde).lte("fecha", hasta)
             .order("documento").range(off, off + 999).execute().data)
        rows += b or []
        if not b or len(b) < 1000:
            break
        off += 1000
    return pd.DataFrame(rows)


def _leer_overrides_maquina(client) -> pd.DataFrame:
    """Lee maquina_vendedor_override (corrección manual de gerencia; vacío si no existe)."""
    vacio = pd.DataFrame(columns=["sociedad_id", "documento", "vendedor_id"])
    try:
        r = (client.table("maquina_vendedor_override")
             .select("sociedad_id,documento,vendedor_id").range(0, 9999).execute())
        return pd.DataFrame(r.data) if r.data else vacio
    except Exception:
        return vacio


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
    mapeo_vendedor = agregar_alias(construir_mapeo_vendedor(resp.data or []),
                                   cargar_alias(client))
    fallback_id = _asegurar_vendedor_sin_asignar(client)
    logger.info("Vendedores en dim_vendedor: %d | fallback 'Sin asignar' id=%s",
                len(mapeo_vendedor), fallback_id)

    # Mapa folio→vendedor del DTE de Obuma para reatribuir los pedidos facturados
    # (Obuma debe estar cargado del período; en run_diario corre antes que esto).
    vendedor_doc = _mapa_vendedor_doc_obuma(client, periodo, fallback_id)
    logger.info("Mapa folio→vendedor (Obuma) del período: %d documentos", len(vendedor_doc))

    log_no_mapeados: list = []
    av = cargar_autoventa_api(periodo, mapeo_vendedor, log_no_mapeados,
                              fallback_vendedor_id=fallback_id,
                              vendedor_doc_obuma=vendedor_doc)

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

    # ── Reatribuir el vendedor de las MÁQUINAS GN al de Autoventa ───────────
    # Las máquinas se derivan de Obuma (cubre ambas sociedades y todos los FL),
    # pero Obuma suele dejar estos documentos 'Sin asignar'. Autoventa sí sabe
    # quién gestionó la máquina en terreno (ej. Tomás). Aquí corregimos SOLO el
    # vendedor de las máquinas del período usando las líneas FL de Autoventa; el
    # override manual de gerencia se re-aplica al final para que siga ganando.
    fm = _leer_maquinas_periodo(client, periodo)
    if not fm.empty:
        fm2 = reatribuir_vendedor_autoventa(
            fm, av.get("_vendedor_fl_folio") or {}, fallback_id=fallback_id)
        fm2 = aplicar_override_vendedor(fm2, _leer_overrides_maquina(client))
        if not fm2["vendedor_id"].astype("Int64").equals(fm["vendedor_id"].astype("Int64")):
            upsert_tabla(client, "fact_maquinas", fm2,
                         on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")
            logger.info("  fact_maquinas GN: vendedor actualizado desde Autoventa.")
        else:
            logger.info("  fact_maquinas GN: sin cambios de vendedor.")

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
