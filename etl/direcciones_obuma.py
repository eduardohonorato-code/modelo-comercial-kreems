"""
Sucursales desde el export Excel de Obuma (cubre Acuña y las notas de crédito).

Autoventa (etl/direcciones.py) solo alcanza a Gran Natural y solo a las facturas
que nacen de un pedido. Pero el export de Obuma trae la dirección del cliente EN
CADA LÍNEA de venta:
    "CLIENTE CODIGO Direccion" · "CLIENTE Direccion"
con lo que se puede atribuir Acuña completo y también las NC, que no tienen pedido
detrás. En los clientes grandes (cadenas con muchos locales) es la mayor parte de
su facturación.

Los códigos de Obuma NO sirven de identidad: el mismo local aparece con dos
códigos distintos y hay códigos alfanuméricos mezclados con numéricos. La identidad es
(cliente_rut + dirección normalizada); el id de estas direcciones es un hash
estable NEGATIVO de esa llave (los positivos son address_id de Autoventa). Si la
misma dirección ya existe vía Autoventa, se reusa ese id: es el mismo local.
"""
import hashlib
import logging
from pathlib import Path

import pandas as pd

from etl.cleaners import (normalizar_columnas, normalizar_rut,
                          normalizar_direccion, parsear_fecha, limpiar_monto)
from etl.config import SOCIEDAD_ID
from etl.upsert import upsert_tabla
from etl.loaders.obuma import _leer_xls_html, _normalizar_col_ndcto

logger = logging.getLogger(__name__)

_COL_DIR_CODIGO = "CLIENTE CODIGO Direccion"
_COL_DIR_TEXTO = "CLIENTE Direccion"


def id_direccion_obuma(cliente_rut: str, dir_norm: str) -> int:
    """Id sintético estable y negativo para una dirección de Obuma."""
    h = hashlib.blake2b(f"{cliente_rut}|{dir_norm}".encode("utf-8"), digest_size=6)
    return -int.from_bytes(h.digest(), "big")   # 48 bits: cabe holgado en bigint


def leer_direcciones_excel(path: Path, sociedad: str = "acuna") -> pd.DataFrame:
    """
    Lee un export de ventas de Obuma y devuelve una fila por LÍNEA con la
    dirección del cliente: sociedad_id, tipo_dcto, n_dcto, cliente_rut, neto,
    codigo_dir, direccion, comuna, region, dir_norm.
    """
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path)
    else:
        df = _leer_xls_html(path)          # los .xls de Obuma son HTML
    df = normalizar_columnas(df)
    df = _normalizar_col_ndcto(df)

    faltan = [c for c in (_COL_DIR_TEXTO, "CLIENTE Rut", "TIPO DCTO", "N° DCTO")
              if c not in df.columns]
    if faltan:
        raise KeyError(f"{path.name}: faltan columnas {faltan}. "
                       f"¿El export de Obuma incluye la dirección del cliente?")

    out = pd.DataFrame({
        "sociedad_id": SOCIEDAD_ID[sociedad],
        "tipo_dcto": df["TIPO DCTO"].astype(str).str.strip().str.upper(),
        "n_dcto": df["N° DCTO"].astype(str).str.strip(),
        "cliente_rut": normalizar_rut(df["CLIENTE Rut"]),
        "neto": limpiar_monto(df["Subtotal Neto"]) if "Subtotal Neto" in df else 0,
        "codigo_dir": df.get(_COL_DIR_CODIGO, pd.Series(index=df.index, dtype=object))
                        .astype(str).str.strip().replace({"nan": None}),
        "direccion": df[_COL_DIR_TEXTO].astype(str).str.strip().replace({"nan": None}),
        "comuna": df.get("CLIENTE Comuna"),
        "region": df.get("CLIENTE Region"),
    })
    if "FECHA DCTO" in df.columns:
        out["fecha"] = parsear_fecha(df["FECHA DCTO"])
    out["dir_norm"] = normalizar_direccion(out["direccion"], out["comuna"],
                                           out["region"])
    n_sin = int(out["dir_norm"].isna().sum())
    if n_sin:
        logger.warning("  [dir-obuma] %s: %d líneas sin dirección (quedan sin sucursal)",
                       path.name, n_sin)
    return out.dropna(subset=["cliente_rut"])


def ids_existentes(client) -> dict[tuple, int]:
    """
    (cliente_rut, dir_norm) → id de las direcciones ya cargadas. Sirve para que una
    dirección de Obuma que es el MISMO local que una de Autoventa reuse su id en
    vez de duplicarse. Las de Autoventa no traen dir_norm calculado (columna nueva
    de sql/028): se calcula aquí y se persiste de paso.
    """
    filas, off = [], 0
    while True:
        r = (client.table("dim_direccion")
             .select("id,cliente_rut,direccion,comuna,ciudad,dir_norm,origen")
             .order("id").range(off, off + 999).execute())
        filas += r.data or []
        if not r.data or len(r.data) < 1000:
            break
        off += 1000
    if not filas:
        return {}
    d = pd.DataFrame(filas)

    faltan = d["dir_norm"].isna()
    if faltan.any():
        d.loc[faltan, "dir_norm"] = normalizar_direccion(
            d.loc[faltan, "direccion"], d.loc[faltan, "comuna"], d.loc[faltan, "ciudad"])
        pend = d[faltan & d["dir_norm"].notna()][["id", "dir_norm"]]
        if not pend.empty:
            upsert_tabla(client, "dim_direccion", pend, on_conflict="id")
            logger.info("dir_norm calculado para %d direcciones existentes", len(pend))

    ok = d[d["cliente_rut"].notna() & d["dir_norm"].notna()]
    return {(r, n): int(i) for r, n, i in
            zip(ok["cliente_rut"], ok["dir_norm"], ok["id"])}


def construir(df: pd.DataFrame, ids_existentes: dict[tuple, int],
              ruts_validos: set[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """
    De las líneas leídas produce:
      · dim: filas nuevas para dim_direccion (origen='obuma').
      · mapa: (tipo_dcto, n_dcto) → direccion_id, para el UPDATE de fact_ventas.

    `ids_existentes`: (cliente_rut, dir_norm) → id ya conocido (típicamente de
    Autoventa) para no duplicar el mismo local con dos ids.
    `ruts_validos`: RUT presentes en dim_cliente. Los Excel traen clientes de meses
    que nunca se cargaron a fact_ventas (violarían el FK y además no tienen ninguna
    venta que atribuir) → se omiten, dejando constancia en el log.

    Si un documento trae más de una dirección (no debería: la dirección es de la
    cabecera), se toma la de mayor monto y se registra en el log.
    """
    d = df.dropna(subset=["dir_norm"]).copy()
    if ruts_validos is not None:
        fuera = ~d["cliente_rut"].isin(ruts_validos)
        if fuera.any():
            logger.warning("  [dir-obuma] %d líneas de %d RUT que no están en "
                           "dim_cliente (sin ventas cargadas): se omiten",
                           int(fuera.sum()), d.loc[fuera, "cliente_rut"].nunique())
            d = d[~fuera]
    if d.empty:
        return pd.DataFrame(), {}

    d["direccion_id"] = [
        ids_existentes.get((r, n)) or id_direccion_obuma(r, n)
        for r, n in zip(d["cliente_rut"], d["dir_norm"])
    ]

    # Documento → dirección (la de mayor monto si hubiera más de una).
    por_doc = (d.groupby(["tipo_dcto", "n_dcto", "direccion_id"])["neto"]
               .sum().abs().reset_index())
    ambiguos = por_doc.groupby(["tipo_dcto", "n_dcto"]).size()
    n_amb = int((ambiguos > 1).sum())
    if n_amb:
        logger.warning("  [dir-obuma] %d documentos con más de una dirección "
                       "(se toma la de mayor monto)", n_amb)
    elegido = (por_doc.sort_values("neto")
               .drop_duplicates(["tipo_dcto", "n_dcto"], keep="last"))
    mapa = {(t, n): int(i) for t, n, i in
            zip(elegido["tipo_dcto"], elegido["n_dcto"], elegido["direccion_id"])}

    nuevas = d[~d["direccion_id"].isin(set(ids_existentes.values()))]
    dim = (nuevas.sort_values("neto")
           .drop_duplicates("direccion_id", keep="last")
           [["direccion_id", "cliente_rut", "direccion", "comuna", "region",
             "codigo_dir", "dir_norm"]]
           .rename(columns={"direccion_id": "id", "region": "ciudad",
                            "codigo_dir": "codigo_externo"}))
    dim["nombre"] = None
    dim["ruta"] = None
    dim["es_principal"] = False
    dim["activa"] = True
    dim["origen"] = "obuma"
    return dim, mapa


def actualizar_fact_ventas(client, mapa: dict, sociedad_id: int) -> int:
    """
    UPDATE fact_ventas.direccion_id por (tipo_dcto, n_dcto). El tipo va en la
    llave porque las NC llevan su propia serie de folios: un n_dcto de NC puede
    repetir el folio de una factura.
    """
    if not mapa:
        return 0
    por_dir: dict[tuple, list[str]] = {}
    for (tipo, n_dcto), dir_id in mapa.items():
        por_dir.setdefault((tipo, int(dir_id)), []).append(n_dcto)

    filas = 0
    for (tipo, dir_id), folios in por_dir.items():
        for i in range(0, len(folios), 100):
            r = (client.table("fact_ventas").update({"direccion_id": dir_id})
                 .eq("sociedad_id", sociedad_id)
                 .eq("tipo_dcto", tipo)
                 .in_("n_dcto", folios[i:i + 100]).execute())
            filas += len(r.data or [])
    return filas
