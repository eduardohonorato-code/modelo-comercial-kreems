"""Conexión central a Supabase. Leer credenciales desde variables de entorno."""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # ETL usa service role (RLS bypass)


def get_client() -> Client:
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)
