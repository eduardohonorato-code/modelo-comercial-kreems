"""
Administración de Usuarios — solo rol gerencia / admin.

Usa SUPABASE_SERVICE_ROLE_KEY para todas las operaciones de Auth y
escritura directa (bypassa RLS). La clave nunca sale del servidor.

Operaciones disponibles:
  - Listar todos los usuarios (auth + perfil_usuario + dim_vendedor)
  - Crear usuario (Auth + perfil_usuario + vincular dim_vendedor)
  - Editar usuario (nombre, rol, vendedor vinculado, activo, contraseña)
  - Desactivar (bloquea login + marca activo=false en dim_vendedor)
  - Eliminar definitivamente (solo admin)
"""
import logging
import secrets
import string
from datetime import datetime, timezone

import streamlit as st

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
ROL_EMOJI   = {"admin": "🔴", "gerencia": "🔵", "vendedor": "🟢"}
ROL_LABEL   = {"admin": "Admin", "gerencia": "Gerencia", "vendedor": "Vendedor"}
ROL_OPTIONS = ["vendedor", "gerencia", "admin"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_password(length: int = 12) -> str:
    """Genera contraseña temporal con al menos una mayúscula, minúscula, dígito y símbolo."""
    pool = string.ascii_letters + string.digits + "!@#"
    pwd = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#"),
    ]
    pwd += [secrets.choice(pool) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)


def _fmt_fecha(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return str(dt)[:10]
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt)[:10]


def _log_accion(accion: str, descripcion: str):
    actor = st.session_state.get("user_id", "desconocido")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("[ADMIN] %s | actor=%s | %s | ts=%s", accion, actor, descripcion, ts)


# ── Carga de datos (cacheada 30 s) ────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner=False)
def _cargar_datos(_cache_bust: str):
    """Carga auth.users + perfil_usuario + dim_vendedor vía service_role."""
    from app.auth import get_client_service
    svc = get_client_service()

    # Usuarios de Auth
    resp = svc.auth.admin.list_users()
    auth_users = resp if isinstance(resp, list) else getattr(resp, "users", [])

    # Roles
    perfs = svc.table("perfil_usuario").select("user_id,rol").execute().data or []
    rol_map = {p["user_id"]: p["rol"] for p in perfs}

    # Vendedores
    vends = (
        svc.table("dim_vendedor")
        .select("id,nombre_canonico,user_id,activo")
        .execute()
        .data or []
    )
    vend_by_uid  = {str(v["user_id"]): v for v in vends if v.get("user_id")}
    vend_sin_user = [v for v in vends if not v.get("user_id")]

    # Mapa username por user_id
    username_map = {p["user_id"]: p.get("username", "") for p in perfs}

    # Merge
    usuarios = []
    for u in auth_users:
        uid  = str(u.id)
        rol  = rol_map.get(uid, "vendedor")
        vend = vend_by_uid.get(uid)
        usuarios.append({
            "user_id":         uid,
            "email":           u.email or "",
            "username":        username_map.get(uid, ""),
            "nombre":          (u.user_metadata or {}).get("name", ""),
            "rol":             rol,
            "vendedor_id":     vend["id"] if vend else None,
            "vendedor_nombre": vend["nombre_canonico"] if vend else None,
            "activo":          vend["activo"] if vend else True,
            "last_sign_in":    u.last_sign_in_at,
        })

    return usuarios, vend_sin_user


def _cache_bust() -> str:
    """Clave que cambia cuando se invalida el caché manualmente."""
    return st.session_state.get("admin_cache_v", "0")


def _invalidar_cache():
    v = int(st.session_state.get("admin_cache_v", 0)) + 1
    st.session_state["admin_cache_v"] = str(v)
    _cargar_datos.clear()


# ── Operaciones de escritura ───────────────────────────────────────────────────

def _crear_usuario(
    email: str, password: str, nombre: str, username: str,
    rol: str, vendedor_id
) -> tuple[bool, str]:
    from app.auth import get_client_service
    svc = get_client_service()
    try:
        resp = svc.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"name": nombre},
        })
        user    = getattr(resp, "user", resp)
        new_uid = str(user.id)

        perfil: dict = {"user_id": new_uid, "rol": rol}
        if username:  # None o "" → no guardar (queda NULL en BD)
            perfil["username"] = username
        svc.table("perfil_usuario").upsert(perfil).execute()

        if vendedor_id:
            svc.table("dim_vendedor").update({"user_id": new_uid}).eq("id", vendedor_id).execute()

        _log_accion("CREAR", f"username={username} email={email} rol={rol}")
        _invalidar_cache()
        return True, new_uid
    except Exception as exc:
        logger.exception("Error creando usuario %s", email)
        return False, str(exc)


def _editar_usuario(
    uid: str,
    nombre: str,
    username: str,
    rol: str,
    vendedor_id_nuevo,
    vendedor_id_anterior,
    activo: bool,
    nueva_pass: str | None,
) -> tuple[bool, str]:
    from app.auth import get_client_service
    svc = get_client_service()
    try:
        attrs: dict = {"user_metadata": {"name": nombre}}
        if nueva_pass:
            attrs["password"] = nueva_pass
        svc.auth.admin.update_user_by_id(uid, attrs)

        # username vacío → NULL (quita el ID corto; el usuario solo puede entrar con email)
        perfil: dict = {
            "user_id":  uid,
            "rol":      rol,
            "username": username if username else None,
        }
        svc.table("perfil_usuario").upsert(perfil).execute()

        # Desvincular vendedor anterior si cambió
        if vendedor_id_anterior and vendedor_id_anterior != vendedor_id_nuevo:
            svc.table("dim_vendedor").update({"user_id": None}).eq("id", vendedor_id_anterior).execute()

        # Vincular / actualizar activo en el vendedor nuevo (o el mismo si no cambió)
        if vendedor_id_nuevo:
            svc.table("dim_vendedor").update({
                "user_id": uid,
                "activo":  activo,
            }).eq("id", vendedor_id_nuevo).execute()
        elif vendedor_id_anterior and not vendedor_id_nuevo:
            # ya se desvinculó arriba; nada más que hacer
            pass

        _log_accion("EDITAR", f"uid={uid[:8]} username={username} rol={rol} vend_nuevo={vendedor_id_nuevo}")
        _invalidar_cache()
        return True, ""
    except Exception as exc:
        logger.exception("Error editando usuario %s", uid)
        return False, str(exc)


def _desactivar_usuario(uid: str, email: str) -> tuple[bool, str]:
    from app.auth import get_client_service
    svc = get_client_service()
    try:
        # Bloquea el login por 100 años
        svc.auth.admin.update_user_by_id(uid, {"ban_duration": "876000h"})
        # Marca inactivo en dim_vendedor (si tiene uno vinculado)
        svc.table("dim_vendedor").update({"activo": False}).eq("user_id", uid).execute()
        _log_accion("DESACTIVAR", f"uid={uid[:8]} email={email}")
        _invalidar_cache()
        return True, ""
    except Exception as exc:
        logger.exception("Error desactivando usuario %s", uid)
        return False, str(exc)


def _eliminar_usuario(uid: str, email: str, vendedor_id) -> tuple[bool, str]:
    from app.auth import get_client_service
    svc = get_client_service()
    try:
        if vendedor_id:
            svc.table("dim_vendedor").update({"user_id": None}).eq("id", vendedor_id).execute()
        svc.table("perfil_usuario").delete().eq("user_id", uid).execute()
        svc.auth.admin.delete_user(uid)
        _log_accion("ELIMINAR_DEFINITIVO", f"uid={uid[:8]} email={email}")
        _invalidar_cache()
        return True, ""
    except Exception as exc:
        logger.exception("Error eliminando usuario %s", uid)
        return False, str(exc)


# ── Sub-paneles ────────────────────────────────────────────────────────────────

def _validar_username(u: str) -> str | None:
    """Retorna mensaje de error o None si es válido."""
    import re
    if not u:
        return "El ID de usuario es obligatorio."
    if len(u) < 3:
        return "El ID debe tener al menos 3 caracteres."
    if len(u) > 30:
        return "El ID no puede superar 30 caracteres."
    if not re.match(r"^[a-z0-9._-]+$", u.lower()):
        return "Solo se permiten letras, números, puntos, guiones y guion bajo. Sin espacios."
    return None


def _panel_crear(vend_sin_user: list):
    st.markdown("#### ➕ Nuevo usuario")

    with st.form("form_crear_usuario", clear_on_submit=False):
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre completo *", placeholder="Juan Pérez")
        username = c2.text_input(
            "ID de usuario (opcional)",
            placeholder="jperez",
            help="Nombre corto para entrar sin email. Se puede asignar ahora o después desde Editar. "
                 "Solo letras, números, puntos, guiones y guion bajo. Sin espacios.",
        )

        c3, c4 = st.columns(2)
        email = c3.text_input(
            "Email (interno) *",
            placeholder="juan.perez@kreems.cl",
            help="El usuario NO verá ni usará este email para entrar. Es solo para Supabase Auth.",
        )
        auto_pwd = c4.checkbox(
            "Generar contraseña automáticamente",
            value=True,
            help="Se mostrará en pantalla hasta que hagas click en 'Ya la copié'.",
        )
        pwd = "" if auto_pwd else c4.text_input(
            "Contraseña temporal *", type="password",
            help="Mínimo 8 caracteres.",
        )

        c5, c6 = st.columns(2)
        rol = c5.selectbox(
            "Rol *", ROL_OPTIONS,
            format_func=lambda r: f"{ROL_EMOJI[r]}  {ROL_LABEL[r]}",
        )

        vendedor_id = None
        if rol == "vendedor":
            with c6:
                if vend_sin_user:
                    opts = {"— Sin vincular —": None}
                    opts.update({v["nombre_canonico"]: v["id"] for v in vend_sin_user})
                    sel = st.selectbox(
                        "Vendedor a vincular",
                        list(opts.keys()),
                        help="Solo aparecen vendedores sin usuario asignado.",
                    )
                    vendedor_id = opts[sel]
                else:
                    st.info("Todos los vendedores ya tienen usuario.")

        submitted = st.form_submit_button("Crear usuario", type="primary",
                                          use_container_width=True)

    if submitted:
        errs = []
        u_clean = username.strip().lower() if username.strip() else None
        if u_clean:
            err_u = _validar_username(u_clean)
            if err_u:
                errs.append(err_u)
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            errs.append("El email interno no tiene formato válido.")
        if not nombre.strip():
            errs.append("El nombre completo es obligatorio.")
        if not auto_pwd and len(pwd) < 8:
            errs.append("La contraseña debe tener al menos 8 caracteres.")

        if errs:
            for e in errs:
                st.error(e)
            return

        # Generar contraseña si corresponde
        pass_final = _gen_password() if auto_pwd else pwd

        with st.spinner("Creando usuario…"):
            ok, result = _crear_usuario(
                email.strip(), pass_final, nombre.strip(),
                u_clean, rol, vendedor_id,
            )

        if ok:
            if auto_pwd:
                # Guardar en session_state para mostrar banner persistente
                st.session_state["admin_pass_pendiente"] = {
                    "nombre":   nombre.strip(),
                    "username": u_clean or email.strip(),
                    "password": pass_final,
                }
            st.session_state.pop("admin_modo", None)
            st.rerun()
        else:
            if "duplicate" in result.lower() or "unique" in result.lower():
                st.error(f"El ID **{u_clean}** ya está en uso. Elige otro.")
            else:
                st.error(f"Error al crear: {result}")


def _panel_editar(usuario: dict, vend_sin_user: list):
    uid = usuario["user_id"]
    st.markdown(f"#### ✏️  Editar — `{usuario['username'] or usuario['email']}`")

    with st.form(f"form_editar_{uid}"):
        c1, c2 = st.columns(2)
        username_nuevo = c1.text_input(
            "ID de usuario (opcional)",
            value=usuario.get("username", "") or "",
            help="Nombre corto con el que puede iniciar sesión. "
                 "Déjalo vacío para que solo entre con email.",
        )
        nombre_nuevo = c2.text_input("Nombre completo", value=usuario["nombre"])

        c3, c4 = st.columns(2)
        rol_nuevo = c3.selectbox(
            "Rol", ROL_OPTIONS,
            index=ROL_OPTIONS.index(usuario["rol"]),
            format_func=lambda r: f"{ROL_EMOJI[r]}  {ROL_LABEL[r]}",
        )

        # Construir lista de vendedores disponibles:
        # el actual (si tiene) + los sin asignar
        vend_actual_item = (
            {"id": usuario["vendedor_id"], "nombre_canonico": usuario["vendedor_nombre"]}
            if usuario["vendedor_id"] else None
        )
        vend_disponibles = []
        if vend_actual_item:
            vend_disponibles.append(vend_actual_item)
        vend_disponibles += [v for v in vend_sin_user if v["id"] != usuario["vendedor_id"]]

        vend_id_nuevo = usuario["vendedor_id"]
        if rol_nuevo == "vendedor":
            with c4:
                mapa = {"— Sin vincular —": None}
                mapa.update({v["nombre_canonico"]: v["id"] for v in vend_disponibles})
                sel_actual = usuario["vendedor_nombre"] if usuario["vendedor_nombre"] else "— Sin vincular —"
                sel_vend   = st.selectbox(
                    "Vendedor vinculado",
                    list(mapa.keys()),
                    index=list(mapa.keys()).index(sel_actual) if sel_actual in mapa else 0,
                )
                vend_id_nuevo = mapa[sel_vend]
        else:
            vend_id_nuevo = None
            if usuario["vendedor_id"]:
                st.caption(
                    f"⚠️ Al cambiar a {ROL_LABEL[rol_nuevo]}, "
                    f"**{usuario['vendedor_nombre']}** quedará sin usuario asignado."
                )

        activo_nuevo = st.toggle(
            "Vendedor activo",
            value=usuario.get("activo", True),
            disabled=(rol_nuevo != "vendedor" or not vend_id_nuevo),
            help="Solo aplica cuando el usuario tiene un vendedor vinculado.",
        )

        resetear_pass = st.checkbox(
            "Generar nueva contraseña temporal",
            help="Se mostrará una sola vez después de guardar.",
        )

        submitted = st.form_submit_button("💾  Guardar cambios", type="primary",
                                           use_container_width=True)

    if submitted:
        u_edit = username_nuevo.strip().lower() if username_nuevo.strip() else None
        if u_edit:
            err_u = _validar_username(u_edit)
            if err_u:
                st.error(err_u)
                return

        nueva_pass = _gen_password() if resetear_pass else None
        with st.spinner("Guardando cambios…"):
            ok, err = _editar_usuario(
                uid, nombre_nuevo.strip(), u_edit,
                rol_nuevo, vend_id_nuevo, usuario["vendedor_id"],
                activo_nuevo, nueva_pass,
            )
        if ok:
            if nueva_pass:
                st.session_state["admin_pass_pendiente"] = {
                    "nombre":   nombre_nuevo.strip(),
                    "username": u_edit or usuario["email"],
                    "password": nueva_pass,
                }
            st.session_state["admin_modo"] = None
            st.session_state.pop("admin_uid", None)
            st.rerun()
        else:
            if "duplicate" in err.lower() or "unique" in err.lower():
                st.error(f"El ID **{username_nuevo.lower()}** ya está en uso. Elige otro.")
            else:
                st.error(f"Error al guardar: {err}")


def _panel_eliminar(usuario: dict, puede_eliminar_definitivo: bool):
    uid    = usuario["user_id"]
    email  = usuario["email"]
    nombre = usuario["nombre"] or email

    st.warning(
        f"⚠️ **¿Qué hacer con {nombre}** (`{email}`)?  \n"
        "Elige una opción a continuación."
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("""
**🔒 Desactivar** *(recomendado)*
- Bloquea el login inmediatamente
- Conserva todo el historial de ventas
- Reversible: vuelve a Editar para reactivar
""")
        if st.button("Desactivar usuario", key=f"btn_desact_{uid}",
                     type="primary", use_container_width=True):
            with st.spinner("Desactivando…"):
                ok, err = _desactivar_usuario(uid, email)
            if ok:
                st.success(f"**{nombre}** desactivado correctamente.")
                st.session_state["admin_modo"] = None
                st.session_state.pop("admin_uid", None)
                st.rerun()
            else:
                st.error(f"Error: {err}")

    with c2:
        if puede_eliminar_definitivo:
            st.markdown("""
**🗑️ Eliminar definitivamente** *(irreversible)*
- Borra la cuenta de Supabase Auth
- Desvincula el vendedor en dim_vendedor
- El historial de ventas queda sin usuario asignado
""")
            if st.button("Eliminar definitivamente", key=f"btn_elim_{uid}",
                         use_container_width=True):
                with st.spinner("Eliminando…"):
                    ok, err = _eliminar_usuario(uid, email, usuario["vendedor_id"])
                if ok:
                    st.success(f"Usuario **{nombre}** eliminado definitivamente.")
                    st.session_state["admin_modo"] = None
                    st.session_state.pop("admin_uid", None)
                    st.rerun()
                else:
                    st.error(f"Error: {err}")
        else:
            st.info(
                "Solo el rol **admin** puede eliminar usuarios definitivamente.  \n"
                "Usa **Desactivar** para bloquear el acceso sin borrar datos."
            )

    if st.button("↩ Cancelar", key=f"btn_cancel_elim_{uid}"):
        st.session_state["admin_modo"] = None
        st.session_state.pop("admin_uid", None)
        st.rerun()


# ── Tabla de usuarios ─────────────────────────────────────────────────────────

def _tabla_usuarios(usuarios: list):
    import pandas as pd

    rows = []
    for u in usuarios:
        rows.append({
            "ID usuario":     u["username"] or "—",
            "Rol":            f"{ROL_EMOJI[u['rol']]}  {ROL_LABEL[u['rol']]}",
            "Nombre":         u["nombre"] or "—",
            "Vendedor":       u["vendedor_nombre"] or "—",
            "Activo":         "✅ Sí" if u["activo"] else "❌ No",
            "Último acceso":  _fmt_fecha(u["last_sign_in"]),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID usuario":    st.column_config.TextColumn(width="small"),
            "Rol":           st.column_config.TextColumn(width="small"),
            "Nombre":        st.column_config.TextColumn(width="medium"),
            "Vendedor":      st.column_config.TextColumn(width="medium"),
            "Activo":        st.column_config.TextColumn(width="small"),
            "Último acceso": st.column_config.TextColumn(width="medium"),
        },
    )


# ── Render principal ──────────────────────────────────────────────────────────

def render():
    from app.auth import es_gerencia, es_admin

    # ── Guard ──
    if not es_gerencia():
        st.warning("🔒 Acceso restringido: solo gerencia o admin.")
        st.stop()

    puede_eliminar = es_admin()

    # ── Cargar datos ──
    try:
        usuarios, vend_sin_user = _cargar_datos(_cache_bust())
    except EnvironmentError as exc:
        st.error(str(exc))
        st.stop()

    # ── Banner de contraseña temporal (persiste hasta que el admin lo cierra) ──
    pending = st.session_state.get("admin_pass_pendiente")
    if pending:
        st.error(
            f"### 🔐 Contraseña temporal generada\n\n"
            f"**Usuario:** {pending['nombre']}  \n"
            f"**ID / Email:** {pending['username']}  \n"
            f"**Contraseña:** `{pending['password']}`  \n\n"
            f"*Cópiala y entrégasela antes de cerrar este aviso.*"
        )
        if st.button("✅  Ya la copié — cerrar aviso", type="primary"):
            del st.session_state["admin_pass_pendiente"]
            st.rerun()
        st.divider()

    # ── Contador ──
    n_total  = len(usuarios)
    n_vend   = sum(1 for u in usuarios if u["rol"] == "vendedor")
    n_ger    = sum(1 for u in usuarios if u["rol"] in ("gerencia", "admin"))
    n_activo = sum(1 for u in usuarios if u["activo"])

    c_info, c_btn = st.columns([5, 2])
    c_info.markdown(
        f"**{n_total} usuarios** — "
        f"🟢 {n_vend} vendedores &nbsp;·&nbsp; "
        f"🔵 {n_ger} gerencia/admin &nbsp;·&nbsp; "
        f"✅ {n_activo} activos"
    )

    modo = st.session_state.get("admin_modo")
    with c_btn:
        if st.button("➕  Nuevo usuario", type="primary",
                     use_container_width=True,
                     disabled=(modo == "crear")):
            st.session_state["admin_modo"] = "crear"
            st.session_state.pop("admin_uid", None)
            st.rerun()

    # ── Formulario crear (aparece antes de la tabla) ──
    if modo == "crear":
        with st.container(border=True):
            _panel_crear(vend_sin_user)
            if st.button("↩  Cancelar", key="btn_cancel_crear"):
                st.session_state.pop("admin_modo", None)
                st.rerun()
        st.divider()

    # ── Tabla ──
    st.markdown('<div class="seccion-titulo">Usuarios registrados</div>',
                unsafe_allow_html=True)
    _tabla_usuarios(usuarios)

    if modo == "crear":
        return  # No mostrar panel Gestionar mientras se crea

    # ── Gestionar usuario seleccionado ──
    st.markdown('<div class="seccion-titulo">Gestionar usuario</div>',
                unsafe_allow_html=True)

    if not usuarios:
        st.info("No hay usuarios registrados.")
        return

    # Construir opciones para el selectbox
    opciones_labels = [
        f"{ROL_EMOJI[u['rol']]}  {u['email']}  ({u['nombre'] or ROL_LABEL[u['rol']]})"
        for u in usuarios
    ]
    uid_actual    = st.session_state.get("admin_uid")
    idx_actual    = next(
        (i for i, u in enumerate(usuarios) if u["user_id"] == uid_actual), 0
    )

    sel_idx = st.selectbox(
        "Seleccionar usuario",
        range(len(opciones_labels)),
        index=idx_actual,
        format_func=lambda i: opciones_labels[i],
        label_visibility="collapsed",
        key="admin_sel_user",
    )
    usuario_sel = usuarios[sel_idx]

    # Actualizar uid en state si el usuario cambió la selección
    if usuario_sel["user_id"] != uid_actual:
        st.session_state["admin_uid"]  = usuario_sel["user_id"]
        st.session_state["admin_modo"] = None
        st.rerun()

    # ── Botones de acción ──
    c_ed, c_de = st.columns([2, 2])
    with c_ed:
        if st.button("✏️  Editar", key="btn_editar",
                     use_container_width=True,
                     type="primary" if modo == "editar" else "secondary"):
            st.session_state["admin_modo"] = "editar"
            st.session_state["admin_uid"]  = usuario_sel["user_id"]
            st.rerun()
    with c_de:
        if st.button("🚫  Desactivar / Eliminar", key="btn_eliminar",
                     use_container_width=True,
                     type="primary" if modo == "eliminar" else "secondary"):
            st.session_state["admin_modo"] = "eliminar"
            st.session_state["admin_uid"]  = usuario_sel["user_id"]
            st.rerun()

    # ── Panel de acción según modo ──
    modo = st.session_state.get("admin_modo")
    uid  = st.session_state.get("admin_uid")

    if not uid:
        return

    # Refrescar datos del usuario seleccionado (puede haber cambiado tras edición)
    usuario_act = next((u for u in usuarios if u["user_id"] == uid), usuario_sel)

    if modo == "editar":
        st.divider()
        with st.container(border=True):
            _panel_editar(usuario_act, vend_sin_user)
            if st.button("↩  Cancelar edición", key="btn_cancel_edit"):
                st.session_state["admin_modo"] = None
                st.rerun()

    elif modo == "eliminar":
        st.divider()
        with st.container(border=True):
            _panel_eliminar(usuario_act, puede_eliminar)
