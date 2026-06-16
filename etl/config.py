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
DATA_DIR = ROOT / "data" / "muestras"

# ── Reconocimiento de archivos por palabra clave ────────────────────────────
# En vez de un patrón glob exacto (que dependía de escribir "ACUÑA" con ñ), se
# busca por palabras clave sobre el nombre NORMALIZADO (sin acentos, mayúsculas).
# Así "acuna", "Acuña" o "ACUÑA" se reconocen igual.
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
