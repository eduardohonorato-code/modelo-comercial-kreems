"""
Loader Autoventa vía API REST (Fase 4) — pedidos (fact_pedidos).

Reemplaza el CSV "Pedidos detalle productos" consumiendo directamente la API de
Autoventa. Produce los mismos DataFrames que la parte de pedidos de
`etl/loaders/autoventa.py`, con la misma llave natural
(sociedad_id, n_pedido, producto_codigo, linea).

Conexión (ver memoria 'autoventa-api-estructura'):
  - Base: https://api.autoventa.io/api/1/companies/{AUTOVENTA_EMPRESA_ID}
  - Header de autenticación: `api-key: <AUTOVENTA_API_KEY_ADMIN>` (literal).
  - Los expand[] son grupos de serialización Symfony ANIDADOS: para ver el
    contenido de una relación hay que sumar los grupos del hijo (sin ellos las
    relaciones llegan como objetos vacíos {}).
  - `/invoices` SOLO filtra por `created_at` (día exacto) → se recorre día a día.
  - `/requests` filtra por `dispatch_date` (día exacto).
  - `price` de una línea es el TOTAL de la línea (no unitario): qty=2,
    price=53.720 = net_amount.

NOTA semántica: el estado de entrega de despachos (Entregada/Rechazada) NO está
expuesto por esta API (vive en DispatchInvoice, solo PATCH). fact_despachos sigue
viniendo del Excel hasta resolverlo con IT.
"""
import os
import json
import logging
import calendar
import urllib.request
import urllib.error
from datetime import date, timedelta

import pandas as pd

from etl.cleaners import normalizar_rut, mapear_vendedor_id
from etl.config import SOCIEDAD_ID

logger = logging.getLogger(__name__)

API_BASE = os.environ.get(
    "AUTOVENTA_API_BASE",
    "https://api.autoventa.io/api/1/companies/" + os.environ.get("AUTOVENTA_EMPRESA_ID", "548"),
)

# Pedidos Autoventa = Gran Natural (igual que el loader Excel)
SOCIEDAD_AUTOVENTA = SOCIEDAD_ID["grannatural"]

# Expands para que /invoices traiga cliente y líneas con contenido
_EXPANDS_INVOICES = "&".join(
    f"expand[]={g}" for g in
    ["invoice_detail", "r_invoice_client", "client_detail",
     "r_invoice_lines", "line_detail"]
)


def _headers() -> dict:
    key = os.environ.get("AUTOVENTA_API_KEY_ADMIN", "").strip()
    if not key:
        raise RuntimeError("AUTOVENTA_API_KEY_ADMIN no está definida en el entorno (.env).")
    return {"api-key": key, "Accept": "application/json"}


def _get(path: str, timeout: int = 90):
    url = API_BASE + path
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} en {path}: {e.read()[:200]!r}")


def _dias(anio: int, mes: int, margen_previo: int = 3) -> list[str]:
    """Días del mes en ISO + un margen previo (facturas creadas antes del 1°
    cuyo invoice_date cae dentro del mes)."""
    ini = date(anio, mes, 1) - timedelta(days=margen_previo)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    out, d = [], ini
    while d <= fin:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def cargar_autoventa_api(
    periodo: tuple,
    mapeo_vendedor: dict,
    log_no_mapeados: list,
    fallback_vendedor_id: int | None = None,
    vendedor_doc_obuma: dict | None = None,
) -> dict:
    """
    Carga los pedidos del período (año, mes) desde la API de Autoventa.

    `vendedor_doc_obuma`: mapa folio (num_documento, str) → vendedor_id del
    DOCUMENTO en Obuma. Si se entrega, los pedidos FACTURADOS se reatribuyen al
    vendedor del DTE de Obuma (fuente de verdad de a quién pertenece la venta
    facturada), en vez del vendedor por-línea de Autoventa. Así Ped.Fact cuadra
    con Fact-NC por vendedor. Ver memoria 'atribucion-vendedor-linea-doc'.

    Returns (espejo de la parte pedidos de cargar_autoventa):
      {fact_pedidos, dim_cliente, stats, _docs_facturados}
    """
    anio, mes = periodo
    mes_prefijo = f"{anio}-{mes:02d}"
    logger.info("  [AV-API] Pedidos Gran Natural %s", mes_prefijo)

    # ── Mapas de apoyo (una llamada cada uno) ───────────────────────────────
    clientes = {c["id"]: c for c in (_get("/clients") or [])}
    logger.info("  [AV-API] mapa clientes=%d", len(clientes))

    # ── 1. Facturas y NC del mes (día a día por created_at) ─────────────────
    invoices = []
    for dia in _dias(anio, mes):
        lote = _get(f"/invoices?created_at={dia}&{_EXPANDS_INVOICES}") or []
        invoices.extend(lote)
    # filtrar a invoice_date dentro del mes y no anuladas
    invoices = [
        i for i in invoices
        if str(i.get("invoice_date") or "")[:7] == mes_prefijo and not i.get("voided")
    ]
    logger.info("  [AV-API] facturas/NC del mes: %d", len(invoices))

    # doc_type observados (mayo 2026): 'invoice' (factura, sales_document_id 33)
    # y 'no_dte' (pedido despachado SIN documento → "Sin DTE" del Excel).
    # NO existe doc_type de nota de crédito en esta API: las NC se emiten en
    # Obuma. Por eso neto_nc queda en 0 aquí (gap conocido y documentado; en el
    # Excel "Neto Nota de Crédito" mayo sumaba solo $29.080).
    filas = []
    tipos_desconocidos: set = set()

    for inv in invoices:
        cli = inv.get("client") or {}
        rut = cli.get("rut")
        fecha = str(inv.get("invoice_date") or "")[:10]
        doc_type = inv.get("doc_type") or ""
        if doc_type == "invoice":
            doc_venta, num_doc = "Factura", str(inv.get("correlative") or "").strip()
        elif doc_type == "no_dte":
            doc_venta, num_doc = "Sin DTE", pd.NA
        else:
            tipos_desconocidos.add(doc_type)
            doc_venta, num_doc = doc_type, str(inv.get("correlative") or "").strip()

        for ln in inv.get("lines") or []:
            filas.append({
                "n_pedido": str(ln.get("request_correlative") or "SIN_PEDIDO"),
                "num_documento": num_doc,
                "doc_venta": doc_venta,
                "fecha": fecha,
                "vendedor_nombre": ln.get("created_by_name") or inv.get("created_by_name"),
                "cliente_rut_raw": rut,
                "producto_codigo": str(ln.get("product_code") or "").strip(),
                "neto": float(ln.get("net_amount") or 0),
                "neto_nc": 0.0,
            })

    if tipos_desconocidos:
        logger.warning("  [AV-API] doc_type no reconocidos (revisar): %s", tipos_desconocidos)

    if not filas:
        logger.warning("  [AV-API] Sin pedidos para %s.", mes_prefijo)
        vacio = pd.DataFrame()
        return {"fact_pedidos": vacio, "dim_cliente": vacio,
                "stats": {"av_api_filas": 0}, "_docs_facturados": set()}

    df = pd.DataFrame(filas)
    sin_dte = int((df["doc_venta"] == "Sin DTE").sum())

    # ── 4. Tipado, vendedor, línea ──────────────────────────────────────────
    df["sociedad_id"] = SOCIEDAD_AUTOVENTA
    df["cliente_rut"] = normalizar_rut(df["cliente_rut_raw"])
    df["vendedor_id"] = mapear_vendedor_id(
        df["vendedor_nombre"], mapeo_vendedor, log_no_mapeados,
        fuente="autoventa_api", fallback_id=fallback_vendedor_id,
    )

    # ── Reatribución al DTE de Obuma (pedidos facturados) ───────────────────
    # Autoventa atribuye el vendedor por línea; Obuma lo atribuye por documento.
    # Para que Ped.Fact cuadre con Fact-NC por vendedor, el vendedor de un pedido
    # FACTURADO debe ser el del documento en Obuma (el DTE es la venta oficial).
    # Solo se sobreescribe cuando el folio cruza un documento de Obuma con un
    # vendedor REAL (si Obuma quedó "Sin asignar", se respeta el de Autoventa,
    # que puede ser mejor; ese hueco se corrige por el lado de Obuma).
    if vendedor_doc_obuma:
        es_fact = df["doc_venta"] != "Sin DTE"
        num = df["num_documento"].astype("string").str.strip()
        nuevo = num.map(vendedor_doc_obuma)
        aplicar = (
            es_fact & nuevo.notna()
            & (nuevo != fallback_vendedor_id)
            & (nuevo != df["vendedor_id"])
        )
        n_reasig = int(aplicar.sum())
        monto_reasig = float(df.loc[aplicar, "neto"].sum())
        df.loc[aplicar, "vendedor_id"] = nuevo[aplicar].astype(int)
        logger.info("  [AV-API] reatribución al DTE de Obuma: %d líneas / $%.0f "
                    "movidos al vendedor del documento", n_reasig, monto_reasig)

    df["linea"] = (
        df.groupby(["sociedad_id", "n_pedido", "producto_codigo"]).cumcount() + 1
    ).astype("Int64")

    fact_pedidos = df[[
        "n_pedido", "num_documento", "doc_venta", "fecha", "vendedor_id",
        "cliente_rut", "producto_codigo", "sociedad_id", "neto", "neto_nc", "linea",
    ]].copy()

    # ── 5. dim_cliente (RUTs nuevos que no vengan de Obuma) ─────────────────
    # nombre legal desde el mapa de clientes de la API
    rut_a_nombre = {}
    for c in clientes.values():
        r = c.get("rut")
        if r:
            rut_a_nombre[r] = c.get("legal_name") or c.get("name")
    dim_cliente = (
        df[["cliente_rut", "cliente_rut_raw", "sociedad_id"]]
        .dropna(subset=["cliente_rut"])
        .drop_duplicates(subset=["cliente_rut"])
        .copy()
    )
    dim_cliente["razon_social"] = dim_cliente["cliente_rut_raw"].map(rut_a_nombre)
    dim_cliente["tipo"] = None
    dim_cliente["es_maquina"] = False
    dim_cliente = dim_cliente.drop(columns=["cliente_rut_raw"])

    docs_facturados = set(
        fact_pedidos.loc[fact_pedidos["doc_venta"] != "Sin DTE", "num_documento"]
        .dropna().astype(str)
    )

    stats = {
        "av_api_filas": len(fact_pedidos),
        "av_api_pedidos": int(fact_pedidos["n_pedido"].nunique()),
        "av_api_facturadas": int((fact_pedidos["doc_venta"] != "Sin DTE").sum()),
        "av_api_sin_dte": sin_dte,
        "av_api_neto": float(fact_pedidos["neto"].sum()),
        "av_api_neto_nc": float(fact_pedidos["neto_nc"].sum()),
    }
    logger.info("  [AV-API] fact_pedidos=%d | pedidos=%d | sin_dte=%d | neto=%.0f | nc=%.0f",
                stats["av_api_filas"], stats["av_api_pedidos"], sin_dte,
                stats["av_api_neto"], stats["av_api_neto_nc"])

    return {
        "fact_pedidos": fact_pedidos,
        "dim_cliente": dim_cliente,
        "stats": stats,
        "_docs_facturados": docs_facturados,
    }
