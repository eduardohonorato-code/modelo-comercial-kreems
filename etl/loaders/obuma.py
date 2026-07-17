"""
Loader Obuma: lee ambas sociedades (Acuña + Gran Natural) y devuelve DataFrames
listos para upsert en dim_cliente, dim_producto y fact_ventas.

Formato de entrada: archivos .xls exportados como HTML por Obuma.
  - Se leen con pd.read_html() usando pathlib.Path.open('rb').
  - Columnas clave: N° DCTO, TIPO DCTO, VENDEDOR, CLIENTE Rut, etc.
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
from etl.config import TIPO_DCTO_NEGATIVO, SOCIEDAD_ID

logger = logging.getLogger(__name__)

# Código de producto centinela para líneas sin ítem (ej. NC globales).
SENTINEL_PRODUCTO = "SIN_ITEM"

# Columnas Obuma → nombres internos
COL_MAP = {
    "FECHA DCTO":          "fecha",
    "TIPO DCTO":           "tipo_dcto",
    "N° DCTO":             "n_dcto",
    "SUCURSAL":            "sucursal",
    "VENDEDOR":            "vendedor_nombre",
    "CLIENTE Rut":         "cliente_rut_raw",
    "CLIENTE Razon Social":"razon_social",
    "CLIENTE Comuna":      "comuna",
    "CLIENTE Region":      "region",
    "CLIENTE Tipo":        "tipo_cliente",
    "Codigo Producto":     "producto_codigo",
    "Producto":            "producto_nombre",
    "Categoria":           "categoria",
    "SubCategoria":        "subcategoria",
    "Fabricante":          "fabricante",
    "U.M.":                "unidad_medida",
    "Cantidad":            "cantidad",
    "Subtotal Neto":       "neto",
    "TOTAL":               "total",
    "Costo Neto Subtotal": "costo",
    "Utilidad (Margen)":   "margen",
}


def _leer_xls_html(path: Path, encoding: str = "utf-8") -> pd.DataFrame:
    """Lee un .xls exportado como HTML de Obuma.

    Los exports vienen con formato numérico chileno (miles '.', decimal ',').
    Sin declararlo, pd.read_html usa thousands=',' y convierte la Cantidad
    '1,00' en 100 al parsear (bug ×100 en fact_ventas.cantidad, irreversible
    aguas abajo porque la columna ya llega numérica a la limpieza).
    """
    with path.open("rb") as fh:
        tablas = pd.read_html(fh, encoding=encoding, header=0, flavor="lxml",
                              thousands=".", decimal=",")
    if not tablas:
        raise ValueError(f"No se encontraron tablas HTML en {path.name}")
    df = tablas[0]
    logger.info("  Leído %s: %d filas × %d cols", path.name, *df.shape)
    return df


def _normalizar_col_ndcto(df: pd.DataFrame) -> pd.DataFrame:
    """
    La columna 'N° DCTO' puede llegar con distintas variantes tipográficas
    según el encoding del export. La identifica y la renombra uniformemente.
    """
    candidatos = [c for c in df.columns if "DCTO" in c.upper() and "N" in c.upper()
                  and "TIPO" not in c.upper() and "FECHA" not in c.upper()]
    if not candidatos:
        raise KeyError(f"No se encontró columna N° DCTO. Columnas: {list(df.columns)}")
    df = df.rename(columns={candidatos[0]: "N° DCTO"})
    return df


def leer_fechas_obuma(path: Path) -> pd.Series:
    """
    Devuelve la columna FECHA DCTO parseada de un export Obuma, sin el resto del
    pipeline. Se usa para (a) elegir el archivo correcto cuando hay varios y
    (b) validar que el período cargado es el esperado.
    """
    df = _leer_xls_html(path)
    df = normalizar_columnas(df)
    col = next((c for c in df.columns if "FECHA DCTO" in c.upper()), None)
    if col is None:
        return pd.Series([], dtype="datetime64[ns]")
    return parsear_fecha(df[col])


def archivo_cubre_periodo(path: Path, anio: int, mes: int) -> bool:
    """True si el export tiene al menos una fila en el año/mes indicado."""
    f = leer_fechas_obuma(path)
    if f.empty:
        return False
    return bool(((f.dt.year == anio) & (f.dt.month == mes)).any())


def cargar_obuma_multi(
    archivos: list[tuple[Path, str]],
    mapeo_vendedor: dict,
    log_no_mapeados: list,
    periodo: tuple | None = None,
    fallback_vendedor_id: int | None = None,
) -> dict:
    """
    Lee y une una lista arbitraria de archivos Obuma, cada uno con su sociedad.

    Generaliza `cargar_obuma` para soportar **varios archivos por sociedad**
    (p.ej. un .xls por mes en la carga histórica). El resto del pipeline
    —limpieza, signo NC, líneas, mapeo de vendedor, dims y hechos— es idéntico,
    así que ambos puntos de entrada producen exactamente el mismo resultado.

    Args:
        archivos: lista de (path, soc_key) donde soc_key ∈ {"acuna","grannatural"}.

    Returns:
        {
          "dim_cliente":  pd.DataFrame,
          "dim_producto": pd.DataFrame,
          "fact_ventas":  pd.DataFrame,
          "stats":        dict,
        }
    """
    dfs = []
    for path, soc_key in archivos:
        df = _leer_xls_html(path)
        df = normalizar_columnas(df)
        df = _normalizar_col_ndcto(df)

        # Renombrar columnas presentes
        rename = {k: v for k, v in COL_MAP.items() if k in df.columns}
        df = df.rename(columns=rename)

        df["sociedad_id"] = SOCIEDAD_ID[soc_key]
        dfs.append(df)
        logger.info("  [%s] %s: %d filas raw", soc_key, path.name, len(df))

    raw = pd.concat(dfs, ignore_index=True)
    logger.info("  Total Obuma (%d archivo/s): %d filas raw", len(archivos), len(raw))

    # ── Limpieza ────────────────────────────────────────────────────────────
    raw["fecha"]     = parsear_fecha(raw["fecha"])

    # Log del rango de fechas por sociedad (detecta exports de otro período).
    for soc_key, soc_id in [("acuna", SOCIEDAD_ID["acuna"]),
                            ("grannatural", SOCIEDAD_ID["grannatural"])]:
        f = raw.loc[raw["sociedad_id"] == soc_id, "fecha"]
        if not f.empty:
            logger.info("  Rango fechas '%s': %s → %s", soc_key,
                        f.min().date(), f.max().date())

    # Filtrado por período (opcional). Garantiza que no se cuele un export de
    # otro mes/año (p.ej. un Acuña de 2025) y aborta si un archivo no aporta
    # filas del período pedido (señal de que se cargó el archivo equivocado).
    if periodo is not None:
        anio, mes = periodo
        raw = raw[(raw["fecha"].dt.year == anio) & (raw["fecha"].dt.month == mes)].copy()
        logger.info("  Filtrado a período %d-%02d: %d filas", anio, mes, len(raw))
        for soc_key, soc_id in [("acuna", SOCIEDAD_ID["acuna"]),
                                ("grannatural", SOCIEDAD_ID["grannatural"])]:
            if raw[raw["sociedad_id"] == soc_id].empty:
                raise ValueError(
                    f"El archivo de '{soc_key}' no tiene filas para {anio}-{mes:02d}. "
                    f"¿Cargaste el export correcto de esa sociedad y ese mes?"
                )

    raw["n_dcto"]    = raw["n_dcto"].astype(str).str.strip()
    raw["tipo_dcto"] = raw["tipo_dcto"].str.upper().str.strip()

    raw["cliente_rut"] = normalizar_rut(raw["cliente_rut_raw"])

    for col in ["neto", "total", "costo", "margen", "cantidad"]:
        if col in raw.columns:
            raw[col] = limpiar_monto(raw[col])

    # NC y ND llevan signo negativo (regla de negocio sección 3)
    es_negativo = raw["tipo_dcto"].isin(TIPO_DCTO_NEGATIVO)
    for col in ["neto", "total", "costo", "margen"]:
        if col in raw.columns:
            raw.loc[es_negativo, col] = -raw.loc[es_negativo, col].abs()

    # Número de línea dentro del documento (para la llave natural de fact_ventas)
    raw["linea"] = raw.groupby(
        ["sociedad_id", "tipo_dcto", "n_dcto"]
    ).cumcount() + 1

    # Mapear vendedor
    raw["vendedor_id"] = mapear_vendedor_id(
        raw["vendedor_nombre"], mapeo_vendedor, log_no_mapeados, fuente="obuma",
        fallback_id=fallback_vendedor_id,
    )

    # ── Estadísticas ────────────────────────────────────────────────────────
    stats = {
        "obuma_filas_raw":      len(raw),
        "obuma_facturas":       (raw["tipo_dcto"].str.contains("FACTURA")).sum(),
        "obuma_notas_credito":  (raw["tipo_dcto"].str.contains("NOTA DE CREDITO")).sum(),
        "obuma_notas_debito":   (raw["tipo_dcto"].str.contains("NOTA DE DEBITO")).sum(),
        "obuma_rut_invalidos":  raw["cliente_rut"].isna().sum(),
        "obuma_vend_no_mapeados": sum(1 for x in log_no_mapeados if x["fuente"] == "obuma"),
    }

    # ── dim_cliente ─────────────────────────────────────────────────────────
    dim_cliente = (
        raw[["cliente_rut", "razon_social", "comuna", "region", "tipo_cliente", "sociedad_id"]]
        .dropna(subset=["cliente_rut"])
        .rename(columns={"tipo_cliente": "tipo"})
        .drop_duplicates(subset=["cliente_rut"])
        .copy()
    )
    dim_cliente["es_maquina"] = False

    # ── dim_producto ─────────────────────────────────────────────────────────
    cols_prod = ["producto_codigo", "producto_nombre", "categoria", "subcategoria",
                 "fabricante", "unidad_medida"]
    cols_prod_present = [c for c in cols_prod if c in raw.columns]
    dim_producto = (
        raw[cols_prod_present]
        .dropna(subset=["producto_codigo"])
        .rename(columns={"producto_nombre": "nombre"})
        .drop_duplicates(subset=["producto_codigo"])
        .copy()
    )
    dim_producto = dim_producto.rename(columns={"producto_codigo": "codigo"})

    # Centinela para líneas sin código de producto (p.ej. Notas de Crédito
    # globales que no apuntan a un ítem). IMPRESCINDIBLE para la idempotencia:
    # la llave natural de fact_ventas incluye producto_codigo y, si va en NULL,
    # Postgres no deduplica (NULL ≠ NULL) e inserta duplicados en cada corrida.
    if SENTINEL_PRODUCTO not in set(dim_producto["codigo"].astype(str)):
        fila = {c: None for c in dim_producto.columns}
        fila["codigo"] = SENTINEL_PRODUCTO
        if "nombre" in fila:
            fila["nombre"] = "(Sin ítem / NC sin producto)"
        dim_producto = pd.concat([dim_producto, pd.DataFrame([fila])],
                                 ignore_index=True)

    # ── fact_ventas ──────────────────────────────────────────────────────────
    cols_fact = [
        "fecha", "tipo_dcto", "n_dcto", "linea", "vendedor_id",
        "cliente_rut", "producto_codigo", "sociedad_id", "sucursal",
        "cantidad", "neto", "total", "costo", "margen",
    ]
    cols_fact_present = [c for c in cols_fact if c in raw.columns]
    fact_ventas = raw[cols_fact_present].copy()
    fact_ventas["fecha"] = fact_ventas["fecha"].dt.date.astype(str)
    # Sin código → centinela (mantiene la llave natural sin NULLs)
    n_sin_item = fact_ventas["producto_codigo"].isna().sum()
    if n_sin_item:
        fact_ventas["producto_codigo"] = fact_ventas["producto_codigo"].fillna(
            SENTINEL_PRODUCTO)
        logger.info("  %d líneas sin código de producto → '%s' (idempotencia)",
                    n_sin_item, SENTINEL_PRODUCTO)

    logger.info(
        "  dim_cliente=%d | dim_producto=%d | fact_ventas=%d",
        len(dim_cliente), len(dim_producto), len(fact_ventas),
    )
    return {
        "dim_cliente":  dim_cliente,
        "dim_producto": dim_producto,
        "fact_ventas":  fact_ventas,
        "stats":        stats,
    }


def cargar_obuma(
    path_acuna: Path,
    path_grannatural: Path,
    mapeo_vendedor: dict,
    log_no_mapeados: list,
    periodo: tuple | None = None,
    fallback_vendedor_id: int | None = None,
) -> dict:
    """
    Carga las dos sociedades a partir de un archivo por sociedad.

    Mantiene la firma original que usa el ETL mensual (`run_etl.py`). Internamente
    delega en `cargar_obuma_multi`, que admite varios archivos por sociedad.
    """
    return cargar_obuma_multi(
        [(path_acuna, "acuna"), (path_grannatural, "grannatural")],
        mapeo_vendedor, log_no_mapeados,
        periodo=periodo, fallback_vendedor_id=fallback_vendedor_id,
    )
