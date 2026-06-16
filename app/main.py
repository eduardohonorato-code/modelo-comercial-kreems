"""
Kreems — Sistema de Seguimiento Comercial
Punto de entrada: streamlit run app/main.py
"""
import sys
import datetime
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Kreems · Seguimiento Comercial",
    page_icon="🍦",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.auth import (login, logout, is_authenticated, es_gerencia,
                      get_rol, MESES, get_client_auth, cambiar_password)
from app.styles import CSS
from app.pages import vendedor, gerencia, analisis, carga, inicio, admin, comisiones

st.markdown(CSS, unsafe_allow_html=True)


# ── LOGIN ──────────────────────────────────────────────────────────────────────
def pantalla_login():
    st.markdown("""
    <div class="login-wrap">
      <div class="login-card">
        <div class="login-logo">
          <h1>🍦 Kreems</h1>
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

    st.markdown("</div></div>", unsafe_allow_html=True)


# ── SIDEBAR ────────────────────────────────────────────────────────────────────
def sidebar():
    with st.sidebar:
        # ── Marca ──
        st.markdown("""
        <div class="sidebar-brand">
          <span class="brand-icon">🍦</span>
          <span class="brand-name">Kreems</span>
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
        if es_gerencia():
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

    return anio, mes


# ── PANTALLA INICIO ────────────────────────────────────────────────────────────
def _pantalla_inicio(client, anio: int, mes: int):
    from app.data import get_resumen, get_calendario
    from app.styles import fmt_clp, fmt_pct, fmt_num, color_pct

    nombre_usuario = st.session_state.get("vendedor_nombre", "")
    nombre_display = nombre_usuario.split()[0] if nombre_usuario else "Usuario"
    nombre_mes = MESES[mes]

    # Saludo
    st.markdown(f"""
    <div style="margin-bottom:1.25rem">
      <div style="font-size:1.35rem;font-weight:700;color:var(--azul)">
        Hola, {nombre_display} 👋
      </div>
      <div style="color:#6B7280;font-size:.88rem;margin-top:.2rem">
        {nombre_mes} {anio} · Sistema de Seguimiento Comercial Kreems
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Acceso rápido ──────────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Acceso rápido</div>',
                unsafe_allow_html=True)

    if es_gerencia():
        nav_cards = [
            ("gerencia", "📊", "Panel Gerencia",
             "Seguimiento de todos los vendedores, objetivos y ranking mensual."),
            ("vendedor", "👤", "Panel Vendedor",
             "KPIs individuales: cumplimiento, máquinas y efectividad."),
            ("analisis", "📈", "Análisis",
             "Ventas por producto, categoría, región y ciclo de máquinas."),
        ]
    else:
        nav_cards = [
            ("vendedor", "👤", "Mi Panel",
             "Tus KPIs del mes: cumplimiento, máquinas y efectividad."),
            ("analisis", "📈", "Análisis",
             "Ventas por producto, categoría, región y ciclo de máquinas."),
        ]

    cols = st.columns(len(nav_cards))
    for i, (page_key, icon, title, desc) in enumerate(nav_cards):
        with cols[i]:
            st.markdown(f"""
            <div class="acceso-card">
              <div class="acceso-card-icon">{icon}</div>
              <div class="acceso-card-title">{title}</div>
              <div class="acceso-card-desc">{desc}</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"Abrir →", key=f"ir_{page_key}", use_container_width=True):
                st.session_state.pagina = page_key
                st.rerun()

    # ── Resumen del período ────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Resumen del período</div>',
                unsafe_allow_html=True)

    df  = get_resumen(client, anio, mes)
    cal = get_calendario(client, anio, mes)

    if df.empty:
        st.markdown(
            '<div class="estado-vacio">Sin datos para el período seleccionado.</div>',
            unsafe_allow_html=True)
        return

    if "nombre_canonico" in df.columns:
        df = df[~df["nombre_canonico"].str.startswith("Vendedor ", na=False)].copy()

    for col in ["fact_nc", "obj_venta", "n_documentos",
                "maquinas_gestionadas", "maquinas_entregadas"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if es_gerencia():
        total_fnc  = df["fact_nc"].sum()
        total_obj  = df["obj_venta"].sum()
        total_docs = df["n_documentos"].sum()
        total_mgst = df["maquinas_gestionadas"].sum()
        pct_global = total_fnc / total_obj if total_obj else None
        n_activos  = int((df["obj_venta"] > 0).sum())
        cls = color_pct(pct_global)

        st.markdown(f"""
        <div class="kpi-grid">
          <div class="kpi-card destacado">
            <div class="kpi-label">Fact-NC Total</div>
            <div class="kpi-value {cls}">{fmt_clp(total_fnc)}</div>
            <div class="kpi-sub">% Cumpl: <strong>{fmt_pct(pct_global)}</strong>
              &nbsp;|&nbsp; Día {cal["dias_trabajados"]}/{cal["dias_totales"]}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Objetivo Total</div>
            <div class="kpi-value">{fmt_clp(total_obj)}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">N° Documentos</div>
            <div class="kpi-value">{fmt_num(total_docs)}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Vendedores con obj.</div>
            <div class="kpi-value">{n_activos}</div>
            <div class="kpi-sub">Máq. gestionadas: {int(total_mgst)}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Mini ranking top 3
        st.markdown('<div class="seccion-titulo">Top 3 — Fact-NC</div>',
                    unsafe_allow_html=True)
        top3 = df[df["obj_venta"] > 0].nlargest(3, "fact_nc")[
            ["nombre_canonico", "fact_nc", "obj_venta"]].copy()
        top3["pct"] = top3["fact_nc"] / top3["obj_venta"].replace(0, float("nan"))
        top3["Fact-NC"]  = top3["fact_nc"].apply(fmt_clp)
        top3["% Cumpl"]  = top3["pct"].apply(fmt_pct)
        st.dataframe(
            top3[["nombre_canonico", "Fact-NC", "% Cumpl"]]
            .rename(columns={"nombre_canonico": "Vendedor"}),
            use_container_width=True, hide_index=True,
        )

    else:
        # Vista vendedor en inicio
        vid = st.session_state.get("vendedor_id")
        fila = df[df["vendedor_id"] == vid] if vid else pd.DataFrame()
        if fila.empty:
            fila = df.iloc[[0]]
        r = fila.iloc[0]
        pct_c = r.get("pct_cumplimiento")
        cls_c = color_pct(pct_c)

        st.markdown(f"""
        <div class="kpi-grid">
          <div class="kpi-card destacado">
            <div class="kpi-label">% Cumplimiento</div>
            <div class="kpi-value {cls_c}">{fmt_pct(pct_c)}</div>
            <div class="kpi-sub">Día {cal["dias_trabajados"]}/{cal["dias_totales"]}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Fact-NC</div>
            <div class="kpi-value">{fmt_clp(r.get("fact_nc"))}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Objetivo</div>
            <div class="kpi-value">{fmt_clp(r.get("obj_venta"))}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Máq. Gestionadas</div>
            <div class="kpi-value">{int(r.get("maquinas_gestionadas") or 0)}</div>
            <div class="kpi-sub">Obj: {int(r.get("obj_maquinas") or 0)}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    if not is_authenticated():
        pantalla_login()
        return

    if "pagina" not in st.session_state:
        st.session_state.pagina = "inicio"

    anio, mes = sidebar()
    pagina    = st.session_state.get("pagina", "inicio")
    client    = get_client_auth()
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


if __name__ == "__main__":
    main()
