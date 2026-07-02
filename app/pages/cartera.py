"""
Página "Cartera Directa" (solo rol gerencia/admin).

Análisis de la cartera de clientes atendidos directamente en los últimos 12
meses cerrados, para decisiones comerciales: reasignación de zonas a
distribuidores y retiro de clientes de bajo desempeño.

Ventana móvil: los 12 meses cerrados anteriores al mes en curso, divididos en
4 trimestres consecutivos (T4 = el más antiguo, T1 = los últimos 3 meses).
Fact-NC = SUM(neto) con NC en negativo, consistente con el resto de la app.
Los insights se calculan solo con datos de la base (sin estimaciones).
"""
from datetime import date

import pandas as pd
import streamlit as st

from app.auth import es_gerencia
from app.styles import fmt_clp, fmt_num
from app.data import get_clientes_historia

_MES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}


# ─── Ventana de 12 meses cerrados ────────────────────────────────────────────────
def _ventana_12m(hoy: date) -> list[str]:
    """Lista de 12 'YYYY-MM' cerrados, del más antiguo al más reciente."""
    anio, mes = hoy.year, hoy.month
    yms = []
    for i in range(12, 0, -1):
        m = mes - i
        a = anio + (m - 1) // 12
        m = (m - 1) % 12 + 1
        yms.append(f"{a}-{m:02d}")
    return yms


def _etiqueta_tri(yms: list[str]) -> str:
    """'Jul 25 – Sep 25' a partir de 3 'YYYY-MM'."""
    def _p(ym):
        a, m = ym.split("-")
        return f"{_MES[int(m)]} {a[2:]}"
    return f"{_p(yms[0])} – {_p(yms[-1])}"


# ─── Carga cacheada por usuario ─────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _hist_cached(scope: str) -> pd.DataFrame:
    from app.auth import get_client_auth
    cli = get_client_auth()
    return get_clientes_historia(cli, None)  # ambas sociedades


def _construir_cartera(hist: pd.DataFrame, yms: list[str]) -> pd.DataFrame:
    """Matriz cliente × trimestre para la ventana de 12 meses."""
    df = hist[hist["ym"].isin(yms)].copy()
    if df.empty:
        return pd.DataFrame()
    tri_de = {ym: f"t{i // 3 + 1}" for i, ym in enumerate(yms)}  # t1=antiguo..t4=reciente
    df["tri"] = df["ym"].map(tri_de)
    piv = (df.pivot_table(index="cliente_rut", columns="tri", values="fact_nc",
                          aggfunc="sum", fill_value=0.0)
             .reindex(columns=["t1", "t2", "t3", "t4"], fill_value=0.0))
    piv["total"] = piv.sum(axis=1)

    ult = df.groupby("cliente_rut")["ym"].max().rename("ultimo_ym")
    meta = (df.sort_values("ym")
              .groupby("cliente_rut")[["razon_social", "comuna", "region"]].last())
    out = piv.join(ult).join(meta).reset_index()
    out["comuna"] = out["comuna"].fillna("(sin comuna)").replace("", "(sin comuna)")

    sem1 = out["t1"] + out["t2"]
    sem2 = out["t3"] + out["t4"]
    out["var_sem"] = (sem2 - sem1) / sem1.where(sem1 != 0)  # NaN si sem1=0 (nuevo)
    out["alerta"] = ""
    out.loc[(sem1 > 0) & (sem2 < sem1 * 0.5), "alerta"] = "Caída >50%"
    out.loc[out["t4"] == 0, "alerta"] = "Sin compra últ. trimestre"
    return out.sort_values("total", ascending=False).reset_index(drop=True)


# ─── Insights automáticos (solo datos reales) ───────────────────────────────────
def _insights(cart: pd.DataFrame, umbral: float) -> list[str]:
    tot = cart["total"].sum()
    if not len(cart) or tot == 0:
        return ["Sin datos suficientes en la ventana de 12 meses."]
    pareto_n = int((cart["total"].cumsum() <= tot * 0.8).sum()) + 1
    top10 = cart.head(10)
    sin_t4 = cart[cart["t4"] == 0]
    caida = cart[cart["alerta"] == "Caída >50%"]
    bajos = cart[(cart["total"] > 0) & (cart["total"] < umbral)]
    neg = cart[cart["total"] <= 0]
    por_com = (cart.groupby("comuna").agg(n=("cliente_rut", "size"), venta=("total", "sum"))
                   .sort_values("venta", ascending=False))
    top_v = por_com.head(3)
    top_n = por_com.sort_values("n", ascending=False).head(3)

    out = [
        f"**Concentración:** el 80% de la venta está en solo **{pareto_n} clientes** "
        f"({pareto_n / len(cart):.0%} de la cartera). El top 10 aporta "
        f"{top10['total'].sum() / tot:.1%} del total.",
        "**Comunas con mayor venta:** " + " · ".join(
            f"{c} ({fmt_clp(v.venta)}, {int(v.n)} cli.)" for c, v in top_v.iterrows()),
        "**Comunas con más clientes:** " + " · ".join(
            f"{c} ({int(v.n)})" for c, v in top_n.iterrows()),
        f"**{len(sin_t4)} clientes sin compras en el último trimestre** "
        f"({fmt_clp(sin_t4['total'].sum())} de venta 12M en riesgo de fuga) → "
        f"revisar contacto o reasignar a distribuidor.",
        f"**{len(caida)} clientes con caída >50%** en el 2° semestre vs el 1° → "
        f"contactar antes de perderlos.",
        f"**{len(bajos)} clientes bajo {fmt_clp(umbral)}** en 12 meses "
        f"(suman {fmt_clp(bajos['total'].sum())}, {bajos['total'].sum() / tot:.1%} del total) → "
        f"candidatos a traspasar a distribuidor o dejar de atender directo.",
    ]
    if len(neg):
        out.append(f"**{len(neg)} clientes con neto ≤ $0** en 12M (solo NC/devoluciones) → evaluar cierre.")
    return out


# ════════════════════════════════════════════════════════════════════════════════
def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver la cartera directa.")
        return

    yms = _ventana_12m(date.today())
    tris = [yms[0:3], yms[3:6], yms[6:9], yms[9:12]]
    et = [_etiqueta_tri(t) for t in tris]

    st.caption(f"Ventana móvil: **{et[0].split(' – ')[0]} → {et[3].split(' – ')[1]}** "
               "(12 meses cerrados, ambas sociedades). Fact-NC neto.")

    scope = f"{st.session_state.get('user_id', '')}:{st.session_state.get('vendedor_id', '')}"
    with st.spinner("Cargando cartera…"):
        hist = _hist_cached(scope)
    if hist.empty:
        st.info("No hay ventas en la base.")
        return
    cart = _construir_cartera(hist, yms)
    if cart.empty:
        st.info("No hay ventas dentro de la ventana de 12 meses.")
        return

    # ── Filtros ──
    c1, c2, c3 = st.columns([2.5, 1.5, 1.5])
    comunas = sorted(cart["comuna"].unique())
    f_com = c1.multiselect("Comuna", comunas, placeholder="Todas las comunas")
    orden = c2.selectbox("Orden", ["Mayor a menor compra", "Menor a mayor compra"])
    umbral = c3.number_input("Umbral baja compra ($)", min_value=0,
                             value=500_000, step=100_000)

    vista = cart[cart["comuna"].isin(f_com)] if f_com else cart
    vista = vista.sort_values("total", ascending=(orden == "Menor a mayor compra"))

    # ── KPIs ──
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Clientes con movimiento 12M", fmt_num(len(vista)))
    k2.metric("Venta neta 12M", fmt_clp(vista["total"].sum()))
    k3.metric("Sin compra últ. trimestre", fmt_num(int((vista["t4"] == 0).sum())))
    k4.metric(f"Bajo {fmt_clp(umbral)} en 12M",
              fmt_num(int(((vista["total"] > 0) & (vista["total"] < umbral)).sum())))

    # ── Tabla ──
    st.markdown('<div class="seccion-titulo">Cartera de clientes — últimos 12 meses</div>',
                unsafe_allow_html=True)
    tot_general = cart["total"].sum()
    tabla = vista[["razon_social", "cliente_rut", "comuna",
                   "t1", "t2", "t3", "t4", "total", "var_sem", "ultimo_ym", "alerta"]].copy()
    tabla["pct"] = tabla["total"] / tot_general if tot_general else 0.0
    tabla = tabla[["razon_social", "cliente_rut", "comuna", "t1", "t2", "t3", "t4",
                   "total", "pct", "var_sem", "ultimo_ym", "alerta"]]
    st.dataframe(
        tabla,
        use_container_width=True, hide_index=True, height=520,
        column_config={
            "razon_social": st.column_config.TextColumn("Cliente", width="medium"),
            "cliente_rut": st.column_config.TextColumn("RUT", width="small"),
            "comuna": st.column_config.TextColumn("Comuna", width="small"),
            "t1": st.column_config.NumberColumn(et[0], format="$%d"),
            "t2": st.column_config.NumberColumn(et[1], format="$%d"),
            "t3": st.column_config.NumberColumn(et[2], format="$%d"),
            "t4": st.column_config.NumberColumn(et[3] + " (últ.)", format="$%d"),
            "total": st.column_config.NumberColumn("Total 12M", format="$%d"),
            "pct": st.column_config.NumberColumn("% total", format="percent"),
            "var_sem": st.column_config.NumberColumn("Var. 2°sem", format="percent",
                                                     help="(últimos 6M − primeros 6M) / primeros 6M. Vacío = sin venta en el 1° semestre (cliente nuevo)."),
            "ultimo_ym": st.column_config.TextColumn("Últ. compra", width="small"),
            "alerta": st.column_config.TextColumn("Alerta", width="small"),
        })
    st.caption(f"{len(tabla)} clientes en la vista · el % total es sobre la cartera completa "
               "(no cambia con el filtro).")

    # ── Descarga ──
    import io
    buf = io.BytesIO()
    exp = tabla.rename(columns={
        "razon_social": "Cliente", "cliente_rut": "RUT", "comuna": "Comuna",
        "t1": et[0], "t2": et[1], "t3": et[2], "t4": et[3] + " (ult)",
        "total": "Total 12M", "pct": "% del total", "var_sem": "Var 2do sem",
        "ultimo_ym": "Ultima compra", "alerta": "Alerta"})
    exp.to_excel(buf, index=False, sheet_name="Cartera 12M")
    st.download_button("⬇️ Descargar Excel (vista actual)", buf.getvalue(),
                       file_name="cartera_clientes_12m.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ── Insights ──
    st.markdown('<div class="seccion-titulo">Análisis ejecutivo (automático)</div>',
                unsafe_allow_html=True)
    for linea in _insights(cart, float(umbral)):
        st.markdown(f"- {linea}")
    st.caption("Calculado solo con la información de la base de datos (sin estimaciones). "
               "Los montos son neto facturado menos notas de crédito.")
