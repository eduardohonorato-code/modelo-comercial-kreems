"""Conexión central a Supabase. Leer credenciales desde variables de entorno."""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # ETL usa service role (RLS bypass)


def get_client() -> Client:
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


def cargar_alias(client: Client) -> list[dict]:
    """
    Lee la tabla vendedor_alias (nombre ERP → vendedor_id) para redirigir la
    facturación de reemplazos de vendedor. Devuelve [] si la tabla aún no existe
    (fail-soft: no rompe el ETL antes de correr el SQL que la crea).
    """
    try:
        r = client.table("vendedor_alias").select("alias,vendedor_id").execute()
        return r.data or []
    except Exception:
        return []


def cargar_reasignaciones(client: Client) -> list[dict]:
    """
    Lee vendedor_reasignacion (id origen → id destino desde una fecha) para
    reasignar por FECHA la facturación de reemplazos de vendedor. Fail-soft si la
    tabla no existe.
    """
    try:
        r = (client.table("vendedor_reasignacion")
             .select("origen_id,destino_id,desde").execute())
        return r.data or []
    except Exception:
        return []
