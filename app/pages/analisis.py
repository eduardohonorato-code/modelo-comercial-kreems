"""Análisis de Ventas — 01 Productos · 02 Geografía · 03 Sucursales."""
import datetime
import calendar as _cal
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.styles import fmt_clp, fmt_num
from app.auth import es_gerencia
from app.data import (
    get_ventas_rango, get_dim_producto_all,
    get_dim_cliente_geo, get_dim_sociedad,
    get_maquinas_rango, get_todos_vendedores,
)

# ─── Paleta & constantes ──────────────────────────────────────────────────────
_C = {
    "azul":   "#C01E6E",   # magenta profundo de marca
    "chart":  "#E62984",   # magenta de marca
    "verde":  "#1A7F4B",
    "amrl":   "#D4881E",
    "rojo":   "#C0392B",
    "violeta":"#7C3AED",
    "cyan":   "#0288D1",
    "slate":  "#64748B",
}
_PALETA = [
    "#E62984", "#1E88E5", "#26A69A", "#D4881E", "#7C3AED",
    "#0288D1", "#1A7F4B", "#F57C00", "#64748B", "#9E175A",
]
_H = 330

MESES_C = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
           7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}


# ─── Helpers UI ───────────────────────────────────────────────────────────────

def _fact_nc(df: pd.DataFrame) -> float:
    """Fact-NC = SUM(neto). NC ya entran con signo negativo (= v_resumen_vendedor_mes)."""
    return float(df["neto"].sum()) if "neto" in df.columns else 0.0


def _kic(icon: str, label: str, value: str,
         sub: str = "", delta=None, color: str = "") -> str:
    val_cls = f"kic-value {color}" if color else "kic-value"
    delta_html = ""
    if delta is not None:
        cls   = "verde" if delta >= 0 else "rojo"
        arrow = "▲" if delta >= 0 else "▼"
        delta_html = (
            f'<div class="kic-delta {cls}">'
            f'{arrow} {fmt_clp(abs(delta))} vs per. ant.</div>'
        )
    sub_html = f'<div class="kic-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi-icon-card">'
        f'<div class="kic-icon">{icon}</div>'
        f'<div class="kic-body">'
        f'<div class="kic-label">{label}</div>'
        f'<div class="{val_cls}">{value}</div>'
        f'{sub_html}{delta_html}'
        f'</div></div>'
    )


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _empty():
    st.markdown(
        '<div class="estado-vacio">Sin datos para los filtros seleccionados.</div>',
        unsafe_allow_html=True,
    )


def _fig_base(fig: go.Figure, h: int = _H) -> go.Figure:
    fig.update_layout(
        height=h, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(t=20, b=8, l=8, r=8),
        font=dict(family="Inter, system-ui, sans-serif", size=11),
    )
    return fig


# ─── Filtros dentro de la página ──────────────────────────────────────────────

def _page_filters(client, anio: int, mes: int):
    ultimo_dia  = _cal.monthrange(anio, mes)[1]
    default_ini = datetime.date(anio, mes, 1)
    default_fin = datetime.date(anio, mes, ultimo_dia)

    # Sincroniza los inputs de fecha cuando cambia el período del sidebar.
    # st.date_input ignora `value` si la key ya existe en session_state,
    # así que limpiamos las keys cuando el usuario cambia de mes/año.
    period_key = f"_anal_period_{anio}_{mes}"
    if period_key not in st.session_state:
        for k in ("anal_ini", "anal_fin"):
            st.session_state.pop(k, None)
        # Limpiar tracking de períodos anteriores
        for k in [k for k in st.session_state if k.startswith("_anal_period_")]:
            del st.session_state[k]
        st.session_state[period_key] = True

    df_soc      = get_dim_sociedad(client)
    df_prod_dim = get_dim_producto_all(client)

    soc_map: dict = {}
    if not df_soc.empty and {"id", "nombre"}.issubset(df_soc.columns):
        soc_map = dict(zip(df_soc["nombre"].str.strip(), df_soc["id"]))
    soc_opts = ["Ambas"] + sorted(soc_map.keys())

    cats_all: list = []
    if not df_prod_dim.empty and "categoria" in df_prod_dim.columns:
        cats_all = sorted(
            df_prod_dim["categoria"]
            .dropna().str.upper().str.strip().unique().tolist()
        )

    _sec("🔍 Filtros")
    c1, c2, c3, c4, c5 = st.columns([1.9, 1.9, 1.9, 3.5, 1.2])

    with c1:
        f_ini = st.date_input("Desde", value=default_ini,
                              key="anal_ini", format="DD/MM/YYYY")
    with c2:
        f_fin = st.date_input("Hasta", value=default_fin,
                              key="anal_fin", format="DD/MM/YYYY")
    with c3:
        soc_sel = st.selectbox("Sociedad", soc_opts, key="anal_soc")
        soc_ids = (
            None if (soc_sel == "Ambas" or soc_sel not in soc_map)
            else [soc_map[soc_sel]]
        )
    with c4:
        cats_sel = st.multiselect(
            "Categoría", cats_all, default=[],
            placeholder="Todas las categorías", key="anal_cats",
        )
    with c5:
        st.markdown("<div style='height:1.65rem'></div>", unsafe_allow_html=True)
        if st.button("↺ Limpiar", key="anal_clear", use_container_width=True):
            for k in ("anal_ini", "anal_fin", "anal_soc", "anal_cats"):
                st.session_state.pop(k, None)
            st.rerun()

    if f_ini > f_fin:
        st.error("La fecha de inicio debe ser anterior a la de fin.")
        return None, None, None, None, df_prod_dim

    st.divider()
    return f_ini, f_fin, soc_ids, cats_sel, df_prod_dim


# ─── Data loading & enrichment ────────────────────────────────────────────────

def _load_pair(client, f_ini, f_fin, soc_ids):
    df_raw   = get_ventas_rango(client, f_ini, f_fin, soc_ids)
    n_dias   = (f_fin - f_ini).days + 1
    prev_fin = f_ini - datetime.timedelta(days=1)
    prev_ini = prev_fin - datetime.timedelta(days=n_dias - 1)
    df_prev  = get_ventas_rango(client, prev_ini, prev_fin, soc_ids)
    return df_raw, df_prev


def _enrich(df: pd.DataFrame, df_prod_dim: pd.DataFrame,
            df_geo: pd.DataFrame, cats_sel: list) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()

    if not df_prod_dim.empty:
        dp = (df_prod_dim
              .rename(columns={"codigo": "producto_codigo"})
              [["producto_codigo", "nombre", "categoria",
                "subcategoria", "fabricante"]])
        dp["categoria"] = dp["categoria"].fillna("SIN CATEGORIA").str.upper().str.strip()
        df = df.merge(dp, on="producto_codigo", how="left")

    if "categoria" not in df.columns:
        df["categoria"] = "SIN CATEGORIA"
    if "nombre" not in df.columns:
        df["nombre"] = df.get("producto_codigo", "?")
    df["categoria"] = df["categoria"].fillna("SIN CATEGORIA")

    # Las líneas de categoría "Servicios" (ej. SER-1 "Servicios de
    # almacenamiento") SÍ son ingreso real facturado y deben sumar en
    # Ventas/Margen (cuadran con v_resumen_vendedor_mes y el panel de
    # vendedores). PERO su campo Cantidad trae basura (90M+ por línea) que
    # infla las Unidades Vendidas. Neutralizamos solo la cantidad: el monto
    # se conserva, las unidades no se contaminan.
    if "cantidad" in df.columns:
        df.loc[df["categoria"] == "SERVICIOS", "cantidad"] = 0

    if not df_geo.empty and "cliente_rut" in df.columns:
        dg = df_geo.rename(columns={"rut": "cliente_rut"})
        df = df.merge(dg, on="cliente_rut", how="left")

    if cats_sel:
        df = df[df["categoria"].isin(cats_sel)]

    # Columna mes para heatmap
    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        df["mes_num"] = df["fecha"].dt.month
        df["mes_lbl"] = df["mes_num"].map(MESES_C)

    return df


# ─── Sección 01 · Productos ───────────────────────────────────────────────────

def _s01_productos(df: pd.DataFrame, df_prev: pd.DataFrame):
    _sec("01 · Análisis de Productos")

    if df.empty:
        _empty()
        return

    ventas = _fact_nc(df)
    skus   = int(df["producto_codigo"].nunique())
    uds    = int(df["cantidad"].sum())
    ndocs  = int(df["n_dcto"].nunique()) if "n_dcto" in df.columns else 0
    ticket = ventas / ndocs if ndocs else 0

    ventas_p = _fact_nc(df_prev) if not df_prev.empty else 0
    delta    = ventas - ventas_p if ventas_p != 0 else None

    html = "".join([
        _kic("💰", "Ventas Totales (Fact-NC)", fmt_clp(ventas), delta=delta),
        _kic("📦", "SKUs Vendidos",            fmt_num(skus)),
        _kic("🔢", "Unidades Vendidas",        fmt_num(uds)),
        _kic("🧾", "Ticket Promedio",           fmt_clp(ticket), sub="por documento"),
    ])
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)

    agg = (
        df.groupby(["producto_codigo", "nombre"])
        .agg(venta=("neto", "sum"), cantidad=("cantidad", "sum"))
        .reset_index()
        .sort_values("venta", ascending=False)
    )

    c1, c2, c3 = st.columns([35, 40, 25])

    # ── Col 1: Top 10 ─────────────────────────────────────────────────────────
    with c1:
        _sec("Top 10 por Monto Vendido")
        top10        = agg.head(10).copy()
        top10["lbl"] = top10["nombre"].str[:28].str.strip()
        max_v        = float(top10["venta"].max()) if not top10.empty else 1.0
        fig1 = go.Figure(go.Bar(
            x=top10["venta"], y=top10["lbl"],
            orientation="h",
            marker_color=_C["chart"],
            text=top10["venta"].apply(fmt_clp),
            textposition="inside",
            insidetextanchor="end",
            textfont=dict(size=9, color="white"),
        ))
        fig1.update_layout(
            xaxis=dict(showticklabels=False, showgrid=False, range=[0, max_v * 1.05]),
            yaxis=dict(autorange="reversed", tickfont=dict(size=9)),
            bargap=0.28,
        )
        st.plotly_chart(_fig_base(fig1), use_container_width=True)

    # ── Col 2: Pareto ──────────────────────────────────────────────────────────
    with c2:
        _sec("Pareto de Ventas por Producto")
        pareto = agg[agg["venta"] > 0].reset_index(drop=True)
        if pareto.empty:
            _empty()
        else:
            pareto["pct_acum"] = pareto["venta"].cumsum() / pareto["venta"].sum() * 100
            pareto["etiq"]     = pareto["nombre"].str[:16].str.strip()

            fig2 = make_subplots(specs=[[{"secondary_y": True}]])
            fig2.add_trace(
                go.Bar(x=pareto["etiq"], y=pareto["venta"],
                       name="Venta", marker_color=_C["chart"], opacity=0.85),
                secondary_y=False,
            )
            fig2.add_trace(
                go.Scatter(x=pareto["etiq"], y=pareto["pct_acum"],
                           name="% Acum.",
                           line=dict(color=_C["rojo"], width=2),
                           mode="lines+markers", marker=dict(size=4)),
                secondary_y=True,
            )
            fig2.add_shape(
                type="line", x0=0, x1=1, y0=80, y1=80,
                xref="paper", yref="y2",
                line=dict(color=_C["amrl"], dash="dash", width=1.5),
            )
            mask_80 = pareto[pareto["pct_acum"] >= 80]
            if not mask_80.empty:
                p80    = mask_80.iloc[0]
                n_skus = int(mask_80.index[0]) + 1
                fig2.add_annotation(
                    x=p80["etiq"], y=80, yref="y2",
                    text=f"80 % ({n_skus} SKUs)",
                    showarrow=True, arrowhead=2,
                    font=dict(color=_C["amrl"], size=9), ax=45, ay=-28,
                )
            fig2.update_yaxes(title_text="Venta neta ($)", secondary_y=False,
                              showgrid=True, gridcolor="#F0F0F5")
            fig2.update_yaxes(title_text="% Acumulado", secondary_y=True,
                              range=[0, 108], ticksuffix="%")
            fig2.update_xaxes(tickangle=-45, tickfont=dict(size=8))
            fig2.update_layout(showlegend=False, bargap=0.1)
            st.plotly_chart(_fig_base(fig2), use_container_width=True)

    # ── Col 3: Donut por categoría ─────────────────────────────────────────────
    with c3:
        _sec("Mix por Categoría")
        agg_cat = (
            df.groupby("categoria")["neto"]
            .sum().reset_index()
            .rename(columns={"neto": "venta"})
            .sort_values("venta", ascending=False)
        )
        fig3 = go.Figure(go.Pie(
            labels=agg_cat["categoria"],
            values=agg_cat["venta"],
            hole=0.55,
            marker=dict(colors=_PALETA),
            textinfo="percent",
            textposition="outside",
            textfont=dict(size=9),
        ))
        fig3.update_layout(
            annotations=[dict(
                text=fmt_clp(ventas), x=0.5, y=0.5,
                font=dict(size=11, color=_C["azul"], family="Inter"),
                showarrow=False,
            )],
            showlegend=True,
            legend=dict(orientation="v", font=dict(size=8),
                        x=-0.05, y=-0.08, traceorder="normal"),
        )
        st.plotly_chart(_fig_base(fig3, h=_H + 70), use_container_width=True)


# ─── Sección 02 · Geográfico ──────────────────────────────────────────────────

def _s02_geografico(df: pd.DataFrame, df_prev: pd.DataFrame, f_ini, f_fin):
    _sec("02 · Análisis Geográfico")

    if df.empty or "region" not in df.columns:
        _empty()
        if "region" not in df.columns:
            st.caption("ℹ️ No se pudo obtener región — verifica el join con dim_cliente.")
        return

    df["region"] = df["region"].fillna("Sin región").str.strip()
    ventas_tot   = _fact_nc(df)

    # KPIs
    regiones_activas = int(df["region"].nunique())
    agg_r = (df.groupby("region")["neto"].sum().reset_index()
             .rename(columns={"neto": "venta"})
             .sort_values("venta", ascending=False))
    mejor = agg_r.iloc[0]["region"]  if not agg_r.empty else "—"
    peor  = agg_r.iloc[-1]["region"] if len(agg_r) > 1  else "—"

    ventas_p = _fact_nc(df_prev) if not df_prev.empty else 0
    delta    = ventas_tot - ventas_p if ventas_p != 0 else None

    html = "".join([
        _kic("🗺️", "Regiones Activas",  str(regiones_activas)),
        _kic("🏆", "Mejor Región",       mejor,         color="verde"),
        _kic("⚠️",  "Peor Región",        peor,          color="rojo"),
        _kic("💰", "Ventas Totales",     fmt_clp(ventas_tot), delta=delta),
    ])
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)

    # Número de meses en el rango (para decidir si mostrar heatmap)
    n_meses = len(df["mes_num"].unique()) if "mes_num" in df.columns else 1

    c1, c2, c3 = st.columns([35, 25, 40])

    # ── Col 1: Barras horizontales por región ──────────────────────────────────
    with c1:
        _sec("Ventas por Región")
        top_r   = agg_r.head(15).copy()
        max_v_r = float(top_r["venta"].max()) if not top_r.empty else 1.0
        fig1    = go.Figure(go.Bar(
            x=top_r["venta"], y=top_r["region"],
            orientation="h",
            marker_color=_C["chart"],
            text=top_r["venta"].apply(fmt_clp),
            textposition="inside",
            insidetextanchor="end",
            textfont=dict(size=9, color="white"),
        ))
        fig1.update_layout(
            xaxis=dict(showticklabels=False, showgrid=False, range=[0, max_v_r * 1.05]),
            yaxis=dict(autorange="reversed", tickfont=dict(size=9)),
            bargap=0.28,
        )
        st.plotly_chart(_fig_base(fig1), use_container_width=True)

    # ── Col 2: Pie de participación ────────────────────────────────────────────
    with c2:
        _sec("Participación por Región")
        fig2 = go.Figure(go.Pie(
            labels=agg_r["region"],
            values=agg_r["venta"],
            marker=dict(colors=_PALETA),
            textinfo="percent+label",
            textposition="outside",
            textfont=dict(size=9),
            hole=0.0,
        ))
        fig2.update_layout(showlegend=False)
        st.plotly_chart(_fig_base(fig2, h=_H + 30), use_container_width=True)

    # ── Col 3: Heatmap meses × regiones (solo si >1 mes) ──────────────────────
    with c3:
        if n_meses > 1 and "mes_num" in df.columns:
            _sec("Evolución por Región y Mes")
            pivot = (
                df.groupby(["region", "mes_num"])["neto"]
                .sum()
                .unstack(fill_value=0)
            )
            meses_ord = sorted(pivot.columns)
            pivot     = pivot[meses_ord]
            xlabels   = [MESES_C.get(m, str(m)) for m in meses_ord]

            fig3 = go.Figure(go.Heatmap(
                z=pivot.values.tolist(),
                x=xlabels,
                y=pivot.index.tolist(),
                colorscale=[
                    [0.0,  "#FDEAF3"],
                    [0.35, "#F49ABF"],
                    [0.70, "#E62984"],
                    [1.0,  "#9E175A"],
                ],
                text=[[fmt_clp(v) for v in row] for row in pivot.values],
                texttemplate="%{text}",
                textfont=dict(size=8, color="#5A1133"),
                hovertemplate="Región: %{y}<br>Mes: %{x}<br>Venta: %{text}<extra></extra>",
                showscale=True,
            ))
            fig3.update_layout(
                xaxis=dict(side="top"),
                yaxis=dict(tickfont=dict(size=9)),
            )
            st.plotly_chart(_fig_base(fig3, h=max(_H, 60 + 35 * len(pivot))),
                            use_container_width=True)
        else:
            _sec("Evolución por Región y Mes")
            st.caption("Selecciona un rango de más de un mes para ver el heatmap de evolución.")


# ─── Sección 03 · Sucursal ────────────────────────────────────────────────────

def _s03_sucursal(df: pd.DataFrame, df_prev: pd.DataFrame):
    _sec("03 · Análisis por Sucursal")

    if df.empty or "sucursal" not in df.columns:
        _empty()
        return

    df["sucursal"] = df["sucursal"].fillna("Sin sucursal").str.strip()
    ventas_tot     = _fact_nc(df)

    # KPIs
    agg_s = (
        df.groupby("sucursal")
        .agg(venta=("neto", "sum"), uds=("cantidad", "sum"),
             n_docs=("n_dcto", "nunique"))
        .reset_index()
        .sort_values("venta", ascending=False)
    )
    n_sucursales = int(agg_s.shape[0])
    mejor_s = agg_s.iloc[0]["sucursal"]  if not agg_s.empty else "—"
    peor_s  = agg_s.iloc[-1]["sucursal"] if len(agg_s) > 1  else "—"

    ventas_p = _fact_nc(df_prev) if not df_prev.empty else 0
    delta    = ventas_tot - ventas_p if ventas_p != 0 else None

    html = "".join([
        _kic("🏪", "Sucursales Activas", str(n_sucursales)),
        _kic("🏆", "Mejor Sucursal",     mejor_s, color="verde"),
        _kic("⚠️",  "Peor Sucursal",      peor_s,  color="rojo"),
        _kic("💰", "Ventas Totales",     fmt_clp(ventas_tot), delta=delta),
    ])
    st.markdown(f'<div class="kpi-grid">{html}</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns([35, 30, 35])

    # ── Col 1: Ranking de sucursales ───────────────────────────────────────────
    with c1:
        _sec("Ranking de Sucursales por Venta")
        top_s   = agg_s.head(15).copy()
        max_v_s = float(top_s["venta"].max()) if not top_s.empty else 1.0
        fig1    = go.Figure(go.Bar(
            x=top_s["venta"], y=top_s["sucursal"],
            orientation="h",
            marker_color=_C["chart"],
            text=top_s["venta"].apply(fmt_clp),
            textposition="inside",
            insidetextanchor="end",
            textfont=dict(size=9, color="white"),
        ))
        fig1.update_layout(
            xaxis=dict(showticklabels=False, showgrid=False, range=[0, max_v_s * 1.05]),
            yaxis=dict(autorange="reversed", tickfont=dict(size=9)),
            bargap=0.28,
        )
        st.plotly_chart(_fig_base(fig1), use_container_width=True)

    # ── Col 2: Tabla Venta Real + % del total ─────────────────────────────────
    with c2:
        _sec("Venta Real por Sucursal")
        st.caption("ℹ️ Objetivos por sucursal pendientes (los objetivos están asignados por vendedor).")
        tbl = agg_s.copy()
        tbl["pct"] = tbl["venta"] / ventas_tot * 100 if ventas_tot else 0
        tbl["Sucursal"]   = tbl["sucursal"]
        tbl["Venta neta"] = tbl["venta"].apply(fmt_clp)
        tbl["% del Total"]= tbl["pct"].apply(lambda x: f"{x:.1f}%")
        tbl["N° Docs"]    = tbl["n_docs"].apply(fmt_num)
        st.dataframe(
            tbl[["Sucursal", "Venta neta", "% del Total", "N° Docs"]],
            use_container_width=True, hide_index=True,
        )

    # ── Col 3: Scatter Ventas vs N° Documentos ────────────────────────────────
    with c3:
        _sec("Ventas vs Actividad por Sucursal")
        if len(agg_s) < 2:
            _empty()
        else:
            media_v = agg_s["venta"].mean()
            media_d = agg_s["n_docs"].mean()

            fig3 = go.Figure()
            fig3.add_trace(go.Scatter(
                x=agg_s["n_docs"],
                y=agg_s["venta"],
                mode="markers+text",
                marker=dict(
                    size=agg_s["uds"].clip(lower=1).pipe(
                        lambda s: 12 + (s - s.min()) / (s.max() - s.min() + 1) * 22
                    ),
                    color=_C["chart"],
                    opacity=0.8,
                    line=dict(width=1, color="white"),
                ),
                text=agg_s["sucursal"],
                textposition="top center",
                textfont=dict(size=9),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Venta neta: %{y:,.0f}<br>"
                    "N° Docs: %{x}<extra></extra>"
                ),
            ))
            # Líneas de cuadrante
            fig3.add_vline(x=media_d, line_dash="dot", line_color=_C["slate"],
                           line_width=1)
            fig3.add_hline(y=media_v, line_dash="dot", line_color=_C["slate"],
                           line_width=1)
            # Etiquetas de cuadrante
            x_max = float(agg_s["n_docs"].max())
            y_max = float(agg_s["venta"].max())
            for txt, x_pos, y_pos, xanch, yanch in [
                ("Alto Vol / Alto $",  x_max, y_max,  "right",  "top"),
                ("Bajo Vol / Alto $",  0,     y_max,  "left",   "top"),
                ("Alto Vol / Bajo $",  x_max, 0,      "right",  "bottom"),
                ("Bajo Vol / Bajo $",  0,     0,      "left",   "bottom"),
            ]:
                fig3.add_annotation(
                    x=x_pos, y=y_pos, text=txt,
                    showarrow=False,
                    font=dict(size=8, color=_C["slate"]),
                    xanchor=xanch, yanchor=yanch,
                )
            fig3.update_xaxes(title_text="N° Documentos", showgrid=True, gridcolor="#F0F0F5")
            fig3.update_yaxes(title_text="Venta neta ($)", showgrid=True, gridcolor="#F0F0F5")
            st.plotly_chart(_fig_base(fig3), use_container_width=True)


# ─── Render principal ─────────────────────────────────────────────────────────

# ─── Sección 04 · Máquinas ────────────────────────────────────────────────────

_TIPO_LBL = {"nueva": "Nuevas (FL-4)", "cambio": "Cambios (FL-1/3/5)",
             "retiro": "Retiros (FL-2)"}
_EST_LBL  = {"entregada": "Entregada", "gestionada": "Pendiente",
             "rechazada": "Rechazada"}
_EST_COLOR = {"Entregada": _C["verde"], "Pendiente": _C["amrl"],
              "Rechazada": _C["rojo"]}


def _s04_maquinas(client, f_ini, f_fin, soc_ids):
    _sec("Máquinas (comodato)")
    st.caption("Aplican los filtros de fecha y sociedad. La categoría no aplica a "
               "máquinas. Fuente: Obuma (FL-x); estado de entrega cruzado con despachos.")

    df = get_maquinas_rango(client, f_ini, f_fin, soc_ids)
    if df.empty:
        _empty()
        return

    total   = len(df)
    nuevas  = int((df["tipo_mov"] == "nueva").sum())
    cambios = int((df["tipo_mov"] == "cambio").sum())
    retiros = int((df["tipo_mov"] == "retiro").sum())
    entreg  = int((df["estado"] == "entregada").sum())
    pend    = int((df["estado"] == "gestionada").sum())
    rech    = int((df["estado"] == "rechazada").sum())
    pct_ent = entreg / total if total else 0
    conv_n  = (((df["tipo_mov"] == "nueva") & (df["estado"] == "entregada")).sum()
               / nuevas) if nuevas else None

    fila1 = "".join([
        _kic("🧊", "Máquinas movidas", fmt_num(total), sub="en el período"),
        _kic("✅", "Nuevas instaladas", fmt_num(nuevas), sub="FL-4 (gestionadas)"),
        _kic("♻️", "Cambios", fmt_num(cambios), sub="FL-1 / FL-3 / FL-5"),
        _kic("⬇️", "Retiros", fmt_num(retiros), sub="FL-2"),
    ])
    st.markdown(f'<div class="kpi-grid">{fila1}</div>', unsafe_allow_html=True)

    fila2 = "".join([
        _kic("📦", "Entregadas en terreno", fmt_num(entreg),
             sub=f"{pct_ent*100:.0f}% de lo movido", color="verde"),
        _kic("⏳", "Pendientes", fmt_num(pend), color="amarillo"),
        _kic("✖️", "Rechazadas", fmt_num(rech), color="rojo"),
        _kic("🔁", "Conversión nuevas→entregada",
             f"{conv_n*100:.0f}%" if conv_n is not None else "—",
             sub="de las nuevas, cuántas se entregaron"),
    ])
    st.markdown(f'<div class="kpi-grid">{fila2}</div>', unsafe_allow_html=True)

    with st.expander("ℹ️ De dónde vienen estos datos (fuentes y cruce)", expanded=False):
        st.markdown("""
**1) Tipo de movimiento — sale de Obuma.** Cada máquina es una línea de la categoría
*"Maquinas"* en las **ventas de Obuma**, identificada por su código **FL**:

| Código | Significado | Se cuenta como |
|---|---|---|
| **FL-4** | Instalación cliente nuevo | **Nueva** (gestionada) |
| **FL-1 / FL-3 / FL-5** | Cambio de máquina (mala, tamaño, etc.) | **Cambio** |
| **FL-2** | Retiro por término | **Retiro** |

**2) Estado de entrega — sale de Autoventa (despachos).** El estado *entregada /
rechazada / pendiente* **no** viene de Obuma: se obtiene del **Detalle de despachos**
de Autoventa, cruzando por número de documento:

`Obuma "N° DCTO"  =  Despachos "Documento"`

- Documento aparece **Entregada** en despachos → *entregada*.
- Aparece **Rechazada** → *rechazada*.
- No tiene despacho (no salió a ruta o no se cargó) → queda **gestionada / pendiente**.

**3) Cómo entra cada fuente:**
- **Gran Natural** (ventas + FL): por **API de Obuma** (automático).
- **Acuña** (ventas + FL): por **Excel** en la página *Carga* (no tiene API).
- **Despachos** (estado de entrega): por **Excel** en *Carga* (Autoventa no expone el
  estado por API).

> Por eso, al subir los despachos, las máquinas de Gran Natural —cargadas por API—
> recién ahí toman su estado *entregada / rechazada*. Sin despacho cargado se ven como
> *gestionadas / pendientes*.
""")

    # El detalle (tabla y desglose por vendedor) es solo para gerencia.
    if not es_gerencia():
        return

    st.divider()

    # ── Evolución mensual del año (nuevas vs retiros + neto) ──────────────────
    _sec(f"Evolución mensual {f_fin.year} · Nuevas vs Retiros")
    dfa = get_maquinas_rango(client, datetime.date(f_fin.year, 1, 1),
                             datetime.date(f_fin.year, 12, 31), soc_ids)
    if dfa.empty:
        _empty()
    else:
        dfa["mes_num"] = dfa["fecha"].dt.month
        piv_m = dfa.pivot_table(index="mes_num", columns="tipo_mov",
                                aggfunc="size", fill_value=0)
        for col in ("nueva", "retiro"):
            if col not in piv_m.columns:
                piv_m[col] = 0
        meses = sorted(piv_m.index)
        x_lbl = [MESES_C[m] for m in meses]
        neto  = (piv_m.loc[meses, "nueva"] - piv_m.loc[meses, "retiro"]).tolist()
        fig_ev = go.Figure()
        fig_ev.add_trace(go.Bar(x=x_lbl, y=piv_m.loc[meses, "nueva"].tolist(),
                                name="Nuevas (FL-4)", marker_color=_C["verde"]))
        fig_ev.add_trace(go.Bar(x=x_lbl, y=piv_m.loc[meses, "retiro"].tolist(),
                                name="Retiros (FL-2)", marker_color=_C["rojo"]))
        fig_ev.add_trace(go.Scatter(x=x_lbl, y=neto, name="Neto (nuevas − retiros)",
                                    mode="lines+markers",
                                    line=dict(color=_C["azul"], width=2)))
        fig_ev.update_layout(barmode="group", yaxis=dict(showgrid=False),
                             legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(_fig_base(fig_ev), use_container_width=True)
        st.caption("El **neto** (nuevas − retiros) es el crecimiento del parque de "
                   "máquinas: positivo = más máquinas en calle.")

    df["tipo_lbl"]   = df["tipo_mov"].map(_TIPO_LBL).fillna(df["tipo_mov"])
    df["estado_lbl"] = df["estado"].map(_EST_LBL).fillna(df["estado"])

    c1, c2 = st.columns([45, 55])
    with c1:
        _sec("Movimientos por tipo y estado")
        ct = pd.crosstab(df["tipo_lbl"], df["estado_lbl"],
                         margins=True, margins_name="Total")
        st.dataframe(ct, use_container_width=True)
    with c2:
        _sec("Estado por tipo de movimiento")
        g = df.groupby(["tipo_lbl", "estado_lbl"]).size().reset_index(name="n")
        fig = go.Figure()
        for est in ["Entregada", "Pendiente", "Rechazada"]:
            sub = g[g["estado_lbl"] == est]
            if sub.empty:
                continue
            fig.add_trace(go.Bar(
                x=sub["tipo_lbl"], y=sub["n"], name=est,
                marker_color=_EST_COLOR.get(est, _C["slate"]),
                text=sub["n"], textposition="inside",
            ))
        fig.update_layout(barmode="stack",
                          legend=dict(orientation="h", y=-0.2),
                          yaxis=dict(showgrid=False))
        st.plotly_chart(_fig_base(fig), use_container_width=True)

    _sec("Detalle por vendedor")
    vend = get_todos_vendedores(client)
    vmap = dict(zip(vend["id"], vend["nombre_canonico"])) if not vend.empty else {}
    df["vend"] = df["vendedor_id"].map(vmap).fillna("Sin asignar")

    piv = pd.crosstab(df["vend"], df["tipo_mov"])
    for col in ("nueva", "cambio", "retiro"):
        if col not in piv.columns:
            piv[col] = 0
    piv["Movidas"]    = piv[["nueva", "cambio", "retiro"]].sum(axis=1)
    ent_v             = df[df["estado"] == "entregada"].groupby("vend").size()
    piv["Entregadas"] = ent_v.reindex(piv.index).fillna(0).astype(int)
    piv["% Entreg."]  = ((piv["Entregadas"] / piv["Movidas"] * 100)
                         .round(0).fillna(0).astype(int).astype(str) + "%")
    piv = (piv.rename(columns={"nueva": "Nuevas", "cambio": "Cambios",
                               "retiro": "Retiros"})
              .reset_index().rename(columns={"vend": "Vendedor"})
              .sort_values("Movidas", ascending=False))
    st.dataframe(
        piv[["Vendedor", "Nuevas", "Cambios", "Retiros",
             "Entregadas", "% Entreg.", "Movidas"]],
        use_container_width=True, hide_index=True,
    )


def render(client, anio: int, mes: int):
    f_ini, f_fin, soc_ids, cats_sel, df_prod_dim = _page_filters(
        client, anio, mes
    )
    if f_ini is None:
        return

    tab_ventas, tab_maquinas = st.tabs(["📊 Ventas", "🧊 Máquinas"])

    with tab_ventas:
        with st.spinner("Cargando datos…"):
            df_raw, df_prev_raw = _load_pair(client, f_ini, f_fin, soc_ids)
            df_geo = get_dim_cliente_geo(client)

        df      = _enrich(df_raw,      df_prod_dim, df_geo, cats_sel)
        df_prev = _enrich(df_prev_raw, df_prod_dim, df_geo, cats_sel)

        n_dias  = (f_fin - f_ini).days + 1
        cat_lbl = f" · Categorías: {', '.join(cats_sel)}" if cats_sel else ""
        st.caption(
            f"📅 {f_ini.strftime('%d/%m/%Y')} → {f_fin.strftime('%d/%m/%Y')}"
            f" · {n_dias} día(s){cat_lbl}"
        )

        _s01_productos(df, df_prev)
        _s02_geografico(df, df_prev, f_ini, f_fin)
        _s03_sucursal(df, df_prev)

    with tab_maquinas:
        _s04_maquinas(client, f_ini, f_fin, soc_ids)
