"""Propuesta de Comisiones v1.1 — scorecard de 5 KPIs (pestaña dentro de Comisiones).

Modelo NUEVO que convive con el de tramos actual (NO lo reemplaza). La comisión
es una **tasa efectiva 0–5% aplicada sobre la venta REAL (Fact-NC)**, repartida
en 5 KPIs ponderados. Cada KPI paga proporcional desde el 80% de su meta y la
tasa total topa en 5%.

  KPI                              Peso     % s/venta   Fuente del dato
  1. Cuota de venta                50%      2,50%       Fact-NC / meta_venta
  2. Nuevos + reactivados          15%      0,75%       historia fact_ventas; META AUTOMÁTICA
                                                        (2% de cartera + 10% de sus dormidos)
  3. Cobertura de cartera          11,67%   0,583%      clientes activos / cartera asignada
                                                        (cartera completa activa = paga sí o sí)
  4. Amplitud de categorías        11,67%   0,583%      líneas distintas x cliente vs meta (2)
  5. Profundidad SKU               11,67%   0,583%      SKUs distintos x categoría llevada vs meta (4)

Cambios v1.1 (acordados con gerencia 2026-07-09):
  · Efectividad de visita ELIMINADA (proxy débil) — su peso se fusionó en el
    bloque parejo Cobertura/Amplitud/SKU.
  · Penetración Galletas NY dejó de pagar: queda como columna INDICADORA.
  · Amplitud se abre en dos: categorías (breadth) y SKUs dentro de la categoría
    (depth, todo el portafolio, excluye Máquinas/Servicios).
  · Meta de Nuevos+Reactivados es automática y autorregulada: más cartera ⇒ más
    nuevos exigidos; más dormidos ⇒ más reactivaciones exigidas.

El cálculo se hace acá en pandas (no en SQL) para reusar el detalle de
fact_ventas. Solo las metas se persisten (comision_v1_meta, sql/022+023+024).
"""
from __future__ import annotations

import calendar
import pandas as pd
import streamlit as st

from app.styles import fmt_clp, fmt_pct, fmt_num
from app.data import (
    get_comisiones, get_ventas_rango, get_dim_producto_all,
    get_comision_v1_meta, upsert_comision_v1_meta, get_cartera_map,
    get_comision_v1_parametros, upsert_comision_v1_parametros,
)

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# ── Parámetros del modelo (diseño fijo del scorecard) ───────────────────────
TASA_MAX = 0.05      # tope de la tasa efectiva (5% sobre la venta real)
UMBRAL   = 0.80      # umbral de pago DEFAULT; editable por KPI desde la app
                     # (tabla comision_v1_parametro, claves umbral_<kpi>)

# key, etiqueta corta, peso (fracción de la tasa máxima).
# El 35% post Cuota/Nuevos se reparte PAREJO entre los otros tres (gerencia).
_P3 = 0.35 / 3
KPIS = [
    ("cuota",     "Cuota de venta",         0.50),
    ("nuevos",    "Nuevos + reactivados",   0.15),
    ("cobertura", "Cobertura de cartera",   _P3),
    ("amplitud",  "Amplitud de categorías", _P3),
    ("sku",       "Profundidad SKU",        _P3),
]
PESO   = {k: p for k, _, p in KPIS}
PCT    = {k: p * TASA_MAX for k, _, p in KPIS}   # % sobre venta de cada KPI

# Defaults de metas cuando no hay valor cargado ni fuente previa.
DEFAULT_META_LINEAS = 2.0   # amplitud: líneas (categorías) x cliente
DEFAULT_META_SKUS   = 4.0   # profundidad: SKUs distintos x categoría llevada

# Meta automática de Nuevos+Reactivados (override manual en meta_nuevos_react):
PCT_META_NUEVOS = 0.02   # nuevos: 2% de la cartera (mín. 2)
PCT_META_REACT  = 0.10   # reactivados: 10% de sus dormidos (mín. 1 si tiene)

GAP_REACTIVACION = 3   # meses sin comprar para considerar dormido/reactivado (≈90 días)

# Categorías que no son "portafolio vendible": fuera de amplitud y profundidad.
CAT_EXCLUIDAS = {"Maquinas", "Servicios", "Sin Categoria"}


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
def _factor(ratio, umbral: float = UMBRAL) -> float | None:
    """Fracción del peso que paga un KPI dado su logro (real/meta).
    0 bajo el umbral, sube lineal hasta 1 en el 100%, tope en 1.
    Umbral ≥ 100% degenera a todo-o-nada (paga completo solo al 100%)."""
    if ratio is None or pd.isna(ratio):
        return None
    if umbral >= 1.0:
        return 1.0 if ratio >= 1.0 else 0.0
    f = (ratio - umbral) / (1.0 - umbral)
    return max(0.0, min(1.0, f))


def _ratio(real, meta):
    if meta is None or pd.isna(meta) or float(meta) <= 0:
        return None
    return float(real) / float(meta)


# ── Cálculo del scorecard por vendedor ──────────────────────────────────────
def _calcular(client, anio: int, mes: int) -> pd.DataFrame:
    base = get_comisiones(client, anio, mes)
    if base.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Excluir vendedores demo del seed y el bucket residual "Sin asignar"
    # (ventas sin vendedor; no es una persona a la que se le pague comisión).
    base = base[~base["nombre_canonico"].str.startswith("Vendedor ", na=False)]
    base = base[base["nombre_canonico"] != "Sin asignar"].copy()
    for c in ["fact_nc", "obj_venta", "n_facturas", "cartera_clientes"]:
        if c in base.columns:
            base[c] = pd.to_numeric(base[c], errors="coerce")

    # Umbrales de pago por KPI (editables desde la app; default 80%).
    params = get_comision_v1_parametros(client)
    umbrales = {k: float(params.get(f"umbral_{k}", UMBRAL)) for k, _, _ in KPIS}

    # Metas v1 (overrides). NULL → default / meta automática.
    metas = get_comision_v1_meta(client, anio, mes)
    if not metas.empty:
        base = base.merge(metas, on="vendedor_id", how="left")
    for c in ["meta_venta", "meta_nuevos_react", "meta_cobertura",
              "meta_lineas", "meta_skus"]:
        if c not in base.columns:
            base[c] = None

    # Cartera OFICIAL (tabla cartera_cliente, del reporte Autoventa): meta real
    # de cobertura y dueño de los clientes dormidos.
    cart_map = get_cartera_map(client)
    if not cart_map.empty:
        cart_counts = (cart_map.dropna(subset=["vendedor_id"])
                       .groupby("vendedor_id").size()
                       .rename("cartera_real").reset_index())
        base = base.merge(cart_counts, on="vendedor_id", how="left")
    if "cartera_real" not in base.columns:
        base["cartera_real"] = None

    # Historia de ventas a nivel línea (nuevos/react, cobertura, amplitud, SKU, dormidos).
    ultimo = calendar.monthrange(anio, mes)[1]
    ffin = f"{anio}-{mes:02d}-{ultimo:02d}"
    hist = get_ventas_rango(client, "2024-01-01", ffin)
    métricas, detalle_cli = _metricas_historia(hist, client, anio, mes, cart_map)

    base = base.merge(métricas, on="vendedor_id", how="left")
    for c in ["nuevos_react", "nuevos_solo", "react_solo", "clientes_activos",
              "amplitud_prom", "sku_prom", "ny_clientes", "ny_pct",
              "cartera_hist", "dormidos"]:
        if c not in base.columns:
            base[c] = 0
        base[c] = base[c].fillna(0)

    # ── Metas efectivas (override v1 → fuente previa → default/auto) ─────────
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
        m_venta = _coalesce(r, "meta_venta", "obj_venta")
        # Cobertura: meta v1 → CARTERA OFICIAL (reporte Autoventa) → cartera del
        # modelo de tramos → proxy histórico (clientes de los últimos 3 meses).
        m_cober = None
        cober_src = None
        for src in ("meta_cobertura", "cartera_real", "cartera_clientes", "cartera_hist"):
            v = r.get(src)
            if v is not None and pd.notna(v) and float(v) > 0:
                m_cober = float(v)
                cober_src = src
                break
        # Nuevos+Reactivados: override manual → META AUTOMÁTICA autorregulada
        # (2% de la cartera para nuevos + 10% de sus dormidos para reactivar).
        dorm = float(r.get("dormidos") or 0)
        ov_nr = r.get("meta_nuevos_react")
        if ov_nr is not None and pd.notna(ov_nr):
            m_nuevos = float(ov_nr)
        else:
            parte_nuevos = max(2.0, round(PCT_META_NUEVOS * (m_cober or 0)))
            parte_react  = max(1.0, round(PCT_META_REACT * dorm)) if dorm > 0 else 0.0
            m_nuevos = parte_nuevos + parte_react
        m_lineas = _coalesce(r, "meta_lineas", "__none__", DEFAULT_META_LINEAS)
        m_skus   = _coalesce(r, "meta_skus", "__none__", DEFAULT_META_SKUS)

        reales = {
            "cuota":     r.get("fact_nc") or 0,
            "nuevos":    r.get("nuevos_react") or 0,
            "cobertura": r.get("clientes_activos") or 0,
            "amplitud":  r.get("amplitud_prom") or 0,   # líneas x cliente
            "sku":       r.get("sku_prom") or 0,        # SKUs x categoría llevada
        }
        metas_ef = {
            "cuota": m_venta, "nuevos": m_nuevos, "cobertura": m_cober,
            "amplitud": m_lineas, "sku": m_skus,
        }

        fila = {
            "vendedor_id": r["vendedor_id"], "nombre_canonico": r["nombre_canonico"],
            "fact_nc": r.get("fact_nc") or 0, "cobertura_source": cober_src,
            "ny_clientes": r.get("ny_clientes") or 0, "ny_pct": r.get("ny_pct") or 0,
            "nuevos_solo": r.get("nuevos_solo") or 0, "react_solo": r.get("react_solo") or 0,
            "dormidos": dorm, "clientes_activos": r.get("clientes_activos") or 0,
            # Overrides crudos (para que el editor distinga manual vs automático)
            "ov_meta_venta": r.get("meta_venta"),
            "ov_meta_nuevos_react": r.get("meta_nuevos_react"),
            "ov_meta_cobertura": r.get("meta_cobertura"),
            "ov_meta_lineas": r.get("meta_lineas"),
            "ov_meta_skus": r.get("meta_skus"),
        }
        tasa = 0.0
        for k, _lbl, _peso in KPIS:
            rt = _ratio(reales[k], metas_ef[k])
            f  = _factor(rt, umbrales[k])
            fila[f"{k}_real"] = reales[k]
            fila[f"{k}_meta"] = metas_ef[k]
            fila[f"{k}_logro"] = rt
            fila[f"{k}_factor"] = f
            fila[f"{k}_umbral"] = umbrales[k]
            aporte = (f or 0.0) * PCT[k]
            fila[f"{k}_aporte"] = aporte
            fila[f"{k}_comision"] = aporte * (r.get("fact_nc") or 0)
            tasa += aporte
        fila["tasa_efectiva"] = tasa
        fila["comision_total"] = tasa * (r.get("fact_nc") or 0)
        filas.append(fila)

    out = pd.DataFrame(filas)
    out = out.sort_values("comision_total", ascending=False, na_position="last")
    return out, detalle_cli


def _metricas_historia(hist: pd.DataFrame, client, anio: int, mes: int,
                       cart_map: pd.DataFrame = None) -> pd.DataFrame:
    """Deriva por vendedor (del mes seleccionado):
    - clientes_activos: clientes distintos con factura este mes.
    - nuevos_solo / react_solo / nuevos_react: 1ª compra (ever) y reactivados
      (gap ≥3m) entre los activos; el KPI usa la suma.
    - amplitud_prom: promedio de líneas (categorías) distintas por cliente.
    - sku_prom: promedio de SKUs distintos POR CATEGORÍA llevada, por cliente
      (profundidad; todo el portafolio, excluye Máquinas/Servicios).
    - ny_clientes / ny_pct: clientes que compraron Galletas NY (indicador).
    - dormidos: clientes sin comprar hace ≥3 meses cuya ÚLTIMA compra fue con
      este vendedor (alimenta la meta automática de reactivados).
    - cartera_hist: clientes distintos de los últimos 3 meses (proxy de cartera).
    La atribución del cliente activo es al vendedor que más le facturó en el mes.

    Devuelve (agg, detalle_cli): agg por vendedor y el detalle a nivel CLIENTE
    (tipo ∈ {nuevo, reactivado, activo, dormido}) para la vista de gerencia."""
    cols = ["vendedor_id", "clientes_activos", "nuevos_react", "nuevos_solo",
            "react_solo", "amplitud_prom", "sku_prom", "ny_clientes", "ny_pct",
            "cartera_hist", "dormidos"]
    det_cols = ["cliente_rut", "vendedor_id", "tipo", "monto", "ultima_ym",
                "meses_sin", "n_lineas", "compro_ny"]
    vacio = (pd.DataFrame(columns=cols), pd.DataFrame(columns=det_cols))
    if hist is None or hist.empty:
        return vacio

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
    fac = h[h["es_fac"] & (h["ym"] <= sel)].copy()
    if fac.empty:
        return vacio

    # Primera compra (ever) y última compra ANTERIOR al mes, por cliente.
    first_ym = fac.groupby("cliente_rut")["ym"].min()
    prev = fac[fac["ym"] < sel].groupby("cliente_rut")["ym"].max()

    # Dormidos: última compra hace ≥3 meses, atribuidos al DUEÑO de la cartera
    # oficial si existe; si el cliente no está asignado, al último vendedor que
    # le facturó (así los huérfanos de ex-vendedores pasan al dueño actual).
    asignado = {}
    if cart_map is not None and not cart_map.empty:
        asignado = {r_["cliente_rut"]: int(r_["vendedor_id"])
                    for _, r_ in cart_map.dropna(subset=["vendedor_id"]).iterrows()}
    last = (fac.sort_values("fecha")
               .groupby("cliente_rut")
               .agg(last_ym=("ym", "max"), last_vend=("vendedor_id", "last")))
    dorm = last[last["last_ym"] <= sel - GAP_REACTIVACION].copy()
    dorm["dueno"] = [asignado.get(rut, lv)
                     for rut, lv in zip(dorm.index, dorm["last_vend"])]
    dorm_df = (dorm.groupby("dueno").size()
               .rename("dormidos").reset_index()
               .rename(columns={"dueno": "vendedor_id"}))

    # Proxy de cartera asignada: clientes distintos de los últimos 3 meses.
    tri = fac[fac["ym"].isin([sel - 2, sel - 1, sel])]
    cartera_hist = (tri.groupby("vendedor_id")["cliente_rut"].nunique()
                      .rename("cartera_hist").reset_index())

    # Detalle de dormidos (para la vista por vendedor): última compra, meses
    # sin comprar y compra histórica, atribuidos a su dueño.
    monto_hist = fac.groupby("cliente_rut")["neto"].sum()
    det_dorm = pd.DataFrame({
        "cliente_rut": dorm.index,
        "vendedor_id": dorm["dueno"].values,
        "tipo": "dormido",
        "monto": monto_hist.reindex(dorm.index).fillna(0).values,
        "ultima_ym": [str(y) for y in dorm["last_ym"]],
        "meses_sin": [(sel - y).n for y in dorm["last_ym"]],
    })

    cur = fac[fac["ym"] == sel].copy()
    if cur.empty:
        out = dorm_df.merge(cartera_hist, on="vendedor_id", how="outer")
        for c in cols:
            if c not in out.columns:
                out[c] = 0
        det = det_dorm.reindex(columns=det_cols)
        return out.fillna(0)[cols], det

    # Atribución cliente → vendedor (el que más le facturó este mes).
    attr = (cur.groupby(["cliente_rut", "vendedor_id"])["neto"].sum()
              .reset_index()
              .sort_values("neto", ascending=False)
              .drop_duplicates("cliente_rut"))
    attr = attr[["cliente_rut", "vendedor_id"]]

    # Estado nuevo / reactivado por cliente activo.
    def _estado(rut):
        if first_ym.get(rut) == sel:
            return "nuevo"                    # 1ª compra ever
        pv = prev.get(rut)
        if pv is None or pd.isna(pv) or (sel - pv).n >= GAP_REACTIVACION:
            return "react"                    # vuelve tras dormir ≥3 meses
        return None
    attr["estado"] = attr["cliente_rut"].map(_estado)

    # Portafolio vendible del mes (sin Máquinas/Servicios) para amplitud y SKU.
    cur_p = cur[~cur["linea"].isin(CAT_EXCLUIDAS)]
    lineas_cli = (cur_p.groupby("cliente_rut")["linea"].nunique()
                    .rename("n_lineas").reset_index())
    # Profundidad: SKUs distintos por (cliente, categoría) → promedio por cliente.
    sku_cat = (cur_p.groupby(["cliente_rut", "linea"])["producto_codigo"]
                    .nunique().reset_index(name="n_skus"))
    sku_cli = (sku_cat.groupby("cliente_rut")["n_skus"].mean()
                    .rename("skus_prof").reset_index())
    ny_cli = (cur.groupby("cliente_rut")["es_ny"].any()
                .rename("compro_ny").reset_index())
    attr = (attr.merge(lineas_cli, on="cliente_rut", how="left")
                 .merge(sku_cli, on="cliente_rut", how="left")
                 .merge(ny_cli, on="cliente_rut", how="left"))
    attr["n_lineas"] = attr["n_lineas"].fillna(0)
    attr["skus_prof"] = attr["skus_prof"].fillna(0)
    attr["compro_ny"] = attr["compro_ny"].fillna(False)

    agg = attr.groupby("vendedor_id").agg(
        clientes_activos=("cliente_rut", "nunique"),
        nuevos_solo=("estado", lambda s: (s == "nuevo").sum()),
        react_solo=("estado", lambda s: (s == "react").sum()),
        amplitud_prom=("n_lineas", "mean"),
        sku_prom=("skus_prof", "mean"),
        ny_clientes=("compro_ny", "sum"),
    ).reset_index()
    agg["nuevos_react"] = agg["nuevos_solo"] + agg["react_solo"]
    agg["ny_pct"] = agg.apply(
        lambda x: (x["ny_clientes"] / x["clientes_activos"]) if x["clientes_activos"] else 0,
        axis=1)
    agg = (agg.merge(cartera_hist, on="vendedor_id", how="outer")
              .merge(dorm_df, on="vendedor_id", how="outer"))
    for c in cols:
        if c not in agg.columns:
            agg[c] = 0

    # Detalle de activos del mes (nuevo/reactivado/activo) con su compra.
    monto_mes = cur.groupby("cliente_rut")["neto"].sum()
    det_act = attr.copy()
    det_act["tipo"] = det_act["estado"].map(
        {"nuevo": "nuevo", "react": "reactivado"}).fillna("activo")
    det_act["monto"] = det_act["cliente_rut"].map(monto_mes).fillna(0)
    det_act["ultima_ym"] = str(sel)
    det_act["meses_sin"] = 0
    detalle = pd.concat([det_act, det_dorm], ignore_index=True)
    detalle = detalle.reindex(columns=det_cols)

    return agg.fillna(0)[cols], detalle


# ── Render ───────────────────────────────────────────────────────────────────
def render_tab(client, anio: int, mes: int):
    st.markdown(
        '<div class="estado-vacio" style="margin-bottom:.75rem">'
        '<strong>Propuesta de Comisiones v1.1</strong> — modelo alternativo (en '
        'evaluación, NO reemplaza el actual). La comisión es una <strong>tasa '
        'efectiva de hasta 5% sobre la venta real</strong>, repartida en 5 KPIs '
        'ponderados. Cada KPI paga proporcional desde el 80% de su meta.</div>',
        unsafe_allow_html=True,
    )

    df, detalle_cli = _calcular(client, anio, mes)
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

    st.markdown('<div class="seccion-titulo">Detalle por vendedor: activos y dormidos</div>',
                unsafe_allow_html=True)
    _detalle_clientes(client, df, detalle_cli)

    with st.expander("ℹ️ Cómo se calcula", expanded=False):
        st.markdown(f"""
        <div class="nota-embudo">
          <ul>
            <li><strong>Comisión $ = tasa efectiva × venta real (Fact-NC)</strong>.
                La tasa efectiva es la suma de los 5 KPIs y topa en 5,00%.</li>
            <li>Cada KPI aporta <em>peso × 5%</em> como máximo: Cuota 2,50%,
                Nuevos+react 0,75%, y Cobertura / Amplitud / Profundidad SKU
                parejos (≈0,58% cada uno).</li>
            <li><strong>Pago proporcional desde el umbral</strong> (80% por defecto,
                <em>editable por KPI</em> en la sección Umbrales de abajo): bajo el umbral
                el KPI paga $0; del umbral al 100% sube lineal; al 100% o más paga completo.
                Ej: umbral 80% y logro 90% → paga la mitad. El umbral vigente de cada
                KPI se ve al pasar el mouse por su celda.</li>
            <li><strong>Cuota</strong> = Fact-NC / meta de venta.</li>
            <li><strong>Nuevos + reactivados</strong> = clientes de 1ª compra + clientes
                que vuelven tras {GAP_REACTIVACION}+ meses dormidos. La <strong>meta es
                automática y autorregulada</strong>: 2% de la cartera (mín. 2) + 10% de
                los dormidos del vendedor (mín. 1). Quien deja dormir su cartera recibe
                una meta de reactivación más alta al mes siguiente. Gerencia puede fijar
                una meta manual que reemplaza a la automática.</li>
            <li><strong>Cobertura</strong> = clientes que compraron / cartera asignada.
                La cartera sale de la <strong>cartera oficial</strong> (reporte de clientes
                de Autoventa, campo Vend. exclusivo); si un vendedor no aparece ahí, se
                estima con sus clientes de los últimos 3 meses. <strong>Bono de
                mantención</strong>: si la cartera completa está activa (100%), el KPI
                paga completo sí o sí.</li>
            <li><strong>Amplitud de categorías</strong> = promedio de líneas (categorías)
                distintas por cliente, contra una meta (ej. 2). Empuja abrir líneas nuevas
                en cada cliente. Excluye Máquinas y Servicios.</li>
            <li><strong>Profundidad SKU</strong> = de las categorías que el cliente ya
                lleva, cuántos SKUs distintos compra en cada una (promedio), contra una
                meta (ej. 4). Empuja vender más variedades dentro de la línea
                («variable sobre variable» con la amplitud: abrir la línea Y profundizarla).</li>
            <li><strong>Galletas NY</strong> es columna indicadora (no paga directo):
                % de clientes del vendedor que llevan la línea nueva. La línea empuja
                igual la Amplitud (categoría nueva) y la Profundidad (sus 13 SKUs).</li>
          </ul>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="seccion-titulo">Editar metas del período</div>',
                unsafe_allow_html=True)
    _editor_metas(client, df, anio, mes)

    st.markdown('<div class="seccion-titulo">Umbrales de pago por KPI</div>',
                unsafe_allow_html=True)
    _editor_umbrales(client)


def _detalle_clientes(client, df: pd.DataFrame, detalle: pd.DataFrame):
    """Para responder al instante '¿quiénes son los activos/dormidos de X?':
    listas nominadas de clientes del vendedor elegido, con estado y montos."""
    if detalle is None or detalle.empty:
        st.caption("Sin detalle de clientes para el período.")
        return

    vendedores = df[["vendedor_id", "nombre_canonico"]].sort_values("nombre_canonico")
    nombre_sel = st.selectbox("Ver clientes de", vendedores["nombre_canonico"].tolist(),
                              key="sel_vend_detalle_v1")
    vid = int(vendedores[vendedores["nombre_canonico"] == nombre_sel]
              .iloc[0]["vendedor_id"])
    d = detalle[detalle["vendedor_id"] == vid].copy()

    # Enriquecer con razón social y comuna. Solo esas columnas: dim_cliente
    # trae un "tipo" propio (tipo de cliente) que colisionaría con el "tipo"
    # del detalle (nuevo/reactivado/dormido) → tipo_x/tipo_y y KeyError.
    try:
        from app.data import get_dim_cliente_full
        dfc = get_dim_cliente_full(client)
        if not dfc.empty:
            dfc = (dfc.rename(columns={"rut": "cliente_rut"})
                      [["cliente_rut", "razon_social", "comuna"]])
            d = d.merge(dfc, on="cliente_rut", how="left")
    except Exception:
        pass
    for col in ["razon_social", "comuna"]:
        if col not in d.columns:
            d[col] = None
    d["razon_social"] = d["razon_social"].fillna(d["cliente_rut"])

    act  = d[d["tipo"] != "dormido"].sort_values("monto", ascending=False)
    dorm = d[d["tipo"] == "dormido"].sort_values("monto", ascending=False)
    n_nuevo = int((act["tipo"] == "nuevo").sum())
    n_react = int((act["tipo"] == "reactivado").sum())

    st.markdown(
        f'<div class="estado-vacio" style="margin-bottom:.5rem"><strong>{nombre_sel}</strong>: '
        f'✅ <strong>{len(act)} clientes activos</strong> este mes '
        f'({n_nuevo} nuevos · {n_react} reactivados) &nbsp;·&nbsp; '
        f'😴 <strong>{len(dorm)} dormidos</strong> por recuperar '
        f'({fmt_clp(dorm["monto"].sum())} de compra histórica).</div>',
        unsafe_allow_html=True)

    tab_a, tab_d = st.tabs([f"✅ Activos ({len(act)})", f"😴 Dormidos ({len(dorm)})"])
    _BADGE = {"nuevo": "<span class='chip-estado' style='color:#1A7F4B'>● Nuevo</span>",
              "reactivado": "<span class='chip-estado' style='color:#a36a10'>● Reactivado</span>",
              "activo": ""}

    with tab_a:
        rows = ""
        for _, r in act.iterrows():
            ny = "🍪" if r.get("compro_ny") else ""
            rows += f"""<tr>
              <td style='text-align:left'>{r['razon_social']}</td>
              <td>{r.get('comuna') or '—'}</td>
              <td style='text-align:left'>{_BADGE.get(r['tipo'], '')}</td>
              <td>{fmt_clp(r['monto'])}</td>
              <td>{fmt_num(r.get('n_lineas'))}</td>
              <td title='Compró Galletas NY'>{ny}</td>
            </tr>"""
        st.markdown(f"""
        <div class="tabla-container"><table class="kreems"><thead><tr>
          <th style='text-align:left'>Cliente</th><th>Comuna</th>
          <th style='text-align:left'>Estado</th>
          <th title='Compra neta del mes'>Compra del mes</th>
          <th title='Líneas (categorías) distintas'>Líneas</th><th>NY</th>
        </tr></thead><tbody>{rows}</tbody></table></div>
        """, unsafe_allow_html=True)

    with tab_d:
        _top = 40
        rows = ""
        for _, r in dorm.head(_top).iterrows():
            rows += f"""<tr>
              <td style='text-align:left'>{r['razon_social']}</td>
              <td>{r.get('comuna') or '—'}</td>
              <td>{r.get('ultima_ym') or '—'}</td>
              <td>{fmt_num(r.get('meses_sin'))}</td>
              <td>{fmt_clp(r['monto'])}</td>
            </tr>"""
        st.markdown(f"""
        <div class="tabla-container"><table class="kreems"><thead><tr>
          <th style='text-align:left'>Cliente</th><th>Comuna</th>
          <th>Última compra</th><th>Meses sin comprar</th>
          <th title='Total facturado históricamente'>Compra histórica</th>
        </tr></thead><tbody>{rows}</tbody></table></div>
        """, unsafe_allow_html=True)
        if len(dorm) > _top:
            st.caption(f"Mostrando los {_top} de mayor compra histórica "
                       f"(de {len(dorm)} dormidos). La lista completa la ve el "
                       f"vendedor en su panel.")


def _editor_umbrales(client):
    """Edición del umbral de pago por KPI (% de la meta desde el cual el KPI
    empieza a pagar). Bajo el umbral → $0; entre umbral y 100% sube lineal;
    ≥100% paga completo. Afecta a TODOS los vendedores y meses no cerrados."""
    params = get_comision_v1_parametros(client)
    if not params:
        st.info("Corre `sql/026_comision_v1_umbral.sql` en Supabase para "
                "habilitar la edición de umbrales (mientras tanto rige el 80%).")
        return

    st.caption("Desde qué % de logro de la meta empieza a pagar cada KPI "
               "(bajo el umbral $0; del umbral al 100% sube lineal; 100% paga "
               "completo). Ej: umbral 60% y logro 80% → paga la mitad. "
               "Afecta a todos los vendedores del mes en vivo.")

    with st.form("form_v1_umbrales", clear_on_submit=False):
        cols = st.columns(len(KPIS))
        nuevos_vals = {}
        for col, (k, lbl, _p) in zip(cols, KPIS):
            actual = float(params.get(f"umbral_{k}", UMBRAL)) * 100
            nuevos_vals[k] = col.number_input(
                lbl, min_value=0.0, max_value=100.0, step=5.0,
                value=round(actual, 1), key=f"umb_{k}")
        submitted = st.form_submit_button("💾 Guardar umbrales", type="primary",
                                          use_container_width=True)
    if submitted:
        try:
            upsert_comision_v1_parametros(
                client,
                {f"umbral_{k}": round(v / 100, 4) for k, v in nuevos_vals.items()})
            st.success("✅ Umbrales guardados.")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")


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
    if k == "cuota":
        detalle = f"{fmt_clp(real)} / {fmt_clp(meta)}"
    elif k == "amplitud":
        detalle = f"{(real or 0):.1f} / {fmt_num(meta)} líneas x cliente"
    elif k == "sku":
        detalle = f"{(real or 0):.1f} / {fmt_num(meta)} SKUs x categoría"
    else:
        detalle = f"{fmt_num(real)} / {fmt_num(meta)}"
    umb = r.get(f"{k}_umbral")
    if umb is not None and pd.notna(umb):
        detalle += f" · paga desde {fmt_pct(umb)}"
    return f"<td class='{cls}' title='{detalle}'>{fmt_pct(logro)}</td>"


def _tabla(df: pd.DataFrame):
    header = (
        "<th style='text-align:left'>Vendedor</th>"
        "<th title='Venta neta de NC del mes'>Venta Real</th>"
        "<th title='Fact-NC / meta de venta'>Cuota</th>"
        "<th title='Clientes de 1ª compra + reactivados / meta automática (2% cartera + 10% dormidos)'>Nuevos+React</th>"
        "<th title='Clientes activos / cartera asignada. Cartera completa activa = paga completo'>Cobertura</th>"
        "<th title='Promedio de líneas (categorías) distintas por cliente / meta'>Amplitud</th>"
        "<th title='Promedio de SKUs distintos por categoría llevada / meta'>Prof. SKU</th>"
        "<th title='Indicador (no paga directo): clientes con Galletas NY y % de penetración'>Galletas NY</th>"
        "<th title='Suma de los 5 KPIs (tope 5%)'>Tasa Efec.</th>"
        "<th title='Tasa efectiva × venta real'>Comisión $</th>"
    )
    rows = ""
    for _, r in df.iterrows():
        # Nuevos+React con desglose en tooltip.
        nv_cls = _cls_factor(r.get("nuevos_factor"))
        nv_tip = (f"{fmt_num(r.get('nuevos_solo'))} nuevos + "
                  f"{fmt_num(r.get('react_solo'))} reactivados / meta "
                  f"{fmt_num(r.get('nuevos_meta'))} · dormidos: {fmt_num(r.get('dormidos'))}")
        # Galletas NY: indicador informativo.
        ny_tip = (f"Penetración {fmt_pct(r.get('ny_pct'))} de "
                  f"{fmt_num(r.get('clientes_activos'))} clientes activos — indicador, no paga directo")
        ny = f"{fmt_num(r.get('ny_clientes'))} ({fmt_pct(r.get('ny_pct'))})"
        rows += f"""<tr>
          <td style='text-align:left'>{r['nombre_canonico']}</td>
          <td>{fmt_clp(r.get('fact_nc'))}</td>
          {_celda_kpi(r, 'cuota')}
          <td class='{nv_cls}' title='{nv_tip}'>{fmt_pct(r.get('nuevos_logro'))}</td>
          {_celda_kpi(r, 'cobertura')}
          {_celda_kpi(r, 'amplitud')}
          {_celda_kpi(r, 'sku')}
          <td title='{ny_tip}'>{ny}</td>
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
    st.caption("**0 = automático**: Cuota→objetivo de venta del mes; "
               "Nuevos+react→meta automática (2% cartera + 10% dormidos); "
               "Cobertura→cartera oficial del reporte Autoventa "
               "(o clientes de últimos 3 meses si el vendedor no está en ella); "
               "Amplitud→2 líneas; Prof. SKU→4 SKUs. "
               "Cualquier valor distinto de 0 reemplaza al automático.")

    vendedores = df[["vendedor_id", "nombre_canonico"]].sort_values("nombre_canonico")
    nombre_sel = st.selectbox("Seleccionar vendedor",
                              vendedores["nombre_canonico"].tolist(),
                              key="sel_vend_comision_v1")
    fila = df[df["nombre_canonico"] == nombre_sel].iloc[0]
    vendedor_id = int(fila["vendedor_id"])

    # Contexto de la meta automática del vendedor seleccionado.
    st.markdown(
        f"**Editando: {nombre_sel}** — {MESES[mes]} {anio} &nbsp;·&nbsp; "
        f"meta automática Nuevos+React del mes: **{fmt_num(fila.get('nuevos_meta'))}** "
        f"(dormidos: {fmt_num(fila.get('dormidos'))})")

    with st.form("form_comision_v1_meta", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        m_venta = c1.number_input(
            "Meta de venta ($)", min_value=0, step=100000,
            value=int(_safe_num(fila.get("ov_meta_venta"))),
            help="0 = objetivo de venta del mes.")
        m_nuevos = c2.number_input(
            "Meta nuevos + reactivados", min_value=0, step=1,
            value=int(_safe_num(fila.get("ov_meta_nuevos_react"))),
            help="0 = automática (2% de cartera + 10% de sus dormidos).")
        m_cober = c3.number_input(
            "Meta cobertura (n° clientes)", min_value=0, step=1,
            value=int(_safe_num(fila.get("ov_meta_cobertura"))),
            help="0 = cartera asignada (o clientes de los últimos 3 meses).")
        c4, c5 = st.columns(2)
        m_lineas = c4.number_input(
            "Meta amplitud (líneas x cliente)", min_value=0.0, step=0.5,
            value=float(_safe_num(fila.get("ov_meta_lineas"))),
            help=f"0 = default ({DEFAULT_META_LINEAS:.0f} líneas).")
        m_skus = c5.number_input(
            "Meta profundidad (SKUs x categoría)", min_value=0.0, step=0.5,
            value=float(_safe_num(fila.get("ov_meta_skus"))),
            help=f"0 = default ({DEFAULT_META_SKUS:.0f} SKUs).")
        submitted = st.form_submit_button("💾 Guardar metas", type="primary",
                                          use_container_width=True)

    if submitted:
        try:
            upsert_comision_v1_meta(
                client, vendedor_id, anio, mes,
                meta_venta=m_venta or None,
                meta_nuevos_react=m_nuevos or None,
                meta_cobertura=m_cober or None,
                meta_lineas=m_lineas or None,
                meta_skus=m_skus or None,
            )
            st.success(f"✅ Metas de **{nombre_sel}** guardadas.")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")
