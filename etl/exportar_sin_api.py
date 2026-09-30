"""
Exporta para el SaaS los datos que ninguna API trae (tarea 6.9 del checklist).

Viven solo en este Supabase porque se cargan a mano o desde Excel: el SaaS no
los puede sincronizar de Autoventa ni de Obuma. No los carga en el SaaS: deja
un paquete para revisar e importar después.

Genera en exports/sin_api_AAAAMMDD/ (carpeta ignorada por git: la cartera y
los despachos traen RUT de clientes, no se suben al repo) y un .zip con lo mismo:

    metas_maquinas.csv      objetivos_maquinas: metas del control de máquinas
    comision_entradas.csv   comision_entrada_mensual: lo que gerencia ingresa
                            cada mes por vendedor (cartera, salas, overrides,
                            ajustes)
    comision_valor_fijo.csv comision_valor_fijo: los valores que se repiten
                            todos los meses si no hay entrada mensual
    cartera.csv             cartera_cliente: la cartera oficial (cliente →
                            vendedor, ruta)
    despachos.csv           fact_despachos: estado de entrega de cada documento
    control.csv             filas por archivo, para cuadrar la importación
    LEEME.txt               qué es cada archivo

Cada archivo con vendedor_id lleva también vendedor_nombre: los id son los de
la app (enteros) y no existen en el SaaS, donde el vendedor es un empleado.

Uso (desde la carpeta del proyecto, con el .env de siempre):
    python -m etl.exportar_sin_api
    python -m etl.exportar_sin_api --salida "D:/respaldos"

No escribe nada en la base: solo lee.
"""
import argparse
import logging
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.config import SOCIEDAD_ID  # noqa: E402
from etl.exportar_acuna import _fmt, _leer_por_id, _leer_tabla  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# El nombre de la sociedad como lo usa el SaaS (ventas.sociedad).
SOCIEDAD_NOMBRE = {v: k for k, v in SOCIEDAD_ID.items()}

# (archivo, tabla, columnas, orden). Las tablas sin id son chicas (< 1.000 filas).
TABLAS = [
    ("metas_maquinas", "objetivos_maquinas", "*", "anio"),
    ("comision_entradas", "comision_entrada_mensual", "*", "vendedor_id"),
    ("comision_valor_fijo", "comision_valor_fijo", "*", "vendedor_id"),
    ("cartera", "cartera_cliente", "*", "cliente_rut"),
]


# ── Armado (funciones puras, se prueban sin base) ───────────────────────────

def pegar_vendedor(df: pd.DataFrame, vendedores: pd.DataFrame) -> pd.DataFrame:
    """Agrega vendedor_nombre junto a vendedor_id. Un id que no está en
    dim_vendedor queda con el nombre vacío: se ve en el archivo en vez de
    perder la fila."""
    if df.empty or "vendedor_id" not in df.columns:
        return df
    ven = vendedores.rename(columns={"id": "vendedor_id", "nombre_canonico": "vendedor_nombre"})
    d = df.copy()
    d["vendedor_id"] = pd.to_numeric(d["vendedor_id"], errors="coerce").astype("Int64")
    ven["vendedor_id"] = pd.to_numeric(ven["vendedor_id"], errors="coerce").astype("Int64")
    d = d.merge(ven[["vendedor_id", "vendedor_nombre"]], on="vendedor_id", how="left")
    cols = list(df.columns)
    cols.insert(cols.index("vendedor_id") + 1, "vendedor_nombre")
    return d[cols]


def pegar_sociedad(df: pd.DataFrame) -> pd.DataFrame:
    """sociedad_id (1/2 de la app) → sociedad ('acuna'/'grannatural' del SaaS)."""
    if df.empty or "sociedad_id" not in df.columns:
        return df
    d = df.copy()
    d.insert(list(d.columns).index("sociedad_id") + 1, "sociedad",
             pd.to_numeric(d["sociedad_id"], errors="coerce").map(SOCIEDAD_NOMBRE))
    return d


def armar_control(archivos: dict) -> pd.DataFrame:
    filas = []
    for nombre, df in archivos.items():
        fila = {"archivo": f"{nombre}.csv", "filas": len(df)}
        if "vendedor_id" in df.columns:
            fila["vendedores"] = int(df["vendedor_id"].nunique())
            fila["sin_nombre_vendedor"] = int(
                (df["vendedor_id"].notna() & df["vendedor_nombre"].isna()).sum())
        if "cliente_rut" in df.columns:
            fila["clientes"] = int(df["cliente_rut"].nunique())
        filas.append(fila)
    return pd.DataFrame(filas)


LEEME = """DATOS SIN API · paquete para el SaaS
Generado el {hoy} desde el Supabase de la app comercial (solo lectura).

Son los datos que ninguna API trae: se cargan a mano o desde Excel en la app,
así que el SaaS no los puede sincronizar. Nada de esto está cargado en el SaaS.

ARCHIVOS
  metas_maquinas.csv       Metas del control de máquinas por mes. La app usa
                           solo meta_gestiones_semana y meta_pct_entregado; las
                           otras columnas son de indicadores que salieron en
                           sep-2026.
  comision_entradas.csv    Lo que gerencia ingresa cada mes por vendedor:
                           cartera_clientes, salas_ganga, los *_override
                           (reemplazan el valor calculado) y ajuste_monto /
                           ajuste_motivo. Llave: (vendedor_id, anio, mes).
  comision_valor_fijo.csv  cartera_clientes y salas_ganga que valen para todos
                           los meses si el vendedor no tiene entrada mensual.
  cartera.csv              La cartera oficial: cada cliente con su vendedor,
                           código de cliente, ruta y n° de sucursales.
  despachos.csv            Estado de entrega de cada documento (Entregada,
                           Rechazada…), del Excel de despachos de Autoventa.
                           `sociedad` ya viene como en el SaaS (grannatural /
                           acuna). es_maquina marca los fletes de máquina.
  control.csv              Filas por archivo, vendedores y clientes distintos.

FORMATO
  CSV en UTF-8, separador coma, punto decimal, fechas AAAA-MM-DD.
  RUT con puntos, guion y DV en mayúscula (12.345.678-9).

VENDEDORES
  vendedor_id es el id de la app (dim_vendedor), que no existe en el SaaS. Por
  eso cada archivo trae vendedor_nombre al lado: en el SaaS se cruza con el
  nombre completo del empleado. Si control.csv muestra sin_nombre_vendedor > 0,
  hay filas con un id que ya no está en dim_vendedor.

PRIVACIDAD
  cartera.csv y despachos.csv traen RUT de clientes. No subir a un repo ni a
  un documento compartido.
"""


def main():
    ap = argparse.ArgumentParser(description="Exporta los datos sin API para el SaaS.")
    ap.add_argument("--salida", default=str(ROOT / "exports"),
                    help="carpeta donde dejar el paquete (por defecto exports/)")
    args = ap.parse_args()

    from etl.db import get_client
    client = get_client()

    vendedores = _leer_tabla(client, "dim_vendedor", "id,nombre_canonico", "id")
    archivos = {}
    for nombre, tabla, columnas, orden in TABLAS:
        df = _leer_tabla(client, tabla, columnas, orden)
        archivos[nombre] = pegar_vendedor(df, vendedores)
        logger.info("  %s: %s filas", tabla, _fmt(len(df)))
    desp = _leer_por_id(client, "fact_despachos", "*")
    archivos["despachos"] = pegar_sociedad(pegar_vendedor(desp, vendedores))
    logger.info("  fact_despachos: %s filas", _fmt(len(desp)))

    control = armar_control(archivos)
    hoy = date.today()
    carpeta = Path(args.salida) / f"sin_api_{hoy:%Y%m%d}"
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, df in archivos.items():
        df.to_csv(carpeta / f"{nombre}.csv", index=False, encoding="utf-8")
    control.to_csv(carpeta / "control.csv", index=False, encoding="utf-8")
    (carpeta / "LEEME.txt").write_text(LEEME.format(hoy=f"{hoy:%d-%m-%Y}"), encoding="utf-8")

    zip_path = carpeta.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(carpeta.iterdir()):
            z.write(f, arcname=f"{carpeta.name}/{f.name}")

    logger.info("\n%s", control.to_string(index=False))
    logger.info("Paquete listo: %s (+ %s)", carpeta, zip_path.name)


if __name__ == "__main__":
    main()
