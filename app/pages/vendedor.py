"""Panel Vendedor: KPIs, tabla mensual, máquinas, efectividad."""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from app.styles import fmt_clp, fmt_pct, fmt_num, color_pct
from app.auth import es_gerencia
from app.data import get_resumen, get_maquinas, get_calendario, get_pedidos_resumen


def _int0(val) -> int:
    """Convierte a int de forma segura: None y NaN devuelven 0."""
    try:
        f = float(val)
        return 0 if f != f else int(f)   # f != f es True solo para NaN
    except (TypeError, ValueError):
        return 0


def render(client, anio: int, mes: int, nombre: str):
    # CSS ya inyectado en main.py

    # ── Datos ────────────────────────────────────────────────────────────────
    df = get_resumen(client, anio, mes)
    cal = get_calendario(client, anio, mes)

    if df.empty:
        st.info("Sin datos para el período seleccionado.")
        return

    # ── Selector de vendedor (solo gerencia/admin) ───────────────────────────
    if es_gerencia() and "nombre_canonico" in df.columns:
        vendedores_disp = df["nombre_canonico"].dropna().unique().tolist()
        if vendedores_disp:
            # Recuperar última selección o usar el primero de la lista
            idx_prev = 0
            prev = st.session_state.get("admin_vend_vista")
            if prev and prev in vendedores_disp:
                idx_prev = vendedores_disp.index(prev)

            nombre_sel = st.selectbox(
                "Ver panel de:",
                vendedores_disp,
                index=idx_prev,
                key="sel_vend_panel",
            )
            st.session_state["admin_vend_vista"] = nombre_sel
            fila = df[df["nombre_canonico"] == nombre_sel]
            if fila.empty:
                fila = df.iloc[[0]]
        else:
            fila = df.iloc[[0]]
    else:
        # Vendedor normal: RLS ya filtró solo sus filas
        vid = st.session_state.get("vendedor_id")
        if vid:
            fila = df[df["vendedor_id"] == vid]
            if fila.empty:
                fila = df.iloc[[0]]
        else:
            fila = df.iloc[[0]]

    r = fila.iloc[0]

    # ── KPIs principales ────────────────────────────────────────────────────
    pct_c = r.get("pct_cumplimiento")
    pct_p = r.get("pct_proyeccion")
    cls_c = color_pct(pct_c)
    cls_p = color_pct(pct_p)
    cls_efec = color_pct(r.get("pct_efectividad"), umbral_ok=0.5, umbral_warn=0.3)

    mes_activo = cal["dias_trabajados"] < cal["dias_totales"]
    sub_proy = (f"Proyección: <strong>{fmt_pct(pct_p)}</strong> &nbsp;|&nbsp; "
                if mes_activo else "")

    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">% Cumplimiento</div>
        <div class="kpi-value {cls_c}">{fmt_pct(pct_c)}</div>
        <div class="kpi-sub">{sub_proy}Día {cal["dias_trabajados"]} de {cal["dias_totales"]}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Objetivo</div>
        <div class="kpi-value">{fmt_clp(r.get("obj_venta"))}</div>
        <div class="kpi-sub">Meta mensual</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact-NC</div>
        <div class="kpi-value">{fmt_clp(r.get("fact_nc"))}</div>
        <div class="kpi-sub">{fmt_clp(r.get("monto_facturas"))} − <span style="color:var(--rojo)">{fmt_clp(abs(r.get("monto_notas_credito") or 0), "")}</span> NC</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Proyección cierre</div>
        <div class="kpi-value {cls_p}">{fmt_clp(r.get("proyeccion_cierre"))}</div>
        <div class="kpi-sub">Al ritmo actual</div>
      </div>
    </div>
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">N° Facturas</div>
        <div class="kpi-value">{fmt_num(r.get("n_facturas"))}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">N° NC</div>
        <div class="kpi-value rojo">{fmt_num(r.get("n_notas_credito"))}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">No Facturado</div>
        <div class="kpi-value amarillo">{fmt_clp(r.get("no_facturado_monto"))}</div>
        <div class="kpi-sub">{fmt_num(r.get("no_facturado_docs"))} pedidos</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">% Efectividad</div>
        <div class="kpi-value {cls_efec}">{fmt_pct(r.get("pct_efectividad"))}</div>
        <div class="kpi-sub">{fmt_num(r.get("n_documentos"))} docs / {fmt_num(r.get("obj_visitas"))} obj</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Barra de progreso visual ─────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Avance del mes</div>', unsafe_allow_html=True)
    _barra_cumplimiento(r, mes_activo)

    # ── Máquinas ────────────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Máquinas</div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    gest  = _int0(r.get("maquinas_gestionadas"))
    entr  = _int0(r.get("maquinas_entregadas"))
    reti  = _int0(r.get("maquinas_retiros"))
    obj_m = _int0(r.get("obj_maquinas"))
    conv = r.get("conversion_gestionada_entregada")

    m1.metric("Obj. Máquinas",   str(obj_m))
    m2.metric("Gestionadas",     str(gest),
              delta=f"{gest - obj_m:+d} vs obj" if obj_m else None)
    m3.metric("Entregadas",      str(entr))
    m4.metric("Retiros",         str(reti))

    if gest > 0:
        st.progress(min(entr / gest, 1.0),
                    text=f"Conversión gestionada→entregada: {fmt_pct(conv)}")

    # ── Gráfico gauge: % cumplimiento ────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Gauge de cumplimiento</div>',
                unsafe_allow_html=True)
    _gauge(pct_c)

    # ── Detalle por documento (tabla compacta) ───────────────────────────────
    st.markdown('<div class="seccion-titulo">Resumen del período</div>',
                unsafe_allow_html=True)
    _tabla_detalle_vendedor(r, cal)


def _barra_cumplimiento(r, mes_activo: bool):
    pct_c = float(r.get("pct_cumplimiento") or 0)
    pct_p = float(r.get("pct_proyeccion") or 0)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[pct_c * 100], y=["Cumplimiento actual"],
        orientation="h",
        marker_color="#1A7F4B" if pct_c >= 1 else "#D4881E" if pct_c >= 0.7 else "#C0392B",
        text=[f"{pct_c*100:.1f}%"], textposition="inside", name="Actual",
    ))
    if mes_activo:
        fig.add_trace(go.Bar(
            x=[pct_p * 100], y=["Proyección cierre"],
            orientation="h", marker_color="#C01E6E",
            text=[f"{pct_p*100:.1f}%"], textposition="inside", name="Proyección",
        ))
    fig.add_vline(x=100, line_dash="dash", line_color="gray", annotation_text="Meta")
    x_max = max(130, pct_p * 120) if mes_activo else max(130, pct_c * 120)
    fig.update_layout(
        height=100 if not mes_activo else 140,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(range=[0, x_max], showgrid=False),
        showlegend=False, plot_bgcolor="white", paper_bgcolor="white",
    )
    st.plotly_chart(fig, use_container_width=True)


def _gauge(pct_c):
    val = float(pct_c or 0) * 100
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=val,
        number={"suffix": "%", "font": {"size": 36}},
        delta={"reference": 100, "suffix": "%"},
        gauge={
            "axis": {"range": [0, 150], "ticksuffix": "%"},
            "bar": {"color": "#1A7F4B" if val >= 100 else "#D4881E" if val >= 70 else "#C0392B"},
            "steps": [
                {"range": [0, 70],  "color": "#FDECEA"},
                {"range": [70, 100], "color": "#FFF3E0"},
                {"range": [100, 150], "color": "#E8F5E9"},
            ],
            "threshold": {
                "line": {"color": "#C01E6E", "width": 3},
                "thickness": 0.75, "value": 100,
            },
        },
        title={"text": "% Cumplimiento"},
    ))
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=40, b=10),
                      paper_bgcolor="white")
    st.plotly_chart(fig, use_container_width=True)


def _tabla_detalle_vendedor(r, cal):
    filas = [
        ("Días trabajados / totales",
         f"{cal['dias_trabajados']} / {cal['dias_totales']}", ""),
        ("Facturas emitidas", fmt_num(r.get("n_facturas")),
         fmt_clp(r.get("monto_facturas"))),
        ("Notas de crédito", fmt_num(r.get("n_notas_credito")),
         fmt_clp(r.get("monto_notas_credito"))),
        ("Fact-NC (neto real)", "", fmt_clp(r.get("fact_nc"))),
        ("Proyección lineal cierre", "", fmt_clp(r.get("proyeccion_cierre"))),
        ("Objetivo de venta", "", fmt_clp(r.get("obj_venta"))),
        ("% Cumplimiento", "", fmt_pct(r.get("pct_cumplimiento"))),
        ("No Facturado (monto)", "", fmt_clp(r.get("no_facturado_monto"))),
        ("No Facturado (docs)", fmt_num(r.get("no_facturado_docs")), ""),
        ("Máquinas gestionadas / obj",
         f"{_int0(r.get('maquinas_gestionadas'))} / {_int0(r.get('obj_maquinas'))}", ""),
        ("Máquinas entregadas", fmt_num(r.get("maquinas_entregadas")), ""),
        ("Retiros", fmt_num(r.get("maquinas_retiros")), ""),
        ("Conversión gestionada→entregada", "", fmt_pct(r.get("conversion_gestionada_entregada"))),
        ("% Efectividad (docs/visitas)",
         f"{fmt_num(r.get('n_documentos'))} docs / {fmt_num(r.get('obj_visitas'))} obj",
         fmt_pct(r.get("pct_efectividad"))),
    ]
    rows_html = ""
    for concepto, cant, monto in filas:
        rows_html += f"""
        <tr>
          <td style="text-align:left">{concepto}</td>
          <td>{cant}</td>
          <td>{monto}</td>
        </tr>"""
    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems">
      <thead><tr>
        <th style="text-align:left">Concepto</th>
        <th>Cantidad / Detalle</th>
        <th>Monto</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>
    """, unsafe_allow_html=True)
