"""
Configuración central del ETL.

Estrategia de descubrimiento de archivos:
  Los exports de Obuma incluyen la fecha en el nombre (ej. "...20260603ACUÑA.xls"),
  por lo que usamos patrones glob en lugar de nombres fijos.
  Si en algún momento se estandarizan los nombres, solo hay que cambiar este archivo.
"""
from pathlib import Path

# Raíz del proyecto (dos niveles arriba de este archivo)
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "muestras"   # legacy: carpeta plana (fallback sin --periodo)

# ── Estructura mensual organizada (esquema vigente) ─────────────────────────
# Una carpeta por fuente bajo data/mensual/. El archivo de cada mes se identifica
# por el período AAAA-MM en el nombre. Convención de nombre: "<fuente>_AAAA-MM".
#   data/mensual/
#     acuna/         acuna_2026-06.xls
#     gran_natural/  gran_natural_2026-06.xls
#     pedidos/       pedidos_2026-06.csv
#     despachos/     despachos_2026-06.xlsx
#     objetivos/     objetivos_2026-06.xlsx
# El reconocimiento es: carpeta (fuente) + token AAAA-MM en el nombre + extensión.
# Tolerante a separador '-' o '_' en el token (2026-06 == 2026_06).
MENSUAL_DIR = ROOT / "data" / "mensual"
FUENTES = {
    "obuma_acuna":         {"carpeta": "acuna",        "ext": ".xls"},
    "obuma_grannatural":   {"carpeta": "gran_natural", "ext": ".xls"},
    "autoventa_pedidos":   {"carpeta": "pedidos",      "ext": ".csv"},
    "autoventa_despachos": {"carpeta": "despachos",    "ext": ".xlsx"},
    "objetivos":           {"carpeta": "objetivos",    "ext": ".xlsx"},  # opcional (editable en app)
}

# ── Reconocimiento legacy por palabra clave (solo carpeta plana data/muestras) ─
# Se mantiene como fallback cuando run_etl se corre SIN --periodo. Con --periodo
# se usa el esquema mensual de arriba (sin ambigüedad).
#   · ext:     extensión esperada (en minúscula).
#   · incluye: TODAS estas palabras deben estar en el nombre.
#   · excluye: NINGUNA de estas palabras puede estar (evita confundir sociedades).
FILE_MATCH = {
    "obuma_acuna":         {"ext": ".xls",  "incluye": ["ACUNA"],           "excluye": ["GRAN", "NATURAL"]},
    "obuma_grannatural":   {"ext": ".xls",  "incluye": ["GRAN", "NATURAL"], "excluye": []},
    "autoventa_pedidos":   {"ext": ".csv",  "incluye": ["PEDIDOS"],         "excluye": []},
    "autoventa_despachos": {"ext": ".xlsx", "incluye": ["DESPACHO"],        "excluye": []},
    "objetivos":           {"ext": ".xlsx", "incluye": ["OBJETIVO"],        "excluye": []},  # puede no existir aún
}

# ── IDs de sociedad (deben coincidir con dim_sociedad en Supabase) ───────────
SOCIEDAD_ID = {
    "acuna":       1,
    "grannatural": 2,
}

# ── Categoría y códigos que identifican movimientos de máquinas ─────────────
# CATEGORIA_MAQUINAS         → nombre de la categoría en Autoventa (detalle CSV).
# CATEGORIA_MAQUINAS_OBUMA   → nombre de la categoría en Obuma (export de ventas).
#   Ojo: difieren entre ERPs ("MAQUINAS_POP" vs "Maquinas").
CATEGORIA_MAQUINAS = "MAQUINAS_POP"
CATEGORIA_MAQUINAS_OBUMA = "Maquinas"
TIPO_MOV_MAP = {
    "FL-4": "nueva",    # Flete Instalación Cliente Nuevo
    "FL-1": "cambio",   # Cambio de máquina
    "FL-3": "cambio",   # Flete Instalación x Cambio Máquina mala
    "FL-5": "cambio",   # Flete Instalación x Cambio Tamaño Máquina
    "FL-2": "retiro",   # Flete Retiro x Término
}

# ── Tipos de documento Obuma: cuáles llevan signo negativo ──────────────────
# "NOTA DE DEBITO" en rigor es positiva (cargo al cliente), pero si el negocio
# la trata como corrección, cambiar aquí.
TIPO_DCTO_NEGATIVO = {"NOTA DE CREDITO ELECTRONICA"}

# ── Tamaño de lote para upserts a Supabase ──────────────────────────────────
BATCH_SIZE = 500
