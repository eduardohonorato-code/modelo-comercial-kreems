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
    omitir_nulos: bool = False,
) -> int:
    """
    Hace upsert de `df` en `tabla` en lotes de BATCH_SIZE filas.

    Args:
        client:      cliente Supabase (service_role).
        tabla:       nombre de la tabla en el schema public.
        df:          DataFrame con las filas a insertar/actualizar.
        on_conflict: columna(s) que forman la llave natural, separadas por coma.
                     Debe coincidir con el UNIQUE constraint de la tabla.
        omitir_nulos: si es True, las columnas que vengan vacías NO se mandan, así
                     el ON CONFLICT DO UPDATE no las pisa con NULL. Para las
                     dimensiones que se alimentan de varias fuentes (dim_cliente:
                     Obuma trae región/comuna/tipo, Autoventa no), donde "no sé"
                     no debe borrar lo que otra fuente sí sabía.
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

    # PostgREST exige que todos los objetos de un mismo request traigan las
    # mismas claves, así que al sacar los nulos se agrupa por "qué columnas
    # quedaron" y se manda un request por grupo.
    if omitir_nulos:
        claves = [c.strip() for c in on_conflict.split(",")]
        grupos: dict = {}
        n_antes = sum(len(r) for r in registros)
        for r in registros:
            limpio = {k: v for k, v in r.items() if v is not None or k in claves}
            grupos.setdefault(tuple(sorted(limpio)), []).append(limpio)
        n_despues = sum(len(r) for g in grupos.values() for r in g)
        if n_antes != n_despues:
            logger.info(
                "  [%s] omitir_nulos: %d celdas vacías NO se mandan (no pisan lo "
                "ya cargado) · %d combinaciones de columnas",
                tabla, n_antes - n_despues, len(grupos))
        particiones = list(grupos.values())
    else:
        particiones = [registros]

    total = len(registros)
    n_lotes = sum(math.ceil(len(p) / BATCH_SIZE) for p in particiones)
    procesados = 0
    i = 0

    for parte in particiones:
        for j in range(math.ceil(len(parte) / BATCH_SIZE)):
            lote = parte[j * BATCH_SIZE : (j + 1) * BATCH_SIZE]
            i += 1
            try:
                client.table(tabla).upsert(lote, on_conflict=on_conflict).execute()
                procesados += len(lote)
                logger.info(
                    "  [%s] lote %d/%d → %d filas OK (total acum. %d de %d)",
                    tabla, i, n_lotes, len(lote), procesados, total,
                )
            except Exception as exc:
                logger.error(
                    "  [%s] lote %d/%d FALLÓ: %s\n  Primera fila del lote: %s",
                    tabla, i, n_lotes, exc, lote[0] if lote else "—",
                )
                raise

    return procesados
