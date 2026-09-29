"""
Kreems — Sistema de Seguimiento Comercial
Punto de entrada: streamlit run app/main.py
"""
import sys
import datetime
import contextlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.styles import LOGO_PATH

st.set_page_config(
    page_title="Kreems · Seguimiento Comercial",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🍦",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.auth import (login, logout, is_authenticated, es_gerencia,
                      get_rol, MESES, get_client_auth, cambiar_password)
from app.styles import CSS, logo_img
from app.version import VERSION
# Los módulos de páginas (app.pages) NO se importan aquí: cargan plotly/data y
# enlentecen la pantalla de login. Se importan dentro de main() tras autenticar.

st.markdown(CSS, unsafe_allow_html=True)

# Indicador de carga centrado y con color de marca (para st.spinner).
st.markdown("""
<style>
[data-testid="stSpinner"]{display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:.5rem;padding:2rem 0;}
[data-testid="stSpinner"] p{color:var(--rosa-deep,#C01E6E);font-weight:600;font-size:1rem;}
</style>
""", unsafe_allow_html=True)


# ── LOGIN ──────────────────────────────────────────────────────────────────────
def pantalla_login():
    logo = logo_img("", alt="Kreems") or "<h1>🍦 Kreems</h1>"
    st.markdown(f"""
    <div class="login-wrap">
      <div class="login-card">
        <div class="login-logo">
          {logo}
          <p>Sistema de Seguimiento Comercial</p>
        </div>
    """, unsafe_allow_html=True)

    with st.form("login_form"):
        identifier = st.text_input(
            "Email o ID de usuario",
            placeholder="jperez  ó  juan@empresa.cl",
        )
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary",
                                          use_container_width=True)

    if submitted:
        if not identifier or not password:
            st.error("Ingresa tu email (o ID) y contraseña.")
        else:
            with st.spinner("Verificando..."):
                ok, msg = login(identifier, password)
            if ok:
                st.rerun()
            else:
                st.error(msg)

    st.markdown(
        f"<p style='text-align:center;color:#94A3B8;font-size:.72rem;"
        f"margin-top:.75rem'>versión {VERSION}</p></div></div>",
        unsafe_allow_html=True)


# ── SIDEBAR ────────────────────────────────────────────────────────────────────
def sidebar():
    with st.sidebar:
        # ── Marca ──
        brand = logo_img("brand-logo", alt="Kreems") or \
            '<span class="brand-name">🍦 Kreems</span>'
        st.markdown(f"""
        <div class="sidebar-brand">
          {brand}
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        # ── Navegación ──
        st.markdown('<p class="nav-section-label">Navegación</p>',
                    unsafe_allow_html=True)

        pagina = st.session_state.get("pagina", "inicio")

        nav_items = [("inicio", "🏠", "Inicio")]
        if es_gerencia():
            nav_items += [
                ("gerencia", "📊", "Panel Gerencia"),
                ("vendedor", "👤", "Panel Vendedor"),
            ]
        else:
            nav_items.append(("vendedor", "👤", "Mi Panel"))
        nav_items.append(("analisis", "📈", "Análisis"))
        nav_items.append(("clientes", "🧾", "Clientes"))
        if es_gerencia():
            nav_items.append(("control_maquinas", "🧊", "Control Máquinas"))
            nav_items.append(("presupuesto", "🎯", "Presupuesto"))
            nav_items.append(("comisiones", "💰", "Comisiones"))
            nav_items.append(("carga",  "📤", "Carga de archivos"))
            nav_items.append(("admin",  "⚙️", "Usuarios"))

        for key, icon, label in nav_items:
            tipo = "primary" if pagina == key else "secondary"
            if st.button(f"{icon}  {label}", key=f"nav_{key}",
                         use_container_width=True, type=tipo):
                st.session_state.pagina = key
                st.rerun()

        st.divider()

        # ── Período ──
        st.markdown('<p class="nav-section-label">Período</p>',
                    unsafe_allow_html=True)

        hoy = datetime.date.today()
        anio_idx = 0 if hoy.year >= 2026 else 1
        mes_idx  = list(MESES.keys()).index(hoy.month) if hoy.month in MESES else 0

        anio = st.selectbox("Año", [2026, 2025], key="sel_anio",
                            index=anio_idx, label_visibility="collapsed")
        mes  = st.selectbox("Mes", list(MESES.keys()), key="sel_mes",
                            format_func=lambda m: MESES[m],
                            index=mes_idx, label_visibility="collapsed")

        # ── Usuario + logout al fondo ──
        st.markdown("<div style='min-height:1.5rem'></div>", unsafe_allow_html=True)
        st.divider()

        nombre = st.session_state.get("vendedor_nombre",
                                      st.session_state.get("email", ""))
        rol = get_rol()
        badge_cls = "badge-gerencia" if es_gerencia() else "badge-vendedor"
        iniciales = "".join(w[0].upper() for w in nombre.split()[:2]) if nombre else "?"

        st.markdown(f"""
        <div class="user-info">
          <div class="user-avatar">{iniciales}</div>
          <div>
            <div class="user-name" title="{nombre}">{nombre}</div>
            <span class="badge {badge_cls}">{rol}</span>
          </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("🔑  Cambiar contraseña"):
            with st.form("form_cambiar_pass", clear_on_submit=True):
                nueva1 = st.text_input("Nueva contraseña", type="password")
                nueva2 = st.text_input("Confirmar contraseña", type="password")
                ok_btn = st.form_submit_button("Guardar", use_container_width=True)
            if ok_btn:
                if len(nueva1) < 8:
                    st.error("Mínimo 8 caracteres.")
                elif nueva1 != nueva2:
                    st.error("Las contraseñas no coinciden.")
                else:
                    ok, err = cambiar_password(nueva1)
                    if ok:
                        st.success("✅ Contraseña actualizada.")
                    else:
                        st.error(f"Error: {err}")

        if st.button("Cerrar sesión", key="btn_logout", use_container_width=True):
            logout()
            st.rerun()

        st.markdown(
            f"<p style='text-align:center;color:#94A3B8;font-size:.7rem;"
            f"margin-top:.5rem'>v {VERSION}</p>", unsafe_allow_html=True)

    return anio, mes


# ── PANTALLA INICIO ────────────────────────────────────────────────────────────


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    if not is_authenticated():
        pantalla_login()
        return

    if "pagina" not in st.session_state:
        st.session_state.pagina = "inicio"

    # Sidebar primero (es liviano y fija la página al hacer clic en nav).
    anio, mes = sidebar()
    pagina    = st.session_state.get("pagina", "inicio")

    # Spinner "Cargando…" en la primera carga Y al cambiar de página (cubre los
    # imports diferidos + la carga de datos, evita ver el contenido viejo mezclado).
    primera = not st.session_state.get("_panel_listo")
    cambio  = st.session_state.get("_pagina_render") != pagina
    with (st.spinner("Cargando tu panel…") if (primera or cambio)
          else contextlib.nullcontext()):
        from app.pages import (vendedor, gerencia, analisis, carga,
                                inicio, admin, comisiones, clientes,
                                presupuesto)

        client = get_client_auth()
        if client is None:
            # La sesión expiró y no se pudo refrescar → volver al login limpio.
            logout()
            st.warning("Tu sesión expiró. Vuelve a ingresar.")
            st.stop()

        nombre_mes     = MESES[mes]
        nombre_usuario = st.session_state.get("vendedor_nombre", "")

        if pagina == "inicio":
            inicio.render(client, anio, mes, nombre_usuario)

        elif pagina == "gerencia":
            st.markdown(f"## 📊 Panel de Gerencia — {nombre_mes} {anio}")
            gerencia.render(client, anio, mes)

        elif pagina == "vendedor":
            if es_gerencia():
                vend_visto = st.session_state.get("admin_vend_vista", "")
                subtitulo  = f" — {vend_visto}" if vend_visto else ""
                st.markdown(f"## 👤 Panel Vendedor{subtitulo} · {nombre_mes} {anio}")
            else:
                st.markdown(f"## 👤 {nombre_usuario}")
            vendedor.render(client, anio, mes, nombre_usuario)

        elif pagina == "analisis":
            st.markdown(f"## 📈 Análisis — {nombre_mes} {anio}")
            analisis.render(client, anio, mes)

        elif pagina == "clientes":
            st.markdown(f"## 🧾 Clientes — {nombre_mes} {anio}")
            clientes.render(client, anio, mes)

        elif pagina == "control_maquinas":
            st.markdown(f"## 🧊 Control de Máquinas — {nombre_mes} {anio}")
            # Import acotado a su propia página: si una página nueva no carga,
            # que se caiga ella y no la app entera. Pasó al estrenar esta
            # sección — Streamlit Cloud se quedó con el app.data viejo en
            # memoria tras el despliegue y el ImportError tumbaba hasta Inicio.
            try:
                from app.pages import control_maquinas
            except ImportError:
                st.error(
                    "Esta sección no cargó porque la app quedó con una mezcla "
                    "de versiones tras el último despliegue. Reinicia desde "
                    "**Manage app → Reboot app** y vuelve a entrar; el resto "
                    "de la app funciona normal mientras tanto."
                )
            else:
                control_maquinas.render(client, anio, mes)

        elif pagina == "presupuesto":
            if not es_gerencia():
                st.session_state.pagina = "inicio"
                st.rerun()
            st.markdown(f"## 🎯 Presupuesto de Venta — {nombre_mes} {anio}")
            presupuesto.render(client, anio, mes)

        elif pagina == "comisiones":
            if not es_gerencia():
                st.session_state.pagina = "inicio"
                st.rerun()
            st.markdown(f"## 💰 Comisiones — {nombre_mes} {anio}")
            comisiones.render(client, anio, mes)

        elif pagina == "carga":
            if not es_gerencia():
                st.session_state.pagina = "inicio"
                st.rerun()
            st.markdown("## 📤 Carga de archivos")
            carga.render(client, anio, mes)

        elif pagina == "admin":
            if not es_gerencia():
                st.session_state.pagina = "inicio"
                st.rerun()
            st.markdown("## ⚙️ Administración de Usuarios")
            admin.render()

        else:
            # Página desconocida (p. ej. una sección eliminada que quedó en la
            # sesión) → volver a Inicio en vez de mostrar el área en blanco.
            st.session_state.pagina = "inicio"
            st.rerun()

    st.session_state["_panel_listo"]   = True
    st.session_state["_pagina_render"] = pagina


if __name__ == "__main__":
    main()
