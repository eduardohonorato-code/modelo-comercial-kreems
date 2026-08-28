"""
Reconcilia el estado de entrega de fact_maquinas contra fact_despachos.

Por qué hace falta un script aparte: la sincronización que corre al subir el
Excel de despachos mira un mes a la vez, y la ruta de una máquina puede caer en
un mes distinto al de su factura (se factura a fin de mes y se entrega al mes
siguiente). Esas máquinas quedaban 'gestionada' para siempre. La página Carga ya
cruza por documento sin importar el mes, pero lo que quedó mal en la base no se
arregla solo: para eso está este script.

Cruza por número de documento (Obuma "N° DCTO" = Autoventa "Documento") sin
filtrar por fecha, y solo escribe las filas cuyo estado cambia.

Uso:
    python -m etl.reconciliar_maquinas --dry-run     # muestra el diff, no escribe
    python -m etl.reconciliar_maquinas              # aplica
"""
import argparse
import logging

import pandas as pd

from etl.db import get_client
from etl.upsert import upsert_tabla

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

COLS_MAQ = ("documento,fecha,vendedor_id,cliente_rut,tipo_mov,estado,sociedad_id")

# Si un documento tiene varios despachos manda el mejor resultado: si alguno
# quedó Entregada el movimiento se ejecutó, aunque antes hubiera un rechazo.
_PRIO = {"entregada": 0, "rechazada": 1, "pendiente": 2}
_MAPA_ESTADO = {"entregada": "entregada", "rechazada": "rechazada",
                "pendiente": "gestionada"}


def _leer_todo(client, tabla: str, select: str) -> pd.DataFrame:
    """Tabla completa, paginada (bypass del límite de 1000 filas de PostgREST)."""
    _PAGE, offset, filas = 1000, 0, []
    while True:
        r = (client.table(tabla).select(select)
             .order("id").range(offset, offset + _PAGE - 1).execute())
        if not r.data:
            break
        filas.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    return pd.DataFrame(filas)


def estado_por_documento(despachos: pd.DataFrame) -> pd.Series:
    d = despachos.copy()
    d["_doc"] = d["documento"].astype(str).str.strip()
    d["_est"] = d["estado"].astype(str).str.strip().str.lower()
    d["_prio"] = d["_est"].map(_PRIO).fillna(9)
    d = d.sort_values(["_prio", "fecha_ruta"])
    return (d.drop_duplicates("_doc").set_index("_doc")["_est"]
            .map(_MAPA_ESTADO).dropna())


def reconciliar(client, dry_run: bool = False) -> pd.DataFrame:
    maq = _leer_todo(client, "fact_maquinas", "id," + COLS_MAQ)
    desp = _leer_todo(client, "fact_despachos", "documento,estado,fecha_ruta")
    logger.info("Máquinas: %d | Despachos: %d", len(maq), len(desp))
    if maq.empty or desp.empty:
        return pd.DataFrame()

    nuevo = maq["documento"].astype(str).str.strip().map(
        estado_por_documento(desp))
    cambia = nuevo.notna() & (nuevo != maq["estado"])
    dif = maq[cambia].copy()
    dif["estado_nuevo"] = nuevo[cambia]
    if dif.empty:
        logger.info("Nada que reconciliar: todos los estados ya están al día.")
        return dif

    dif["_mes"] = pd.to_datetime(dif["fecha"], errors="coerce").dt.to_period("M")
    logger.info("Movimientos con estado desactualizado: %d", len(dif))
    logger.info("\n%s", pd.crosstab(
        dif["_mes"].astype(str), [dif["estado"], dif["estado_nuevo"]]).to_string())
    logger.info("\nPor tipo de movimiento:\n%s", pd.crosstab(
        dif["tipo_mov"], dif["estado_nuevo"]).to_string())

    if dry_run:
        logger.info("\n--dry-run: no se escribió nada.")
        return dif

    filas = dif.drop(columns=["id", "estado", "_mes"]).rename(
        columns={"estado_nuevo": "estado"})
    n = upsert_tabla(client, "fact_maquinas", filas,
                     on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")
    logger.info("\nActualizadas %d máquinas.", n)
    return dif


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra el diff sin escribir en la base")
    args = ap.parse_args()
    reconciliar(get_client(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
