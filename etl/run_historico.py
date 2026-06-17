"""
ETL de CARGA HISTÓRICA — Kreems Sistema Comercial.

Carga el histórico completo a Supabase ANTES de que el sistema entre en
operación. Reutiliza el pipeline idempotente del ETL mensual (loaders, cleaners
y upsert). Solo añade lo necesario para:
  · leer VARIOS archivos por fuente (un .xls por mes / sociedad),
  · derivar las máquinas desde Obuma (categoría 'Maquinas', códigos FL-x),
  · reportar por sociedad y por mes.

NO toca el ETL mensual (`run_etl.py`) ni `/data/muestras`.

Estructura esperada (solo lectura):
    data/mensual/
    ├── acuña/            obuma_ventas_acuña_2026_01..05.xls       (ene→hoy)
    ├── gran_natural/     obuma_ventas_grannatural_2026_02..05.xls (feb→hoy)
    └── autoventa/        pedidos_detalle_productos_<mes>.csv      (feb→hoy)
                          detalle_despachos_<mes>.xlsx             (feb→hoy)

Uso:
    python -m etl.run_historico
    python etl/run_historico.py

Idempotencia: re-ejecutable sin duplicar (upsert por llave natural). Cargar un
mes, varios o todo el histórico da el mismo estado final.
"""
import logging
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Permite ejecutar como script directo además de como módulo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

from etl.db import get_client
from etl.config import SOCIEDAD_ID
from etl.cleaners import construir_mapeo_vendedor
from etl.upsert import upsert_tabla
from etl.maquinas import derivar_maquinas_obuma, aplicar_estado_despachos
from etl.loaders.obuma import cargar_obuma_multi
from etl.loaders.autoventa import cargar_autoventa

# -- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
        logging.FileHandler(
            Path(__file__).parent.parent / "etl_historico.log", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_HIST = ROOT / "data" / "mensual"

# Meses en español → número (acepta nombre completo y abreviatura)
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}


# -- Helpers de descubrimiento -------------------------------------------------

def _norm(texto: str) -> str:
    """minúsculas sin acentos."""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _mes_desde_token(nombre: str) -> int | None:
    """
    Detecta el número de mes desde el nombre de un archivo Autoventa.
    Acepta dos convenciones:
      · numérica  'AAAA_MM' / 'AAAA-MM'  (estándar recomendado, escalable)
      · palabra   'feb' / 'febrero'      (compatibilidad con archivos antiguos)
    """
    n = _norm(nombre)
    m = re.search(r"(20\d{2})[_\-]?(0[1-9]|1[0-2])(?!\d)", n)
    if m:
        return int(m.group(2))
    # Palabra del mes (probar nombres largos antes que abreviaturas: febrero/feb)
    for token in sorted(MESES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){token}(?![a-z])", n):
            return MESES[token]
    return None


def _descubrir_obuma(filtro: tuple | None = None) -> list[tuple[Path, str]]:
    """
    Lista (path, soc_key) para los .xls de Acuña y Gran Natural.
    Si `filtro=(año, mes)`, solo incluye los archivos cuyo nombre contiene
    'AAAA_MM' (formato de los exports Obuma).
    """
    token = f"{filtro[0]}_{filtro[1]:02d}" if filtro else None
    archivos: list[tuple[Path, str]] = []
    for carpeta, soc_key in [("acuña", "acuna"), ("gran_natural", "grannatural")]:
        # 'acuña' puede estar como 'acuna' según el sistema de archivos
        for nombre_dir in {carpeta, _norm(carpeta)}:
            d = DATA_HIST / nombre_dir
            if d.is_dir():
                for p in sorted(d.glob("*.xls")):
                    if token is None or token in p.stem:
                        archivos.append((p, soc_key))
                break
    return archivos


def _descubrir_autoventa(filtro: tuple | None = None) -> list[tuple[int, Path, Path]]:
    """
    Empareja por número de mes: (mes, pedidos_detalle.csv, detalle_despachos.xlsx).
    Solo usa el DETALLE de productos (CSV ';'), no los .xlsx resumen.
    Si `filtro=(año, mes)`, solo incluye ese mes.
    """
    mes_filtro = filtro[1] if filtro else None
    d = DATA_HIST / "autoventa"
    pedidos = {}
    for p in sorted(d.glob("pedidos_detalle_productos*.csv")):
        m = _mes_desde_token(p.stem)
        if m and (mes_filtro is None or m == mes_filtro):
            pedidos[m] = p
    despachos = {}
    for p in sorted(d.glob("detalle_despachos*.xlsx")):
        m = _mes_desde_token(p.stem)
        if m and (mes_filtro is None or m == mes_filtro):
            despachos[m] = p

    pares = []
    for mes in sorted(set(pedidos) | set(despachos)):
        pp, dp = pedidos.get(mes), despachos.get(mes)
        if pp is None or dp is None:
            logger.warning("  Autoventa mes %02d incompleto (pedidos=%s, despachos=%s) — se omite",
                           mes, pp.name if pp else None, dp.name if dp else None)
            continue
        pares.append((mes, pp, dp))
    return pares


def _asegurar_vendedor_sin_asignar(client) -> int:
    resp = (client.table("dim_vendedor")
            .select("id").eq("nombre_canonico", "Sin asignar").execute())
    if resp.data:
        return resp.data[0]["id"]
    ins = (client.table("dim_vendedor")
           .insert({"nombre_canonico": "Sin asignar", "activo": True}).execute())
    return ins.data[0]["id"]


def _guardar_log_no_mapeados(log: list):
    if not log:
        logger.info("Sin vendedores no mapeados en esta carga. ✓")
        return
    import csv
    ruta = ROOT / "etl_historico_no_mapeados.csv"
    with ruta.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["fuente", "nombre_original"])
        writer.writeheader()
        writer.writerows(log)
    unicos = {r["nombre_original"] for r in log}
    logger.warning("Vendedores no mapeados (%d únicos): %s\n  → Detalle en %s",
                   len(unicos), sorted(unicos), ruta.name)


# -- Reporte -------------------------------------------------------------------

def _reporte_por_sociedad_mes(fact_ventas, fact_despachos) -> list[dict]:
    """Devuelve (y loguea) la tabla N° docs / líneas / % match por sociedad y mes."""
    logger.info("\n%s\n REPORTE POR SOCIEDAD Y MES\n%s", "=" * 60, "=" * 60)
    fv = fact_ventas.copy()
    fv["mes"] = fv["fecha"].astype(str).str[:7]
    des = fact_despachos.copy()
    if not des.empty:
        des["mes"] = des["fecha_ruta"].astype(str).str[:7]

    soc_nombre = {1: "Acuña", 2: "Gran Natural"}
    filas = []
    for soc_id in sorted(fv["sociedad_id"].dropna().unique()):
        sub = fv[fv["sociedad_id"] == soc_id]
        logger.info("\n[%s]  %-7s %-10s %-10s %s",
                    soc_nombre.get(soc_id, soc_id),
                    "mes", "n_docs", "n_lineas", "% match Obuma↔despachos")
        for mes in sorted(sub["mes"].dropna().unique()):
            s = sub[sub["mes"] == mes]
            n_docs = s["n_dcto"].astype(str).nunique()
            n_lineas = len(s)
            ndcto = set(s["n_dcto"].astype(str).str.strip())
            d_mes = (set(des[des["mes"] == mes]["documento"].astype(str).str.strip())
                     if not des.empty else set())
            inter = ndcto & d_mes
            pct = round(100 * len(inter) / len(d_mes), 1) if d_mes else None
            pct_txt = f"{pct:.0f}% ({len(inter)}/{len(d_mes)} despachos)" if pct is not None else "—"
            logger.info("        %-7s %-10d %-10d %s", mes, n_docs, n_lineas, pct_txt)
            filas.append({
                "sociedad": soc_nombre.get(soc_id, str(soc_id)),
                "mes": mes, "n_docs": n_docs, "n_lineas": n_lineas,
                "despachos": len(d_mes), "match": len(inter),
                "pct_match": pct,
            })
    return filas


# -- Núcleo reutilizable (CLI + webapp) ----------------------------------------

def procesar_carga(client, obuma_files: list[tuple[Path, str]],
                   av_pares: list[tuple[int, Path, Path]],
                   mapeo_vendedor: dict, fallback_id: int) -> dict:
    """
    Núcleo del ETL: recibe los archivos ya localizados (en disco o en un temp),
    los carga y hace upsert idempotente a Supabase. Lo usan tanto la línea de
    comandos (`run`) como la página de carga de la webapp, para que el resultado
    sea idéntico desde cualquier punto de entrada.

    Returns: dict con conteos, reporte por sociedad/mes y vendedores no mapeados.
    """
    log_no_mapeados: list = []

    # Obuma → ventas + dims. Puede venir vacío (p.ej. carga web de solo despachos:
    # Gran Natural ya entró por API). En ese caso se omite el bloque Obuma.
    if obuma_files:
        obuma = cargar_obuma_multi(obuma_files, mapeo_vendedor, log_no_mapeados,
                                   periodo=None, fallback_vendedor_id=fallback_id)
    else:
        logger.info("  Sin archivos Obuma en esta carga — se omite (ventas/máquinas "
                    "no se tocan; el estado de máquinas se sincroniza con despachos).")
        obuma = {
            "dim_cliente":  pd.DataFrame(columns=["cliente_rut", "razon_social",
                                                  "comuna", "region", "tipo",
                                                  "sociedad_id", "es_maquina"]),
            "dim_producto": pd.DataFrame(columns=["codigo"]),
            "fact_ventas":  pd.DataFrame(columns=["fecha", "tipo_dcto", "n_dcto",
                                                  "producto_codigo", "cliente_rut",
                                                  "sociedad_id", "vendedor_id"]),
            "stats":        {"obuma_filas_raw": 0},
        }
    fact_ventas = obuma["fact_ventas"]

    # Máquinas (fuente única: Obuma)
    fact_maquinas = derivar_maquinas_obuma(fact_ventas)

    # Autoventa → pedidos + despachos
    pedidos_parts, despachos_parts, dimcli_av_parts = [], [], []
    for mes, pp, dp in av_pares:
        logger.info("  Autoventa mes %02d: pedidos=%s | despachos=%s", mes,
                    pp.name if pp else "—", dp.name if dp else "—")
        av = cargar_autoventa(pp, dp, mapeo_vendedor, log_no_mapeados,
                              fallback_vendedor_id=fallback_id)
        pedidos_parts.append(av["fact_pedidos"])
        despachos_parts.append(av["fact_despachos"])
        dimcli_av_parts.append(av["dim_cliente"])

    fact_pedidos   = (pd.concat(pedidos_parts, ignore_index=True)
                      .drop_duplicates(subset=["sociedad_id", "n_pedido", "producto_codigo", "linea"])
                      if pedidos_parts else pd.DataFrame())
    fact_despachos = (pd.concat(despachos_parts, ignore_index=True)
                      .drop_duplicates(subset=["sociedad_id", "documento", "cliente_rut"])
                      if despachos_parts else pd.DataFrame())
    dim_cliente_av = (pd.concat(dimcli_av_parts, ignore_index=True)
                      .drop_duplicates(subset=["cliente_rut"])
                      if dimcli_av_parts else pd.DataFrame(columns=["cliente_rut"]))

    # Estado de máquinas según despachos
    fact_maquinas = aplicar_estado_despachos(fact_maquinas, fact_despachos)

    # dim_cliente: Obuma (prioridad) + Autoventa; marcar es_maquina
    dim_cliente = (
        obuma["dim_cliente"].set_index("cliente_rut")
        .combine_first(dim_cliente_av.set_index("cliente_rut"))
        .reset_index()
        .rename(columns={"cliente_rut": "rut"})
    )
    ruts_maquina = set(fact_maquinas["cliente_rut"].dropna().astype(str))
    if ruts_maquina:
        dim_cliente["es_maquina"] = dim_cliente["rut"].astype(str).isin(ruts_maquina)

    # Upserts idempotentes
    logger.info("\n-- Upserts a Supabase --")
    if not dim_cliente.empty:
        upsert_tabla(client, "dim_cliente", dim_cliente, on_conflict="rut")
    if not obuma["dim_producto"].empty:
        upsert_tabla(client, "dim_producto", obuma["dim_producto"], on_conflict="codigo")
    if not fact_ventas.empty:
        upsert_tabla(client, "fact_ventas", fact_ventas,
                     on_conflict="sociedad_id,tipo_dcto,n_dcto,producto_codigo,linea")
    if not fact_pedidos.empty:
        upsert_tabla(client, "fact_pedidos", fact_pedidos,
                     on_conflict="sociedad_id,n_pedido,producto_codigo,linea")
    if not fact_despachos.empty:
        upsert_tabla(client, "fact_despachos", fact_despachos,
                     on_conflict="sociedad_id,documento,cliente_rut")
    if not fact_maquinas.empty:
        upsert_tabla(client, "fact_maquinas", fact_maquinas,
                     on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")

    _guardar_log_no_mapeados(log_no_mapeados)
    reporte_sm = _reporte_por_sociedad_mes(fact_ventas, fact_despachos)

    return {
        "ok": True,
        "obuma_stats": obuma["stats"],
        "conteos": {
            "fact_ventas": len(fact_ventas),
            "fact_pedidos": len(fact_pedidos),
            "fact_despachos": len(fact_despachos),
            "fact_maquinas": len(fact_maquinas),
            "dim_cliente": len(dim_cliente),
            "dim_producto": len(obuma["dim_producto"]),
        },
        "por_sociedad_mes": reporte_sm,
        "no_mapeados": sorted({r["nombre_original"] for r in log_no_mapeados}),
    }


def run(mes: tuple | None = None):
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("ETL HISTÓRICO Kreems — inicio %s", inicio.isoformat())
    logger.info("Carpeta: %s", DATA_HIST)
    if mes:
        logger.info("Filtro: solo mes %d-%02d", *mes)
    else:
        logger.info("Filtro: ninguno — se procesan todos los archivos de la carpeta")
    logger.info("=" * 60)

    if not DATA_HIST.is_dir():
        logger.error("No existe %s. Abortando.", DATA_HIST)
        sys.exit(1)

    client = get_client()
    logger.info("Conexión Supabase OK")

    obuma_files = _descubrir_obuma(mes)
    av_pares    = _descubrir_autoventa(mes)
    if not obuma_files:
        logger.error("No se encontraron archivos Obuma en %s. Abortando.", DATA_HIST)
        sys.exit(1)
    logger.info("Obuma: %d archivo/s — %s", len(obuma_files),
                [p.name for p, _ in obuma_files])
    logger.info("Autoventa: %d mes/es — %s", len(av_pares),
                [(m, pp.name, dp.name) for m, pp, dp in av_pares])

    resp = client.table("dim_vendedor").select("id, nombre_canonico").execute()
    mapeo_vendedor = construir_mapeo_vendedor(resp.data or [])
    fallback_id = _asegurar_vendedor_sin_asignar(client)
    logger.info("dim_vendedor: %d vendedores | 'Sin asignar' id=%s",
                len(mapeo_vendedor), fallback_id)

    rep = procesar_carga(client, obuma_files, av_pares, mapeo_vendedor, fallback_id)

    fin = datetime.now()
    logger.info("\n%s", "=" * 60)
    logger.info("ETL histórico completado en %.1f s", (fin - inicio).total_seconds())
    logger.info("Conteos: %s", rep["conteos"])
    logger.info("%s", "=" * 60)


def _parse_mes(valor: str | None) -> tuple | None:
    """'2026-05' → (2026, 5). None si no se pasa."""
    if not valor:
        return None
    try:
        anio, mes = valor.split("-")
        return (int(anio), int(mes))
    except (ValueError, AttributeError):
        raise SystemExit(f"--mes inválido: '{valor}'. Formato esperado AAAA-MM (ej. 2026-05).")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Carga a Supabase desde data/mensual/ (idempotente). "
                    "Sin --mes procesa todos los archivos; con --mes solo ese período.")
    ap.add_argument("--mes", metavar="AAAA-MM",
                    help="Procesar solo el mes indicado (ej. 2026-05). Útil para la "
                         "actualización semanal del mes en curso.")
    args = ap.parse_args()
    run(_parse_mes(args.mes))
