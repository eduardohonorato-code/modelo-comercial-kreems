"""
Loader Autoventa: lee pedidos (CSV) y despachos (XLSX).
Produce:
  - fact_pedidos   (incluye flag facturado / no-facturado)
  - fact_despachos
  - fact_maquinas  (derivado de MAQUINAS_POP + estado de despacho)
  - dim_cliente adicionales (RUTs nuevos que no vengan de Obuma)
"""
import logging
from pathlib import Path

import pandas as pd

from etl.cleaners import (
    normalizar_columnas,
    normalizar_rut,
    parsear_fecha,
    limpiar_monto,
    mapear_vendedor_id,
)
from etl.config import CATEGORIA_MAQUINAS, TIPO_MOV_MAP, SOCIEDAD_ID

logger = logging.getLogger(__name__)

# Autoventa pedidos no distingue sociedad en el archivo.
# Toda la carga de Autoventa se asigna a Gran Natural (id=2) salvo que
# se cree un segundo export separado por sociedad en el futuro.
SOCIEDAD_AUTOVENTA = SOCIEDAD_ID["grannatural"]


def _leer_pedidos(path: Path) -> pd.DataFrame:
    """CSV con delimitador ';' y BOM utf-8."""
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    df = normalizar_columnas(df)
    logger.info("  Pedidos leídos: %d filas × %d cols", *df.shape)
    return df


def _leer_despachos(path: Path) -> pd.DataFrame:
    """XLSX estándar."""
    df = pd.read_excel(path, engine="openpyxl")
    df = normalizar_columnas(df)
    logger.info("  Despachos leídos: %d filas × %d cols", *df.shape)
    return df


_COLS_PEDIDOS_VACIO = [
    "RUT Cliente", "Fecha doc.", "Neto", "Neto Nota de Crédito", "Num documento",
    "N° pedido", "Doc. venta", "Cod. Prod.", "Categoría", "Vendedor",
    "Nombre cliente", "Comuna", "Ciudad",
]
_COLS_DESPACHOS_VACIO = [
    "Rut", "Fecha ruta", "Documento", "Estado", "Devolución", "Peso (Kgs)",
    "Transportista", "Vendedor", "Cliente", "Comuna", "Cuidad",
]


def cargar_autoventa(
    path_pedidos: Path | None,
    path_despachos: Path | None,
    mapeo_vendedor: dict,
    log_no_mapeados: list,
    fallback_vendedor_id: int | None = None,
) -> dict:
    """
    Returns:
        {
          "fact_pedidos":   pd.DataFrame,
          "fact_despachos": pd.DataFrame,
          "fact_maquinas":  pd.DataFrame,
          "dim_cliente":    pd.DataFrame,   # RUTs nuevos de Autoventa
          "stats":          dict,
        }

    `path_pedidos` y/o `path_despachos` pueden ser None: en ese caso esa fuente se
    omite (DataFrame vacío) y el resto se procesa igual. Útil cuando GN/pedidos ya
    vienen por API y solo se carga el despacho (o Acuña) por Excel.
    """
    ped = (_leer_pedidos(path_pedidos) if path_pedidos is not None
           else pd.DataFrame(columns=_COLS_PEDIDOS_VACIO))
    des = (_leer_despachos(path_despachos) if path_despachos is not None
           else pd.DataFrame(columns=_COLS_DESPACHOS_VACIO))

    # ────────────────────────────────────────────────────────────────────────
    # PEDIDOS
    # ────────────────────────────────────────────────────────────────────────
    ped["sociedad_id"]  = SOCIEDAD_AUTOVENTA
    ped["cliente_rut"]  = normalizar_rut(ped["RUT Cliente"])
    ped["fecha"]        = parsear_fecha(ped.get("Fecha doc.", ped.get("Fecha pedido")))
    ped["neto"]         = limpiar_monto(ped["Neto"])
    ped["neto_nc"]      = limpiar_monto(ped.get("Neto Nota de Crédito", pd.Series(dtype=float)))
    ped["num_documento"]= ped["Num documento"].where(
                              ped["Num documento"].notna(),
                              other=pd.NA,
                          ).astype("Int64").astype(str).where(
                              ped["Num documento"].notna(), pd.NA
                          )
    # N° pedido puede llegar como float ("3263.0") desde el CSV → int → str
    _n_ped_num = pd.to_numeric(ped["N° pedido"], errors="coerce")
    ped["n_pedido"] = _n_ped_num.where(_n_ped_num.notna(), other=pd.NA) \
                                .astype("Int64").astype(str) \
                                .where(_n_ped_num.notna(), other="SIN_PEDIDO")
    ped["doc_venta"]       = ped["Doc. venta"].str.strip()
    ped["producto_codigo"] = ped["Cod. Prod."].astype(str).str.strip()

    # Eliminar filas de pie/encabezado del export (ej. "--- FIN EXPORTACION ---")
    mask_footer = ped["producto_codigo"].str.startswith("---", na=False)
    if mask_footer.any():
        logger.warning("  Eliminando %d filas de pie/encabezado del CSV: %s",
                       mask_footer.sum(),
                       ped.loc[mask_footer, "producto_codigo"].unique().tolist())
        ped = ped[~mask_footer].copy()
    ped["linea"]           = (
        ped.groupby(["sociedad_id", "n_pedido", "producto_codigo"]).cumcount() + 1
    ).astype("Int64")

    ped["vendedor_id"]  = mapear_vendedor_id(
        ped["Vendedor"], mapeo_vendedor, log_no_mapeados, fuente="autoventa_pedidos",
        fallback_id=fallback_vendedor_id,
    )

    # ── fact_pedidos ─────────────────────────────────────────────────────────
    cols_ped = [
        "n_pedido", "num_documento", "doc_venta", "fecha",
        "vendedor_id", "cliente_rut", "producto_codigo",
        "sociedad_id", "neto", "neto_nc", "linea",
    ]
    fact_pedidos = ped[[c for c in cols_ped if c in ped.columns]].copy()
    fact_pedidos["fecha"] = fact_pedidos["fecha"].dt.date.astype(str)

    # ── fact_maquinas (derivado de MAQUINAS_POP) ─────────────────────────────
    mask_maq = (
        ped["Categoría"].str.upper().str.strip() == CATEGORIA_MAQUINAS
    ) & ped["producto_codigo"].isin(TIPO_MOV_MAP)

    maq = ped[mask_maq].copy()
    maq["tipo_mov"]  = maq["producto_codigo"].map(TIPO_MOV_MAP)
    maq["estado"]    = "gestionada"   # default; se actualizará al cruzar despachos
    maq["documento"] = maq["num_documento"].fillna(maq["n_pedido"])

    fact_maquinas_base = maq[[
        "documento", "fecha", "vendedor_id", "cliente_rut",
        "tipo_mov", "estado", "sociedad_id",
    ]].copy()

    logger.info(
        "  Máquinas derivadas de pedidos: %d (nuevas=%d, cambios=%d, retiros=%d)",
        len(maq),
        (maq["tipo_mov"] == "nueva").sum(),
        (maq["tipo_mov"] == "cambio").sum(),
        (maq["tipo_mov"] == "retiro").sum(),
    )

    # ────────────────────────────────────────────────────────────────────────
    # DESPACHOS
    # ────────────────────────────────────────────────────────────────────────
    des["sociedad_id"]  = SOCIEDAD_AUTOVENTA
    des["cliente_rut"]  = normalizar_rut(des["Rut"])
    des["fecha_ruta"]   = parsear_fecha(des["Fecha ruta"])
    des["documento"]    = des["Documento"].astype(str).str.strip()
    des["estado"]       = des["Estado"].str.strip()
    des["devolucion"]   = des["Devolución"].fillna(0).astype(bool)
    des["peso"]         = limpiar_monto(des.get("Peso (Kgs)", pd.Series(dtype=float)))
    des["transportista"]= des.get("Transportista", pd.Series(dtype=str))

    des["vendedor_id"]  = mapear_vendedor_id(
        des["Vendedor"], mapeo_vendedor, log_no_mapeados, fuente="autoventa_despachos",
        fallback_id=fallback_vendedor_id,
    )

    # Marcar si el despacho corresponde a una máquina
    docs_maquinas = set(fact_maquinas_base["documento"].dropna().astype(str))
    des["es_maquina"] = des["documento"].isin(docs_maquinas)

    # ── fact_despachos ───────────────────────────────────────────────────────
    cols_des = [
        "documento", "fecha_ruta", "vendedor_id", "cliente_rut",
        "estado", "devolucion", "peso", "es_maquina", "transportista", "sociedad_id",
    ]
    fact_despachos = des[[c for c in cols_des if c in des.columns]].copy()
    fact_despachos["fecha_ruta"] = fact_despachos["fecha_ruta"].dt.date.astype(str)

    # ── Actualizar estado de máquinas cruzando con despachos ─────────────────
    # Un pedido de máquina puede tener despacho Entregada/Rechazada/Pendiente.
    estado_despacho = (
        des[des["es_maquina"]][["documento", "estado"]]
        .drop_duplicates("documento")
        .set_index("documento")["estado"]
        .str.lower()
        .map({"entregada": "entregada", "rechazada": "rechazada", "pendiente": "gestionada"})
    )

    def _estado_final(row):
        doc = str(row["documento"])
        return estado_despacho.get(doc, row["estado"])

    fact_maquinas_base["estado"] = fact_maquinas_base.apply(_estado_final, axis=1)
    fact_maquinas_base["fecha"]  = fact_maquinas_base["fecha"].dt.date.astype(str) \
        if hasattr(fact_maquinas_base["fecha"], "dt") else fact_maquinas_base["fecha"].astype(str)

    # ── Cruce Obuma ↔ Autoventa (estadística de match) ───────────────────────
    docs_pedidos_facturados = set(
        ped[ped["doc_venta"] != "Sin DTE"]["num_documento"].dropna().astype(str)
    )
    # El resultado del % de match se reporta en run_etl.py usando los n_dcto de Obuma.

    # ── dim_cliente: RUTs de pedidos
    dim_cliente_ped = (
        ped[["cliente_rut", "Nombre cliente", "Comuna", "Ciudad", "sociedad_id"]]
        .dropna(subset=["cliente_rut"])
        .rename(columns={"Nombre cliente": "razon_social", "Comuna": "comuna", "Ciudad": "region"})
        .drop_duplicates(subset=["cliente_rut"])
        .copy()
    )
    dim_cliente_ped["tipo"]       = None
    dim_cliente_ped["es_maquina"] = False

    # ── dim_cliente: RUTs de despachos (pueden no aparecer en pedidos ni Obuma)
    dim_cliente_des = (
        des[["cliente_rut", "Cliente", "Comuna", "Cuidad", "sociedad_id"]]
        .dropna(subset=["cliente_rut"])
        .rename(columns={"Cliente": "razon_social", "Comuna": "comuna", "Cuidad": "region"})
        .drop_duplicates(subset=["cliente_rut"])
        .copy()
    )
    dim_cliente_des["tipo"]       = None
    dim_cliente_des["es_maquina"] = False

    # Combinar ambos: pedidos tiene prioridad sobre despachos
    dim_cliente_av = (
        dim_cliente_ped.set_index("cliente_rut")
        .combine_first(dim_cliente_des.set_index("cliente_rut"))
        .reset_index()
    )

    # ── Estadísticas ─────────────────────────────────────────────────────────
    stats = {
        "pedidos_total":       len(ped),
        "pedidos_facturados":  (ped["doc_venta"] != "Sin DTE").sum(),
        "pedidos_sin_dte":     (ped["doc_venta"] == "Sin DTE").sum(),
        "despachos_total":     len(des),
        "despachos_entregada": (des["estado"] == "Entregada").sum(),
        "despachos_rechazada": (des["estado"] == "Rechazada").sum(),
        "despachos_pendiente": (des["estado"] == "Pendiente").sum(),
        "maquinas_total":      len(fact_maquinas_base),
        "docs_pedidos_facturados": len(docs_pedidos_facturados),
    }

    logger.info(
        "  fact_pedidos=%d | fact_despachos=%d | fact_maquinas=%d | dim_cliente_av=%d",
        len(fact_pedidos), len(fact_despachos), len(fact_maquinas_base), len(dim_cliente_av),
    )

    return {
        "fact_pedidos":   fact_pedidos,
        "fact_despachos": fact_despachos,
        "fact_maquinas":  fact_maquinas_base,
        "dim_cliente":    dim_cliente_av,
        "stats":          stats,
        "_docs_facturados": docs_pedidos_facturados,
    }
