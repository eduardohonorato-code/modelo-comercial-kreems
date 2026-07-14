"""Clientes — CRM analítico comercial.

Pestañas:
  📋 Resumen      → KPIs ejecutivos + concentración de cartera + evolución.
  🧩 Segmentación → ABC por facturación + RFM.
  🚨 Alertas      → insights accionables (crecen, caen, riesgo, potencial…).
  🏆 Ranking      → tabla avanzada con badges, barras y tendencia.
  👤 Ficha        → vista detalle por cliente (CRM).

Todas las métricas salen de fact_ventas (vía get_clientes_historia, paginado)
y RLS filtra automáticamente: el vendedor ve solo sus clientes; gerencia, todos.
Fact-NC = SUM(neto), con las NC ya en negativo (consistente con el resto de la app).

Definiciones temporales (cadencia mensual; el "mes actual" = período del sidebar):
  · Recency = nº de meses desde la última compra (0 = compró este mes).
  · Activo      → recency ≤ 1   · Riesgo  → recency = 2   · Perdido → recency ≥ 3
  · Nuevo       → su primera compra histórica es el mes actual.
  · Recuperado  → compró este mes y antes estuvo ≥ 3 meses sin comprar.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from app.styles import fmt_clp, fmt_num, fmt_pct
from app.data import (get_clientes_historia, get_cliente_detalle,
                      get_direcciones_cliente, get_dim_sociedad)

_CHART = "#E62984"
_PALETA = ["#E62984", "#1E88E5", "#26A69A", "#D4881E", "#7C3AED",
           "#0288D1", "#1A7F4B", "#F57C00", "#64748B", "#9E175A"]
_MES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}

# Color por estado (badges / chips / gráficos).
_ESTADO_COLOR = {
    "Activo": "#1A7F4B", "Nuevo": "#1E88E5", "Recuperado": "#7C3AED",
    "Riesgo": "#D4881E", "Perdido": "#C0392B", "Sin compras": "#94A3B8",
}

_CSS = """
<style>
.cl-badge{display:inline-block;padding:.12rem .55rem;border-radius:999px;
  font-size:.72rem;font-weight:700;color:#fff;white-space:nowrap}
.cl-chip{display:inline-block;padding:.05rem .45rem;border-radius:6px;
  font-size:.7rem;font-weight:700}
.cl-bar-wrap{background:#EEF2F6;border-radius:5px;height:8px;width:100%;
  min-width:60px;overflow:hidden}
.cl-bar-fill{height:8px;border-radius:5px;background:#E62984}
.cl-alert{border:1px solid #EEF2F6;border-left:4px solid var(--ac,#E62984);
  border-radius:10px;padding:.7rem .9rem;margin-bottom:.55rem;background:#fff}
.cl-alert .t{font-weight:700;font-size:.82rem;color:#0F172A}
.cl-alert .s{font-size:.75rem;color:#64748B;margin-top:.1rem}
.cl-alert .v{float:right;font-weight:800}
</style>
"""


# ─── Helpers UI ────────────────────────────────────────────────────────────────
def _sec(t):
    st.markdown(f'<div class="seccion-titulo">{t}</div>', unsafe_allow_html=True)


def _empty(msg="Sin datos para los filtros seleccionados."):
    st.markdown(f'<div class="estado-vacio">{msg}</div>', unsafe_allow_html=True)


def _badge(estado):
    c = _ESTADO_COLOR.get(estado, "#94A3B8")
    return f'<span class="cl-badge" style="background:{c}">{estado}</span>'


def _ym_label(ym):
    if not ym or pd.isna(ym):
        return "—"
    y, m = ym.split("-")
    return f"{_MES.get(int(m), m)} {y}"


def _fig(fig, h=330):
    fig.update_layout(
        height=h, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=24, b=8, l=8, r=8),
        font=dict(family="Inter, system-ui, sans-serif", size=11),
    )
    return fig


# ─── Núcleo analítico: perfil por cliente ───────────────────────────────────────
def _build_perfil(hist: pd.DataFrame, current_ym: str) -> pd.DataFrame:
    """Una fila por cliente con todas las métricas derivadas."""
    p_cur = pd.Period(current_ym, "M")
    prev_ym = str(p_cur - 1)
    hist = hist[hist["ym"] <= current_ym].copy()

    recs = []
    for rut, g in hist.groupby("cliente_rut"):
        info = g.iloc[0]
        activos = sorted(g.loc[g["n_facturas"] > 0, "ym"].unique())
        ventas_total = float(g["fact_nc"].sum())
        fac_total = int(g["n_facturas"].sum())
        fact_cur = float(g.loc[g["ym"] == current_ym, "fact_nc"].sum())
        nfac_cur = int(g.loc[g["ym"] == current_ym, "n_facturas"].sum())
        fact_prev = float(g.loc[g["ym"] == prev_ym, "fact_nc"].sum())

        if activos:
            last_p = pd.Period(activos[-1], "M")
            recency = (p_cur - last_p).n
            first_ym = activos[0]
        else:
            recency, first_ym = None, None

        if not activos:
            estado = "Sin compras"
        elif first_ym == current_ym:
            estado = "Nuevo"
        else:
            recuperado = False
            if recency == 0 and len(activos) >= 2:
                gap = (pd.Period(activos[-1], "M") - pd.Period(activos[-2], "M")).n
                recuperado = gap >= 3
            if recuperado:
                estado = "Recuperado"
            elif recency <= 1:
                estado = "Activo"
            elif recency == 2:
                estado = "Riesgo"
            else:
                estado = "Perdido"

        if fact_prev > 0:
            trend = (fact_cur - fact_prev) / fact_prev
        elif fact_cur > 0:
            trend = 1.0           # apareció este mes (sin base previa)
        else:
            trend = None

        recs.append({
            "cliente_rut": rut,
            "razon_social": info.get("razon_social") or rut,
            "comuna": info.get("comuna"), "region": info.get("region"),
            "ventas_total": ventas_total, "fac_total": fac_total,
            "fact_cur": fact_cur, "nfac_cur": nfac_cur, "fact_prev": fact_prev,
            "frecuencia": len(activos), "recency": recency,
            "last_ym": activos[-1] if activos else None,
            "ticket": ventas_total / fac_total if fac_total else 0.0,
            "estado": estado, "trend": trend,
        })

    df = pd.DataFrame(recs)
    if df.empty:
        return df

    # ── ABC por facturación acumulada ──
    df = df.sort_values("ventas_total", ascending=False).reset_index(drop=True)
    tot = df["ventas_total"].clip(lower=0).sum()
    df["cum_pct"] = df["ventas_total"].clip(lower=0).cumsum() / tot if tot else 0
    df["abc"] = pd.cut(df["cum_pct"], [-0.01, 0.8, 0.95, 1.01],
                       labels=["A", "B", "C"]).astype(str)
    df["part"] = df["ventas_total"] / tot if tot else 0

    # ── RFM (scores 1-5) ──
    def _rank(s, asc):
        r = s.rank(method="average", pct=True, ascending=asc)
        return (r * 5).clip(1, 5).round().astype(int)

    rec_fill = df["recency"].fillna(df["recency"].max() + 1 if df["recency"].notna().any() else 99)
    df["R"] = _rank(rec_fill, asc=False)            # menos recency → mejor
    df["F"] = _rank(df["frecuencia"], asc=True)
    df["M"] = _rank(df["ventas_total"], asc=True)
    df["segmento"] = df.apply(_segmento, axis=1)
    return df


def _segmento(r):
    if r["estado"] == "Recuperado":
        return "En recuperación"
    if r["estado"] == "Nuevo":
        return "Nuevos"
    if r["estado"] == "Perdido":
        return "Dormidos"
    if r["estado"] == "Riesgo":
        return "En riesgo"
    if r["M"] >= 4 and r["F"] >= 4:
        return "Estratégicos"
    if r["F"] >= 4:
        return "Frecuentes"
    if r["F"] <= 2:
        return "Ocasionales"
    return "Regulares"


# ─── Carga cacheada por usuario ─────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _hist_cached(scope: str, soc_key: str) -> pd.DataFrame:
    from app.auth import get_client_auth
    cli = get_client_auth()
    sids = None if soc_key == "all" else [int(x) for x in soc_key.split(",")]
    return get_clientes_historia(cli, sids)


# ════════════════════════════════════════════════════════════════════════════════
def render(client, anio: int, mes: int):
    st.markdown(_CSS, unsafe_allow_html=True)
    current_ym = f"{anio}-{mes:02d}"

    # ── Filtro sociedad ──
    df_soc = get_dim_sociedad(client)
    soc_map = (dict(zip(df_soc["nombre"].str.strip(), df_soc["id"]))
               if not df_soc.empty and {"id", "nombre"}.issubset(df_soc.columns) else {})
    c1, _ = st.columns([2, 5])
    with c1:
        soc_sel = st.selectbox("Sociedad", ["Ambas"] + sorted(soc_map.keys()),
                               key="cli_soc")
    soc_key = ("all" if soc_sel == "Ambas" or soc_sel not in soc_map
               else str(soc_map[soc_sel]))

    scope = f"{st.session_state.get('user_id','')}:{st.session_state.get('vendedor_id','')}"
    hist = _hist_cached(scope, soc_key)
    if hist.empty:
        _empty("Aún no hay ventas cargadas para tus clientes.")
        return

    perfil = _build_perfil(hist, current_ym)
    if perfil.empty:
        _empty()
        return

    tabs = st.tabs(["📋 Resumen", "🧩 Segmentación", "🚨 Alertas",
                    "🏆 Ranking", "👤 Ficha cliente"])
    with tabs[0]:
        _tab_resumen(perfil, hist, current_ym)
    with tabs[1]:
        _tab_segmentacion(perfil)
    with tabs[2]:
        _tab_alertas(perfil)
    with tabs[3]:
        _tab_ranking(perfil)
    with tabs[4]:
        _tab_ficha(client, perfil, hist, current_ym)


# ─── TAB 1 · Resumen ejecutivo ──────────────────────────────────────────────────
def _tab_resumen(perfil, hist, current_ym):
    p_cur = pd.Period(current_ym, "M")
    compraron = perfil[perfil["nfac_cur"] > 0]
    n_compra = len(compraron)
    fact_mes = float(compraron["fact_cur"].sum())
    nfac_mes = int(compraron["nfac_cur"].sum())

    n_activos = int((perfil["estado"] == "Activo").sum())
    n_nuevos = int((perfil["estado"] == "Nuevo").sum())
    n_recup = int((perfil["estado"] == "Recuperado").sum())
    n_riesgo = int((perfil["estado"] == "Riesgo").sum())
    n_perd = int((perfil["estado"] == "Perdido").sum())

    venta_prom = fact_mes / n_compra if n_compra else 0
    frec_prom = nfac_mes / n_compra if n_compra else 0
    ticket = fact_mes / nfac_mes if nfac_mes else 0

    _sec(f"Resumen ejecutivo · {_ym_label(current_ym)}")
    # Fila 1: estados de cartera (conteos)
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado"><div class="kpi-label">Clientes activos</div>
        <div class="kpi-value">{fmt_num(n_activos)}</div>
        <div class="kpi-sub">compra ≤ 1 mes</div></div>
      <div class="kpi-card"><div class="kpi-label">Nuevos</div>
        <div class="kpi-value" style="color:{_ESTADO_COLOR['Nuevo']}">{fmt_num(n_nuevos)}</div>
        <div class="kpi-sub">1ª compra este mes</div></div>
      <div class="kpi-card"><div class="kpi-label">Recuperados</div>
        <div class="kpi-value" style="color:{_ESTADO_COLOR['Recuperado']}">{fmt_num(n_recup)}</div>
        <div class="kpi-sub">volvieron tras ≥3 m</div></div>
      <div class="kpi-card"><div class="kpi-label">En riesgo</div>
        <div class="kpi-value" style="color:{_ESTADO_COLOR['Riesgo']}">{fmt_num(n_riesgo)}</div>
        <div class="kpi-sub">sin comprar hace 2 m</div></div>
      <div class="kpi-card"><div class="kpi-label">Perdidos</div>
        <div class="kpi-value" style="color:{_ESTADO_COLOR['Perdido']}">{fmt_num(n_perd)}</div>
        <div class="kpi-sub">≥ 3 m sin comprar</div></div>
    </div>
    """, unsafe_allow_html=True)

    # Fila 2: métricas monetarias del mes
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Venta prom. / cliente</div>
        <div class="kpi-value">{fmt_clp(venta_prom)}</div>
        <div class="kpi-sub">Fact-NC mes / clientes que compraron</div></div>
      <div class="kpi-card"><div class="kpi-label">Frecuencia prom.</div>
        <div class="kpi-value">{frec_prom:.1f}</div>
        <div class="kpi-sub">facturas por cliente (mes)</div></div>
      <div class="kpi-card"><div class="kpi-label">Ticket promedio</div>
        <div class="kpi-value">{fmt_clp(ticket)}</div>
        <div class="kpi-sub">Fact-NC mes / N° facturas</div></div>
      <div class="kpi-card"><div class="kpi-label">Clientes que compraron</div>
        <div class="kpi-value">{fmt_num(n_compra)}</div>
        <div class="kpi-sub">de {fmt_num(len(perfil))} en cartera</div></div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([3, 2])

    # Evolución de la cartera (Fact-NC por mes)
    with col1:
        _sec("Evolución de la cartera")
        serie = (hist[hist["ym"] <= current_ym]
                 .groupby("ym")["fact_nc"].sum().reset_index())
        fig = go.Figure(go.Scatter(
            x=[_ym_label(m) for m in serie["ym"]], y=serie["fact_nc"],
            mode="lines+markers", line=dict(color=_CHART, width=3),
            fill="tozeroy", fillcolor="rgba(230,41,132,.08)",
            hovertemplate="%{x}<br>Fact-NC: %{y:,.0f}<extra></extra>"))
        st.plotly_chart(_fig(fig, 300), use_container_width=True)

    # Concentración de cartera (Pareto + Top10/Top20)
    with col2:
        _sec("Concentración de cartera")
        d = perfil[perfil["ventas_total"] > 0].sort_values(
            "ventas_total", ascending=False).reset_index(drop=True)
        total = d["ventas_total"].sum()
        top10 = d.head(10)["ventas_total"].sum() / total if total else 0
        top20 = d.head(20)["ventas_total"].sum() / total if total else 0
        dep_cls = "rojo-bg" if top10 >= 0.5 else ("amarillo-bg" if top10 >= 0.35 else "verde-bg")
        st.markdown(f"""
        <div class="kpi-grid" style="grid-template-columns:1fr 1fr">
          <div class="kpi-card"><div class="kpi-label">Top 10 clientes</div>
            <div class="kpi-value {dep_cls}">{fmt_pct(top10)}</div>
            <div class="kpi-sub">de las ventas</div></div>
          <div class="kpi-card"><div class="kpi-label">Top 20 clientes</div>
            <div class="kpi-value">{fmt_pct(top20)}</div>
            <div class="kpi-sub">de las ventas</div></div>
        </div>
        """, unsafe_allow_html=True)
        if top10 >= 0.5:
            st.warning(f"⚠️ Dependencia alta: el top 10 concentra el {fmt_pct(top10)} "
                       "de la facturación. Conviene diversificar la cartera.")

    # Curva de Pareto (acumulado)
    _sec("Curva de Pareto — acumulado de ventas")
    d = perfil[perfil["ventas_total"] > 0].sort_values(
        "ventas_total", ascending=False).reset_index(drop=True).head(40)
    d["rank"] = range(1, len(d) + 1)
    d["cum"] = d["ventas_total"].cumsum() / perfil["ventas_total"].clip(lower=0).sum()
    fig = go.Figure()
    fig.add_bar(x=d["rank"], y=d["ventas_total"], marker_color="rgba(230,41,132,.55)",
                name="Fact-NC", hovertext=d["razon_social"],
                hovertemplate="%{hovertext}<br>%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=d["rank"], y=d["cum"], yaxis="y2", mode="lines+markers",
                  line=dict(color="#1E293B", width=2.5), name="% acumulado",
                  hovertemplate="Top %{x}: %{y:.0%}<extra></extra>"))
    fig.add_hline(y=0.8, line_dash="dash", line_color="#94A3B8", yref="y2")
    fig.update_layout(
        yaxis2=dict(overlaying="y", side="right", range=[0, 1], tickformat=".0%"),
        xaxis_title="Clientes (ordenados por Fact-NC)",
        legend=dict(orientation="h", y=1.12, x=0))
    st.plotly_chart(_fig(fig, 320), use_container_width=True)


# ─── TAB 2 · Segmentación ───────────────────────────────────────────────────────
def _tab_segmentacion(perfil):
    _sec("Segmentación ABC por facturación")
    abc = (perfil.groupby("abc")
           .agg(clientes=("cliente_rut", "count"),
                ventas=("ventas_total", "sum")).reindex(["A", "B", "C"]).fillna(0))
    tot_v = abc["ventas"].sum()
    cols = st.columns(3)
    desc = {"A": "≈80% de las ventas", "B": "siguiente 15%", "C": "último 5%"}
    colA = {"A": "#1A7F4B", "B": "#D4881E", "C": "#94A3B8"}
    for i, k in enumerate(["A", "B", "C"]):
        with cols[i]:
            pv = abc.loc[k, "ventas"] / tot_v if tot_v else 0
            st.markdown(f"""
            <div class="kpi-card"><div class="kpi-label">
              <span class="cl-chip" style="background:{colA[k]}22;color:{colA[k]}">Clase {k}</span>
              &nbsp;{desc[k]}</div>
              <div class="kpi-value">{int(abc.loc[k,'clientes'])} <span style="font-size:.8rem;color:#94A3B8">clientes</span></div>
              <div class="kpi-sub">{fmt_clp(abc.loc[k,'ventas'])} · {fmt_pct(pv)}</div></div>
            """, unsafe_allow_html=True)

    st.divider()
    _sec("Segmentación RFM")
    st.caption("R = recencia (meses desde última compra) · F = nº de meses con compra · "
               "M = Fact-NC acumulado. Cada eje se puntúa 1–5 por ranking.")

    seg = (perfil.groupby("segmento")
           .agg(clientes=("cliente_rut", "count"),
                ventas=("ventas_total", "sum"))
           .sort_values("ventas", ascending=False).reset_index())
    c1, c2 = st.columns([1, 1])
    with c1:
        fig = go.Figure(go.Bar(
            x=seg["clientes"], y=seg["segmento"], orientation="h",
            marker_color=_PALETA[:len(seg)],
            text=seg["clientes"], textposition="auto"))
        fig.update_layout(yaxis=dict(autorange="reversed"),
                          xaxis_title="N° de clientes")
        _sec("Clientes por segmento")
        st.plotly_chart(_fig(fig, 330), use_container_width=True)
    with c2:
        _sec("Mapa RFM (recencia vs valor)")
        pp = perfil[perfil["recency"].notna()].copy()
        fig = go.Figure()
        for s in seg["segmento"]:
            sub = pp[pp["segmento"] == s]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["recency"], y=sub["ventas_total"], mode="markers", name=s,
                marker=dict(size=(sub["frecuencia"] * 4 + 6), opacity=.75),
                text=sub["razon_social"],
                hovertemplate="%{text}<br>Recency: %{x} m<br>Fact-NC: %{y:,.0f}<extra></extra>"))
        fig.update_layout(xaxis_title="Recency (meses)", yaxis_title="Fact-NC acumulado",
                          legend=dict(font=dict(size=9)))
        st.plotly_chart(_fig(fig, 330), use_container_width=True)


# ─── TAB 3 · Alertas comerciales ────────────────────────────────────────────────
def _alert_card(color, icon, titulo, sub, valor=""):
    return (f'<div class="cl-alert" style="--ac:{color}">'
            f'<span class="v" style="color:{color}">{valor}</span>'
            f'<div class="t">{icon} {titulo}</div>'
            f'<div class="s">{sub}</div></div>')


def _lista_alertas(df, color, icon, sub_fn, valor_fn, vacio):
    if df.empty:
        st.caption(vacio)
        return
    html = ""
    for _, r in df.iterrows():
        html += _alert_card(color, icon, r["razon_social"], sub_fn(r), valor_fn(r))
    st.markdown(html, unsafe_allow_html=True)


def _tab_alertas(perfil):
    p = perfil.copy()
    p["delta"] = p["fact_cur"] - p["fact_prev"]

    _sec("📈 Crecimiento y caída (mes vs mes anterior)")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🟢 Mayor crecimiento**")
        crece = p[p["delta"] > 0].nlargest(5, "delta")
        _lista_alertas(crece, "#1A7F4B", "▲",
                       lambda r: f"{_ym_label(str(pd.Period(r['last_ym'],'M'))) if r['last_ym'] else ''} · {fmt_clp(r['fact_prev'])} → {fmt_clp(r['fact_cur'])}",
                       lambda r: "+" + fmt_clp(r["delta"]),
                       "Sin crecimientos este mes.")
    with c2:
        st.markdown("**🔴 Mayor caída**")
        cae = p[p["delta"] < 0].nsmallest(5, "delta")
        _lista_alertas(cae, "#C0392B", "▼",
                       lambda r: f"{fmt_clp(r['fact_prev'])} → {fmt_clp(r['fact_cur'])}",
                       lambda r: fmt_clp(r["delta"]),
                       "Sin caídas este mes.")

    st.divider()
    _sec("🚪 Abandono y riesgo")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Dejaron de comprar (perdidos de mayor valor)**")
        perd = p[p["estado"] == "Perdido"].nlargest(5, "ventas_total")
        _lista_alertas(perd, "#C0392B", "⛔",
                       lambda r: f"Última compra: {_ym_label(r['last_ym'])} · {r['recency']:.0f} meses",
                       lambda r: fmt_clp(r["ventas_total"]),
                       "Sin clientes perdidos relevantes. 🎉")
    with c2:
        st.markdown("**Riesgo de abandono (2 meses sin comprar)**")
        rie = p[p["estado"] == "Riesgo"].nlargest(5, "ventas_total")
        _lista_alertas(rie, "#D4881E", "⚠️",
                       lambda r: f"Última compra: {_ym_label(r['last_ym'])}",
                       lambda r: fmt_clp(r["ventas_total"]),
                       "Sin clientes en riesgo.")

    st.divider()
    _sec("💡 Oportunidades")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Comprando menos de lo habitual**")
        menos = p[(p["estado"].isin(["Activo", "Riesgo"])) & (p["fact_prev"] > 0)
                  & (p["trend"].notna()) & (p["trend"] < -0.3)].nsmallest(5, "trend")
        _lista_alertas(menos, "#D4881E", "🔻",
                       lambda r: f"Cae {fmt_pct(abs(r['trend']))} vs mes anterior",
                       lambda r: fmt_clp(r["fact_cur"]),
                       "Sin caídas relevantes en activos.")
    with c2:
        st.markdown("**Potencial de crecimiento (upsell)**")
        pot = p[(p["abc"] == "B") & (p["estado"] == "Activo")
                & (p["frecuencia"] >= 3)].nlargest(5, "ventas_total")
        _lista_alertas(pot, "#1E88E5", "🚀",
                       lambda r: f"Clase B frecuente ({r['frecuencia']:.0f} meses) — candidato a clase A",
                       lambda r: fmt_clp(r["ventas_total"]),
                       "Sin candidatos claros de upsell.")


# ─── TAB 4 · Ranking ────────────────────────────────────────────────────────────
def _tab_ranking(perfil):
    _sec("Ranking de clientes")
    c1, c2 = st.columns([2, 3])
    with c1:
        estados = ["Todos"] + sorted(perfil["estado"].unique().tolist())
        f_est = st.selectbox("Estado", estados, key="cli_rk_est")
    with c2:
        top_n = st.slider("Mostrar", 10, 100, 25, step=5, key="cli_rk_n")

    d = perfil if f_est == "Todos" else perfil[perfil["estado"] == f_est]
    d = d.sort_values("ventas_total", ascending=False).head(top_n)
    if d.empty:
        _empty()
        return
    max_part = d["part"].max() or 1

    header = ("<th style='text-align:left'>#</th><th style='text-align:left'>Cliente</th>"
              "<th>Clase</th><th>Venta acumulada</th><th>Participación</th>"
              "<th>Ticket prom.</th><th>Frec.</th><th>Última compra</th>"
              "<th>Tendencia</th><th>Estado</th>")
    rows = ""
    for i, (_, r) in enumerate(d.iterrows(), start=1):
        bar_w = int((r["part"] / max_part) * 100)
        if r["trend"] is None:
            tend = "<span style='color:#94A3B8'>—</span>"
        elif r["trend"] >= 0:
            tend = f"<span style='color:#1A7F4B;font-weight:700'>▲ {fmt_pct(r['trend'])}</span>"
        else:
            tend = f"<span style='color:#C0392B;font-weight:700'>▼ {fmt_pct(abs(r['trend']))}</span>"
        abc_c = {"A": "#1A7F4B", "B": "#D4881E", "C": "#94A3B8"}.get(r["abc"], "#94A3B8")
        rows += f"""<tr>
          <td style='text-align:left'>{i}</td>
          <td style='text-align:left'>{r['razon_social']}</td>
          <td><span class="cl-chip" style="background:{abc_c}22;color:{abc_c}">{r['abc']}</span></td>
          <td>{fmt_clp(r['ventas_total'])}</td>
          <td><div style="display:flex;align-items:center;gap:.4rem">
                <div class="cl-bar-wrap"><div class="cl-bar-fill" style="width:{bar_w}%"></div></div>
                <span style="font-size:.72rem;color:#475569">{fmt_pct(r['part'])}</span></div></td>
          <td>{fmt_clp(r['ticket'])}</td>
          <td>{int(r['frecuencia'])}</td>
          <td>{_ym_label(r['last_ym'])}</td>
          <td>{tend}</td>
          <td>{_badge(r['estado'])}</td>
        </tr>"""
    st.markdown(f"""<div class="tabla-container">
      <table class="kreems"><thead><tr>{header}</tr></thead>
      <tbody>{rows}</tbody></table></div>""", unsafe_allow_html=True)


# ─── TAB 5 · Ficha cliente ──────────────────────────────────────────────────────
def _health_score(r, n_meses):
    """0-100: combina recencia (40), frecuencia (30) y tendencia (30)."""
    rec = r["recency"]
    rec_c = 0 if rec is None else max(0, 1 - rec / 3)
    frec_c = min(1, r["frecuencia"] / n_meses) if n_meses else 0
    t = r["trend"]
    if t is None:
        tr_c = 0.3
    elif t >= 0.1:
        tr_c = 1.0
    elif t >= -0.1:
        tr_c = 0.6
    elif t >= -0.3:
        tr_c = 0.35
    else:
        tr_c = 0.1
    return round(100 * (0.4 * rec_c + 0.3 * frec_c + 0.3 * tr_c))


def _tab_ficha(client, perfil, hist, current_ym):
    opts = perfil.sort_values("ventas_total", ascending=False)
    label_map = {f"{r['razon_social']}  ·  {r['cliente_rut']}": r["cliente_rut"]
                 for _, r in opts.iterrows()}
    sel = st.selectbox("Selecciona un cliente", list(label_map.keys()), key="cli_ficha")
    rut = label_map[sel]
    r = perfil[perfil["cliente_rut"] == rut].iloc[0]
    n_meses = hist[hist["ym"] <= current_ym]["ym"].nunique()
    score = _health_score(r, n_meses)
    sc_color = "#1A7F4B" if score >= 66 else ("#D4881E" if score >= 40 else "#C0392B")

    # ── Cabecera + KPIs ──
    st.markdown(f"### {r['razon_social']}  {_badge(r['estado'])}", unsafe_allow_html=True)
    st.caption(f"{r['cliente_rut']} · {r.get('comuna') or '—'}, {r.get('region') or '—'} · "
               f"Clase {r['abc']} · Segmento {r['segmento']}")

    k = st.columns(5)
    k[0].metric("Fact-NC acumulado", fmt_clp(r["ventas_total"]))
    k[1].metric("Ticket promedio", fmt_clp(r["ticket"]))
    k[2].metric("Frecuencia", f"{int(r['frecuencia'])} meses")
    k[3].metric("Última compra", _ym_label(r["last_ym"]))
    k[4].metric("Recency", "—" if r["recency"] is None else f"{int(r['recency'])} m")

    c1, c2 = st.columns([2, 1])
    # Evolución ventas + pedidos
    with c1:
        _sec("Evolución de ventas y pedidos")
        gv = (hist[(hist["cliente_rut"] == rut) & (hist["ym"] <= current_ym)]
              .groupby("ym")["fact_nc"].sum().reset_index())
        dfv, dfp = get_cliente_detalle(client, rut)
        fig = go.Figure()
        fig.add_bar(x=[_ym_label(m) for m in gv["ym"]], y=gv["fact_nc"],
                    marker_color=_CHART, name="Ventas (Fact-NC)",
                    hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>")
        if not dfp.empty:
            gp = dfp.copy()
            gp["ym"] = gp["fecha"].dt.strftime("%Y-%m")
            gp = gp[gp["ym"] <= current_ym].groupby("ym")["neto"].sum().reset_index()
            fig.add_trace(go.Scatter(
                x=[_ym_label(m) for m in gp["ym"]], y=gp["neto"], mode="lines+markers",
                line=dict(color="#1E88E5", width=2.5), name="Pedidos (neto)",
                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"))
        fig.update_layout(legend=dict(orientation="h", y=1.15, x=0))
        st.plotly_chart(_fig(fig, 300), use_container_width=True)

    # Health score gauge
    with c2:
        _sec("Salud comercial")
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=score,
            number={"suffix": "/100", "font": {"size": 26}},
            gauge={"axis": {"range": [0, 100]},
                   "bar": {"color": sc_color},
                   "steps": [{"range": [0, 40], "color": "#FDECEA"},
                             {"range": [40, 66], "color": "#FEF6E7"},
                             {"range": [66, 100], "color": "#E8F5EE"}]}))
        st.plotly_chart(_fig(fig, 230), use_container_width=True)
        st.caption("Recencia 40% · frecuencia 30% · tendencia 30%")

    # Facturación por sucursal / dirección
    _bloque_sucursales(client, rut, dfv, current_ym)

    # Mix de productos / categorías
    if not dfv.empty:
        c1, c2 = st.columns(2)
        with c1:
            _sec("Mix de categorías")
            if "categoria" in dfv.columns:
                cat = (dfv.groupby(dfv["categoria"].fillna("SIN CATEGORÍA"))["neto"]
                       .sum().sort_values(ascending=False).head(8))
                fig = go.Figure(go.Pie(labels=cat.index, values=cat.values, hole=.55,
                                       marker_colors=_PALETA))
                fig.update_traces(textposition="inside", textinfo="percent")
                st.plotly_chart(_fig(fig, 300), use_container_width=True)
        with c2:
            _sec("Top productos")
            col = "nombre_producto" if "nombre_producto" in dfv.columns else "producto_codigo"
            prod = (dfv.groupby(dfv[col].fillna("?"))["neto"]
                    .sum().sort_values(ascending=False).head(8).iloc[::-1])
            fig = go.Figure(go.Bar(x=prod.values, y=prod.index, orientation="h",
                                   marker_color=_CHART,
                                   text=[fmt_clp(v) for v in prod.values],
                                   textposition="auto"))
            st.plotly_chart(_fig(fig, 300), use_container_width=True)


# ─── Facturación por sucursal / dirección ──────────────────────────────────────
def _txt(v) -> str:
    """Texto limpio de un campo que puede venir None o NaN (las direcciones que
    vienen de Obuma no traen `nombre`, y `NaN or ''` devuelve NaN, no '')."""
    return "" if v is None or pd.isna(v) else str(v).strip()


def _label_sucursal(r) -> str:
    """Nombre de la sucursal; si es la genérica 'Dirección principal', usa la calle."""
    nombre = _txt(r.get("nombre"))
    calle = _txt(r.get("direccion"))
    if not nombre or nombre.lower().startswith("dirección principal"):
        nombre = "Casa matriz" if r.get("es_principal") else (calle or "Sin nombre")
    return nombre


def _bloque_sucursales(client, rut, dfv, current_ym):
    """
    Desglose de Fact-NC por sucursal (dirección de despacho del pedido).

    Solo hay dirección donde la venta pasó por Autoventa (Gran Natural, facturas):
    Acuña y las notas de crédito quedan sin sucursal y se reportan aparte en vez
    de repartirse a ojo.
    """
    if dfv.empty:
        return
    if "direccion_id" not in dfv.columns:
        st.info("No se pudo leer `fact_ventas.direccion_id` (¿falta correr "
                "sql/027_dim_direccion.sql en Supabase?).")
        return

    v = dfv[dfv["fecha"].dt.strftime("%Y-%m") <= current_ym].copy()
    con_dir = v[v["direccion_id"].notna()].copy()
    if con_dir.empty:
        st.caption("📍 Sin sucursal identificada: no hay facturas de Gran Natural "
                   "cruzadas con un pedido de Autoventa para este cliente.")
        return
    con_dir["direccion_id"] = con_dir["direccion_id"].astype("int64")

    # Lo que quedó SIN atribuir (no se reparte a ojo). Es poco: la dirección de
    # despacho la trae Obuma en cada documento (Excel para Acuña, API para Gran
    # Natural) y Autoventa la trae en el pedido. Quedan fuera sobre todo las NC de
    # anulación, cuya observación dice "Anulación venta" en vez de una dirección.
    es_nc = ~v["tipo_dcto"].astype(str).str.upper().str.startswith("FACTURA")
    sin = v[v["direccion_id"].isna()]
    nc_sin_dir = float(sin.loc[es_nc.reindex(sin.index, fill_value=False), "neto"].sum())
    fac_sin_dir = float(sin.loc[~es_nc.reindex(sin.index, fill_value=False), "neto"].sum())

    try:
        dirs = get_direcciones_cliente(
            client, rut, ids=sorted(con_dir["direccion_id"].unique().tolist()))
    except Exception as exc:
        st.warning(f"No se pudo leer dim_direccion: {exc}")
        return
    if dirs.empty:
        st.warning("Las ventas tienen sucursal asignada, pero dim_direccion no "
                   "devolvió filas (revisar RLS/grants de la tabla).")
        return

    dirs = dirs.rename(columns={"id": "direccion_id"}).copy()
    dirs["sucursal"] = dirs.apply(_label_sucursal, axis=1)
    con_dir = con_dir.merge(
        dirs[["direccion_id", "sucursal", "direccion", "comuna", "ruta"]],
        on="direccion_id", how="left")
    con_dir["sucursal"] = con_dir["sucursal"].fillna("Sucursal desconocida")
    con_dir["ym"] = con_dir["fecha"].dt.strftime("%Y-%m")

    g = (con_dir.groupby(["direccion_id", "sucursal"])
         .agg(fact_nc=("neto", "sum"), n_fac=("n_dcto", "nunique"),
              ultima=("ym", "max"), direccion=("direccion", "first"),
              comuna=("comuna", "first"), ruta=("ruta", "first"))
         .reset_index().sort_values("fact_nc", ascending=False))

    if len(g) == 1:
        com = _txt(g.iloc[0].get("comuna"))
        st.caption(f"📍 Un solo punto de venta: **{g.iloc[0]['sucursal']}**"
                   + (f" · {com}" if com else ""))
        return

    _sec(f"Facturación por sucursal ({len(g)} puntos de venta)")
    st.caption("Fact-NC por punto de venta. La sucursal sale de la dirección de "
               "despacho del documento (Obuma para Acuña, el pedido de Autoventa "
               "para Gran Natural).")

    total = g["fact_nc"].sum() or 1
    c1, c2 = st.columns([3, 2])
    with c1:
        rows = ""
        for i, (_, r) in enumerate(g.iterrows(), start=1):
            part = r["fact_nc"] / total
            bar_w = int((r["fact_nc"] / (g["fact_nc"].max() or 1)) * 100)
            sub = " · ".join(x for x in [_txt(r.get("direccion")), _txt(r.get("comuna"))] if x)
            _ruta = _txt(r.get("ruta"))
            ruta = (f"<span class='cl-chip' style='background:#E6298422;"
                    f"color:#E62984'>{_ruta}</span>") if _ruta else ""
            rows += f"""<tr>
              <td style='text-align:left'>{i}</td>
              <td style='text-align:left'><b>{r['sucursal']}</b> {ruta}
                  <div style='font-size:.72rem;color:#64748B'>{sub}</div></td>
              <td>{fmt_clp(r['fact_nc'])}</td>
              <td><div style="display:flex;align-items:center;gap:.4rem">
                    <div class="cl-bar-wrap"><div class="cl-bar-fill" style="width:{bar_w}%"></div></div>
                    <span style="font-size:.72rem;color:#475569">{fmt_pct(part)}</span></div></td>
              <td>{int(r['n_fac'])}</td>
              <td>{_ym_label(r['ultima'])}</td>
            </tr>"""
        st.markdown(f"""<div class="tabla-container">
          <table class="kreems"><thead><tr>
            <th style='text-align:left'>#</th><th style='text-align:left'>Sucursal</th>
            <th>Fact-NC</th><th>Participación</th><th>N° facturas</th><th>Última compra</th>
          </tr></thead><tbody>{rows}</tbody></table></div>""", unsafe_allow_html=True)

    with c2:
        top = g.head(6)["sucursal"].tolist()
        ev = con_dir.copy()
        ev["grupo"] = ev["sucursal"].where(ev["sucursal"].isin(top), "Otras")
        piv = (ev.groupby(["ym", "grupo"])["neto"].sum().reset_index()
               .pivot(index="ym", columns="grupo", values="neto").fillna(0).sort_index())
        fig = go.Figure()
        for i, col in enumerate(piv.columns):
            fig.add_bar(x=[_ym_label(m) for m in piv.index], y=piv[col], name=col,
                        marker_color=_PALETA[i % len(_PALETA)],
                        hovertemplate="%{x}<br>%{y:,.0f}<extra>" + str(col) + "</extra>")
        fig.update_layout(barmode="stack",
                          legend=dict(orientation="h", y=-0.25, x=0, font=dict(size=10)))
        st.plotly_chart(_fig(fig, 330), use_container_width=True)

    notas = []
    if nc_sin_dir:
        notas.append(f"{fmt_clp(abs(nc_sin_dir))} en notas de crédito de anulación "
                     "(el ERP no indica a qué local corresponden)")
    if fac_sin_dir:
        notas.append(f"{fmt_clp(fac_sin_dir)} facturados sin dirección en el ERP")
    if notas:
        st.caption("⚠️ Fuera de este desglose: " + " · ".join(notas) + ".")
