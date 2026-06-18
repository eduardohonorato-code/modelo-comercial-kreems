"""
Loader Obuma vía API REST (Fase 4) — solo Gran Natural SPA.

Reemplaza el export Excel por consumo directo de la API de Obuma para la
sociedad Gran Natural (empresa_id 2763 en Obuma). Produce EXACTAMENTE los mismos
DataFrames que `etl/loaders/obuma.py` (dim_cliente, dim_producto, fact_ventas),
de modo que el upsert idempotente de `run_etl.py` se reutiliza sin cambios.

Conexión (ver memoria 'obuma-api-conexion-funciona'):
  - Base por clúster de empresa: https://api-g1.obuma.cl/v1.0  (NO api.obuma.cl)
  - Headers: access-token (OBUMA_API_KEY) + access-url (obligatorio, si no -> Error 004)
  - v1.0 solamente (v2.0 no autorizada para Kreems).

Acuña NO se carga por aquí (es otra empresa en Obuma, necesitaría otra API key);
sigue por el loader de Excel.
"""
import os
import json
import logging
import calendar
import urllib.request
import urllib.error
from datetime import date

import pandas as pd

from etl.cleaners import normalizar_rut, mapear_vendedor_id, _normalizar_nombre
from etl.config import SOCIEDAD_ID, TIPO_DCTO_NEGATIVO

logger = logging.getLogger(__name__)

# ── Conexión ──────────────────────────────────────────────────────────────────
API_BASE = os.environ.get("OBUMA_API_BASE", "https://api-g1.obuma.cl/v1.0")
API_ACCESS_URL = os.environ.get("OBUMA_API_ACCESS_URL", "https://api-g1.obuma.cl")
PAGE_SIZE = 1000  # máximo permitido por Obuma

# ── Mapeos de código → texto ──────────────────────────────────────────────────
# Tipo de documento (código SII) → mismo texto que usa el loader Excel, para que
# TIPO_DCTO_NEGATIVO y el resto del pipeline funcionen igual.
TIPO_DCTO_MAP = {
    "33": "FACTURA ELECTRONICA",
    "34": "FACTURA NO AFECTA O EXENTA ELECTRONICA",
    "39": "BOLETA ELECTRONICA",
    "41": "BOLETA EXENTA ELECTRONICA",
    "56": "NOTA DE DEBITO ELECTRONICA",
    "61": "NOTA DE CREDITO ELECTRONICA",
}

# Solo estos tipos cuentan como venta (Fact-NC), igual que el reporte Excel
# "Ventas por Sucursal". Se EXCLUYE el tipo 4 (Nota de Venta, pedido interno que
# aún no es DTE) y el 52 (guía de despacho), que inflarían el total. Validado:
# filtrar a {33,34,61} reproduce exacto el Fact-NC del panel (Gran Natural mayo
# 2026 = $47.554.337, idéntico a la carga Excel).
DTE_VALIDOS = {"33", "34", "61"}

# Código de región (SII/INE) → nombre. La API entrega comuna/region como código;
# el front usa el nombre de región para la geografía, así que lo resolvemos aquí.
REGION_MAP = {
    "1": "Tarapacá", "2": "Antofagasta", "3": "Atacama", "4": "Coquimbo",
    "5": "Valparaíso", "6": "O'Higgins", "7": "Maule", "8": "Biobío",
    "9": "La Araucanía", "10": "Los Lagos", "11": "Aysén", "12": "Magallanes",
    "13": "Metropolitana", "14": "Los Ríos", "15": "Arica y Parinacota",
    "16": "Ñuble",
}

# Sucursales de Obuma: la API entrega rel_sucursal_id (número interno). Se mapea
# al MISMO nombre que trae el export Excel (mayúsculas, sin acento) para que el
# análisis agrupe ambas fuentes en la misma sucursal. IDs identificados por la
# región dominante de sus clientes (Gran Natural).
SUCURSAL_MAP = {
    "257": "CONCEPCION",
    "266": "SANTIAGO",
    "267": "TEMUCO",
}


# ── Cliente HTTP ──────────────────────────────────────────────────────────────

def _headers() -> dict:
    key = os.environ.get("OBUMA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OBUMA_API_KEY no está definida en el entorno (.env).")
    return {
        "access-token": key,
        "access-url": API_ACCESS_URL,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _get(path: str) -> dict:
    """
    GET a un endpoint de la API; devuelve el JSON ya parseado.

    OJO: Obuma responde HTTP 200 SIEMPRE, incluso en error, y además incluye un
    campo `result: {result:"0", result_detail:"10-4"}` que aparece TANTO en éxito
    como en fallo (no es un código de error fiable). Por eso la detección de error
    se basa en:
      - HTTP != 200,
      - body que no es JSON con clave 'data' (los errores reales llegan como
        texto plano tipo "Error 004... Error de autenticacion." / "Error 101...").
    """
    url = API_BASE + path
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} en {path}: {e.read()[:200]!r}")
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"Respuesta no-JSON en {path}: {body[:150]!r}")
    if not isinstance(j, dict) or "data" not in j:
        raise RuntimeError(f"Respuesta inesperada en {path}: {body[:150]!r}")
    return j


def _get_paginado(metodo: str, filtros: str = "") -> list[dict]:
    """
    Trae TODAS las filas de un método `<recurso>.<metodo>.json` paginando de a
    PAGE_SIZE. `metodo` es el nombre completo antes de `.json`, ej. 'ventas.list'
    o 'ventas.listItems'. `filtros` son parámetros extra de query.
    """
    filas: list[dict] = []
    page = 1
    total_items = None
    while True:
        q = f"?limit={PAGE_SIZE}&page={page}"
        if filtros:
            q += "&" + filtros
        j = _get(f"/{metodo}.json/{q}")
        data = j.get("data") or []
        filas.extend(data)
        if total_items is None:
            total_items = int(j.get("data-total-items") or 0)
        logger.info("    %s pág %d (+%d filas; acum %d/%d)",
                    metodo, page, len(data), len(filas), total_items)
        # Cortar cuando ya juntamos todo, la página vino incompleta o vacía.
        if not data or len(filas) >= total_items or len(data) < PAGE_SIZE:
            break
        page += 1
    return filas


# ── Helpers de montos ─────────────────────────────────────────────────────────

def _num(v) -> float:
    """Convierte un valor de la API (string numérico) a float; 0 si vacío/None."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ── Carga principal ───────────────────────────────────────────────────────────

def cargar_obuma_api(
    periodo: tuple,
    mapeo_vendedor: dict,
    log_no_mapeados: list,
    fallback_vendedor_id: int | None = None,
) -> dict:
    """
    Carga las ventas de Gran Natural desde la API para un período (año, mes).

    Devuelve el mismo dict que `cargar_obuma`:
      {dim_cliente, dim_producto, fact_ventas, stats}
    """
    anio, mes = periodo
    soc_id = SOCIEDAD_ID["grannatural"]
    f_desde = date(anio, mes, 1).isoformat()
    f_hasta = date(anio, mes, calendar.monthrange(anio, mes)[1]).isoformat()
    filtro_fecha = f"fecha_desde={f_desde}&fecha_hasta={f_hasta}"

    logger.info("  [API] Gran Natural %d-%02d (%s → %s)", anio, mes, f_desde, f_hasta)

    # 1. Líneas de venta (nivel ítem) y cabeceras (nivel documento)
    logger.info("  [API] Descargando líneas de venta…")
    items = _get_paginado("ventas.listItems", filtro_fecha)
    logger.info("  [API] Descargando cabeceras…")
    cabeceras = _get_paginado("ventas.list", filtro_fecha)

    if not items:
        logger.warning("  [API] Sin líneas de venta para %d-%02d.", anio, mes)
        vacio = pd.DataFrame()
        return {"dim_cliente": vacio, "dim_producto": vacio,
                "fact_ventas": vacio, "stats": {"obuma_api_filas": 0}}

    # 2. Diccionarios de apoyo
    logger.info("  [API] Descargando dimensiones (clientes, productos, empleados)…")
    cab_por_id = {c["venta_id"]: c for c in cabeceras}
    clientes = {c["cliente_id"]: c for c in _get_paginado("clientes.list")}
    productos = {p["producto_codigo_comercial"]: p for p in _get_paginado("productos.list")}
    empleados = {e["empleado_id"]: e for e in _get_paginado("empleados.list")}
    # Mapa código→nombre de categoría (la API entrega el id en producto_categoria)
    cat_nombre = {c["producto_categoria_id"]: c["producto_categoria_nombre"]
                  for c in _get_paginado("productosCategorias.list")}

    def _nombre_empleado(emp_id: str) -> str:
        e = empleados.get(emp_id)
        if not e:
            return ""
        partes = [e.get("empleado_nombres", ""), e.get("empleado_apellido_p", ""),
                  e.get("empleado_apellido_m", "")]
        return " ".join(p for p in partes if p).strip()

    # 3. Construir filas de fact_ventas
    filas = []
    descartadas = 0
    for it in items:
        venta_id = it.get("venta_id")
        cab = cab_por_id.get(venta_id, {})
        tipo_cod = str(it.get("venta_tipo_dcto") or cab.get("venta_tipo_dcto") or "")
        # Filtrar a DTE reales (factura/NC); descartar notas de venta, guías, etc.
        if tipo_cod not in DTE_VALIDOS:
            descartadas += 1
            continue
        tipo_dcto = TIPO_DCTO_MAP.get(tipo_cod, f"TIPO {tipo_cod}")

        cli = clientes.get(cab.get("rel_cliente_id"), {})
        filas.append({
            "anio": cab.get("venta_ano"), "mes": cab.get("venta_mes"), "dia": cab.get("venta_dia"),
            "tipo_dcto": tipo_dcto,
            "n_dcto": str(it.get("venta_nro_dcto") or cab.get("venta_nro_dcto") or "").strip(),
            "vendedor_nombre": _nombre_empleado(cab.get("rel_vendedor_id")),
            "cliente_rut_raw": cli.get("cliente_rut"),
            "razon_social": cli.get("cliente_razon_social"),
            "comuna": cli.get("cliente_comuna_facturacion"),
            "region_cod": str(cli.get("cliente_region_facturacion") or ""),
            "producto_codigo": str(it.get("codigo_comercial") or "").strip(),
            "sucursal": SUCURSAL_MAP.get(
                str(cab.get("rel_sucursal_id") or ""),
                str(cab.get("rel_sucursal_id") or ""),
            ),
            "cantidad": _num(it.get("cantidad")),
            "neto": _num(it.get("subtotal")),
            "costo": _num(it.get("costo_subtotal") or it.get("costo")),
        })

    df = pd.DataFrame(filas)

    # 4. Fecha y tipado
    df["fecha"] = pd.to_datetime(
        df["anio"].astype(str) + "-" + df["mes"].astype(str).str.zfill(2)
        + "-" + df["dia"].astype(str).str.zfill(2),
        errors="coerce",
    )
    df["region"] = df["region_cod"].map(REGION_MAP).fillna(df["region_cod"])
    df["sociedad_id"] = soc_id
    df["cliente_rut"] = normalizar_rut(df["cliente_rut_raw"])

    # 5. Signo de NC en montos (regla de negocio sección 3, igual que el Excel)
    es_neg = df["tipo_dcto"].isin(TIPO_DCTO_NEGATIVO)
    for col in ["neto", "costo"]:
        df.loc[es_neg, col] = -df.loc[es_neg, col].abs()
    # total y margen derivados (la API entrega neto y costo por línea)
    df["total"] = df["neto"]               # total real lleva IVA a nivel doc; en línea usamos neto
    df["margen"] = df["neto"] - df["costo"]

    # 6. Número de línea dentro del documento (llave natural de fact_ventas)
    df["linea"] = df.groupby(["sociedad_id", "tipo_dcto", "n_dcto"]).cumcount() + 1

    # 7. Enriquecer categoría/fabricante desde productos
    def _prod_attr(codigo, attr):
        p = productos.get(codigo)
        return p.get(attr) if p else None
    df["categoria_cod"] = df["producto_codigo"].map(lambda c: _prod_attr(c, "producto_categoria"))
    df["categoria"] = df["categoria_cod"].map(cat_nombre).fillna(df["categoria_cod"])
    df["subcategoria"] = df["producto_codigo"].map(lambda c: _prod_attr(c, "producto_subcategoria"))
    df["fabricante"] = df["producto_codigo"].map(lambda c: _prod_attr(c, "producto_fabricante"))
    df["producto_nombre"] = df["producto_codigo"].map(lambda c: _prod_attr(c, "producto_nombre"))
    df["unidad_medida"] = df["producto_codigo"].map(lambda c: _prod_attr(c, "producto_unidad_medida"))

    # 8. Mapear vendedor nombre → id
    df["vendedor_id"] = mapear_vendedor_id(
        df["vendedor_nombre"], mapeo_vendedor, log_no_mapeados, fuente="obuma_api",
        fallback_id=fallback_vendedor_id,
    )

    # 9. Armar los DataFrames de salida (mismo esquema que el loader Excel)
    SENTINEL = "SIN_ITEM"
    df["producto_codigo"] = df["producto_codigo"].replace("", SENTINEL).fillna(SENTINEL)

    dim_cliente = (
        df[["cliente_rut", "razon_social", "comuna", "region", "sociedad_id"]]
        .dropna(subset=["cliente_rut"])
        .drop_duplicates(subset=["cliente_rut"])
        .copy()
    )
    dim_cliente["tipo"] = None
    dim_cliente["es_maquina"] = False

    dim_producto = (
        df[["producto_codigo", "producto_nombre", "categoria", "subcategoria",
            "fabricante", "unidad_medida"]]
        .dropna(subset=["producto_codigo"])
        .drop_duplicates(subset=["producto_codigo"])
        .rename(columns={"producto_codigo": "codigo", "producto_nombre": "nombre"})
        .copy()
    )

    fact_ventas = df[[
        "fecha", "tipo_dcto", "n_dcto", "linea", "vendedor_id", "cliente_rut",
        "producto_codigo", "sociedad_id", "sucursal", "cantidad", "neto", "total",
        "costo", "margen",
    ]].copy()
    fact_ventas["fecha"] = fact_ventas["fecha"].dt.date.astype(str)

    stats = {
        "obuma_api_filas": len(fact_ventas),
        "obuma_api_facturas": int((df["tipo_dcto"].str.contains("FACTURA")).sum()),
        "obuma_api_notas_credito": int((df["tipo_dcto"].str.contains("NOTA DE CREDITO")).sum()),
        "obuma_api_rut_invalidos": int(df["cliente_rut"].isna().sum()),
        "obuma_api_lineas_descartadas_no_dte": descartadas,
        "obuma_api_neto_total": float(fact_ventas["neto"].sum()),
    }
    logger.info("  [API] dim_cliente=%d | dim_producto=%d | fact_ventas=%d | neto=%.0f",
                len(dim_cliente), len(dim_producto), len(fact_ventas), stats["obuma_api_neto_total"])

    return {
        "dim_cliente": dim_cliente,
        "dim_producto": dim_producto,
        "fact_ventas": fact_ventas,
        "stats": stats,
    }
