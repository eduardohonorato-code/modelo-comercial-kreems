"""
Reconcilia el estado de entrega de fact_maquinas y la marca es_maquina de
fact_despachos contra los despachos.

Por qué hace falta un script aparte: la sincronización que corre al subir el
Excel de despachos mira un mes a la vez, y la ruta de una máquina puede caer en
un mes distinto al de su factura (se factura a fin de mes y se entrega al mes
siguiente). Esas máquinas quedaban 'gestionada' para siempre. La página Carga ya
cruza por documento sin importar el mes, pero lo que quedó mal en la base no se
arregla solo: para eso está este script.

Cruza por (documento, cliente_rut) — Obuma "N° DCTO" = Autoventa "Documento",
del MISMO cliente — sin filtrar por fecha, y solo escribe lo que cambia:

  1. Estado de la máquina según el despacho de su cliente.
  2. Cruces falsos: una máquina en 'entregada'/'rechazada' sin despacho de su
     cliente, pero cuyo folio SÍ tiene despacho de otro cliente, vuelve a
     'gestionada'. Es el rastro del cruce antiguo solo por número: las NC de
     flete (serie de folios propia) calzaban con facturas de otro cliente.
  3. es_maquina de cada despacho = su (documento, cliente) es una máquina.

Uso:
    python -m etl.reconciliar_maquinas --dry-run     # muestra el diff, no escribe
    python -m etl.reconciliar_maquinas              # aplica
"""
import argparse
import logging

import pandas as pd

from etl.db import get_client
from etl.maquinas import (estado_por_documento_cliente,
                          marcar_despachos_maquina, _norm_doc)
from etl.upsert import upsert_tabla

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

COLS_MAQ = ("documento,fecha,vendedor_id,cliente_rut,tipo_mov,estado,sociedad_id")

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


def diff_estados(maq: pd.DataFrame, desp: pd.DataFrame) -> pd.DataFrame:
    """Filas de fact_maquinas cuyo estado cambia, con la columna estado_nuevo."""
    estado = estado_por_documento_cliente(desp)
    doc = _norm_doc(maq["documento"])
    rut = maq["cliente_rut"].astype(str).str.strip()
    nuevo = pd.Series([estado.get(k) for k in zip(doc, rut)],
                      index=maq.index, dtype=object)

    # Cruce falso heredado: el folio tiene despacho, pero de OTRO cliente.
    docs_desp = set(_norm_doc(desp["documento"]))
    mal_cruzada = (nuevo.isna() & doc.isin(docs_desp)
                   & maq["estado"].isin(["entregada", "rechazada"]))
    nuevo = nuevo.mask(mal_cruzada, "gestionada")

    cambia = nuevo.notna() & (nuevo != maq["estado"])
    dif = maq[cambia].copy()
    dif["estado_nuevo"] = nuevo[cambia]
    dif["motivo"] = mal_cruzada[cambia].map(
        {True: "cruce con despacho de otro cliente",
         False: "despacho del mismo cliente"})
    return dif


def diff_es_maquina(desp: pd.DataFrame, maq: pd.DataFrame) -> pd.DataFrame:
    """Despachos cuya marca es_maquina cambia, con la columna es_maquina_nuevo."""
    esperado = marcar_despachos_maquina(desp, maq)["es_maquina"]
    actual = desp["es_maquina"].fillna(False).astype(bool)
    cambia = esperado != actual
    dif = desp[cambia].copy()
    dif["es_maquina_nuevo"] = esperado[cambia]
    return dif


def reconciliar(client, dry_run: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    maq = _leer_todo(client, "fact_maquinas", "id," + COLS_MAQ)
    desp = _leer_todo(client, "fact_despachos",
                      "id,documento,cliente_rut,estado,fecha_ruta,es_maquina")
    logger.info("Máquinas: %d | Despachos: %d", len(maq), len(desp))
    if maq.empty or desp.empty:
        return pd.DataFrame(), pd.DataFrame()

    dif = diff_estados(maq, desp)
    dif_em = diff_es_maquina(desp, maq)

    if dif.empty:
        logger.info("Estados de máquinas: nada que reconciliar.")
    else:
        dif["_mes"] = pd.to_datetime(dif["fecha"], errors="coerce").dt.to_period("M")
        logger.info("Movimientos con estado desactualizado: %d", len(dif))
        logger.info("\n%s", pd.crosstab(
            dif["_mes"].astype(str), [dif["estado"], dif["estado_nuevo"]]).to_string())
        logger.info("\nPor tipo de movimiento:\n%s", pd.crosstab(
            dif["tipo_mov"], dif["estado_nuevo"]).to_string())
        logger.info("\nDetalle:\n%s", dif[
            ["documento", "fecha", "cliente_rut", "tipo_mov", "estado",
             "estado_nuevo", "motivo"]].to_string(index=False))

    if dif_em.empty:
        logger.info("\nes_maquina de despachos: nada que reconciliar.")
    else:
        logger.info("\nDespachos con es_maquina desactualizado: %d", len(dif_em))
        logger.info("\n%s", dif_em[
            ["id", "documento", "fecha_ruta", "cliente_rut", "estado",
             "es_maquina", "es_maquina_nuevo"]].to_string(index=False))

    if dry_run:
        logger.info("\n--dry-run: no se escribió nada.")
        return dif, dif_em

    if not dif.empty:
        filas = dif.drop(columns=["id", "estado", "_mes", "motivo"]).rename(
            columns={"estado_nuevo": "estado"})
        n = upsert_tabla(client, "fact_maquinas", filas,
                         on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")
        logger.info("\nActualizadas %d máquinas.", n)
    # es_maquina por id: la columna no es parte de la llave natural del upsert.
    for valor in (True, False):
        ids = sorted(int(i) for i in
                     dif_em.loc[dif_em["es_maquina_nuevo"] == valor, "id"])
        for i in range(0, len(ids), 100):
            (client.table("fact_despachos").update({"es_maquina": valor})
             .in_("id", ids[i:i + 100]).execute())
        if ids:
            logger.info("Despachos marcados es_maquina=%s: %d", valor, len(ids))
    return dif, dif_em


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra el diff sin escribir en la base")
    args = ap.parse_args()
    reconciliar(get_client(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
