"""Clientes — ranking de clientes por Fact-NC del período.

RLS aplica automáticamente: un vendedor ve solo sus clientes; gerencia ve
todos. Las métricas se calculan sobre fact_ventas (vía get_top_clientes,
paginado) — Fact-NC = SUM(neto), con las NC ya en negativo.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from app.styles import fmt_clp, fmt_num, fmt_pct
from app.data import get_top_clientes, get_dim_sociedad

_CHART = "#E62984"  # magenta de marca


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _empty():
    st.markdown(
        '<div class="estado-vacio">Sin clientes con ventas en el período '
        'seleccionado.</div>', unsafe_allow_html=True)


def render(client, anio: int, mes: int):
    # ── Filtros ──────────────────────────────────────────────────────────────
    df_soc = get_dim_sociedad(client)
    soc_map: dict = {}
    if not df_soc.empty and {"id", "nombre"}.issubset(df_soc.columns):
        soc_map = dict(zip(df_soc["nombre"].str.strip(), df_soc["id"]))
    soc_opts = ["Ambas"] + sorted(soc_map.keys())

    _sec("🔍 Filtros")
    c1, c2 = st.columns([2, 3])
    with c1:
        soc_sel = st.selectbox("Sociedad", soc_opts, key="cli_soc")
        soc_ids = (None if (soc_sel == "Ambas" or soc_sel not in soc_map)
                   else [soc_map[soc_sel]])
    with c2:
        top_n = st.slider("Mostrar top N clientes", 5, 50, 15, step=5,
                          key="cli_topn")
    st.divider()

    # ── Datos ────────────────────────────────────────────────────────────────
    df = get_top_clientes(client, anio, mes, soc_ids)
    if df.empty:
        _empty()
        return

    # Solo clientes con facturación positiva neta para el ranking principal.
    df_pos = df[df["fact_nc"] > 0].copy()
    if df_pos.empty:
        df_pos = df.copy()

    total_fnc   = float(df_pos["fact_nc"].sum())
    n_clientes  = int(len(df_pos))
    ticket_prom = total_fnc / n_clientes if n_clientes else 0
    top_cli     = df_pos.iloc[0]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    _sec("Resumen de clientes")
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">Clientes con ventas</div>
        <div class="kpi-value">{fmt_num(n_clientes)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact-NC Total</div>
        <div class="kpi-value">{fmt_clp(total_fnc)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Ticket promedio</div>
        <div class="kpi-value">{fmt_clp(ticket_prom)}</div>
        <div class="kpi-sub">por cliente</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Cliente top</div>
        <div class="kpi-value" style="font-size:1.05rem">{top_cli['razon_social']}</div>
        <div class="kpi-sub">{fmt_clp(top_cli['fact_nc'])}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Gráfico top N ────────────────────────────────────────────────────────
    _sec(f"Top {min(top_n, n_clientes)} clientes — Fact-NC")
    top = df_pos.head(top_n).iloc[::-1]  # invertido para que el #1 quede arriba
    fig = go.Figure(go.Bar(
        x=top["fact_nc"], y=top["razon_social"], orientation="h",
        marker_color=_CHART,
        text=[fmt_clp(v) for v in top["fact_nc"]],
        textposition="auto",
        hovertemplate="%{y}<br>Fact-NC: %{text}<extra></extra>",
    ))
    fig.update_layout(
        height=max(320, 26 * len(top)),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=10, b=8, l=8, r=8),
        font=dict(family="Inter, system-ui, sans-serif", size=11),
        xaxis_title=None, yaxis_title=None,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Tabla detalle ────────────────────────────────────────────────────────
    _sec(f"Detalle — top {min(top_n, n_clientes)}")
    tabla = df_pos.head(top_n).copy()
    tabla["pct"] = tabla["fact_nc"] / total_fnc if total_fnc else 0

    header = (
        "<th style='text-align:left'>#</th>"
        "<th style='text-align:left'>Cliente</th>"
        "<th style='text-align:left'>RUT</th>"
        "<th style='text-align:left'>Comuna</th>"
        "<th style='text-align:left'>Región</th>"
        "<th title='Facturación neta de notas de crédito'>Fact-NC</th>"
        "<th title='Facturas distintas'>N° Fact.</th>"
        "<th title='Participación sobre el Fact-NC total'>% del total</th>"
    )
    rows = ""
    for i, (_, r) in enumerate(tabla.iterrows(), start=1):
        rows += f"""<tr>
          <td style='text-align:left'>{i}</td>
          <td style='text-align:left'>{r['razon_social']}</td>
          <td style='text-align:left'>{r['cliente_rut'] or '—'}</td>
          <td style='text-align:left'>{r.get('comuna') or '—'}</td>
          <td style='text-align:left'>{r.get('region') or '—'}</td>
          <td>{fmt_clp(r['fact_nc'])}</td>
          <td>{fmt_num(r['n_facturas'])}</td>
          <td>{fmt_pct(r['pct'])}</td>
        </tr>"""

    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems"><thead><tr>{header}</tr></thead>
    <tbody>{rows}</tbody></table></div>
    """, unsafe_allow_html=True)
