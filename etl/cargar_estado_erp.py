"""
Carga del estado activo/inactivo del cliente (ERP Autoventa) a Supabase.

Fuente: export 'lista_clientes.xlsx' del ERP, con columnas:
  · cliente_rut     → RUT (se normaliza al formato de dim_cliente).
  · cliente_activo  → 1 (activo) / 0 (inactivo).
  · cliente_razon_social (opcional, para referencia).

Upsert idempotente en public.cliente_estado_erp (llave rut). Reutilizable desde
la línea de comandos y desde la página Carga de la webapp.

Uso CLI:
    python -m etl.cargar_estado_erp "ruta/lista_clientes.xlsx"
"""
import sys
from pathlib import Path

import pandas as pd

from etl.cleaners import normalizar_rut
from etl.upsert import upsert_tabla

COL_RUT = "cliente_rut"
COL_ACTIVO = "cliente_activo"
COL_RAZON = "cliente_razon_social"


def parse_lista_clientes(fuente) -> pd.DataFrame:
    """
    Lee el Excel/archivo de clientes y devuelve un DataFrame listo para upsert:
    columnas rut (normalizado), activo (bool), razon_social. `fuente` puede ser
    una ruta o un objeto tipo archivo (BytesIO de un upload de Streamlit).
    """
    df = pd.read_excel(fuente, dtype=str)
    faltan = [c for c in (COL_RUT, COL_ACTIVO) if c not in df.columns]
    if faltan:
        raise ValueError(
            f"El archivo no tiene las columnas esperadas: {faltan}. "
            f"Se requiere '{COL_RUT}' y '{COL_ACTIVO}' (1/0).")

    activo = df[COL_ACTIVO].astype(str).str.strip().isin(["1", "1.0", "true", "True"])
    out = pd.DataFrame({
        "rut": normalizar_rut(df[COL_RUT]),
        "activo": [bool(x) for x in activo],  # bool nativo (no numpy.bool_) para JSON
        "razon_social": (df[COL_RAZON] if COL_RAZON in df.columns else None),
    })
    out = out.dropna(subset=["rut"])
    out = out[out["rut"].astype(str).str.len() > 0]
    # un RUT puede repetirse en la lista: nos quedamos con la última fila
    out = out.drop_duplicates(subset=["rut"], keep="last").reset_index(drop=True)
    return out


def cargar_estado_erp(client, fuente) -> dict:
    """Parsea y hace upsert a cliente_estado_erp. Devuelve conteos."""
    df = parse_lista_clientes(fuente)
    if df.empty:
        return {"filas": 0, "activos": 0, "inactivos": 0}
    upsert_tabla(client, "cliente_estado_erp", df, on_conflict="rut")
    return {
        "filas": len(df),
        "activos": int(df["activo"].sum()),
        "inactivos": int((~df["activo"]).sum()),
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from etl.db import get_client

    if len(sys.argv) < 2:
        raise SystemExit("Uso: python -m etl.cargar_estado_erp <ruta_lista_clientes.xlsx>")
    ruta = Path(sys.argv[1])
    if not ruta.exists():
        raise SystemExit(f"No existe el archivo: {ruta}")
    rep = cargar_estado_erp(get_client(), ruta)
    print(f"cliente_estado_erp cargado: {rep['filas']} clientes "
          f"({rep['activos']} activos, {rep['inactivos']} inactivos)")
