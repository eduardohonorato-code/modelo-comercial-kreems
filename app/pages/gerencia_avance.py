"""Panel Gerencia → vista "Avance de comisiones" (modelo nuevo, desde oct-2026).

Foto diaria del EQUIPO: cumplimiento de los 5 indicadores, lo que lleva cada
uno hoy, la proyección al cierre y la comisión proyectada, descargable en PNG.
Abajo, las metas del mes para editarlas sin salir del panel. La ficha individual
"Mi avance del mes" vive en el Panel Vendedor.

Rendimiento: el cálculo usa solo las ventas del mes (avance_mes), no la historia
desde 2024 (de ~70 s a ~4 s), y queda en caché hasta que cambie una meta.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from app.avance_comisiones import avance_mes, corte_por_defecto
from app.data import (get_comision_v1_parametros, get_objetivos, upsert_objetivo)
from app.export_avance import (fuentes_ruta, subtitulo, tabla_tablero, tablero_png,
                               _COLS_TABLERO, _GRUPOS_TABLERO)
from app.styles import fmt_clp, fmt_pct

MESES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
         7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
         12: "Diciembre"}


def firma_metas(client, anio: int, mes: int) -> str:
    """Huella de las metas vigentes (parámetros + objetivos del mes). Entra en la
    llave del caché: si gerencia cambia una meta, el avance se recalcula solo."""
    try:
        p = get_comision_v1_parametros(client)
        o = get_objetivos(client, anio, mes)
        o = o.drop(columns=[c for c in o.columns if "actualizado" in c], errors="ignore")
        return json.dumps([sorted(p.items()), o.to_dict("records")], default=str,
                          sort_keys=True)
    except Exception:
        return ""


def _version_calculo() -> str:
    """Huella del código del cálculo y de las imágenes. Va en la llave del caché:
    Streamlit solo invalida si cambia el cuerpo de la función cacheada, no los
    módulos que llama, y tras un deploy entregaba resultados viejos (KeyError
    'comision_hoy', 2026-10-07)."""
    import hashlib
    import inspect
    from app import avance_comisiones, export_avance
    from app.pages import comisiones_v1
    fuente = "".join(inspect.getsource(m) for m in
                     (avance_comisiones, export_avance, comisiones_v1))
    return hashlib.md5(fuente.encode("utf-8")).hexdigest()


@st.cache_data(ttl=600, show_spinner="Calculando el avance del mes…")
def _avance_cache(_client, anio: int, mes: int, corte: date, usuario: str, firma: str,
                  version: str):
    """`usuario` va en la llave a propósito: con RLS cada usuario ve datos
    distintos (un vendedor solo los suyos) y el caché no se puede compartir."""
    return avance_mes(_client, anio, mes, corte)


def avance_cacheado(client, anio: int, mes: int, corte: date, usuario: str, firma: str):
    return _avance_cache(client, anio, mes, corte, usuario, firma, _version_calculo())


def _usuario() -> str:
    return str(st.session_state.get("user_id", ""))


def _tabla_html(d: pd.DataFrame) -> str:
    disp, texto, fondo = tabla_tablero(d)
    cols = [k for k, _ in _COLS_TABLERO]
    g = "<th></th>"
    for titulo, color, c0, c1 in _GRUPOS_TABLERO:
        g += (f"<th colspan='{c1 - c0 + 1}' style='background:{color};color:white'>"
              f"{titulo.title()}</th>")
    # Marco del color de cada categoría: borde izquierdo en su 1ª columna y
    # derecho en la última, de arriba a abajo.
    borde, color_de = {}, {}
    for _t, color, c0, c1 in _GRUPOS_TABLERO:
        color_de.update({cols[j]: color for j in range(c0, c1 + 1)})
        borde[cols[c0]] = borde.get(cols[c0], "") + f"border-left:3px solid {color};"
        borde[cols[c1]] = borde.get(cols[c1], "") + f"border-right:3px solid {color};"
    h = "".join(f"<th style='{'text-align:left;' if k == 'vend' else ''}{borde.get(k, '')}'>"
                f"{t}</th>" for k, t in _COLS_TABLERO)
    filas = ""
    ult = len(disp) - 1
    for i, fila in disp.iterrows():
        tds = ""
        for k in cols:
            estilo = ("text-align:left;" if k == "vend" else "") + borde.get(k, "")
            if i == ult and k in color_de:
                estilo += f"border-bottom:3px solid {color_de[k]};"
            if (i, k) in fondo:
                estilo += f"background:{fondo[(i, k)]};color:{texto[(i, k)]};font-weight:700;"
            tds += f"<td style='{estilo}'>{fila[k]}</td>"
        filas += f"<tr{' class=total-row' if i == ult else ''}>{tds}</tr>"
    return (f"<div class='tabla-container'><table class='kreems'><thead><tr>{g}</tr>"
            f"<tr>{h}</tr></thead><tbody>{filas}</tbody></table></div>")


def render_avance(client, anio: int, mes: int):
    corte_def = corte_por_defecto(anio, mes)
    if corte_def is None:
        st.info(f"{MESES[mes]} {anio} todavía no empieza: no hay avance que mostrar.")
        return
    c1, c2 = st.columns([1, 3])
    corte = c1.date_input(
        "Fecha de corte", value=corte_def, min_value=date(anio, mes, 1),
        max_value=corte_def, format="DD-MM-YYYY", key=f"corte_avance_{anio}_{mes}",
        help="Último día con datos cargados. Por defecto, ayer: la carga diaria corre "
             "de madrugada.")

    df, ctx, base = avance_cacheado(client, anio, mes, corte, _usuario(),
                                    firma_metas(client, anio, mes))
    if df is None or df.empty:
        st.info("Sin datos de comisiones para el período.")
        return
    if not ctx["dias_transcurridos"]:
        st.info("Todavía no hay días hábiles transcurridos: la proyección parte el primer "
                "día hábil del mes.")
        _metas(client, anio, mes, df, base)
        return

    # Quiénes van: con objetivo de venta; si aún no hay objetivos, los que facturaron.
    con_obj = df[df["cuota_meta"].fillna(0) > 0]
    defecto = set((con_obj if not con_obj.empty else df[df["cuota_llevas"] > 0])["vendedor"])
    nombres = df["vendedor"].tolist()
    sel = c2.multiselect("Vendedores en el panel", nombres,
                         default=[n for n in nombres if n in defecto],
                         key=f"vend_avance_{anio}_{mes}")
    d = df[df["vendedor"].isin(sel)].sort_values("comision_proy", ascending=False)
    if d.empty:
        st.info("Elige al menos un vendedor.")
        _metas(client, anio, mes, df, base)
        return

    st.caption(f"**{subtitulo(ctx)}** · Proyección lineal por días hábiles: lo que lleva "
               f"÷ {ctx['dias_transcurridos']} días × {ctx['dias_mes']} días. Los primeros "
               "3–4 días hábiles la proyección salta mucho; desde la 2ª semana es confiable.")

    sin_obj = d[d["cuota_meta"].isna() | (d["cuota_meta"] == 0)]["vendedor"].tolist()
    avisos = []
    if sin_obj:
        avisos.append(f"<strong>{len(sin_obj)} sin objetivo de venta</strong>: cárgalo en "
                      f"<em>Metas del mes</em> (abajo) → {', '.join(sin_obj)}.")
    if not ctx["visitas_habilitadas"]:
        avisos.append("<strong>Visitas sin carga automática:</strong> falta correr "
                      "<code>sql/045_fact_visitas.sql</code>.")
    if avisos:
        st.markdown('<div class="nota-embudo" style="border-left-color:#f59e0b">⚠️ '
                    + "<br>".join(avisos) + "</div>", unsafe_allow_html=True)

    vp = d["venta_proy"].sum()
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">Comisión proyectada del equipo</div>
        <div class="kpi-value">{fmt_clp(d['comision_proy'].sum())}</div>
        <div class="kpi-sub">tasa {fmt_pct(d['comision_proy'].sum() / vp if vp else None)} · al ritmo de hoy</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Comisión que llevas</div>
        <div class="kpi-value">{fmt_clp(d['comision_hoy'].sum())}</div>
        <div class="kpi-sub">tasa {fmt_pct(d['comision_hoy'].sum() / d['cuota_llevas'].sum() if d['cuota_llevas'].sum() else None)} · si el mes cerrara hoy</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact-NC que llevas</div>
        <div class="kpi-value">{fmt_clp(d['cuota_llevas'].sum())}</div>
        <div class="kpi-sub">meta: {fmt_clp(d['cuota_meta'].sum(min_count=1))}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact-NC proyectada</div>
        <div class="kpi-value">{fmt_clp(vp)}</div>
        <div class="kpi-sub">{fmt_pct(vp / d['cuota_meta'].sum() if d['cuota_meta'].sum() else None)} de la meta</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(_tabla_html(d), unsafe_allow_html=True)
    st.caption("**Cuota:** % proy. = Fact-NC proyectada ÷ meta. **Resto (Llevas):** lo acumulado; "
               "el color dice si, al ritmo actual, ese indicador cobra al cierre (verde ≥ 100% "
               "· amarillo = cobra parcial desde el piso · rojo = no cobra). **Comisión que llevas** = "
               "si el mes cerrara hoy · **Proyectada** = al ritmo de hoy hasta fin de mes. "
               "Clientes nuevos = máquinas nuevas (FL-4) facturadas. " + fuentes_ruta(d))

    # La versión del cálculo va en la clave: un PNG generado antes de un deploy no
    # debe seguir ofreciéndose con el formato viejo.
    clave = f"{anio}_{mes:02d}_{corte.isoformat()}_{_version_calculo()[:8]}"
    if st.button("🖼️ Generar tablero del equipo en PNG", key=f"btn_tab_{clave}"):
        st.session_state[f"_png_tab_{clave}"] = tablero_png(d, ctx, anio, mes)
    png = st.session_state.get(f"_png_tab_{clave}")
    if png:
        st.download_button("⬇️ Descargar tablero (PNG)", png,
                           f"avance_equipo_{corte.isoformat()}.png", "image/png",
                           key=f"dl_tab_{clave}")
    st.caption("La ficha individual de cada vendedor (\"Mi avance del mes\") está en el "
               "**Panel Vendedor**.")

    _metas(client, anio, mes, df, base)

    with st.expander("ℹ️ Cómo se calcula cada número", expanded=False):
        st.markdown(f"""
        <div class="nota-embudo"><ul>
          <li><strong>Comisión = tasa × venta neta del mes.</strong> La tasa suma los 5
              indicadores (cuota 1,50% · nuevos 1,00% · efectividad 1,00% · ruta 0,75% ·
              SKU 0,75% = 5,00%). Cada uno paga desde su piso, proporcional, tope 100%.</li>
          <li><strong>Proyección al cierre:</strong> lo acumulado ÷ días hábiles
              transcurridos × días hábiles del mes ({ctx['dias_transcurridos']} de
              {ctx['dias_mes']}). SKU es un promedio y no se proyecta; la efectividad se
              topa en el 100% de la cartera.</li>
          <li><strong>Clientes nuevos</strong> = máquinas nuevas (FL-4) facturadas en el
              mes, igual que "Máq. ingresadas"; meta = objetivo de máquinas.</li>
          <li><strong>Cartera:</strong> cartera oficial de Autoventa. <strong>Compraron</strong>:
              clientes distintos con factura en el mes.</li>
          <li><strong>Visitas programadas:</strong> reporte de Autoventa cargado en Comisiones
              → si no, "Visitas programadas" de las metas del mes → si no, estimadas con la
              ruta de cada cliente (semanal = 1 por semana, quincenal = la mitad, mensual =
              1; 4% de error contra el reporte de septiembre).</li>
          <li><strong>Visitas hechas:</strong> GPS de Autoventa, una por cliente por semana
              (3,5–6,8% de diferencia con el reporte). Para pagar manda el reporte oficial.</li>
        </ul></div>
        """, unsafe_allow_html=True)


# ── Metas del mes (editables aquí mismo) ────────────────────────────────────
def _metas(client, anio: int, mes: int, df: pd.DataFrame, base: pd.DataFrame):
    from app.pages.comisiones_v1 import _editor_metas_generales, _editor_umbrales

    st.markdown('<div class="seccion-titulo">🎯 Metas del mes</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="nota-embudo">📅 <strong>Día 1 de cada mes:</strong> exportar de '
        'Autoventa <em>clientes.csv</em> y <em>direcciones_despacho.csv</em> y recargar la '
        'cartera (<code>python -m etl.cargar_cartera "&lt;clientes.csv&gt;" '
        '"&lt;direcciones.csv&gt;" --reemplazar</code>). Esa foto de clientes, vendedores y '
        'rutas es la base de la efectividad de cartera y de las visitas programadas del mes. '
        'Hacerlo solo el día 1: recargar a mitad de mes cambia el cálculo del mes en curso y '
        'de los meses pasados que se vuelvan a abrir. Después, fijar las metas aquí abajo. '
        'Guía completa: <code>docs/rutina_dia_1_comisiones.md</code>.</div>',
        unsafe_allow_html=True)
    tab_v, tab_g, tab_p = st.tabs(["Por vendedor", "Generales (%, SKU)", "Piso de pago"])

    with tab_v:
        st.caption(f"Metas de **{MESES[mes]} {anio}** por vendedor. La meta de clientes "
                   "nuevos es el objetivo de máquinas. \"Visitas programadas\": si cargas "
                   "aquí el total del mes que muestra Autoventa, el panel lo usa en vez de "
                   "estimarlo (0 = estimar por ruta).")
        obj = get_objetivos(client, anio, mes)
        ids = df[["vendedor_id", "vendedor"]].copy()
        if not obj.empty:
            ids = ids.merge(obj[["vendedor_id", "obj_venta", "obj_maquinas", "obj_visitas"]],
                            on="vendedor_id", how="left")
        for c in ("obj_venta", "obj_maquinas", "obj_visitas"):
            if c not in ids.columns:
                ids[c] = 0
            ids[c] = pd.to_numeric(ids[c], errors="coerce").fillna(0).astype(int)
        ids = ids.sort_values("vendedor").reset_index(drop=True)
        ed = st.data_editor(
            ids.drop(columns=["vendedor_id"]), hide_index=True, use_container_width=True,
            disabled=["vendedor"], key=f"ed_metas_{anio}_{mes}",
            column_config={
                "vendedor": st.column_config.TextColumn("Vendedor"),
                "obj_venta": st.column_config.NumberColumn(
                    "Objetivo de venta ($)", min_value=0, step=100000, format="%d"),
                "obj_maquinas": st.column_config.NumberColumn(
                    "Clientes nuevos (máquinas)", min_value=0, step=1),
                "obj_visitas": st.column_config.NumberColumn(
                    "Visitas programadas", min_value=0, step=1,
                    help="Total del mes según Autoventa. 0 = estimar por la ruta."),
            })
        if st.button("💾 Guardar metas por vendedor", type="primary",
                     key=f"save_metas_{anio}_{mes}"):
            n = 0
            try:
                for i, r in ed.iterrows():
                    orig = ids.iloc[i]
                    if any(int(r[c] or 0) != int(orig[c]) for c in
                           ("obj_venta", "obj_maquinas", "obj_visitas")):
                        upsert_objetivo(client, int(orig["vendedor_id"]), anio, mes,
                                        float(r["obj_venta"] or 0), int(r["obj_maquinas"] or 0),
                                        int(r["obj_visitas"] or 0))
                        n += 1
                st.success(f"✅ {n} vendedor(es) actualizados." if n else "Sin cambios.")
                if n:
                    st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")

    with tab_g:
        _editor_metas_generales(client, anio, mes)

    with tab_p:
        _editor_umbrales(client, base)
