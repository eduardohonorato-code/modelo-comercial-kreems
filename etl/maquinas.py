"""
Derivación de máquinas (comodato) — FUENTE ÚNICA compartida.

Tanto el ETL mensual (`run_etl.py`) como la carga histórica (`run_historico.py`)
usan estas funciones, de modo que ambos producen exactamente la misma
`fact_maquinas`. Las máquinas se derivan de **Obuma** (no de Autoventa) porque:
  · Obuma cubre AMBAS sociedades (Acuña + Gran Natural).
  · Trae los 5 códigos FL (FL-1/2/3/4/5), no solo FL-1/2/4.
El estado de entrega ('entregada'/'rechazada') se resuelve cruzando con los
despachos de Autoventa por número de documento Y RUT del cliente.
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


# Si un documento tiene varios despachos manda el mejor resultado: si alguno
# quedó Entregada el movimiento se ejecutó, aunque antes hubiera un rechazo.
# Mismo orden que `_PRIO_DESP` en app/export_maquinas.py.
_PRIO_DESP = {"entregada": 0, "rechazada": 1, "pendiente": 2}
_MAPA_ESTADO = {"entregada": "entregada", "rechazada": "rechazada",
                "pendiente": "gestionada"}

# El cruce máquina↔despacho es por documento Y cliente. Solo por número, una
# nota de crédito de flete (serie de folios propia, 600–900 en 2026) calzaba con
# una factura de OTRO cliente con el mismo número: la NC 618 de julio salía
# "entregada" con el despacho de marzo de la factura 618. De 320 cruces desde
# feb-2026, los únicos con RUT distinto eran esas 4 NC; ninguna factura.
_LLAVE = ["_doc", "cliente_rut"]


def _norm_doc(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _con_llave(df: pd.DataFrame, quien: str) -> pd.DataFrame:
    if "cliente_rut" not in df.columns:
        raise ValueError(
            f"{quien} necesita la columna cliente_rut: el cruce con despachos "
            "es por (documento, cliente_rut), no solo por número.")
    out = df.copy()
    out["_doc"] = _norm_doc(out["documento"])
    out["cliente_rut"] = out["cliente_rut"].astype(str).str.strip()
    return out


def estado_por_documento_cliente(fact_despachos: pd.DataFrame) -> pd.Series:
    """
    Estado de entrega por (documento, cliente_rut), resolviendo varios
    despachos al mejor resultado: Entregada > Rechazada > Pendiente.
    Devuelve una Serie con MultiIndex (_doc, cliente_rut) → estado de máquina.
    """
    d = _con_llave(fact_despachos, "estado_por_documento_cliente")
    # Sin RUT no hay cómo saber de quién es el despacho: no confirma nada.
    d = d[fact_despachos["cliente_rut"].notna()]
    d["_est"] = d["estado"].astype(str).str.strip().str.lower()
    d["_prio"] = d["_est"].map(_PRIO_DESP).fillna(9)
    orden = ["_prio", "fecha_ruta"] if "fecha_ruta" in d.columns else ["_prio"]
    d = d.sort_values(orden, kind="stable")
    return (d.drop_duplicates(_LLAVE).set_index(_LLAVE)["_est"]
            .map(_MAPA_ESTADO).dropna())


def aplicar_estado_despachos(fact_maquinas: pd.DataFrame,
                             fact_despachos: pd.DataFrame) -> pd.DataFrame:
    """
    Cruza máquinas con despachos por (documento, cliente_rut) y marca el estado:
      Entregada → 'entregada' | Rechazada → 'rechazada' | Pendiente → 'gestionada'.
    Si el documento tuvo varios despachos manda el mejor resultado.
    Si una máquina no tiene despacho de SU cliente, conserva el estado que traía.
    """
    if fact_maquinas.empty or fact_despachos.empty:
        return fact_maquinas

    estado = estado_por_documento_cliente(fact_despachos)
    m = _con_llave(fact_maquinas, "aplicar_estado_despachos")
    nuevo = pd.Series(
        [estado.get(k) for k in zip(m["_doc"], m["cliente_rut"])],
        index=fact_maquinas.index, dtype=object)
    fact_maquinas = fact_maquinas.copy()
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
    Marca `es_maquina` en los despachos cuyo (documento, cliente_rut) corresponde
    a una máquina. La fuente de verdad de qué documento es máquina es
    `fact_maquinas` (derivada de Obuma, categoría 'Maquinas'/FL-x), NO los
    pedidos de Autoventa: por eso se recalcula aquí, donde ya tenemos las
    máquinas del período. El cliente va en la llave para que una NC de flete no
    marque como máquina el despacho de producto de otro cliente con el mismo folio.
    """
    if fact_despachos.empty:
        return fact_despachos
    fd = fact_despachos.copy()
    if fact_maquinas.empty:
        fd["es_maquina"] = False
        return fd
    m = _con_llave(fact_maquinas.dropna(subset=["documento", "cliente_rut"]),
                   "marcar_despachos_maquina")
    llaves = set(zip(m["_doc"], m["cliente_rut"]))
    d = _con_llave(fd, "marcar_despachos_maquina")
    fd["es_maquina"] = [k in llaves for k in zip(d["_doc"], d["cliente_rut"])]
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
