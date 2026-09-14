"""
Reconcilia la cola de pedidos de flete "Sin DTE" contra Autoventa.

Por qué hace falta: el ETL diario solo vuelve a leer el MES EN CURSO. Un pedido
de julio que se factura o se anula en septiembre queda congelado en la base con
el estado que tenía la última vez que se leyó su mes, y sigue apareciendo como
pendiente para siempre. En la revisión del 14-09-2026 la app mostraba 37 sin DTE
y Autoventa tenía 27: 2 ya estaban facturados y 8 ya no existían (anulados, o
reingresados con otro número por el vendedor de reemplazo).

Qué hace: toma los pedidos FL abiertos de la base, los busca en `/requests` de
Autoventa en sus fechas de despacho y en una ventana móvil alrededor de hoy
(una fecha reprogramada cae ahí), y corrige `estado_pedido`:

  · aparece facturado        → 'invoiced'  (deja de contar como pendiente)
  · aparece sin facturar     → el estado que informa la API
  · no aparece en ninguna    → 'no_existe_en_api' (se trata como fantasma)

Solo toca `estado_pedido`; nunca borra filas. Si un pedido marcado como
inexistente vuelve a aparecer, la carga normal de su mes le repone el estado.

Uso:
    python -m etl.reconciliar_pedidos_sin_dte --dry-run
    python -m etl.reconciliar_pedidos_sin_dte
"""
import argparse
import logging
from datetime import date, timedelta

import pandas as pd

from etl.db import get_client
from etl.loaders.autoventa_api import _get, _EXPANDS_REQUESTS

logger = logging.getLogger(__name__)

CODIGOS_FL = ["FL-1", "FL-2", "FL-3", "FL-4", "FL-5"]
NO_EXISTE = "no_existe_en_api"

# Ventana móvil de fechas de despacho que se barre además de las propias de cada
# pedido. Cubre las reprogramaciones habituales sin recorrer el año entero.
DIAS_ATRAS, DIAS_ADELANTE = 45, 30


def _cola_abierta(client) -> pd.DataFrame:
    """Pedidos FL de la base que hoy cuentan como sin DTE."""
    filas, off = [], 0
    while True:
        r = (client.table("fact_pedidos")
             .select("n_pedido,fecha,doc_venta,estado_pedido,producto_codigo")
             .in_("producto_codigo", CODIGOS_FL)
             .eq("doc_venta", "Sin DTE")
             .order("id").range(off, off + 999).execute())
        if not r.data:
            break
        filas += r.data
        if len(r.data) < 1000:
            break
        off += 1000
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df["n_pedido"] = df["n_pedido"].astype(str).str.strip()
    est = df["estado_pedido"].astype(str).str.lower()
    # Ya resueltos en una pasada anterior: no hace falta volver a buscarlos.
    return df[~est.isin(["invoiced", NO_EXISTE])]


def _estado_en_api(fechas: set) -> dict:
    """correlativo → {'status', 'billed'} de lo que devuelve /requests."""
    out = {}
    for f in sorted(fechas):
        try:
            reqs = _get(f"/requests?dispatch_date={f.isoformat()}&{_EXPANDS_REQUESTS}") or []
        except Exception as exc:
            # Si una fecha falla, no se puede afirmar que el pedido no existe:
            # se aborta en vez de marcar fantasmas por error.
            raise RuntimeError(f"Autoventa no respondió para {f}: {exc}") from exc
        for r in reqs:
            fl = [l for l in (r.get("lines") or [])
                  if str(l.get("product_code", "")).upper().startswith("FL-")]
            if not fl:
                continue
            out[str(r.get("correlative"))] = {
                "status": r.get("status"),
                "billed": all(bool(l.get("billed")) for l in fl),
            }
    return out


def reconciliar(client=None, dry_run: bool = False, hoy: date | None = None) -> dict:
    client = client or get_client()
    hoy = hoy or date.today()
    cola = _cola_abierta(client)
    if cola.empty:
        logger.info("Cola sin DTE vacía: nada que reconciliar.")
        return {"revisados": 0}

    fechas = set(pd.to_datetime(cola["fecha"], errors="coerce").dropna().dt.date)
    d = hoy - timedelta(days=DIAS_ATRAS)
    while d <= hoy + timedelta(days=DIAS_ADELANTE):
        fechas.add(d)
        d += timedelta(days=1)

    logger.info("Reconciliando %d pedidos sin DTE contra Autoventa (%d fechas)…",
                len(cola), len(fechas))
    api = _estado_en_api(fechas)

    cambios = []
    for n in cola["n_pedido"].unique():
        info = api.get(n)
        if info is None:
            nuevo = NO_EXISTE
        elif info["billed"] or str(info["status"]).lower() == "invoiced":
            nuevo = "invoiced"
        else:
            nuevo = str(info["status"])
        actual = cola.loc[cola["n_pedido"] == n, "estado_pedido"].iloc[0]
        if str(actual) != nuevo:
            cambios.append((n, actual, nuevo))

    res = {
        "revisados": int(cola["n_pedido"].nunique()),
        "facturados": sum(1 for c in cambios if c[2] == "invoiced"),
        "no_existen": sum(1 for c in cambios if c[2] == NO_EXISTE),
        "otros_cambios": sum(1 for c in cambios if c[2] not in ("invoiced", NO_EXISTE)),
    }
    for n, antes, despues in cambios:
        logger.info("  pedido %s: %s → %s", n, antes, despues)
    logger.info("Resultado: %s", res)

    if not dry_run:
        for n, _, nuevo in cambios:
            (client.table("fact_pedidos").update({"estado_pedido": nuevo})
             .eq("n_pedido", n).in_("producto_codigo", CODIGOS_FL)
             .eq("doc_venta", "Sin DTE").execute())
    return res


def main():
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    reconciliar(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    main()
