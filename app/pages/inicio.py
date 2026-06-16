"""
Dashboard Comercial — Página de inicio.

Secciones:
  S1  Header (título + última actualización)
  S2  KPI Cards — 6 tarjetas con íconos
  S3  Tabla vendedores · Ranking barras · Gauge cumplimiento
  S4  Evolución diaria · % Ritmo · Scatter desempeño
  S5  Proyección · Top/Risk · Insights automáticos

Solo-lectura: ninguna función modifica datos ni tablas.
RLS aplica automáticamente vía el JWT del cliente.
"""
import datetime
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.auth import MESES, es_gerencia
from app.data import (
    get_calendario,
    get_resumen,
    get_ultima_factura,
    get_ventas_diarias,
)
from app.styles import color_pct, fmt_clp, fmt_num, fmt_pct, logo_img


# ── Paleta (idéntica a CSS variables) ─────────────────────────────────────────
_AZUL      = "#C01E6E"   # magenta profundo de marca (texto/línea principal)
_ROSA      = "#E62984"   # magenta de marca (acento)
_VERDE     = "#1A7F4B"
_ROJO      = "#C0392B"
_AMARILLO  = "#D4881E"
_GRIS      = "#6B7280"
_GRIS_LIGHT= "#E5E7EB"
_BG        = "#FBF7FA"

def _color_semaforo(pct) -> str:
    if pct is None:
        return _GRIS
    if pct >= 1.0:
        return _VERDE
    if pct >= 0.8:
        return _AMARILLO
    return _ROJO

def _cls_semaforo(pct) -> str:
    if pct is None:
        return ""
    if pct >= 1.0:
        return "verde"
    if pct >= 0.8:
        return "amarillo"
    return "rojo"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mes_anterior(anio: int, mes: int) -> tuple:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _preparar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Tipifica y filtra filas demo."""
    df = df[~df["nombre_canonico"].str.startswith("Vendedor ", na=False)].copy()
    for col in ["fact_nc", "obj_venta", "proyeccion_cierre", "pct_cumplimiento",
                "n_documentos", "n_facturas", "maquinas_gestionadas",
                "maquinas_entregadas", "maquinas_retiros", "no_facturado_monto"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "pct_cumplimiento" not in df.columns and "obj_venta" in df.columns:
        df["pct_cumplimiento"] = df.apply(
            lambda r: r["fact_nc"] / r["obj_venta"] if r["obj_venta"] else None,
            axis=1,
        )
    return df


# ── S1: Header ────────────────────────────────────────────────────────────────

def _render_header(anio: int, mes: int, ultima_factura: str):
    nombre_mes = MESES[mes]
    logo = logo_img("brand-logo-on-dark", alt="Kreems") or "🍦"
    st.markdown(f"""
    <div class="dash-header">
      <div class="dash-header-left">
        <span class="dash-header-logo">{logo}</span>
        <div>
          <div class="dash-header-title">Dashboard Comercial</div>
          <div class="dash-header-sub">{nombre_mes} {anio}</div>
        </div>
      </div>
      <div class="dash-header-right">
        <span class="dash-header-update">🕐 Última actualización: {ultima_factura}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── S2: KPI Cards ─────────────────────────────────────────────────────────────

def _render_kpis(df: pd.DataFrame, cal: dict, pct_anterior):
    fact_nc   = float(df["fact_nc"].sum())
    obj_total = float(df["obj_venta"].sum())
    pct_cumpl = fact_nc / obj_total if obj_total else None

    dias_t   = int(cal.get("dias_trabajados", 0))
    dias_tot = int(cal.get("dias_totales", 30))
    pct_dias = dias_t / dias_tot if dias_tot else 0
    ritmo    = pct_dias * obj_total
    brecha   = fact_nc - obj_total

    # Delta vs mes anterior
    if pct_anterior is not None and pct_cumpl is not None:
        delta_pp  = (pct_cumpl - pct_anterior) * 100
        delta_txt = f"{'▲' if delta_pp >= 0 else '▼'} {abs(delta_pp):.1f} pp vs mes ant."
        delta_cls = "verde" if delta_pp >= 0 else "rojo"
    else:
        delta_txt = "Sin dato mes anterior"
        delta_cls = "gris"

    cumpl_cls  = _cls_semaforo(pct_cumpl)
    border_col = _color_semaforo(pct_cumpl)
    brecha_cls = "verde" if brecha >= 0 else "rojo"
    brecha_ico = "📈" if brecha >= 0 else "📉"
    brecha_sub = "Sobre la meta" if brecha >= 0 else "Bajo la meta"
    brecha_pfx = "+" if brecha >= 0 else ""

    st.markdown(f"""
    <div class="kpi-6-grid">

      <div class="kpi-icon-card">
        <div class="kic-icon">💰</div>
        <div class="kic-body">
          <div class="kic-label">Venta Real del Mes</div>
          <div class="kic-value">{fmt_clp(fact_nc)}</div>
          <div class="kic-sub">Fact‑NC acumulado</div>
        </div>
      </div>

      <div class="kpi-icon-card">
        <div class="kic-icon">🎯</div>
        <div class="kic-body">
          <div class="kic-label">Meta del Mes</div>
          <div class="kic-value">{fmt_clp(obj_total)}</div>
          <div class="kic-sub">Objetivo del período</div>
        </div>
      </div>

      <div class="kpi-icon-card" style="border-left-color:{border_col}">
        <div class="kic-icon">📊</div>
        <div class="kic-body">
          <div class="kic-label">Cumplimiento Total</div>
          <div class="kic-value {cumpl_cls}">{fmt_pct(pct_cumpl)}</div>
          <div class="kic-delta {delta_cls}">{delta_txt}</div>
        </div>
      </div>

      <div class="kpi-icon-card">
        <div class="kic-icon">📅</div>
        <div class="kic-body">
          <div class="kic-label">Días Transcurridos</div>
          <div class="kic-value">{dias_t} / {dias_tot}</div>
          <div class="kic-sub">{pct_dias*100:.0f}% del mes transcurrido</div>
        </div>
      </div>

      <div class="kpi-icon-card">
        <div class="kic-icon">⏱️</div>
        <div class="kic-body">
          <div class="kic-label">Ritmo Esperado</div>
          <div class="kic-value">{fmt_clp(ritmo)}</div>
          <div class="kic-sub">Proyección lineal a hoy</div>
        </div>
      </div>

      <div class="kpi-icon-card" style="border-left-color:{_color_semaforo(pct_cumpl)}">
        <div class="kic-icon">{brecha_ico}</div>
        <div class="kic-body">
          <div class="kic-label">Brecha a Meta</div>
          <div class="kic-value {brecha_cls}">{brecha_pfx}{fmt_clp(brecha)}</div>
          <div class="kic-sub">{brecha_sub}</div>
        </div>
      </div>

    </div>
    """, unsafe_allow_html=True)


# ── S3: Tabla · Ranking · Gauge ───────────────────────────────────────────────

def _render_tabla_vendedores(df: pd.DataFrame):
    """Tabla compacta con dot semáforo de color.
    Solo muestra vendedores con objetivo cargado; los sin objetivo
    aparecen en un resumen colapsado al pie para no inflar la tabla.
    """
    df_con_obj = df[df["obj_venta"] > 0].sort_values("fact_nc", ascending=False)
    df_sin_obj = df[df["obj_venta"] == 0]
    filas = []
    for i, row in enumerate(df_con_obj.itertuples(), 1):
        pct = getattr(row, "pct_cumplimiento", None)
        try:
            pct = float(pct) if pct is not None else None
        except Exception:
            pct = None
        if pct is None and getattr(row, "obj_venta", 0):
            pct = row.fact_nc / row.obj_venta
        cls = _cls_semaforo(pct)
        brecha = row.fact_nc - row.obj_venta
        b_cls  = "verde-bg" if brecha >= 0 else "rojo-bg"
        b_pfx  = "+" if brecha >= 0 else ""
        nombre = str(row.nombre_canonico)
        nombre_short = nombre.split()[0] if " " in nombre else nombre[:12]
        filas.append(f"""
        <tr>
          <td style="text-align:center;color:{_GRIS};font-size:.68rem">{i}</td>
          <td style="text-align:left">
            <span class="semaforo-dot {cls}"></span>{nombre_short}
          </td>
          <td>{fmt_clp(row.fact_nc)}</td>
          <td>{fmt_clp(row.obj_venta)}</td>
          <td style="font-weight:700;color:{_color_semaforo(pct)}">{fmt_pct(pct)}</td>
          <td class="{b_cls}">{b_pfx}{fmt_clp(brecha)}</td>
        </tr>""")

    # Fila TOTAL (solo vendedores con objetivo)
    tot_fnc = float(df["fact_nc"].sum())          # total real incluye sin-obj
    tot_fnc_obj = float(df_con_obj["fact_nc"].sum())
    tot_obj = float(df_con_obj["obj_venta"].sum())
    tot_pct = tot_fnc_obj / tot_obj if tot_obj else None
    tot_br  = tot_fnc_obj - tot_obj
    tb_pfx  = "+" if tot_br >= 0 else ""
    tot_cls = "verde-bg" if tot_br >= 0 else "rojo-bg"

    filas.append(f"""
    <tr class="total-row">
      <td></td>
      <td style="text-align:left">TOTAL</td>
      <td>{fmt_clp(tot_fnc_obj)}</td>
      <td>{fmt_clp(tot_obj)}</td>
      <td style="font-weight:700;color:{_color_semaforo(tot_pct)}">{fmt_pct(tot_pct)}</td>
      <td class="{tot_cls}">{tb_pfx}{fmt_clp(tot_br)}</td>
    </tr>""")

    # Vendedores sin objetivo: fila resumen colapsada
    sin_obj_html = ""
    if not df_sin_obj.empty:
        n_sin = len(df_sin_obj)
        fnc_sin = float(df_sin_obj["fact_nc"].sum())
        sin_obj_html = f"""
        <tr style="background:#F9FAFB">
          <td colspan="2" style="text-align:left;color:{_GRIS};font-size:.68rem;
              font-style:italic;padding:.3rem .55rem">
            + {n_sin} sin objetivo ({fmt_clp(fnc_sin)} en ventas)
          </td>
          <td colspan="4" style="font-size:.68rem;color:{_GRIS}"></td>
        </tr>"""

    html = f"""
    <div class="tabla-container">
    <table class="kreems">
      <thead><tr>
        <th style="text-align:center;min-width:24px">N°</th>
        <th style="text-align:left;min-width:80px">Vendedor</th>
        <th>Fact‑NC</th><th>Objetivo</th><th>% Cumpl</th><th>Brecha</th>
      </tr></thead>
      <tbody>{"".join(filas)}{sin_obj_html}</tbody>
    </table>
    </div>"""
    st.markdown(html, unsafe_allow_html=True)


def _render_ranking_barras(df: pd.DataFrame, height: int = 260):
    """Barras horizontales ordenadas por % cumplimiento (solo con objetivo)."""
    dft = df[df["obj_venta"] > 0].copy()
    dft["pct"] = dft["fact_nc"] / dft["obj_venta"]
    dft = dft.sort_values("pct", ascending=True)
    if dft.empty:
        st.info("Sin objetivos cargados.")
        return

    dft["nombre_short"] = dft["nombre_canonico"].str.split().str[0]
    colores = [_color_semaforo(p) for p in dft["pct"]]
    textos  = [fmt_pct(p) for p in dft["pct"]]
    pct_max = dft["pct"].max()

    fig = go.Figure(go.Bar(
        x=dft["pct"] * 100,
        y=dft["nombre_short"],
        orientation="h",
        marker_color=colores,
        text=textos,
        textposition="outside",
        textfont=dict(size=10, color=_AZUL),
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.add_vline(x=100, line_dash="dash", line_color=_GRIS,
                  annotation_text="100%", annotation_font_size=9,
                  annotation_font_color=_GRIS,
                  annotation_position="top")
    fig.update_layout(
        margin=dict(l=0, r=45, t=8, b=0),
        xaxis=dict(title="", showgrid=True, gridcolor=_GRIS_LIGHT,
                   ticksuffix="%",
                   range=[0, max(pct_max * 100 * 1.22, 135)]),
        yaxis=dict(title="", tickfont=dict(size=10)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Inter, sans-serif", color=_AZUL),
        height=height,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_gauge(pct_total, obj_total: float = 0, bar_height: int = 260):
    """
    Velocímetro: 0–150%, colores semáforo.
    `bar_height` se pasa desde la fila para alinearse con el bar chart.
    """
    val = round((pct_total or 0) * 100, 1)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=val,
        delta={"reference": 100, "suffix": " pp",
               "font": {"size": 11},
               "increasing": {"color": _VERDE},
               "decreasing": {"color": _ROJO}},
        number={"suffix": "%", "font": {"size": 28, "color": _AZUL,
                                         "family": "Inter, sans-serif"}},
        domain={"x": [0, 1], "y": [0.15, 1]},   # deja espacio abajo para delta
        gauge={
            "axis": {"range": [0, 150], "tickwidth": 1,
                     "tickcolor": _GRIS, "tickfont": {"size": 8},
                     "nticks": 6},
            "bar":  {"color": _color_semaforo(pct_total), "thickness": .3},
            "bgcolor": "white",
            "borderwidth": 0,
            "steps": [
                {"range": [0,   80],  "color": "#FEE2E2"},
                {"range": [80,  100], "color": "#FEF9C3"},
                {"range": [100, 150], "color": "#DCFCE7"},
            ],
            "threshold": {
                "line": {"color": _AZUL, "width": 2},
                "thickness": .75,
                "value": 100,
            },
        },
    ))
    meta_txt = f"Meta: {fmt_clp(obj_total)}" if obj_total else ""
    fig.update_layout(
        annotations=[dict(
            text=meta_txt, x=.5, y=.04, xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=10, color=_GRIS, family="Inter, sans-serif"),
        )],
        margin=dict(l=15, r=15, t=15, b=10),
        paper_bgcolor="white",
        height=bar_height,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_fila_media(df: pd.DataFrame):
    st.markdown('<div class="seccion-titulo">Cumplimiento por vendedor</div>',
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns([4, 4, 3])

    # Calcular la altura del bar chart primero para alinear el gauge
    n_con_obj = int((df["obj_venta"] > 0).sum())
    bar_h = max(220, n_con_obj * 34 + 40)

    with c1:
        _render_tabla_vendedores(df)

    with c2:
        st.caption("Ranking de cumplimiento")
        _render_ranking_barras(df, height=bar_h)

    with c3:
        st.caption("Cumplimiento total vs meta")
        tot_fnc = float(df["fact_nc"].sum())
        tot_obj = float(df["obj_venta"].sum())
        pct_total = tot_fnc / tot_obj if tot_obj else 0
        _render_gauge(pct_total, obj_total=tot_obj, bar_height=bar_h)


# ── S4: Gráficos diarios + Scatter ────────────────────────────────────────────

def _render_evolucion(df_diario: pd.DataFrame, obj_total: float,
                      dias_t: int, dias_tot: int):
    """Línea real acumulada vs meta lineal."""
    if df_diario.empty:
        st.info("Sin datos diarios para el período.")
        return

    df = df_diario.copy()
    df["acum"] = df["neto_dia"].cumsum()

    # Meta lineal: sube de 0 a obj_total en dias_tot
    # Solo dibujamos hasta el día de datos disponibles
    n = len(df)
    df["meta_dia"] = [obj_total * (i + 1) / dias_tot for i in range(n)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["fecha"], y=df["acum"],
        mode="lines+markers",
        name="Real acumulado",
        line=dict(color=_AZUL, width=2.5),
        marker=dict(size=4, color=_AZUL),
        hovertemplate="%{x|%d %b}: <b>%{y:,.0f}</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df["fecha"], y=df["meta_dia"],
        mode="lines",
        name="Meta lineal",
        line=dict(color=_GRIS, width=1.5, dash="dot"),
        hovertemplate="%{x|%d %b} meta: <b>%{y:,.0f}</b><extra></extra>",
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=8, b=0),
        xaxis=dict(showgrid=False, tickformat="%d %b", tickfont=dict(size=9)),
        yaxis=dict(showgrid=True, gridcolor=_GRIS_LIGHT, tickfont=dict(size=9),
                   tickformat=",.0f"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="left", x=0, font=dict(size=9)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Inter, sans-serif"),
        height=210,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_ritmo(df_diario: pd.DataFrame, obj_total: float, dias_tot: int):
    """% cumplimiento acumulado diario vs ritmo esperado lineal."""
    if df_diario.empty or not obj_total:
        st.info("Sin datos.")
        return

    df = df_diario.copy()
    df["acum"] = df["neto_dia"].cumsum()
    df["pct_real"] = df["acum"] / obj_total * 100

    n = len(df)
    df["pct_ritmo"] = [100 * (i + 1) / dias_tot for i in range(n)]

    # Área entre curvas: verde si real > ritmo, rojo si real < ritmo
    sobre = df["pct_real"] >= df["pct_ritmo"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["fecha"], y=df["pct_real"],
        fill="tonexty" if False else None,
        mode="lines",
        name="% real",
        line=dict(color=_AZUL, width=2.5),
        hovertemplate="%{x|%d %b}: <b>%{y:.1f}%</b><extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df["fecha"], y=df["pct_ritmo"],
        mode="lines",
        name="Ritmo esperado",
        line=dict(color=_GRIS, width=1.5, dash="dot"),
        hovertemplate="%{x|%d %b} ritmo: <b>%{y:.1f}%</b><extra></extra>",
    ))
    # Línea de 100%
    fig.add_hline(y=100, line_dash="dash", line_color=_VERDE,
                  line_width=1, opacity=.6)
    fig.update_layout(
        margin=dict(l=0, r=0, t=8, b=0),
        xaxis=dict(showgrid=False, tickformat="%d %b", tickfont=dict(size=9)),
        yaxis=dict(showgrid=True, gridcolor=_GRIS_LIGHT,
                   ticksuffix="%", tickfont=dict(size=9)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="left", x=0, font=dict(size=9)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Inter, sans-serif"),
        height=210,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_scatter(df: pd.DataFrame):
    """Scatter: eje X = Fact-NC, eje Y = % cumplimiento. Un punto por vendedor."""
    dft = df[df["obj_venta"] > 0].copy()
    if dft.empty:
        st.info("Sin datos suficientes.")
        return

    dft["pct"] = dft["fact_nc"] / dft["obj_venta"] * 100
    dft["nombre_short"] = dft["nombre_canonico"].str.split().str[0]
    dft["color"] = dft["pct"].apply(
        lambda p: _VERDE if p >= 100 else (_AMARILLO if p >= 80 else _ROJO))

    media_fnc = dft["fact_nc"].mean()
    media_pct = dft["pct"].mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dft["fact_nc"],
        y=dft["pct"],
        mode="markers+text",
        text=dft["nombre_short"],
        textposition="top center",
        textfont=dict(size=9, color=_AZUL),
        marker=dict(color=dft["color"], size=10, opacity=.85,
                    line=dict(width=1, color="white")),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Fact-NC: %{x:,.0f}<br>"
            "Cumpl: %{y:.1f}%<extra></extra>"
        ),
    ))
    # Líneas de referencia
    fig.add_vline(x=media_fnc, line_dash="dot", line_color=_GRIS,
                  line_width=1, opacity=.5)
    fig.add_hline(y=100, line_dash="dot", line_color=_VERDE,
                  line_width=1, opacity=.5)

    # Etiquetas de cuadrantes
    x_max = dft["fact_nc"].max() * 1.1
    fig.add_annotation(x=x_max * .98, y=145, text="Alto vol / Alto cumpl",
                       showarrow=False, font=dict(size=7.5, color=_GRIS),
                       xanchor="right")
    fig.add_annotation(x=x_max * .02, y=145, text="Bajo vol / Alto cumpl",
                       showarrow=False, font=dict(size=7.5, color=_GRIS),
                       xanchor="left")
    fig.add_annotation(x=x_max * .98, y=5, text="Alto vol / Bajo cumpl",
                       showarrow=False, font=dict(size=7.5, color=_GRIS),
                       xanchor="right")
    fig.add_annotation(x=x_max * .02, y=5, text="Bajo vol / Bajo cumpl",
                       showarrow=False, font=dict(size=7.5, color=_GRIS),
                       xanchor="left")

    fig.update_layout(
        margin=dict(l=0, r=0, t=8, b=0),
        xaxis=dict(showgrid=True, gridcolor=_GRIS_LIGHT,
                   tickformat=",.0f", tickfont=dict(size=9), title=""),
        yaxis=dict(showgrid=True, gridcolor=_GRIS_LIGHT,
                   ticksuffix="%", tickfont=dict(size=9),
                   title="", range=[0, 150]),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Inter, sans-serif"),
        height=210,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_fila_graficos(df: pd.DataFrame, df_diario: pd.DataFrame,
                          cal: dict):
    st.markdown('<div class="seccion-titulo">Evolución del mes</div>',
                unsafe_allow_html=True)
    obj_total = float(df["obj_venta"].sum())
    dias_t    = int(cal.get("dias_trabajados", 0))
    dias_tot  = int(cal.get("dias_totales", 30))

    c1, c2, c3 = st.columns([4, 4, 3])
    with c1:
        st.caption("Acumulado diario real vs meta")
        _render_evolucion(df_diario, obj_total, dias_t, dias_tot)
    with c2:
        st.caption("% Cumplimiento diario vs ritmo esperado")
        _render_ritmo(df_diario, obj_total, dias_tot)
    with c3:
        st.caption("Matriz de desempeño")
        _render_scatter(df)


# ── S5: Proyección · Top/Risk · Insights ─────────────────────────────────────

def _render_proyeccion(df: pd.DataFrame, cal: dict):
    fact_nc   = float(df["fact_nc"].sum())
    obj_total = float(df["obj_venta"].sum())
    dias_t    = int(cal.get("dias_trabajados", 1))
    dias_tot  = int(cal.get("dias_totales", 30))
    proy      = (fact_nc / dias_t * dias_tot) if dias_t else 0
    resultado = proy - obj_total
    res_cls   = "verde" if resultado >= 0 else "rojo"
    res_pfx   = "+" if resultado >= 0 else ""

    st.markdown(f"""
    <table class="proy-table">
      <tr class="proy-row-header"><td colspan="2">Proyección fin de mes</td></tr>
      <tr><td>Venta actual (Fact‑NC)</td><td>{fmt_clp(fact_nc)}</td></tr>
      <tr><td>Proyección cierre</td><td>{fmt_clp(proy)}</td></tr>
      <tr><td>Meta del mes</td><td>{fmt_clp(obj_total)}</td></tr>
      <tr>
        <td>Resultado esperado</td>
        <td style="color:{'#1A7F4B' if resultado>=0 else '#C0392B'}">
          {res_pfx}{fmt_clp(resultado)}
        </td>
      </tr>
    </table>
    """, unsafe_allow_html=True)


def _render_top_risk(df: pd.DataFrame):
    dft = df[df["obj_venta"] > 0].copy()
    dft["pct"] = dft["fact_nc"] / dft["obj_venta"]
    dft = dft.sort_values("pct", ascending=False).reset_index(drop=True)

    top3   = dft.head(3)
    riesgo = dft[dft["pct"] < 0.8]

    medallas = ["🥇", "🥈", "🥉"]

    def _li_top(i, row):
        # row es un namedtuple de itertuples → acceso por atributo
        cls  = _cls_semaforo(row.pct)
        name = str(row.nombre_canonico).split()[0]
        return (f'<li><span class="top-rank">{medallas[i]}</span>'
                f'<span class="top-name">{name}</span>'
                f'<span class="top-pct {cls}">{fmt_pct(row.pct)}</span></li>')

    def _li_risk(row):
        cls  = _cls_semaforo(row.pct)
        name = str(row.nombre_canonico).split()[0]
        return (f'<li><span class="top-rank">⚠️</span>'
                f'<span class="top-name">{name}</span>'
                f'<span class="top-pct {cls}">{fmt_pct(row.pct)}</span></li>')

    top_html  = "".join(_li_top(i, r) for i, r in enumerate(top3.itertuples()))
    risk_html = "".join(_li_risk(r) for r in riesgo.itertuples())
    if not risk_html:
        risk_html = '<li style="color:#6B7280;font-size:.74rem">Todos sobre 80% ✓</li>'

    c_top, c_risk = st.columns(2)
    with c_top:
        st.markdown(
            f'<div style="font-size:.68rem;font-weight:700;color:{_AZUL};'
            f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:.3rem">'
            f'Top 3</div>'
            f'<ul class="top-list">{top_html}</ul>',
            unsafe_allow_html=True,
        )
    with c_risk:
        st.markdown(
            f'<div style="font-size:.68rem;font-weight:700;color:{_ROJO};'
            f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:.3rem">'
            f'En riesgo</div>'
            f'<ul class="top-list">{risk_html}</ul>',
            unsafe_allow_html=True,
        )


def _render_insights(df: pd.DataFrame, cal: dict):
    fact_nc   = float(df["fact_nc"].sum())
    obj_total = float(df["obj_venta"].sum())
    pct_cumpl = fact_nc / obj_total if obj_total else 0
    dias_t    = int(cal.get("dias_trabajados", 1))
    dias_tot  = int(cal.get("dias_totales", 30))
    pct_dias  = dias_t / dias_tot if dias_tot else 0
    proy      = (fact_nc / dias_t * dias_tot) if dias_t else 0
    resultado = proy - obj_total

    dft = df[df["obj_venta"] > 0].copy()
    dft["pct"] = dft["fact_nc"] / dft["obj_venta"]
    n_riesgo  = int((dft["pct"] < 0.8).sum())
    n_cumpl   = int((dft["pct"] >= 1.0).sum())
    n_total   = len(dft)

    pp_diff   = (pct_cumpl - pct_dias) * 100
    sobre_bajo = "sobre" if pp_diff >= 0 else "bajo"

    bullets = []

    bullets.append((
        "📌",
        f"El equipo lleva <b>{fmt_pct(pct_cumpl)}</b> de cumplimiento, "
        f"<b>{abs(pp_diff):.1f} pp {sobre_bajo}</b> el ritmo esperado del mes.",
    ))

    if n_riesgo > 0:
        bullets.append((
            "⚠️",
            f"<b>{n_riesgo} de {n_total}</b> vendedor{'es' if n_riesgo>1 else ''} "
            f"{'están' if n_riesgo>1 else 'está'} bajo 80% de cumplimiento.",
        ))
    else:
        bullets.append(("✅", "Todos los vendedores superan el 80% de cumplimiento."))

    if resultado >= 0:
        bullets.append((
            "📈",
            f"Al ritmo actual se proyecta cerrar <b>{fmt_clp(resultado)} sobre la meta</b>.",
        ))
    else:
        bullets.append((
            "📉",
            f"Al ritmo actual se proyecta cerrar <b>{fmt_clp(abs(resultado))} bajo la meta</b>.",
        ))

    if n_cumpl > 0:
        bullets.append((
            "🏆",
            f"<b>{n_cumpl} vendedor{'es' if n_cumpl>1 else ''}</b> "
            f"ya {'superaron' if n_cumpl>1 else 'superó'} el 100% del objetivo.",
        ))

    items = "".join(
        f'<div class="insight-item">'
        f'<span class="insight-bullet">{ico}</span>'
        f'<span>{txt}</span></div>'
        for ico, txt in bullets
    )
    st.markdown(
        f'<div class="insight-card">'
        f'<div class="insight-title">💡 Insights del período</div>'
        f'{items}</div>',
        unsafe_allow_html=True,
    )


def _render_fila_inferior(df: pd.DataFrame, cal: dict):
    st.markdown('<div class="seccion-titulo">Proyección y análisis</div>',
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 4, 4])
    with c1:
        _render_proyeccion(df, cal)
    with c2:
        st.markdown(
            '<div style="font-size:.72rem;font-weight:700;color:#6B7280;'
            'text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem">'
            'Top performers / En riesgo</div>',
            unsafe_allow_html=True,
        )
        _render_top_risk(df)
    with c3:
        _render_insights(df, cal)


# ── Vista Vendedor (S2 simplificado) ─────────────────────────────────────────

def _render_kpis_vendedor(df: pd.DataFrame, cal: dict):
    """Para el rol vendedor: los mismos 6 KPIs pero sobre su única fila."""
    _render_kpis(df, cal, pct_anterior=None)


# ── Entry point ───────────────────────────────────────────────────────────────

def render(client, anio: int, mes: int, nombre_usuario: str = ""):
    # Datos comunes
    ultima_factura = get_ultima_factura(client, anio, mes)
    _render_header(anio, mes, ultima_factura)

    df = get_resumen(client, anio, mes)
    if df.empty:
        st.markdown(
            '<div class="estado-vacio">Sin datos para el período seleccionado. '
            'Carga los archivos del mes en <b>Carga de archivos</b>.</div>',
            unsafe_allow_html=True,
        )
        return

    df  = _preparar_df(df)
    cal = get_calendario(client, anio, mes)

    # Delta cumplimiento vs mes anterior (gerencia)
    pct_anterior = None
    if es_gerencia():
        anio_prev, mes_prev = _mes_anterior(anio, mes)
        df_prev = get_resumen(client, anio_prev, mes_prev)
        if not df_prev.empty:
            df_prev = _preparar_df(df_prev)
            fnc_p = df_prev["fact_nc"].sum()
            obj_p = df_prev["obj_venta"].sum()
            pct_anterior = fnc_p / obj_p if obj_p else None

    # S2 — KPIs (ambos roles)
    _render_kpis(df, cal, pct_anterior)

    # S3–S5 — solo gerencia (vendedor ve sus datos vía Panel Vendedor)
    if not es_gerencia():
        nombre = nombre_usuario.split()[0] if nombre_usuario else "vendedor"
        st.info(f"Hola {nombre}, aquí ves tu resumen mensual. "
                "Para el detalle completo abre **Mi Panel** en el menú.")
        return

    # S3 — Tabla + Ranking + Gauge
    _render_fila_media(df)

    # S4 — Evolución diaria + Ritmo + Scatter
    df_diario = get_ventas_diarias(client, anio, mes)
    _render_fila_graficos(df, df_diario, cal)

    # S5 — Proyección + Top/Risk + Insights
    _render_fila_inferior(df, cal)
