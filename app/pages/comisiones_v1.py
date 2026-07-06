"""Propuesta de Comisiones v1 — scorecard de 5 KPIs (pestaña dentro de Comisiones).

Modelo NUEVO que convive con el de tramos actual (NO lo reemplaza). La comisión
es una **tasa efectiva 0–5% aplicada sobre la venta REAL (Fact-NC)**, repartida
en 5 KPIs ponderados. Cada KPI paga proporcional desde el 80% de su meta y la
tasa total topa en 5%.

  KPI                              Peso   % s/venta   Fuente del dato
  1. Cuota de venta                50%    2,50%       Fact-NC / meta_venta
  2. Clientes nuevos + react.      15%    0,75%       fact_ventas (historia) por vendedor
  3. Cobertura de cartera          15%    0,75%       clientes activos / cartera asignada
  4. Penetración Galletas NY       10%    0,50%       % de clientes que compran la línea nueva vs meta
  5. Amplitud de portafolio         5%    0,25%       promedio de líneas distintas por cliente vs meta
  6. Efectividad de visita          5%    0,25%       N°facturas / obj_visitas (mismo proxy actual)

El cálculo se hace acá en pandas (no en SQL) para reusar el detalle de
fact_ventas y la lógica de clientes/productos. Solo las metas se persisten
(tabla comision_v1_meta, sql/022).
"""
from __future__ import annotations

import calendar
import pandas as pd
import streamlit as st

from app.styles import fmt_clp, fmt_pct, fmt_num
from app.data import (
    get_comisiones, get_ventas_rango, get_dim_producto_all,
    get_comision_v1_meta, upsert_comision_v1_meta,
)

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# ── Parámetros del modelo (diseño fijo del scorecard) ───────────────────────
TASA_MAX = 0.05      # tope de la tasa efectiva (5% sobre la venta real)
UMBRAL   = 0.80      # cada KPI empieza a pagar desde el 80% de su meta

# key, etiqueta corta, peso (fracción de la tasa máxima)
KPIS = [
    ("cuota",       "Cuota de venta",              0.50),
    ("nuevos",      "Nuevos + reactivados",        0.15),
    ("cobertura",   "Cobertura de cartera",        0.15),
    ("galletas",    "Penetración Galletas NY",     0.10),
    ("amplitud",    "Amplitud de portafolio",      0.05),
    ("efectividad", "Efectividad de visita",       0.05),
]
PESO   = {k: p for k, _, p in KPIS}
PCT    = {k: p * TASA_MAX for k, _, p in KPIS}   # % sobre venta de cada KPI

# Defaults de metas cuando no hay valor cargado ni fuente previa.
DEFAULT_META_NUEVOS   = 3
# "galletas" = penetración Galletas NY = % de clientes con la línea nueva
# (meta FRACCIÓN: 0.30 = 30%). "amplitud" = promedio de líneas x cliente (meta 2).
DEFAULT_META_GALLETAS = 0.30
DEFAULT_META_LINEAS   = 2.0

GAP_REACTIVACION = 3   # meses sin comprar para considerar "reactivado" (≈90 días)


# ── Canonización de categorías (líneas) ─────────────────────────────────────
# Los ERP traen categorías duplicadas (POTE vs Helados Pote, etc.). Se unifican
# para no inflar el conteo de "líneas distintas por cliente".
_CANON_CAT = {
    "pote": "Helados Pote", "helados pote": "Helados Pote",
    "paletas": "Helados Paletas", "helados paletas": "Helados Paletas",
    "multipack": "Helados Multipack", "helados multipack": "Helados Multipack",
    "bacha": "Helados Bacha", "helados bacha": "Helados Bacha",
}


def _canon_cat(cat) -> str:
    if cat is None or (isinstance(cat, float) and pd.isna(cat)):
        return "Sin Categoria"
    s = str(cat).strip()
    return _CANON_CAT.get(s.lower(), s)


def _es_galleta_ny(codigo, categoria) -> bool:
    """Línea propia Galletas New York: categoria='Galletas' o código GNY-*."""
    cod = str(codigo or "").upper()
    cat = str(categoria or "").strip().lower()
    return cod.startswith("GNY") or cat == "galletas"


# ── Motor de pago ────────────────────────────────────────────────────────────
def _factor(ratio) -> float | None:
    """Fracción del peso que paga un KPI dado su logro (real/meta).
    0 bajo el 80%, sube lineal hasta 1 en el 100%, tope en 1."""
    if ratio is None or pd.isna(ratio):
        return None
    f = (ratio - UMBRAL) / (1.0 - UMBRAL)
    return max(0.0, min(1.0, f))


def _ratio(real, meta):
    if meta is None or pd.isna(meta) or float(meta) <= 0:
        return None
    return float(real) / float(meta)


# ── Cálculo del scorecard por vendedor ──────────────────────────────────────
def _calcular(client, anio: int, mes: int) -> pd.DataFrame:
    base = get_comisiones(client, anio, mes)
    if base.empty:
        return pd.DataFrame()

    # Excluir vendedores demo del seed y el bucket residual "Sin asignar"
    # (ventas sin vendedor; no es una persona a la que se le pague comisión).
    base = base[~base["nombre_canonico"].str.startswith("Vendedor ", na=False)]
    base = base[base["nombre_canonico"] != "Sin asignar"].copy()
    for c in ["fact_nc", "obj_venta", "obj_visitas", "n_facturas", "cartera_clientes"]:
        if c in base.columns:
            base[c] = pd.to_numeric(base[c], errors="coerce")

    # Metas v1 (overrides). NULL → default de la fuente previa.
    metas = get_comision_v1_meta(client, anio, mes)
    if not metas.empty:
        base = base.merge(metas, on="vendedor_id", how="left")
    for c in ["meta_venta", "meta_nuevos_react", "meta_cobertura",
              "meta_amplitud", "meta_visitas"]:
        if c not in base.columns:
            base[c] = None

    # Historia de ventas a nivel línea (para nuevos/reactivados, cobertura, amplitud).
    ultimo = calendar.monthrange(anio, mes)[1]
    ffin = f"{anio}-{mes:02d}-{ultimo:02d}"
    hist = get_ventas_rango(client, "2024-01-01", ffin)
    métricas = _metricas_historia(hist, client, anio, mes)

    base = base.merge(métricas, on="vendedor_id", how="left")
    for c in ["nuevos_react", "clientes_activos", "amplitud_prom",
              "ny_clientes", "ny_pct", "cartera_hist"]:
        if c not in base.columns:
            base[c] = 0
        base[c] = base[c].fillna(0)

    # ── Metas efectivas (override v1 → fuente previa → default) ──────────────
    def _coalesce(row, override, fallback, default=None):
        v = row.get(override)
        if v is not None and pd.notna(v):
            return float(v)
        v = row.get(fallback)
        if v is not None and pd.notna(v):
            return float(v)
        return default

    filas = []
    for _, r in base.iterrows():
        m_venta   = _coalesce(r, "meta_venta", "obj_venta")
        m_visitas = _coalesce(r, "meta_visitas", "obj_visitas")
        # Cobertura: meta v1 → cartera cargada (modelo actual) → proxy histórico
        # (clientes distintos de los últimos 3 meses). Se ignoran valores ≤0.
        m_cober = None
        cober_src = None
        for src in ("meta_cobertura", "cartera_clientes", "cartera_hist"):
            v = r.get(src)
            if v is not None and pd.notna(v) and float(v) > 0:
                m_cober = float(v)
                cober_src = src
                break
        m_nuevos   = _coalesce(r, "meta_nuevos_react", "__none__", DEFAULT_META_NUEVOS)
        m_galletas = _coalesce(r, "meta_amplitud", "__none__", DEFAULT_META_GALLETAS)
        m_lineas   = _coalesce(r, "meta_lineas", "__none__", DEFAULT_META_LINEAS)

        reales = {
            "cuota":       r.get("fact_nc") or 0,
            "nuevos":      r.get("nuevos_react") or 0,
            "cobertura":   r.get("clientes_activos") or 0,
            "galletas":    r.get("ny_pct") or 0,          # penetración Galletas NY
            "amplitud":    r.get("amplitud_prom") or 0,   # promedio de líneas x cliente
            "efectividad": r.get("n_facturas") or 0,
        }
        metas_ef = {
            "cuota": m_venta, "nuevos": m_nuevos, "cobertura": m_cober,
            "galletas": m_galletas, "amplitud": m_lineas, "efectividad": m_visitas,
        }

        fila = {
            "vendedor_id": r["vendedor_id"], "nombre_canonico": r["nombre_canonico"],
            "fact_nc": r.get("fact_nc") or 0, "cobertura_source": cober_src,
            "ny_clientes": r.get("ny_clientes") or 0, "ny_pct": r.get("ny_pct") or 0,
        }
        tasa = 0.0
        for k, _lbl, _peso in KPIS:
            rt = _ratio(reales[k], metas_ef[k])
            f  = _factor(rt)
            fila[f"{k}_real"] = reales[k]
            fila[f"{k}_meta"] = metas_ef[k]
            fila[f"{k}_logro"] = rt
            fila[f"{k}_factor"] = f
            aporte = (f or 0.0) * PCT[k]
            fila[f"{k}_aporte"] = aporte
            fila[f"{k}_comision"] = aporte * (r.get("fact_nc") or 0)
            tasa += aporte
        fila["tasa_efectiva"] = tasa
        fila["comision_total"] = tasa * (r.get("fact_nc") or 0)
        filas.append(fila)

    out = pd.DataFrame(filas)
    return out.sort_values("comision_total", ascending=False, na_position="last")


def _metricas_historia(hist: pd.DataFrame, client, anio: int, mes: int) -> pd.DataFrame:
    """Deriva por vendedor (del mes seleccionado):
    - clientes_activos: clientes distintos con factura este mes.
    - nuevos_react: de esos, cuántos son 1ª compra (ever) o reactivados (gap ≥3m).
    - amplitud_prom: promedio de líneas (categorías) distintas por cliente activo.
    - ny_clientes / ny_pct: clientes que compraron Galletas NY (foco de la línea nueva).
    La atribución del cliente es al vendedor que más le facturó en el mes."""
    cols = ["vendedor_id", "clientes_activos", "nuevos_react",
            "amplitud_prom", "ny_clientes", "ny_pct", "cartera_hist"]
    if hist is None or hist.empty:
        return pd.DataFrame(columns=cols)

    h = hist.copy()
    h["fecha"] = pd.to_datetime(h["fecha"], errors="coerce")
    h = h.dropna(subset=["fecha"])
    h["neto"] = pd.to_numeric(h["neto"], errors="coerce").fillna(0)
    h["ym"] = h["fecha"].dt.to_period("M")
    h["es_fac"] = h["tipo_dcto"].astype(str).str.contains("factura", case=False, na=False)

    # Enriquecer con categoría del producto.
    prod = get_dim_producto_all(client)
    if not prod.empty:
        prod = prod.rename(columns={"codigo": "producto_codigo"})
        h = h.merge(prod[["producto_codigo", "categoria"]], on="producto_codigo", how="left")
    else:
        h["categoria"] = None
    h["linea"] = h["categoria"].map(_canon_cat)
    h["es_ny"] = [_es_galleta_ny(c, cat)
                  for c, cat in zip(h["producto_codigo"], h.get("categoria"))]

    sel = pd.Period(f"{anio}-{mes:02d}", freq="M")
    fac = h[h["es_fac"]].copy()
    if fac.empty:
        return pd.DataFrame(columns=cols)

    # Primera compra (ever) y meses con compra por cliente.
    por_cli_ym = fac.groupby("cliente_rut")["ym"]
    first_ym = por_cli_ym.min()
    # Última compra ANTERIOR al mes seleccionado, por cliente.
    prev = (fac[fac["ym"] < sel].groupby("cliente_rut")["ym"].max())

    # Proxy de cartera asignada: clientes distintos que el vendedor facturó en
    # los últimos 3 meses (default cuando gerencia no cargó la cartera real).
    win = [sel - 2, sel - 1, sel]
    tri = fac[fac["ym"].isin(win)]
    cartera_hist = (tri.groupby("vendedor_id")["cliente_rut"].nunique()
                      .rename("cartera_hist").reset_index())

    cur = fac[fac["ym"] == sel].copy()
    if cur.empty:
        return pd.DataFrame(columns=cols)

    # Atribución cliente → vendedor (el que más le facturó este mes).
    attr = (cur.groupby(["cliente_rut", "vendedor_id"])["neto"].sum()
              .reset_index()
              .sort_values("neto", ascending=False)
              .drop_duplicates("cliente_rut"))
    attr = attr[["cliente_rut", "vendedor_id"]]

    # Estado nuevo/reactivado por cliente activo.
    def _estado(rut) -> bool:
        fy = first_ym.get(rut)
        if fy is not None and fy == sel:
            return True                      # 1ª compra ever
        pv = prev.get(rut)
        if pv is None or pd.isna(pv):
            return True                      # activo pero sin compra previa registrada
        return (sel - pv).n >= GAP_REACTIVACION
    clientes_cur = attr["cliente_rut"].unique()
    estado = {rut: _estado(rut) for rut in clientes_cur}
    attr = attr.assign(nuevo_react=attr["cliente_rut"].map(estado))

    # Líneas distintas por cliente (este mes) y flag NY por cliente.
    lineas_cli = (cur.groupby("cliente_rut")["linea"].nunique()
                    .rename("n_lineas").reset_index())
    ny_cli = (cur.groupby("cliente_rut")["es_ny"].any()
                .rename("compro_ny").reset_index())
    attr = (attr.merge(lineas_cli, on="cliente_rut", how="left")
                 .merge(ny_cli, on="cliente_rut", how="left"))
    attr["n_lineas"] = attr["n_lineas"].fillna(0)
    attr["compro_ny"] = attr["compro_ny"].fillna(False)

    agg = attr.groupby("vendedor_id").agg(
        clientes_activos=("cliente_rut", "nunique"),
        nuevos_react=("nuevo_react", "sum"),
        amplitud_prom=("n_lineas", "mean"),
        ny_clientes=("compro_ny", "sum"),
    ).reset_index()
    agg["ny_pct"] = agg.apply(
        lambda x: (x["ny_clientes"] / x["clientes_activos"]) if x["clientes_activos"] else 0,
        axis=1)
    agg = agg.merge(cartera_hist, on="vendedor_id", how="left")
    agg["cartera_hist"] = agg["cartera_hist"].fillna(0)
    return agg


# ── Render ───────────────────────────────────────────────────────────────────
def render_tab(client, anio: int, mes: int):
    st.markdown(
        '<div class="estado-vacio" style="margin-bottom:.75rem">'
        '<strong>Propuesta de Comisiones v1</strong> — modelo alternativo (en '
        'evaluación, NO reemplaza el actual). La comisión es una <strong>tasa '
        'efectiva de hasta 5% sobre la venta real</strong>, repartida en 6 KPIs '
        'ponderados. Cada KPI paga proporcional desde el 80% de su meta.</div>',
        unsafe_allow_html=True,
    )

    df = _calcular(client, anio, mes)
    if df.empty:
        st.info("Sin datos para el período. Verifica que existan ventas/objetivos "
                "del mes y que tu usuario tenga rol gerencia.")
        return

    # ── KPIs globales ────────────────────────────────────────────────────────
    tot_com   = df["comision_total"].fillna(0).sum()
    tot_venta = df["fact_nc"].fillna(0).sum()
    tasa_glob = (tot_com / tot_venta) if tot_venta else 0
    n_con_com = int((df["comision_total"].fillna(0) > 0).sum())

    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">Comisión total (v1)</div>
        <div class="kpi-value">{fmt_clp(tot_com)}</div>
        <div class="kpi-sub">{n_con_com} vendedores con comisión</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Venta real (Fact-NC)</div>
        <div class="kpi-value">{fmt_clp(tot_venta)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Tasa efectiva global</div>
        <div class="kpi-value">{fmt_pct(tasa_glob)}</div>
        <div class="kpi-sub">tope 5,00%</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="seccion-titulo">Scorecard por vendedor</div>',
                unsafe_allow_html=True)
    _tabla(df)

    # Aviso: vendedores sin cartera asignada (cobertura usa proxy → no paga).
    if "cobertura_source" in df.columns:
        proxy = df[df["cobertura_source"] == "cartera_hist"]
        sin_cober = df[df["cobertura_source"].isna()]
        if not proxy.empty or not sin_cober.empty:
            partes = []
            if not proxy.empty:
                nombres = ", ".join(proxy["nombre_canonico"].tolist())
                partes.append(
                    f"<strong>{len(proxy)} vendedor(es) con cartera SIN asignar</strong>: "
                    f"se está usando una cartera estimada (clientes distintos de los "
                    f"últimos 3 meses) como denominador. Mientras no se cargue la cartera "
                    f"real asignada, el KPI de cobertura no paga. → {nombres}.")
            if not sin_cober.empty:
                nombres2 = ", ".join(sin_cober["nombre_canonico"].tolist())
                partes.append(
                    f"<strong>{len(sin_cober)} sin cartera ni ventas recientes</strong> "
                    f"(cobertura “—”): {nombres2}.")
            st.markdown(
                '<div class="nota-embudo" style="border-left-color:#f59e0b">⚠️ '
                + " ".join(partes)
                + " Carga la cartera de cada vendedor en el editor de metas de abajo "
                  "(campo <em>Meta cobertura</em>) o en el modelo de comisiones actual.</div>",
                unsafe_allow_html=True,
            )

    with st.expander("ℹ️ Cómo se calcula", expanded=False):
        st.markdown(f"""
        <div class="nota-embudo">
          <ul>
            <li><strong>Comisión $ = tasa efectiva × venta real (Fact-NC)</strong>.
                La tasa efectiva es la suma de los 5 KPIs y topa en 5,00%.</li>
            <li>Cada KPI aporta <em>peso × 5%</em> como máximo: Cuota 2,50%,
                Nuevos+react 0,75%, Cobertura 0,75%, Galletas NY 0,50%, Amplitud 0,25%,
                Efectividad 0,25%.</li>
            <li><strong>Pago proporcional desde el 80%</strong>: si el logro (real/meta)
                es &lt;80% el KPI paga $0; entre 80% y 100% sube lineal; al 100% o más paga completo.
                Ej: logro 90% → paga la mitad del KPI.</li>
            <li><strong>Cuota</strong> = Fact-NC / meta de venta. &nbsp;
                <strong>Nuevos+react</strong> = clientes de 1ª compra o que vuelven tras
                {GAP_REACTIVACION}+ meses. &nbsp;
                <strong>Cobertura</strong> = clientes que compraron / cartera asignada
                (si no hay cartera cargada, usa los clientes distintos de los últimos 3 meses).</li>
            <li><strong>Galletas NY</strong> = penetración de la línea nueva = % de los
                clientes del vendedor que compraron Galletas NY, contra una meta de %.
                Premia colocar la línea nueva; no castiga al cliente que solo lleva paletas
                (que las paletas no caigan lo cuida la Cuota).</li>
            <li><strong>Amplitud</strong> = promedio de líneas (categorías) distintas por
                cliente, contra una meta (ej. 2). Empuja la variedad general del portafolio.</li>
            <li><strong>Efectividad</strong> = N°facturas / objetivo de visitas
                (mismo proxy del modelo actual; no hay captura de visitas reales).</li>
          </ul>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="seccion-titulo">Editar metas del período</div>',
                unsafe_allow_html=True)
    _editor_metas(client, df, anio, mes)


def _cls_factor(f) -> str:
    if f is None or pd.isna(f):
        return ""
    if f >= 1.0:
        return "verde-bg"
    if f > 0:
        return "amarillo-bg"
    return "rojo-bg"


def _celda_kpi(r, k) -> str:
    """Celda: logro% con color por factor y tooltip real/meta."""
    logro = r.get(f"{k}_logro")
    real  = r.get(f"{k}_real")
    meta  = r.get(f"{k}_meta")
    cls   = _cls_factor(r.get(f"{k}_factor"))
    if k in ("cuota",):
        detalle = f"{fmt_clp(real)} / {fmt_clp(meta)}"
    elif k == "amplitud":
        detalle = f"{(real or 0):.1f} / {fmt_num(meta)} líneas x cli"
    else:
        detalle = f"{fmt_num(real)} / {fmt_num(meta)}"
    return f"<td class='{cls}' title='{detalle}'>{fmt_pct(logro)}</td>"


def _tabla(df: pd.DataFrame):
    header = (
        "<th style='text-align:left'>Vendedor</th>"
        "<th title='Venta neta de NC del mes'>Venta Real</th>"
        "<th title='Fact-NC / meta de venta'>Cuota</th>"
        "<th title='Clientes nuevos + reactivados / meta'>Nuevos+React</th>"
        "<th title='Clientes activos / cartera asignada'>Cobertura</th>"
        "<th title='N° clientes con Galletas NY (penetración) — coloreado según logro vs meta'>Galletas NY</th>"
        "<th title='Promedio de líneas (categorías) distintas por cliente / meta'>Amplitud</th>"
        "<th title='N°facturas / objetivo de visitas'>Efectividad</th>"
        "<th title='Suma de los 6 KPIs (tope 5%)'>Tasa Efec.</th>"
        "<th title='Tasa efectiva × venta real'>Comisión $</th>"
    )
    rows = ""
    for _, r in df.iterrows():
        # Galletas NY: N° clientes + penetración, coloreado por el factor del KPI.
        ny_cls = _cls_factor(r.get("galletas_factor"))
        ny_meta = r.get("galletas_meta")
        ny_tip = (f"Penetración {fmt_pct(r.get('ny_pct'))} de {fmt_num(r.get('clientes_activos'))} "
                  f"clientes · meta {fmt_pct(ny_meta)} · logro {fmt_pct(r.get('galletas_logro'))}")
        ny = f"{fmt_num(r.get('ny_clientes'))} ({fmt_pct(r.get('ny_pct'))})"
        rows += f"""<tr>
          <td style='text-align:left'>{r['nombre_canonico']}</td>
          <td>{fmt_clp(r.get('fact_nc'))}</td>
          {_celda_kpi(r, 'cuota')}
          {_celda_kpi(r, 'nuevos')}
          {_celda_kpi(r, 'cobertura')}
          <td class='{ny_cls}' title='{ny_tip}'>{ny}</td>
          {_celda_kpi(r, 'amplitud')}
          {_celda_kpi(r, 'efectividad')}
          <td><strong>{fmt_pct(r.get('tasa_efectiva'))}</strong></td>
          <td><strong>{fmt_clp(r.get('comision_total'))}</strong></td>
        </tr>"""

    rows += f"""<tr class='total-row'>
      <td style='text-align:left'>TOTAL</td>
      <td>{fmt_clp(df['fact_nc'].sum())}</td>
      <td></td><td></td><td></td><td></td><td></td><td></td>
      <td></td>
      <td>{fmt_clp(df['comision_total'].sum())}</td>
    </tr>"""

    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems"><thead><tr>{header}</tr></thead>
    <tbody>{rows}</tbody></table></div>
    """, unsafe_allow_html=True)


def _safe_num(val, default=0):
    try:
        v = float(val)
        return default if pd.isna(v) else v
    except (TypeError, ValueError):
        return default


def _editor_metas(client, df: pd.DataFrame, anio: int, mes: int):
    st.caption("Las metas en blanco usan el default: Cuota→objetivo de venta, "
               "Efectividad→objetivo de visitas, Cobertura→cartera asignada "
               "(o clientes de los últimos 3 meses si no hay cartera cargada). "
               "Nuevos+reactivados, Galletas NY y Amplitud parten de un default editable.")

    vendedores = df[["vendedor_id", "nombre_canonico"]].sort_values("nombre_canonico")
    nombre_sel = st.selectbox("Seleccionar vendedor",
                              vendedores["nombre_canonico"].tolist(),
                              key="sel_vend_comision_v1")
    fila = df[df["nombre_canonico"] == nombre_sel].iloc[0]
    vendedor_id = int(fila["vendedor_id"])

    st.markdown(f"**Editando: {nombre_sel}** — {MESES[mes]} {anio}")
    with st.form("form_comision_v1_meta", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        m_venta = c1.number_input(
            "Meta de venta ($)", min_value=0, step=100000,
            value=int(_safe_num(fila.get("cuota_meta"))),
            help="Default = objetivo de venta del mes.")
        m_nuevos = c2.number_input(
            "Meta nuevos + reactivados", min_value=0, step=1,
            value=int(_safe_num(fila.get("nuevos_meta"), DEFAULT_META_NUEVOS)))
        m_cober = c3.number_input(
            "Meta cobertura (n° clientes)", min_value=0, step=1,
            value=int(_safe_num(fila.get("cobertura_meta"))),
            help="Default = cartera asignada del modelo actual.")
        c4, c5, c6 = st.columns(3)
        m_galletas_pct = c4.number_input(
            "Meta Galletas NY (% de clientes)", min_value=0.0, max_value=100.0, step=5.0,
            value=round(float(_safe_num(fila.get("galletas_meta"), DEFAULT_META_GALLETAS)) * 100, 1),
            help="% de tus clientes que deberían llevar Galletas NY (ej. 30%).")
        m_lineas = c5.number_input(
            "Meta amplitud (líneas x cliente)", min_value=0.0, step=0.5,
            value=float(_safe_num(fila.get("amplitud_meta"), DEFAULT_META_LINEAS)),
            help="Promedio de líneas (categorías) distintas por cliente (ej. 2).")
        m_visitas = c6.number_input(
            "Meta visitas", min_value=0, step=1,
            value=int(_safe_num(fila.get("efectividad_meta"))),
            help="Default = objetivo de visitas del mes.")
        submitted = st.form_submit_button("💾 Guardar metas", type="primary",
                                          use_container_width=True)

    if submitted:
        try:
            upsert_comision_v1_meta(
                client, vendedor_id, anio, mes,
                meta_venta=m_venta or None,
                meta_nuevos_react=m_nuevos,
                meta_cobertura=m_cober or None,
                meta_amplitud=round(m_galletas_pct / 100, 4),
                meta_visitas=m_visitas or None,
                meta_lineas=m_lineas,
            )
            st.success(f"✅ Metas de **{nombre_sel}** guardadas.")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")
