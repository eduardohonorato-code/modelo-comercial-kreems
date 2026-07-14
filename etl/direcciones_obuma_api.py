"""
Sucursales de Gran Natural desde la API de Obuma (sin Excel).

Gran Natural ya no se exporta a Excel: se carga por la API. Ahí la dirección de
despacho del documento viene en `venta_observacion`, en texto:
    "CALLE 123. Comuna. Region.(Ruta: XX-Q1.)"
Eso cubre lo que Autoventa no puede: las NOTAS DE CRÉDITO, que se emiten en Obuma
sin pedido detrás y por eso nunca traían sucursal. También rellena las facturas
que no cruzaron con un pedido.

La API no entrega un id de dirección (`venta_direccion_despacho` viene en 0), así
que la sucursal se resuelve por (cliente_rut + dirección normalizada) contra
dim_direccion — la misma identidad que usa el loader del Excel de Obuma.

Las NC de anulación traen `venta_observacion = "Anulación venta"` en vez de una
dirección: esas quedan sin sucursal (no se adivina a qué local corresponden).
"""
import logging
import re

import pandas as pd

from etl.cleaners import normalizar_rut, normalizar_direccion
from etl.config import SOCIEDAD_ID
from etl.loaders.obuma_api import (_get_paginado, REGION_MAP, TIPO_DCTO_MAP,
                                   DTE_VALIDOS)

logger = logging.getLogger(__name__)

SOCIEDAD_GN = SOCIEDAD_ID["grannatural"]

# "CALLE 123. Comuna. Region.(Ruta: XX.)" → calle / comuna / region
_RE_OBS = re.compile(
    r"^(?P<calle>.+?)\.\s*(?P<comuna>[^.]+)\.\s*(?P<region>[^.(]+)\.?\s*\(",
    re.S)


def _parsear_observacion(texto: str) -> tuple:
    """(calle, comuna, region) desde venta_observacion; (None,)*3 si no es una
    dirección (ej. 'Anulación venta')."""
    t = str(texto or "").strip()
    if not t or "(" not in t:
        return (None, None, None)
    m = _RE_OBS.match(t)
    if not m:
        return (None, None, None)
    return (m.group("calle").strip(), m.group("comuna").strip(),
            m.group("region").strip())


def leer_direcciones_api(periodo: tuple) -> pd.DataFrame:
    """
    Documentos de Gran Natural del período con su dirección de despacho.
    Devuelve las mismas columnas que etl.direcciones_obuma.leer_direcciones_excel,
    para poder reusar `construir` y `actualizar_fact_ventas`.
    """
    anio, mes = periodo
    ultimo = pd.Period(f"{anio}-{mes:02d}").days_in_month
    filtro = f"&fecha_desde={anio}-{mes:02d}-01&fecha_hasta={anio}-{mes:02d}-{ultimo}"

    cabeceras = _get_paginado("ventas.list", filtro)
    clientes = {c["cliente_id"]: c for c in _get_paginado("clientes.list")}
    logger.info("  [dir-api] documentos del período: %d", len(cabeceras))

    filas = []
    for cab in cabeceras:
        tipo_cod = str(cab.get("venta_tipo_dcto") or "")
        if tipo_cod not in DTE_VALIDOS:      # mismo filtro que el loader de ventas
            continue
        calle, comuna, region = _parsear_observacion(cab.get("venta_observacion"))
        cli = clientes.get(cab.get("rel_cliente_id"), {})
        filas.append({
            "sociedad_id": SOCIEDAD_GN,
            "tipo_dcto": TIPO_DCTO_MAP.get(tipo_cod, f"TIPO {tipo_cod}"),
            "n_dcto": str(cab.get("venta_nro_dcto") or "").strip(),
            "cliente_rut_raw": cli.get("cliente_rut"),
            "neto": abs(float(cab.get("venta_neto") or 0)),
            "codigo_dir": None,              # la API no entrega id de dirección
            "direccion": calle,
            "comuna": comuna,
            "region": region,
        })
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df["cliente_rut"] = normalizar_rut(df["cliente_rut_raw"])
    df = df.drop(columns=["cliente_rut_raw"]).dropna(subset=["cliente_rut"])
    df["dir_norm"] = normalizar_direccion(df["direccion"], df["comuna"], df["region"])

    sin = df["dir_norm"].isna()
    if sin.any():
        por_tipo = df.loc[sin, "tipo_dcto"].value_counts().to_dict()
        logger.warning("  [dir-api] %d documentos sin dirección en la observación "
                       "(anulaciones): %s", int(sin.sum()), por_tipo)
    return df
