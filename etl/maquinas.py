"""
Derivación de máquinas (comodato) — FUENTE ÚNICA compartida.

Tanto el ETL mensual (`run_etl.py`) como la carga histórica (`run_historico.py`)
usan estas funciones, de modo que ambos producen exactamente la misma
`fact_maquinas`. Las máquinas se derivan de **Obuma** (no de Autoventa) porque:
  · Obuma cubre AMBAS sociedades (Acuña + Gran Natural).
  · Trae los 5 códigos FL (FL-1/2/3/4/5), no solo FL-1/2/4.
El estado de entrega ('entregada'/'rechazada') se resuelve cruzando con los
despachos de Autoventa por número de documento.
"""
import logging
import pandas as pd

from etl.config import TIPO_MOV_MAP

logger = logging.getLogger(__name__)

# Columnas de salida (coinciden con la tabla fact_maquinas)
COLS_MAQUINAS = ["documento", "fecha", "vendedor_id", "cliente_rut",
                 "tipo_mov", "estado", "sociedad_id"]


def derivar_maquinas_obuma(fact_ventas: pd.DataFrame) -> pd.DataFrame:
    """
    Construye fact_maquinas desde las líneas FL-x de Obuma (categoría 'Maquinas').
    `documento = n_dcto`, estado inicial 'gestionada' (se actualiza con despachos).

    La llave natural de fact_maquinas es (sociedad_id, documento, cliente_rut,
    tipo_mov), así que se descartan filas sin documento/RUT y se deduplica.
    """
    fv = fact_ventas.copy()
    fv["producto_codigo"] = fv["producto_codigo"].astype(str).str.upper().str.strip()
    maq = fv[fv["producto_codigo"].isin(TIPO_MOV_MAP)].copy()
    if maq.empty:
        return pd.DataFrame(columns=COLS_MAQUINAS)

    maq["tipo_mov"]  = maq["producto_codigo"].map(TIPO_MOV_MAP)
    maq["documento"] = maq["n_dcto"].astype(str).str.strip()
    maq["estado"]    = "gestionada"
    maq = maq[COLS_MAQUINAS]

    antes = len(maq)
    maq = maq.dropna(subset=["documento", "cliente_rut"])
    maq = maq[maq["documento"].str.lower() != "nan"]
    maq = maq.drop_duplicates(
        subset=["sociedad_id", "documento", "cliente_rut", "tipo_mov"]
    )
    logger.info(
        "  Máquinas Obuma: %d líneas FL → %d movimientos únicos "
        "(descartadas %d sin rut/doc) | nuevas=%d cambios=%d retiros=%d",
        antes, len(maq), antes - len(maq),
        (maq["tipo_mov"] == "nueva").sum(),
        (maq["tipo_mov"] == "cambio").sum(),
        (maq["tipo_mov"] == "retiro").sum(),
    )
    return maq


def aplicar_estado_despachos(fact_maquinas: pd.DataFrame,
                             fact_despachos: pd.DataFrame) -> pd.DataFrame:
    """
    Cruza máquinas con despachos por documento y marca el estado de entrega:
      Entregada → 'entregada' | Rechazada → 'rechazada' | Pendiente → 'gestionada'.
    Si un documento de máquina no tiene despacho, queda 'gestionada'.
    """
    if fact_maquinas.empty or fact_despachos.empty:
        return fact_maquinas

    mapa = {"entregada": "entregada", "rechazada": "rechazada",
            "pendiente": "gestionada"}
    estado_doc = (
        fact_despachos.assign(_d=fact_despachos["documento"].astype(str).str.strip())
        .drop_duplicates("_d")
        .set_index("_d")["estado"].astype(str).str.lower().map(mapa)
        .dropna()
    )
    fact_maquinas = fact_maquinas.copy()
    nuevo = fact_maquinas["documento"].astype(str).str.strip().map(estado_doc)
    fact_maquinas["estado"] = nuevo.fillna(fact_maquinas["estado"])
    logger.info(
        "  Estado máquinas tras cruce con despachos: entregadas=%d | "
        "rechazadas=%d | gestionadas=%d",
        (fact_maquinas["estado"] == "entregada").sum(),
        (fact_maquinas["estado"] == "rechazada").sum(),
        (fact_maquinas["estado"] == "gestionada").sum(),
    )
    return fact_maquinas


def marcar_despachos_maquina(fact_despachos: pd.DataFrame,
                             fact_maquinas: pd.DataFrame) -> pd.DataFrame:
    """
    Marca `es_maquina` en los despachos cuyo documento corresponde a una máquina.
    La fuente de verdad de qué documento es máquina es `fact_maquinas` (derivada
    de Obuma, categoría 'Maquinas'/FL-x), NO los pedidos de Autoventa: por eso se
    recalcula aquí, donde ya tenemos las máquinas del período.
    """
    if fact_despachos.empty:
        return fact_despachos
    docs = (set(fact_maquinas["documento"].dropna().astype(str).str.strip())
            if not fact_maquinas.empty else set())
    fd = fact_despachos.copy()
    fd["es_maquina"] = fd["documento"].astype(str).str.strip().isin(docs)
    return fd


def reatribuir_vendedor_autoventa(fact_maquinas: pd.DataFrame,
                                  vendedor_por_folio: dict,
                                  fallback_id: int | None = None) -> pd.DataFrame:
    """
    Reasigna el vendedor de cada máquina al que figura en AUTOVENTA para ese
    documento (folio). Para las máquinas (comodato, gestión en terreno) Autoventa
    es la fuente correcta de quién colocó/retiró la máquina; Obuma suele dejar
    esos documentos en 'Sin asignar'. Así el conteo por vendedor coincide con el
    reporte que trabaja desde Autoventa (ej. Tomás, jefe de ventas, conserva sus
    máquinas en su propia fila).

    `vendedor_por_folio`: dict folio(str) → vendedor_id (tomado de las líneas FL
    de Autoventa). Solo afecta filas cuyo `documento` está en el mapa (GN
    facturadas). No toca Acuña (folios que no vienen de Autoventa). Si el mapa
    apunta al fallback 'Sin asignar', se respeta la atribución previa.
    """
    if fact_maquinas.empty or not vendedor_por_folio:
        return fact_maquinas
    fm = fact_maquinas.copy()
    doc = fm["documento"].astype(str).str.strip()
    nuevo = doc.map(vendedor_por_folio)
    aplicar = nuevo.notna() & (nuevo != fm["vendedor_id"])
    if fallback_id is not None:
        aplicar &= (nuevo != fallback_id)
    n = int(aplicar.sum())
    if n:
        fm.loc[aplicar, "vendedor_id"] = nuevo[aplicar].astype(int)
        logger.info("  Máquinas reatribuidas al vendedor de Autoventa: %d "
                    "movimientos", n)
    return fm


def aplicar_override_vendedor(fact_maquinas: pd.DataFrame,
                              overrides: pd.DataFrame) -> pd.DataFrame:
    """
    Reasigna el vendedor de máquinas según la tabla `maquina_vendedor_override`
    (llave sociedad_id + documento). La atribución oficial es por el VENDEDOR de
    Obuma; esto solo corrige excepciones cargadas a mano por gerencia (ej. FL-x
    con vendedor vacío en Obuma). Si la tabla viene vacía, no cambia nada.
    """
    if fact_maquinas.empty or overrides is None or overrides.empty:
        return fact_maquinas

    ov = overrides.copy()
    ov["documento"] = ov["documento"].astype(str).str.strip()
    ov["sociedad_id"] = pd.to_numeric(ov["sociedad_id"], errors="coerce")
    mapa = ov.set_index(["sociedad_id", "documento"])["vendedor_id"]

    fm = fact_maquinas.copy()
    claves = list(zip(
        pd.to_numeric(fm["sociedad_id"], errors="coerce"),
        fm["documento"].astype(str).str.strip(),
    ))
    nuevo = pd.Series([mapa.get(k) for k in claves], index=fm.index)
    n_aplicados = int(nuevo.notna().sum())
    fm["vendedor_id"] = nuevo.fillna(fm["vendedor_id"]).astype("Int64")
    if n_aplicados:
        logger.info("  Override de vendedor aplicado a %d máquinas (tabla manual).",
                    n_aplicados)
    return fm
