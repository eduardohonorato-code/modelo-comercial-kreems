"""
Presupuesto de Venta (gerencia/admin).

Compara el presupuesto mensual contra la venta real (Fact-NC), muestra la
estacionalidad histórica y sugiere el objetivo del próximo mes combinando:
ritmo reciente desestacionalizado, crecimiento interanual y presupuesto.

La decisión FINAL de objetivos sigue siendo manual (Panel Gerencia → Editar
objetivos); esta sección solo entrega la base cuantitativa para decidir.

Datos livianos (sql/017): presupuesto_venta y ventas_historicas guardan solo
el monto mensual total — sin detalle de facturación, para no cargar la BD.
"""
import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.styles import fmt_clp, fmt_pct, color_pct
from app.data import (get_presupuesto, upsert_presupuesto,
                      get_ventas_historicas, upsert_venta_historica,
                      get_real_mensual, get_participacion_vendedores)

MESES_ABR = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
             7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
MESES_NOM = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo",
             6: "Junio", 7: "Julio", 8: "Agosto", 9: "Septiembre",
             10: "Octubre", 11: "Noviembre", 12: "Diciembre"}

# Paleta de la sección (consistente con el resto de la app)
C_REAL, C_PPTO, C_H1, C_H2 = "#E62984", "#1B3A6B", "#1E88E5", "#9AB6D9"


# ── Helpers de cálculo ────────────────────────────────────────────────────────

def _estacionalidad(hist: pd.DataFrame) -> pd.Series:
    """
    Índice de estacionalidad: % del año que representa cada mes, promediando
    los años históricos completos. Serie index=mes (1-12), valores que suman 1.
    """
    if hist.empty:
        return pd.Series(dtype=float)
    shares = []
    for a, g in hist.groupby("anio"):
        tot = g["monto"].sum()
        if tot > 0 and len(g) >= 10:  # solo años (casi) completos
            s = g.set_index("mes")["monto"] / tot
            shares.append(s)
    if not shares:
        return pd.Series(dtype=float)
    return pd.concat(shares, axis=1).mean(axis=1)


def _meses_cerrados(real: pd.DataFrame, anio: int, hoy: datetime.date) -> list:
    """Meses del año con venta real ya CERRADA (excluye el mes en curso)."""
    if real.empty:
        return []
    meses = sorted(real.loc[real["fact_nc"] > 0, "mes"].tolist())
    if anio == hoy.year:
        meses = [m for m in meses if m < hoy.month]
    return meses


def _sugerencia(real: pd.DataFrame, hist: pd.DataFrame, ppto: pd.DataFrame,
                estac: pd.Series, anio: int, mes_obj: int,
                hoy: datetime.date) -> dict:
    """
    Tres referencias independientes para el objetivo del mes a planificar:
      A) Ritmo reciente desestacionalizado × estacionalidad del mes objetivo.
      B) Mismo mes del año pasado × crecimiento de los últimos meses.
      C) Presupuesto del mes.
    Sugerido = promedio de las disponibles. Devuelve dict con todo el detalle.
    """
    out = {"refs": {}, "base_meses": []}
    cerrados = _meses_cerrados(real, anio, hoy)
    base = cerrados[-3:]  # últimos 3 meses cerrados
    out["base_meses"] = base
    real_idx = real.set_index("mes")["fact_nc"] if not real.empty else pd.Series(dtype=float)

    # A) Desestacionalizado: (venta base / peso estacional base) × peso mes obj
    if base and not estac.empty and mes_obj in estac.index:
        peso_base = sum(estac.get(m, 0) for m in base)
        venta_base = sum(real_idx.get(m, 0) for m in base)
        if peso_base > 0 and venta_base > 0:
            out["refs"]["estacionalidad"] = venta_base / peso_base * float(estac[mes_obj])

    # B) Año pasado × crecimiento reciente (mismos meses base, año anterior)
    prev = anio - 1
    h_prev = hist[hist["anio"] == prev].set_index("mes")["monto"] if not hist.empty else pd.Series(dtype=float)
    if base and mes_obj in h_prev.index and h_prev.get(mes_obj, 0) > 0:
        prev_base = sum(h_prev.get(m, 0) for m in base)
        venta_base = sum(real_idx.get(m, 0) for m in base)
        if prev_base > 0 and venta_base > 0:
            growth = venta_base / prev_base
            out["refs"]["interanual"] = float(h_prev[mes_obj]) * growth
            out["growth"] = growth

    # C) Presupuesto del mes objetivo
    p = ppto.set_index("mes")["monto"] if not ppto.empty else pd.Series(dtype=float)
    if mes_obj in p.index and p.get(mes_obj, 0) > 0:
        out["refs"]["presupuesto"] = float(p[mes_obj])

    if out["refs"]:
        out["sugerido"] = sum(out["refs"].values()) / len(out["refs"])
    return out


# ── Render ────────────────────────────────────────────────────────────────────

def render(client, anio: int, mes: int):
    hoy = datetime.date.today()

    ppto = get_presupuesto(client, anio)
    hist = get_ventas_historicas(client)
    real = get_real_mensual(client, anio)

    for df, col in [(ppto, "monto"), (hist, "monto")]:
        if not df.empty:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    sin_datos = ppto.empty and hist.empty
    if sin_datos:
        st.info(
            "**Primera vez aquí:** ingresa el presupuesto del año y las ventas "
            "históricas en los editores de más abajo (⚙️ *Mantener datos*). "
            "Si al guardar sale un error de tabla inexistente, hay que correr "
            "`sql/017_presupuesto.sql` en el SQL Editor de Supabase (una vez)."
        )

    estac = _estacionalidad(hist)
    real_idx = real.set_index("mes") if not real.empty else pd.DataFrame()
    ppto_idx = ppto.set_index("mes")["monto"] if not ppto.empty else pd.Series(dtype=float)

    # ── 1. KPIs del mes seleccionado ─────────────────────────────────────────
    ppto_mes = float(ppto_idx.get(mes, 0))
    real_mes = float(real_idx["fact_nc"].get(mes, 0)) if not real_idx.empty else 0.0
    proy_mes = float(real_idx["proyeccion"].get(mes, 0)) if not real_idx.empty else 0.0
    es_mes_curso = (anio == hoy.year and mes == hoy.month)

    pct_ppto = real_mes / ppto_mes if ppto_mes else None
    pct_proy = proy_mes / ppto_mes if ppto_mes else None
    cls_p, cls_y = color_pct(pct_ppto), color_pct(pct_proy)

    st.markdown(f"""
    <div class="kpi-grid-4">
      <div class="kpi-card">
        <div class="kpi-label">Presupuesto {MESES_ABR[mes]}</div>
        <div class="kpi-value">{fmt_clp(ppto_mes) if ppto_mes else "—"}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Real Fact-NC</div>
        <div class="kpi-value">{fmt_clp(real_mes)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">% vs Presupuesto</div>
        <div class="kpi-value {cls_p}">{fmt_pct(pct_ppto)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">{"Proyección Cierre" if es_mes_curso else "Cierre del mes"}</div>
        <div class="kpi-value {cls_y if es_mes_curso else ''}">{fmt_clp(proy_mes if es_mes_curso else real_mes)}</div>
        <div class="kpi-sub">{("% Proy vs Ppto: <strong>" + fmt_pct(pct_proy) + "</strong>") if es_mes_curso else "mes cerrado"}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 2. Gráfico anual: Real vs Presupuesto vs años anteriores ─────────────
    st.markdown('<div class="seccion-titulo">Año completo — Real vs Presupuesto vs históricos</div>',
                unsafe_allow_html=True)

    meses_x = [MESES_ABR[m] for m in range(1, 13)]
    fig = go.Figure()

    # Real (solo meses transcurridos si es el año en curso)
    if not real.empty:
        r = real.copy()
        if anio == hoy.year:
            r = r[r["mes"] <= hoy.month]
        fig.add_bar(x=[MESES_ABR[m] for m in r["mes"]], y=r["fact_nc"],
                    name=f"Real {anio}", marker_color=C_REAL,
                    text=[fmt_clp(v) for v in r["fact_nc"]], textposition="none",
                    hovertemplate="%{x}: %{text}<extra>Real</extra>")

    if not ppto.empty:
        p = ppto.sort_values("mes")
        fig.add_scatter(x=[MESES_ABR[m] for m in p["mes"]], y=p["monto"],
                        name="Presupuesto", mode="lines+markers",
                        line=dict(color=C_PPTO, width=2.5, dash="dash"),
                        hovertemplate="%{x}: $%{y:,.0f}<extra>Presupuesto</extra>")

    if not hist.empty:
        anios_h = sorted(hist["anio"].unique(), reverse=True)[:2]
        for i, a in enumerate(anios_h):
            h = hist[hist["anio"] == a].sort_values("mes")
            fig.add_scatter(x=[MESES_ABR[m] for m in h["mes"]], y=h["monto"],
                            name=str(a), mode="lines+markers",
                            line=dict(color=[C_H1, C_H2][i], width=1.8),
                            marker=dict(size=5),
                            hovertemplate="%{x}: $%{y:,.0f}<extra>" + str(a) + "</extra>")

    fig.update_layout(
        height=380, margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(categoryorder="array", categoryarray=meses_x),
        yaxis=dict(tickformat=",.0f", gridcolor="#EEE"),
        legend=dict(orientation="h", y=1.08, x=0),
        separators=",.",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 3. Tabla mensual: Ppto / Real / % / interanual ───────────────────────
    st.markdown('<div class="seccion-titulo">Detalle mensual</div>',
                unsafe_allow_html=True)

    anios_h = sorted(hist["anio"].unique(), reverse=True)[:2] if not hist.empty else []
    h_idx = {a: hist[hist["anio"] == a].set_index("mes")["monto"] for a in anios_h}

    header = ("<th style='text-align:left'>Mes</th><th>Presupuesto</th><th>Real</th>"
              "<th title='Real / Presupuesto'>% Cumpl Ppto</th>")
    for a in anios_h:
        header += (f"<th>{a}</th>"
                   f"<th title='Crecimiento del Real {anio} vs {a}'>Δ% vs {a}</th>")

    rows, tot = "", {"p": 0.0, "r": 0.0, "h": {a: 0.0 for a in anios_h}}
    meses_mostrar = range(1, 13)
    for m in meses_mostrar:
        p_m = float(ppto_idx.get(m, 0))
        r_m = float(real_idx["fact_nc"].get(m, 0)) if not real_idx.empty else 0.0
        transcurrido = not (anio == hoy.year and m > hoy.month)
        pct = (r_m / p_m) if (p_m and transcurrido) else None
        cls = color_pct(pct) if (pct is not None and not (anio == hoy.year and m == hoy.month)) else ""
        badge = (" <span style='font-size:.7em;color:#999'>(en curso)</span>"
                 if (anio == hoy.year and m == hoy.month) else "")
        fila = (f"<td style='text-align:left'>{MESES_NOM[m]}{badge}</td>"
                f"<td>{fmt_clp(p_m) if p_m else '—'}</td>"
                f"<td>{fmt_clp(r_m) if (r_m and transcurrido) else '—'}</td>"
                f"<td class='{cls}'>{fmt_pct(pct) if pct is not None else '—'}</td>")
        for a in anios_h:
            h_m = float(h_idx[a].get(m, 0))
            delta = (r_m / h_m - 1) if (h_m and r_m and transcurrido) else None
            d_txt = ("<span style='color:#1A7F4B'>▲ " if (delta or 0) >= 0 else "<span style='color:#C0392B'>▼ ") \
                    + fmt_pct(abs(delta)) + "</span>" if delta is not None else "—"
            fila += f"<td>{fmt_clp(h_m) if h_m else '—'}</td><td>{d_txt}</td>"
            if transcurrido and r_m:
                tot["h"][a] += h_m
        rows += f"<tr>{fila}</tr>"
        if transcurrido:
            tot["p"] += p_m
            tot["r"] += r_m

    pct_tot = (tot["r"] / tot["p"]) if tot["p"] else None
    fila_t = (f"<td style='text-align:left'>TOTAL (a la fecha)</td>"
              f"<td>{fmt_clp(tot['p'])}</td><td>{fmt_clp(tot['r'])}</td>"
              f"<td class='{color_pct(pct_tot)}'>{fmt_pct(pct_tot)}</td>")
    for a in anios_h:
        d = (tot["r"] / tot["h"][a] - 1) if tot["h"][a] else None
        fila_t += (f"<td>{fmt_clp(tot['h'][a])}</td>"
                   f"<td>{fmt_pct(d) if d is not None else '—'}</td>")
    rows += f"<tr class='total-row'>{fila_t}</tr>"

    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems"><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>
    </div>
    """, unsafe_allow_html=True)
    st.caption("Δ% compara el Real del año actual contra el mismo mes del año histórico. "
               "El TOTAL considera solo meses ya transcurridos (comparación justa).")

    # ── 4. Estacionalidad ─────────────────────────────────────────────────────
    if not estac.empty:
        st.markdown('<div class="seccion-titulo">Estacionalidad histórica</div>',
                    unsafe_allow_html=True)
        col_g, col_t = st.columns([3, 2])
        with col_g:
            e = estac.reindex(range(1, 13)).fillna(0)
            colores = [C_REAL if m == mes else "#D8DEE9" for m in e.index]
            fig_e = go.Figure(go.Bar(
                x=[MESES_ABR[m] for m in e.index], y=(e * 100).round(1),
                marker_color=colores,
                text=[f"{v:.1f}%" for v in (e * 100)], textposition="outside",
                hovertemplate="%{x}: %{y:.1f}% del año<extra></extra>"))
            fig_e.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                                plot_bgcolor="white", paper_bgcolor="white",
                                yaxis=dict(visible=False), showlegend=False)
            st.plotly_chart(fig_e, use_container_width=True)
        with col_t:
            peso_mes = float(estac.get(mes, 0))
            prom = 1 / 12
            vs = peso_mes / prom - 1 if peso_mes else 0
            direc = "más fuerte" if vs >= 0 else "más débil"
            st.markdown(f"""
            <div class="nota-embudo">
              <p><strong>{MESES_NOM[mes]}</strong> representa históricamente
              <strong>{peso_mes*100:.1f}%</strong> de la venta anual —
              un {abs(vs)*100:.0f}% {direc} que un mes promedio (8,3%).</p>
              <p>El índice se calcula con los años históricos completos
              ingresados abajo. Úsalo para no fijar objetivos "planos": en
              helados el verano pesa mucho más que el invierno.</p>
            </div>
            """, unsafe_allow_html=True)

    # ── 5. Sugerencia de objetivo del próximo mes ─────────────────────────────
    st.markdown('<div class="seccion-titulo">Sugerencia de objetivo</div>',
                unsafe_allow_html=True)

    default_obj = min(hoy.month + 1, 12) if anio == hoy.year else 1
    mes_obj = st.selectbox("Mes a planificar", list(range(1, 13)),
                           index=default_obj - 1, format_func=lambda m: MESES_NOM[m],
                           key="ppto_mes_obj")

    sug = _sugerencia(real, hist, ppto, estac, anio, mes_obj, hoy)
    refs, base = sug["refs"], sug["base_meses"]

    if not refs:
        st.markdown(
            '<div class="estado-vacio">Aún no hay datos suficientes para sugerir '
            '(se necesita al menos presupuesto, o históricos + meses reales cerrados).</div>',
            unsafe_allow_html=True)
    else:
        base_txt = ", ".join(MESES_ABR[m] for m in base) if base else "—"
        nombres = {
            "estacionalidad": ("📉 Ritmo reciente × estacionalidad",
                               f"Venta de {base_txt} desestacionalizada y proyectada al peso histórico de {MESES_NOM[mes_obj]}"),
            "interanual": ("📆 Año pasado × crecimiento",
                           f"{MESES_NOM[mes_obj]} {anio-1} ajustado por el crecimiento reciente"
                           + (f" ({fmt_pct(sug.get('growth', 0) - 1 if sug.get('growth') else None)})"
                              if sug.get("growth") else "")),
            "presupuesto": ("🎯 Presupuesto definido",
                            f"Lo planificado para {MESES_NOM[mes_obj]} en el presupuesto anual"),
        }
        cards = ""
        for k, v in refs.items():
            t, d = nombres[k]
            cards += f"""
            <div class="kpi-card">
              <div class="kpi-label">{t}</div>
              <div class="kpi-value">{fmt_clp(v)}</div>
              <div class="kpi-sub">{d}</div>
            </div>"""
        cards += f"""
        <div class="kpi-card destacado">
          <div class="kpi-label">Sugerido {MESES_NOM[mes_obj]}</div>
          <div class="kpi-value">{fmt_clp(sug["sugerido"])}</div>
          <div class="kpi-sub">promedio de las {len(refs)} referencia(s)</div>
        </div>"""
        st.markdown(f'<div class="kpi-grid-4">{cards}</div>', unsafe_allow_html=True)
        st.caption("La decisión final sigue siendo de gerencia: los objetivos por vendedor "
                   "se cargan igual que siempre en **Panel Gerencia → Editar objetivos**. "
                   "Esto es solo la base cuantitativa.")

        # Reparto sugerido por vendedor (participación últimos meses cerrados)
        if base:
            part = get_participacion_vendedores(client, anio, base)
            if not part.empty:
                with st.expander(f"👥 Reparto sugerido por vendedor (según participación {base_txt})"):
                    part = part.copy()
                    part["obj_sug"] = (part["share"] * sug["sugerido"] / 100000).round() * 100000
                    tabla = part[["nombre_canonico", "fact_nc", "share", "obj_sug"]].copy()
                    tabla.columns = ["Vendedor", f"Fact-NC {base_txt}", "Participación", "Objetivo sugerido"]
                    tabla[f"Fact-NC {base_txt}"] = tabla[f"Fact-NC {base_txt}"].apply(fmt_clp)
                    tabla["Participación"] = tabla["Participación"].apply(fmt_pct)
                    tabla["Objetivo sugerido"] = tabla["Objetivo sugerido"].apply(fmt_clp)
                    st.dataframe(tabla, use_container_width=True, hide_index=True)
                    st.caption("Redondeado a $100.000. Es una referencia por participación "
                               "histórica — ajusta según cartera, máquinas nuevas o foco comercial.")

    # ── 6. Mantención de datos (editores) ─────────────────────────────────────
    st.markdown('<div class="seccion-titulo">⚙️ Mantener datos</div>',
                unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        with st.expander(f"✏️ Presupuesto {anio}", expanded=sin_datos):
            base_df = pd.DataFrame({"Mes": [MESES_NOM[m] for m in range(1, 13)]})
            base_df["Monto"] = [float(ppto_idx.get(m, 0)) for m in range(1, 13)]
            edit = st.data_editor(
                base_df, hide_index=True, use_container_width=True,
                disabled=["Mes"], key=f"ed_ppto_{anio}",
                column_config={"Monto": st.column_config.NumberColumn(
                    "Monto ($)", min_value=0, step=100000, format="%d")},
            )
            if st.button("Guardar presupuesto", key="btn_ppto", type="primary"):
                try:
                    for i, row in edit.iterrows():
                        upsert_presupuesto(client, anio, i + 1, float(row["Monto"] or 0))
                    st.success(f"Presupuesto {anio} guardado.")
                    st.rerun()
                except Exception as e:
                    _error_guardado(e)

    with col2:
        with st.expander("✏️ Ventas históricas (años anteriores)", expanded=sin_datos):
            anio_h = st.number_input("Año", min_value=2015, max_value=anio - 1,
                                     value=anio - 1, key="ppto_anio_hist")
            h_sel = (hist[hist["anio"] == anio_h].set_index("mes")["monto"]
                     if not hist.empty else pd.Series(dtype=float))
            base_h = pd.DataFrame({"Mes": [MESES_NOM[m] for m in range(1, 13)]})
            base_h["Monto"] = [float(h_sel.get(m, 0)) for m in range(1, 13)]
            edit_h = st.data_editor(
                base_h, hide_index=True, use_container_width=True,
                disabled=["Mes"], key=f"ed_hist_{anio_h}",
                column_config={"Monto": st.column_config.NumberColumn(
                    "Venta total ($)", min_value=0, step=100000, format="%d")},
            )
            if st.button("Guardar históricos", key="btn_hist", type="primary"):
                try:
                    for i, row in edit_h.iterrows():
                        upsert_venta_historica(client, int(anio_h), i + 1,
                                               float(row["Monto"] or 0))
                    st.success(f"Ventas {anio_h} guardadas.")
                    st.rerun()
                except Exception as e:
                    _error_guardado(e)

    st.caption("Solo se guarda el monto mensual total (12 números por año) — "
               "la base de datos no se sobrecarga con detalle histórico.")


def _error_guardado(e: Exception):
    msg = str(e)
    if "does not exist" in msg or "42P01" in msg or "schema cache" in msg:
        st.error("Las tablas de presupuesto aún no existen en Supabase. "
                 "Corre **sql/017_presupuesto.sql** en el SQL Editor "
                 "(Dashboard → SQL Editor → pegar el archivo → Run) y reintenta.")
    else:
        st.error(f"No se pudo guardar: {msg}")
