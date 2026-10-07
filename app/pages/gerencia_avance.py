"""Panel Gerencia → pestaña "Avance de comisiones" (modelo nuevo, desde oct-2026).

Foto diaria del equipo con el cumplimiento proyectado de los 5 indicadores y la
comisión proyectada, más la ficha "Mi avance del mes" de cada vendedor. Todo se
descarga en PNG de alta calidad para mandar por WhatsApp. El cálculo vive en
app/avance_comisiones.py y las imágenes en app/export_avance.py.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.avance_comisiones import (INDICADORES, calcular_avance, corte_por_defecto,
                                   estado)
from app.export_avance import (SEM, clp, ficha_png, fichas_zip, nombre_archivo, num,
                               pct, subtitulo, tablero_png)
from app.styles import fmt_clp, fmt_pct

MESES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
         7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
         12: "Diciembre"}


@st.cache_data(ttl=300, show_spinner="Calculando el avance del mes…")
def _avance(_client, anio: int, mes: int, corte: date, _user: str):
    from app.pages.comisiones_v1 import _calcular
    base, _, _ = _calcular(_client, anio, mes)
    return calcular_avance(_client, anio, mes, corte, base)


def _celda(r, k) -> str:
    c = r.get(f"{k}_cumpl")
    bg, fg = SEM[estado(c, r.get(f"{k}_umbral", 0.8))]
    return (f"<td style='background:{bg};color:{fg};font-weight:700'>"
            f"{pct(c)}</td>")


def _tabla_html(df: pd.DataFrame) -> str:
    grupos = [("Cuota de venta", 3), ("Clientes nuevos", 3), ("Cobertura de ruta", 3),
              ("Efectividad de cartera", 3), ("Amplitud de SKU", 3), ("Comisión proyectada", 4)]
    g = "<th></th>" + "".join(f"<th colspan='{n}'>{t}</th>" for t, n in grupos)
    h = ("<th style='text-align:left'>Vendedor</th>"
         "<th>Venta</th><th>Meta</th><th title='Cumplimiento proyectado al cierre'>Proy.</th>"
         "<th>Llevas</th><th>Meta</th><th>Proy.</th>"
         "<th>Visitas</th><th title='Visitas programadas del mes'>Program.</th><th>Proy.</th>"
         "<th>Compraron</th><th>Cartera</th><th>Proy.</th>"
         "<th>SKU/cli.</th><th>Meta</th><th>Proy.</th>"
         "<th>Tasa</th><th>Comisión</th><th>Si cumple todo</th><th>Se deja</th>")
    filas = ""
    for _, r in df.iterrows():
        ruta_tip = ("reporte de Autoventa" if r["ruta_fuente"] == "reporte"
                    else "estimado (GPS y rutas de Autoventa)")
        filas += (
            f"<tr><td style='text-align:left'>{r['vendedor']}</td>"
            f"<td>{clp(r['cuota_llevas'])}</td><td>{clp(r['cuota_meta'])}</td>{_celda(r, 'cuota')}"
            f"<td>{num(r['nuevos_llevas'])}</td><td>{num(r['nuevos_meta'])}</td>{_celda(r, 'nuevos')}"
            f"<td title='{ruta_tip}'>{num(r['ruta_llevas'])}</td>"
            f"<td title='{ruta_tip}'>{num(r['ruta_agend'])}</td>{_celda(r, 'ruta')}"
            f"<td>{num(r['cobertura_llevas'])}</td><td>{num(r['cobertura_cartera'])}</td>"
            f"{_celda(r, 'cobertura')}"
            f"<td>{num(r['amplitud_llevas'], 1)}</td><td>{num(r['amplitud_meta'], 1)}</td>"
            f"{_celda(r, 'amplitud')}"
            f"<td>{pct(r['tasa_proy'], 2)}</td><td><strong>{clp(r['comision_proy'])}</strong></td>"
            f"<td>{clp(r['si_todo'])}</td><td>{clp(r['dejando'])}</td></tr>")
    vp = df["venta_proy"].sum()
    filas += (
        f"<tr class='total-row'><td style='text-align:left'>TOTAL EQUIPO</td>"
        f"<td>{clp(df['cuota_llevas'].sum())}</td><td>{clp(df['cuota_meta'].sum(min_count=1))}</td>"
        + "<td></td>" * 13
        + f"<td>{pct(df['comision_proy'].sum() / vp, 2) if vp else '—'}</td>"
          f"<td>{clp(df['comision_proy'].sum())}</td><td>{clp(df['si_todo'].sum())}</td>"
          f"<td>{clp(df['dejando'].sum())}</td></tr>")
    return (f"<div class='tabla-container'><table class='kreems'><thead><tr>{g}</tr>"
            f"<tr>{h}</tr></thead><tbody>{filas}</tbody></table></div>")


def render_avance(client, anio: int, mes: int):
    corte_def = corte_por_defecto(anio, mes)
    if corte_def is None:
        st.info(f"{MESES[mes]} {anio} todavía no empieza: no hay avance que mostrar.")
        return
    ini = date(anio, mes, 1)
    c1, c2 = st.columns([1, 3])
    corte = c1.date_input(
        "Fecha de corte", value=corte_def, min_value=ini, max_value=corte_def,
        format="DD-MM-YYYY", key=f"corte_avance_{anio}_{mes}",
        help="Último día con datos cargados. Por defecto, ayer: la carga diaria "
             "corre de madrugada.")

    df, ctx = _avance(client, anio, mes, corte, str(st.session_state.get("user_id", "")))
    if df is None or df.empty:
        st.info("Sin datos de comisiones para el período.")
        return
    if not ctx["dias_transcurridos"]:
        st.info("Todavía no hay días hábiles transcurridos en el mes: la proyección "
                "parte desde el primer día hábil.")
        return

    # Quiénes van en el panel: con objetivo de venta cargado; si aún no hay
    # objetivos del mes, los que ya facturaron algo (deja fuera a quien no vende).
    con_obj = df[df["cuota_meta"].fillna(0) > 0]
    defecto = (con_obj if not con_obj.empty else df[df["cuota_llevas"] > 0])["vendedor"]
    nombres = df["vendedor"].tolist()
    sel = c2.multiselect("Vendedores en el panel", nombres,
                         default=[n for n in nombres if n in set(defecto)],
                         key=f"vend_avance_{anio}_{mes}")
    d = df[df["vendedor"].isin(sel)].sort_values("comision_proy", ascending=False)
    if d.empty:
        st.info("Elige al menos un vendedor.")
        return

    st.caption(f"**{subtitulo(ctx)}** · Proyección lineal por días hábiles: lo que lleva "
               f"÷ {ctx['dias_transcurridos']} días × {ctx['dias_mes']} días. Los primeros "
               "3–4 días hábiles la proyección salta mucho; desde la 2ª semana es confiable.")

    avisos = []
    sin_obj = d[d["cuota_meta"].isna() | (d["cuota_meta"] == 0)]["vendedor"].tolist()
    if sin_obj:
        avisos.append(f"<strong>{len(sin_obj)} sin objetivo de venta</strong> del mes: "
                      f"la cuota no paga ni proyecta hasta cargarlo en <em>Editar objetivos "
                      f"del período</em> (abajo). → {', '.join(sin_obj)}.")
    if not ctx["visitas_habilitadas"]:
        avisos.append("<strong>Visitas sin carga automática:</strong> falta correr "
                      "<code>sql/045_fact_visitas.sql</code> en Supabase. Mientras tanto "
                      "la cobertura de ruta solo cuenta lo cargado desde el reporte de "
                      "Autoventa en Comisiones.")
    if avisos:
        st.markdown('<div class="nota-embudo" style="border-left-color:#f59e0b">⚠️ '
                    + "<br>".join(avisos) + "</div>", unsafe_allow_html=True)

    vp = d["venta_proy"].sum()
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">Comisión proyectada del equipo</div>
        <div class="kpi-value">{fmt_clp(d['comision_proy'].sum())}</div>
        <div class="kpi-sub">al ritmo de hoy</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Venta proyectada</div>
        <div class="kpi-value">{fmt_clp(vp)}</div>
        <div class="kpi-sub">meta: {fmt_clp(d['cuota_meta'].sum(min_count=1))}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Tasa proyectada</div>
        <div class="kpi-value">{fmt_pct(d['comision_proy'].sum() / vp if vp else None)}</div>
        <div class="kpi-sub">de un máximo de 5,00%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Se está dejando en la mesa</div>
        <div class="kpi-value">{fmt_clp(d['dejando'].sum())}</div>
        <div class="kpi-sub">si todos llegaran al 100%</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(_tabla_html(d), unsafe_allow_html=True)
    st.caption("Proy. = cumplimiento proyectado al cierre. Verde ≥ 100% · Amarillo = "
               "cobra parcial (desde el piso) · Rojo = bajo el piso, no cobra ese indicador. "
               "Visitas y programadas: reporte de Autoventa si está cargado en Comisiones; "
               "si no, estimadas (GPS de Autoventa, una visita por cliente por semana, y "
               "rutas de la cartera).")

    # ── Descargas PNG ───────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Imágenes para enviar por WhatsApp</div>',
                unsafe_allow_html=True)
    clave = f"{anio}_{mes:02d}_{corte.isoformat()}"
    col_t, col_f = st.columns(2)
    with col_t:
        st.markdown("**Tablero del equipo**")
        if st.button("🖼️ Generar tablero PNG", key=f"btn_tab_{clave}",
                     use_container_width=True):
            st.session_state[f"_png_tab_{clave}"] = tablero_png(d, ctx, anio, mes)
        png = st.session_state.get(f"_png_tab_{clave}")
        if png:
            st.download_button("⬇️ Descargar tablero", png,
                               f"avance_equipo_{corte.isoformat()}.png", "image/png",
                               key=f"dl_tab_{clave}", use_container_width=True)
        if st.button("📦 Generar todas las fichas (ZIP)", key=f"btn_zip_{clave}",
                     use_container_width=True):
            st.session_state[f"_zip_{clave}"] = fichas_zip(d, ctx)
        z = st.session_state.get(f"_zip_{clave}")
        if z:
            st.download_button("⬇️ Descargar fichas (ZIP)", z,
                               f"fichas_vendedores_{corte.isoformat()}.zip",
                               "application/zip", key=f"dl_zip_{clave}",
                               use_container_width=True)
    with col_f:
        st.markdown("**Ficha de un vendedor** (\"Mi avance del mes\")")
        quien = st.selectbox("Vendedor", d["vendedor"].tolist(), key=f"sel_ficha_{clave}",
                             label_visibility="collapsed")
        r = d[d["vendedor"] == quien].iloc[0]
        k_png = f"_png_ficha_{clave}_{r['vendedor_id']}"
        if st.button("🖼️ Generar ficha PNG", key=f"btn_ficha_{clave}",
                     use_container_width=True):
            st.session_state[k_png] = ficha_png(r, ctx)
        png = st.session_state.get(k_png)
        if png:
            st.download_button("⬇️ Descargar ficha", png, nombre_archivo(quien, ctx),
                               "image/png", key=f"dl_ficha_{clave}_{r['vendedor_id']}",
                               use_container_width=True)
    if png := st.session_state.get(k_png):
        st.image(png, use_container_width=True)

    with st.expander("ℹ️ Cómo se calcula cada número", expanded=False):
        st.markdown(f"""
        <div class="nota-embudo"><ul>
          <li><strong>Comisión = tasa × venta neta del mes.</strong> La tasa suma los 5
              indicadores (cuota 1,50% · nuevos 1,00% · efectividad 1,00% · amplitud 0,75%
              · ruta 0,75% = 5,00%). Cada uno paga desde su piso, proporcional a lo que
              cumple, con tope 100%. Es el mismo motor de <em>Comisiones → Propuesta</em>:
              las metas, pisos y definiciones se cambian ahí.</li>
          <li><strong>Proyección al cierre:</strong> lo acumulado ÷ días hábiles
              transcurridos × días hábiles del mes ({ctx['dias_transcurridos']} de
              {ctx['dias_mes']}). La amplitud es un promedio y se proyecta igual a lo que
              lleva; la efectividad se topa en el 100% de la cartera.</li>
          <li><strong>Cartera:</strong> clientes asignados en la cartera oficial de
              Autoventa. <strong>Compraron</strong>: clientes distintos con factura en el mes.</li>
          <li><strong>Visitas programadas:</strong> del reporte de Autoventa si se cargó
              en Comisiones; si no, desde la ruta de cada cliente de la cartera: semanal =
              1 por semana del mes ({ctx['semanas']} semanas), quincenal = la mitad,
              mensual = 1. Autoventa agenda por semana.</li>
          <li><strong>Visitas hechas:</strong> del reporte si está cargado; si no, el GPS de
              Autoventa contando una visita por cliente por semana (validado contra el
              reporte: 3–9% de diferencia). El dato oficial para pagar es el reporte de
              fin de mes.</li>
          <li><strong>Si cumple todo</strong> = 5% × la mayor entre la meta y la venta
              proyectada. <strong>Se deja</strong> = esa cifra − la comisión proyectada.</li>
        </ul></div>
        """, unsafe_allow_html=True)
