"""Manejo de sesión y autenticación con Supabase Auth."""
import base64
import json
import os
import time
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_URL  = os.environ["SUPABASE_URL"]
_ANON = os.environ["SUPABASE_ANON_KEY"]

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def get_client_anon() -> Client:
    """Cliente sin autenticar (solo para login)."""
    return create_client(_URL, _ANON)


def get_client_service() -> Client:
    """
    Cliente con SERVICE_ROLE key: bypassa RLS completamente.
    Usar SOLO en funciones admin (server-side). Nunca exponer al browser.
    """
    _svc_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not _svc_key:
        raise EnvironmentError(
            "SUPABASE_SERVICE_ROLE_KEY no está definida. "
            "Agrégala al .env para usar el panel de administración."
        )
    return create_client(_URL, _svc_key)


def _jwt_expira_en(token: str) -> float:
    """Retorna el timestamp Unix de expiración del JWT (0 si no se puede leer)."""
    try:
        payload_b64 = token.split(".")[1]
        # Agregar padding faltante
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:
        return 0.0


def _refrescar_token_si_necesario() -> str | None:
    """
    Si el access_token expira en menos de 60 segundos, lo renueva con el
    refresh_token y actualiza session_state. Retorna el token vigente.
    """
    token = st.session_state.get("access_token")
    if not token:
        return None

    exp = _jwt_expira_en(token)
    if exp and time.time() < exp - 60:
        return token  # Todavía vigente

    # Intentar refresh
    refresh = st.session_state.get("refresh_token")
    if not refresh:
        return token  # Sin refresh token; que falle en la query

    try:
        anon = create_client(_URL, _ANON)
        resp = anon.auth.refresh_session(refresh)
        if resp.session:
            st.session_state["access_token"]  = resp.session.access_token
            st.session_state["refresh_token"] = resp.session.refresh_token
            return resp.session.access_token
    except Exception:
        pass  # Si falla el refresh, devolver el token viejo y dejar que la query falle

    return token


def get_client_auth() -> Client | None:
    """
    Devuelve un cliente Supabase con el JWT del usuario en sesión.
    Refresca el token automáticamente si está por expirar (< 60 s).
    El token activa RLS: vendedor solo ve sus filas; gerencia ve todo.
    """
    token = _refrescar_token_si_necesario()
    if not token:
        return None
    client = create_client(_URL, _ANON)
    client.postgrest.auth(token)
    return client


def login(identifier: str, password: str) -> tuple[bool, str]:
    """
    Login dual: acepta email O nombre corto (username).
    - Si el input contiene '@'  → se usa directamente como email.
    - Si no contiene '@'        → se resuelve via get_email_by_username().
    Retorna (exito, mensaje_error).
    """
    try:
        client     = get_client_anon()
        identifier = identifier.strip()

        if "@" in identifier:
            # ── Camino email ──────────────────────────────────────────────────
            email = identifier
        else:
            # ── Camino username: resolver a email ─────────────────────────────
            lookup = client.rpc(
                "get_email_by_username", {"p_username": identifier.lower()}
            ).execute()
            email = lookup.data  # TEXT o None si no existe el username
            if not email:
                return False, "ID de usuario no encontrado. Verifica o intenta con tu email."

        # ── Autenticar con el email resuelto ──────────────────────────────────
        resp    = client.auth.sign_in_with_password({"email": email, "password": password})
        session = resp.session
        user    = resp.user
        st.session_state["access_token"]  = session.access_token
        st.session_state["refresh_token"] = session.refresh_token
        st.session_state["user_id"]       = str(user.id)
        st.session_state["email"]         = user.email

        # ── Cargar rol y vendedor_id con el token recién obtenido ─────────────
        auth_client = get_client_auth()
        _cargar_perfil(auth_client, str(user.id))
        return True, ""
    except Exception as e:
        msg = str(e)
        if "Invalid login" in msg or "invalid" in msg.lower():
            return False, "Contraseña incorrecta."
        return False, f"Error de conexión: {msg}"


def _cargar_perfil(client: Client, user_id: str):
    """Carga rol y vendedor_id en session_state."""
    # Rol desde perfil_usuario
    rp = client.table("perfil_usuario").select("rol").eq("user_id", user_id).execute()
    rol = rp.data[0]["rol"] if rp.data else "vendedor"
    st.session_state["rol"] = rol

    # vendedor_id desde dim_vendedor (para filtrar la vista con RLS)
    rv = client.table("dim_vendedor").select("id,nombre_canonico").eq("user_id", user_id).execute()
    if rv.data:
        st.session_state["vendedor_id"]       = rv.data[0]["id"]
        st.session_state["vendedor_nombre"]   = rv.data[0]["nombre_canonico"]
    else:
        st.session_state["vendedor_id"]     = None
        st.session_state["vendedor_nombre"] = st.session_state.get("email", "Usuario")


def logout():
    """Cierra sesión y limpia session_state."""
    try:
        client = get_client_anon()
        client.auth.sign_out()
    except Exception:
        pass
    for key in ["access_token", "refresh_token", "user_id", "email", "rol",
                "vendedor_id", "vendedor_nombre"]:
        st.session_state.pop(key, None)


def is_authenticated() -> bool:
    return bool(st.session_state.get("access_token"))


def get_rol() -> str:
    return st.session_state.get("rol", "vendedor")


def es_gerencia() -> bool:
    return get_rol() in ("gerencia", "admin")


def es_admin() -> bool:
    return get_rol() == "admin"


def cambiar_password(nueva_pass: str) -> tuple[bool, str]:
    """Cambia la contraseña del usuario actualmente autenticado."""
    client = get_client_auth()
    if not client:
        return False, "No hay sesión activa."
    try:
        client.auth.update_user({"password": nueva_pass})
        return True, ""
    except Exception as e:
        return False, str(e)
