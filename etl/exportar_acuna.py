"""
Exporta el histórico de Acuña para importarlo en el SaaS.

Acuña dejó de facturar en julio de 2026 (su último documento en la base es del
13-07-2026) y no tiene API: todo lo que hay de ella está en este Supabase,
cargado desde los Excel de Obuma. Es una carga ÚNICA, no un proceso que se
repita, así que el objetivo es un paquete de archivos autoexplicativo que el
SaaS pueda importar sin tener que preguntar nada.

Genera en exports/acuna_AAAAMMDD/ (carpeta ignorada por git: trae RUT y datos
de clientes, no se sube al repo) y un .zip con lo mismo:

    venta_items.csv   una fila por línea de documento (~247 mil)
    ventas.csv        una fila por documento, con sus totales
    clientes.csv      los clientes que aparecen en esas ventas
    productos.csv     los productos que aparecen en esas ventas
    vendedores.csv    los vendedores que aparecen en esas ventas
    maquinas.csv      los movimientos de máquina (FL-x) de Acuña
    control.csv       líneas, documentos y montos por mes y tipo de documento
    LEEME.txt         qué es cada columna y cómo cuadrar la importación

Uso (desde la carpeta del proyecto, con el .env de siempre):
    python -m etl.exportar_acuna
    python -m etl.exportar_acuna --salida "D:/respaldos"

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SOCIEDAD = SOCIEDAD_ID["acuna"]
_PAGINA = 1000   # tope de filas por consulta de PostgREST

_COLS_VENTA = ("id,fecha,tipo_dcto,n_dcto,linea,vendedor_id,cliente_rut,producto_codigo,"
               "sucursal,cantidad,neto,total,costo,margen,direccion_id")
_COLS_ITEMS = ["sociedad", "fecha", "tipo_dcto", "n_dcto", "linea", "cliente_rut",
               "cliente_razon_social", "vendedor_id", "vendedor_nombre",
               "cod_vendedor_autoventa", "producto_codigo", "producto_nombre",
               "categoria", "subcategoria", "fabricante", "unidad_medida", "sucursal",
               "direccion_id", "direccion_nombre", "direccion", "direccion_comuna",
               "cantidad", "neto", "total", "costo", "margen"]


# ── Lectura ──────────────────────────────────────────────────────────────────

def _leer_por_id(client, tabla: str, columnas: str, **filtros) -> pd.DataFrame:
    """Toda la tabla filtrada, paginando por id (más estable que por offset)."""
    filas, ultimo = [], 0
    while True:
        q = client.table(tabla).select(columnas).gt("id", ultimo)
        for col, val in filtros.items():
            q = q.eq(col, val)
        lote = q.order("id").limit(_PAGINA).execute().data or []
        filas.extend(lote)
        if len(lote) < _PAGINA:
            break
        ultimo = lote[-1]["id"]
        if (len(filas) // _PAGINA) % 25 == 0:
            logger.info("  %s: %s filas leídas…", tabla, _fmt(len(filas)))
    return pd.DataFrame(filas)


def _leer_tabla(client, tabla: str, columnas: str, orden: str) -> pd.DataFrame:
    """Tablas chicas sin id (dimensiones), paginando por offset."""
    filas, desde = [], 0
    while True:
        lote = (client.table(tabla).select(columnas).order(orden)
                .range(desde, desde + _PAGINA - 1).execute().data or [])
        filas.extend(lote)
        if len(lote) < _PAGINA:
            break
        desde += _PAGINA
    return pd.DataFrame(filas)


def _contar(client, tabla: str, **filtros) -> int:
    q = client.table(tabla).select("id", count="exact")
    for col, val in filtros.items():
        q = q.eq(col, val)
    return q.limit(1).execute().count or 0


# ── Armado (funciones puras, se prueban sin base) ───────────────────────────

def armar_items(ventas: pd.DataFrame, clientes: pd.DataFrame, productos: pd.DataFrame,
                vendedores: pd.DataFrame, direcciones: pd.DataFrame) -> pd.DataFrame:
    """Una fila por línea, con los nombres pegados para que el archivo se lea solo."""
    v = ventas.copy()
    v["sociedad"] = "Acuña"
    cli = clientes.rename(columns={"rut": "cliente_rut", "razon_social": "cliente_razon_social"})
    v = v.merge(cli[["cliente_rut", "cliente_razon_social"]], on="cliente_rut", how="left")
    prod = productos.rename(columns={"codigo": "producto_codigo", "nombre": "producto_nombre"})
    v = v.merge(prod[["producto_codigo", "producto_nombre", "categoria", "subcategoria",
                      "fabricante", "unidad_medida"]], on="producto_codigo", how="left")
    ven = vendedores.rename(columns={"id": "vendedor_id", "nombre_canonico": "vendedor_nombre"})
    v = v.merge(ven[["vendedor_id", "vendedor_nombre", "cod_vendedor_autoventa"]],
                on="vendedor_id", how="left")
    if direcciones is not None and not direcciones.empty:
        d = direcciones.rename(columns={"id": "direccion_id", "nombre": "direccion_nombre",
                                        "comuna": "direccion_comuna"})
        v = v.merge(d[["direccion_id", "direccion_nombre", "direccion", "direccion_comuna"]],
                    on="direccion_id", how="left")
    for col in _COLS_ITEMS:
        if col not in v.columns:
            v[col] = None
    v["direccion_id"] = pd.to_numeric(v["direccion_id"], errors="coerce").astype("Int64")
    v["vendedor_id"] = pd.to_numeric(v["vendedor_id"], errors="coerce").astype("Int64")
    return (v[_COLS_ITEMS]
            .sort_values(["fecha", "tipo_dcto", "n_dcto", "linea"])
            .reset_index(drop=True))


def armar_documentos(items: pd.DataFrame) -> pd.DataFrame:
    """Cabecera por documento. La llave es (tipo_dcto, n_dcto): el mismo folio
    existe como factura y como nota de crédito (2 casos en Acuña)."""
    llave = ["sociedad", "tipo_dcto", "n_dcto"]
    return (items.groupby(llave, as_index=False)
            .agg(fecha=("fecha", "first"), cliente_rut=("cliente_rut", "first"),
                 cliente_razon_social=("cliente_razon_social", "first"),
                 vendedor_id=("vendedor_id", "first"),
                 vendedor_nombre=("vendedor_nombre", "first"),
                 sucursal=("sucursal", "first"), n_lineas=("linea", "size"),
                 neto=("neto", "sum"), total=("total", "sum"),
                 costo=("costo", "sum"), margen=("margen", "sum"))
            .sort_values(["fecha", "tipo_dcto", "n_dcto"])
            .reset_index(drop=True))


def armar_control(items: pd.DataFrame) -> pd.DataFrame:
    """Los números contra los que se cuadra la importación, por mes y tipo."""
    c = items.assign(mes=items["fecha"].astype(str).str[:7])
    tabla = (c.groupby(["mes", "tipo_dcto"], as_index=False)
             .agg(lineas=("linea", "size"), documentos=("n_dcto", "nunique"),
                  neto=("neto", "sum"), total=("total", "sum"), costo=("costo", "sum")))
    total = pd.DataFrame([{"mes": "TOTAL", "tipo_dcto": "(todos)",
                           "lineas": len(c),
                           "documentos": c.groupby(["tipo_dcto", "n_dcto"]).ngroups,
                           "neto": c["neto"].sum(), "total": c["total"].sum(),
                           "costo": c["costo"].sum()}])
    return pd.concat([tabla, total], ignore_index=True)


_LEEME = """\
HISTÓRICO DE VENTAS ACUÑA · paquete de importación para el SaaS
Generado el {hoy} desde el Supabase de la app comercial (solo lectura).

Acuña dejó de facturar en julio de 2026. Esto es TODO lo que hay de ella:
{lineas} líneas, {docs} documentos, del {desde} al {hasta}. Neto total: {neto}.

FORMATO
  CSV en UTF-8, separador coma, punto decimal, fechas AAAA-MM-DD.
  RUT con el formato de la app: con puntos, guion y DV en mayúscula
  (12.345.678-9 / 7.654.321-K). Si el SaaS guarda el RUT de otra forma,
  normalizarlo antes de cruzar con sus clientes.

REGLAS QUE HAY QUE RESPETAR AL IMPORTAR
  · Las notas de crédito YA vienen con signo negativo (cantidad, neto, total,
    costo, margen). No invertirlas otra vez. Fact-NC = suma de neto.
  · Llave de una línea: (sociedad, tipo_dcto, n_dcto, producto_codigo, linea).
    Llave de un documento: (sociedad, tipo_dcto, n_dcto). El folio solo NO es
    único: Acuña y Gran Natural son empresas distintas en Obuma y repiten
    números, y dentro de Acuña 2 folios existen como factura y como NC.
  · N° de facturas de un vendedor = documentos distintos con tipo FACTURA.
  · Líneas con producto_codigo FL-1…FL-5 son fletes de máquina (comodato),
    no venta de helado: FL-4 instalación, FL-1/FL-3/FL-5 cambio, FL-2 retiro.
    Ya vienen resumidas en maquinas.csv.
  · Acuña nunca pasó por el despacho de Autoventa: sus máquinas no tienen
    estado de entrega confirmado ("Sin información" en la app).

ARCHIVOS
  venta_items.csv  una fila por línea (el detalle que pide el SaaS)
  ventas.csv       una fila por documento, con la suma de sus líneas
  clientes.csv     rut, razón social, comuna, región, tipo
  productos.csv    código, nombre, categoría, subcategoría, fabricante, unidad
  vendedores.csv   id de la app, nombre, código Autoventa, activo
  maquinas.csv     documento, fecha, vendedor, cliente, tipo de movimiento
  control.csv      líneas, documentos, neto, total y costo por mes y tipo

CÓMO CUADRAR
  Después de importar, la suma de neto y el conteo de líneas y documentos por
  mes y tipo tienen que dar exactamente lo de control.csv (fila TOTAL incluida).
"""


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


# ── Programa ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Exporta el histórico de Acuña para el SaaS.")
    ap.add_argument("--salida", default=str(ROOT / "exports"),
                    help="Carpeta donde dejar el paquete (por defecto exports/).")
    args = ap.parse_args()

    from etl.db import get_client
    client = get_client()

    esperadas = _contar(client, "fact_ventas", sociedad_id=SOCIEDAD)
    logger.info("Acuña tiene %s líneas de venta en la base. Leyendo…", _fmt(esperadas))
    ventas = _leer_por_id(client, "fact_ventas", _COLS_VENTA, sociedad_id=SOCIEDAD)
    if len(ventas) != esperadas:
        logger.error("Se leyeron %s líneas y la base dice %s. No se exporta nada.",
                     len(ventas), esperadas)
        sys.exit(1)

    clientes = _leer_tabla(client, "dim_cliente", "rut,razon_social,comuna,region,tipo", "rut")
    productos = _leer_tabla(client, "dim_producto",
                            "codigo,nombre,categoria,subcategoria,fabricante,unidad_medida",
                            "codigo")
    vendedores = _leer_tabla(client, "dim_vendedor",
                             "id,nombre_canonico,cod_vendedor_autoventa,activo", "id")
    direcciones = _leer_tabla(client, "dim_direccion", "id,nombre,direccion,comuna", "id")
    maq = _leer_por_id(client, "fact_maquinas",
                       "id,documento,fecha,vendedor_id,cliente_rut,tipo_mov,estado",
                       sociedad_id=SOCIEDAD)

    items = armar_items(ventas, clientes, productos, vendedores, direcciones)
    docs = armar_documentos(items)
    control = armar_control(items)

    ruts = set(items["cliente_rut"].dropna())
    codigos = set(items["producto_codigo"].dropna())
    vids = set(items["vendedor_id"].dropna().astype(int))
    cli_out = clientes[clientes["rut"].isin(ruts)].sort_values("rut")
    prod_out = productos[productos["codigo"].isin(codigos)].sort_values("codigo")
    ven_out = vendedores[vendedores["id"].isin(vids)].sort_values("id")
    maq_out = pd.DataFrame()
    if not maq.empty:
        nombres = dict(zip(vendedores["id"], vendedores["nombre_canonico"]))
        maq_out = (maq.drop(columns=["id"])
                   .assign(sociedad="Acuña",
                           vendedor_nombre=lambda m: m["vendedor_id"].map(nombres))
                   .sort_values(["fecha", "documento"]))

    for nombre, df, total in (("clientes", cli_out, len(ruts)),
                              ("productos", prod_out, len(codigos))):
        if len(df) != total:
            logger.warning("%s: %s de %s tienen ficha en la base; el resto va solo en "
                           "venta_items.", nombre, len(df), total)

    carpeta = Path(args.salida) / f"acuna_{date.today():%Y%m%d}"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos = {
        "venta_items.csv": items, "ventas.csv": docs, "clientes.csv": cli_out,
        "productos.csv": prod_out, "vendedores.csv": ven_out, "maquinas.csv": maq_out,
        "control.csv": control,
    }
    for nombre, df in archivos.items():
        df.to_csv(carpeta / nombre, index=False, encoding="utf-8")
        logger.info("  %-16s %s filas", nombre, _fmt(len(df)))

    (carpeta / "LEEME.txt").write_text(_LEEME.format(
        hoy=f"{date.today():%d-%m-%Y}", lineas=_fmt(len(items)),
        docs=_fmt(len(docs)), desde=items["fecha"].min(), hasta=items["fecha"].max(),
        neto=_fmt(items["neto"].sum())), encoding="utf-8")

    zip_path = carpeta.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(carpeta.iterdir()):
            z.write(f, arcname=f"{carpeta.name}/{f.name}")

    logger.info("Listo: %s", zip_path)
    logger.info("Cuadre: %s líneas · %s documentos · neto %s",
                _fmt(len(items)), _fmt(len(docs)), _fmt(items["neto"].sum()))


if __name__ == "__main__":
    main()
