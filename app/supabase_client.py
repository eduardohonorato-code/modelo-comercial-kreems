"""Conexión Supabase para la app Streamlit. Usa la anon key (RLS activo)."""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]  # Clave pública; RLS controla el acceso


def get_client() -> Client:
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)
