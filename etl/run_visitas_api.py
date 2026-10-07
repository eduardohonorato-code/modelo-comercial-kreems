"""
Carga de visitas de los vendedores desde la API de Autoventa (GPS).

Endpoint v2 `visit-reports/visits-details`: una fila por check-in del vendedor
en un cliente, con o sin pedido. Se guarda crudo en `fact_visitas` (sql/045) y
el Panel Gerencia deriva de ahí las visitas del indicador Cobertura de ruta.

Uso:
    python -m etl.run_visitas_api --periodo 2026-10
    python -m etl.run_visitas_api --periodo 2026-10 --dry-run

Idempotente: upsert por id (hash de fecha-hora, vendedor, cliente y pedido).
Fail-soft si la tabla aún no existe: avisa en el log y no cuenta como error,
para que la carga diaria no quede en rojo antes de correr el SQL.

El vendedor se resuelve igual que en los pedidos: nombre de Autoventa →
dim_vendedor (+ alias), y después la reasignación por fecha (Joaquín visita
con la cuenta de Diego, Francisco con la de Maicol desde sep-2026).
"""
import argparse
import calendar
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from etl.db import get_client, cargar_alias, cargar_reasignaciones
from etl.cleaners import (construir_mapeo_vendedor, agregar_alias,
                          aplicar_reasignacion, mapear_vendedor_id, normalizar_rut)
from etl.upsert import upsert_tabla

logger = logging.getLogger(__name__)

_URL = ("https://api.autoventa.io/api/2/companies/{empresa}/"
        "visit-reports/visits-details")
_LIMITE = 100          # máximo que acepta la API por página


def _descargar(desde: date, hasta: date) -> list[dict]:
    """Todas las visitas entre dos fechas (ambas inclusive), paginando."""
    key = os.environ["AUTOVENTA_API_KEY_ADMIN"]
    url = _URL.format(empresa=os.environ.get("AUTOVENTA_EMPRESA_ID", "548"))
    params = {"from": desde.strftime("%d/%m/%Y"), "to": hasta.strftime("%d/%m/%Y"),
              "limit": _LIMITE}
    filas, pagina = [], 1
    while True:
        q = urllib.parse.urlencode({**params, "page": pagina})
        req = urllib.request.Request(f"{url}?{q}", headers={
            "api-key": key, "Accept": "application/json"})
        for intento in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError):
                if intento == 2:
                    raise
                time.sleep(5 * (intento + 1))
        items = j.get("items", []) if isinstance(j, dict) else (j or [])
        filas += items
        if len(items) < _LIMITE:
            return filas
        pagina += 1


def _id(row) -> str:
    base = "|".join(str(row.get(k) or "") for k in
                    ("date", "salesman_id", "client_id", "request_id"))
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def transformar(filas: list[dict], client) -> pd.DataFrame:
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame(filas)
    out = pd.DataFrame({
        "id": [_id(r) for r in filas],
        # "2026-07-31T20:50:51-0400": el día local va en los 10 primeros caracteres
        "fecha": df["date"].astype(str).str[:10],
        "fecha_hora": pd.to_datetime(df["date"], utc=True, errors="coerce")
                        .dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "salesman_code": df.get("salesman_code"),
        "salesman_name": df.get("salesman_name"),
        "cliente_rut": normalizar_rut(df["client_rut"]),
        "client_id": pd.to_numeric(df.get("client_id"), errors="coerce").astype("Int64"),
        "request_id": df.get("request_id"),
        "comentario": df.get("comments"),
    })
    out["request_id"] = out["request_id"].where(
        out["request_id"].notna() & (out["request_id"].astype(str) != "None"), None)
    out["con_pedido"] = out["request_id"].notna()

    resp = client.table("dim_vendedor").select("id,nombre_canonico").execute()
    mapeo = agregar_alias(construir_mapeo_vendedor(resp.data or []), cargar_alias(client))
    no_map: list = []
    out["vendedor_id"] = mapear_vendedor_id(out["salesman_name"], mapeo, no_map,
                                            fuente="autoventa_visitas")
    if no_map:
        nombres = sorted({x["nombre_original"] for x in no_map})
        logger.warning("  [visitas] %d visitas de usuarios sin vendedor: %s",
                       len(no_map), nombres)
    out = aplicar_reasignacion(out, cargar_reasignaciones(client), col_fecha="fecha")
    return out.drop_duplicates("id")


def _tabla_existe(client) -> bool:
    try:
        client.table("fact_visitas").select("id").limit(1).execute()
        return True
    except Exception as exc:
        txt = str(exc)
        if "42P01" in txt or "PGRST205" in txt or "does not exist" in txt:
            return False
        raise


def run(periodo: tuple, dry_run: bool = False) -> int:
    anio, mes = periodo
    desde = date(anio, mes, 1)
    hasta = min(date(anio, mes, calendar.monthrange(anio, mes)[1]), date.today())
    if hasta < desde:
        return 0
    client = get_client()
    if not dry_run and not _tabla_existe(client):
        logger.warning("  [visitas] La tabla fact_visitas no existe: correr "
                       "sql/045_fact_visitas.sql en Supabase. Se omite la carga.")
        return 0

    logger.info("=" * 60)
    logger.info("VISITAS Autoventa %d-%02d (%s a %s)", anio, mes, desde, hasta)
    filas = _descargar(desde, hasta)
    df = transformar(filas, client)
    logger.info("  [visitas] %d check-ins descargados · %d vendedores · %d con pedido",
                len(df), df["vendedor_id"].nunique() if len(df) else 0,
                int(df["con_pedido"].sum()) if len(df) else 0)
    if dry_run or df.empty:
        return len(df)
    n = upsert_tabla(client, "fact_visitas", df, on_conflict="id")
    logger.info("  [visitas] upsert OK: %d filas", n)
    return n


def _parse_periodo(valor: str) -> tuple:
    a, m = valor.split("-")
    return int(a), int(m)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--periodo", required=True, help="AAAA-MM")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(_parse_periodo(args.periodo), dry_run=args.dry_run)
