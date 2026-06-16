"""
Helper genérico de upsert a Supabase en lotes.
Usa el método .upsert() del cliente oficial que emite
  INSERT ... ON CONFLICT (col) DO UPDATE SET ...
lo que garantiza idempotencia sin importar cuántos períodos traiga el archivo.
"""
import math
import logging
import pandas as pd
from supabase import Client
from etl.config import BATCH_SIZE

logger = logging.getLogger(__name__)


def upsert_tabla(
    client: Client,
    tabla: str,
    df: pd.DataFrame,
    on_conflict: str,
) -> int:
    """
    Hace upsert de `df` en `tabla` en lotes de BATCH_SIZE filas.

    Args:
        client:      cliente Supabase (service_role).
        tabla:       nombre de la tabla en el schema public.
        df:          DataFrame con las filas a insertar/actualizar.
        on_conflict: columna(s) que forman la llave natural, separadas por coma.
                     Debe coincidir con el UNIQUE constraint de la tabla.
    Returns:
        Número de filas procesadas.
    """
    if df.empty:
        logger.info("  [%s] DataFrame vacío, nada que insertar.", tabla)
        return 0

    # Reemplazar NaN/NaT por None (JSON null) para que Postgres los acepte
    registros = (
        df.where(pd.notna(df), other=None)
        .astype(object)
        .where(df.notna(), other=None)
        .to_dict(orient="records")
    )

    total = len(registros)
    n_lotes = math.ceil(total / BATCH_SIZE)
    procesados = 0

    for i in range(n_lotes):
        lote = registros[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        try:
            client.table(tabla).upsert(lote, on_conflict=on_conflict).execute()
            procesados += len(lote)
            logger.info(
                "  [%s] lote %d/%d → %d filas OK (total acum. %d)",
                tabla, i + 1, n_lotes, len(lote), procesados,
            )
        except Exception as exc:
            logger.error(
                "  [%s] lote %d/%d FALLÓ: %s\n  Primera fila del lote: %s",
                tabla, i + 1, n_lotes, exc, lote[0] if lote else "—",
            )
            raise

    return procesados
