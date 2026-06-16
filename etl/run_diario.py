"""
Carga diaria automática — Obuma (ventas+máquinas) + Autoventa (pedidos) vía API.

Pensado para el Programador de tareas de Windows: sin argumentos, calcula solo
qué períodos cargar y deja registro en etl_auto.log.

Reglas:
  - Siempre carga el MES EN CURSO.
  - Los primeros 5 días del mes carga TAMBIÉN el mes anterior (facturas emitidas
    el 30-31 pueden llegar tarde; el upsert idempotente las incorpora sin duplicar).
  - Si una fuente falla (ej. API caída), la otra igual se carga; el error queda
    en el log y el código de salida es != 0 para que el Programador lo marque.

Uso manual (equivale a lo que corre la tarea programada):
    python -m etl.run_diario
"""
import sys
import logging
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cargar el .env con ruta explícita: la tarea programada puede partir con otro
# directorio de trabajo y load_dotenv() sin ruta no lo encontraría.
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# Log a archivo + consola (configurar ANTES de importar los runners, que
# también llaman basicConfig — solo la primera configuración aplica).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "etl_auto.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

from etl.run_obuma_api import run as run_obuma          # noqa: E402
from etl.run_autoventa_api import run as run_autoventa  # noqa: E402


def _periodos(hoy: date) -> list[tuple]:
    """Mes en curso; + mes anterior si estamos en los primeros 5 días."""
    out = [(hoy.year, hoy.month)]
    if hoy.day <= 5:
        prev = (hoy.year, hoy.month - 1) if hoy.month > 1 else (hoy.year - 1, 12)
        out.insert(0, prev)
    return out


def main() -> int:
    hoy = date.today()
    periodos = _periodos(hoy)
    logger.info("#" * 60)
    logger.info("CARGA DIARIA %s | períodos: %s",
                datetime.now().isoformat(timespec="seconds"),
                [f"{a}-{m:02d}" for a, m in periodos])

    errores = 0
    for periodo in periodos:
        for nombre, fn in [("Obuma", run_obuma), ("Autoventa", run_autoventa)]:
            try:
                fn(periodo)
            except Exception:
                errores += 1
                logger.exception("FALLO %s %d-%02d (la carga continúa con el resto)",
                                 nombre, *periodo)

    logger.info("CARGA DIARIA terminada | errores: %d", errores)
    logger.info("#" * 60)
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
