"""
Control de Máquinas — los indicadores del comodato contra su meta.

Es la contraparte del Panel Gerencia, pero para las máquinas: en vez de medir la
facturación de cada vendedor, mide el recorrido de una máquina y en qué etapa se
está cayendo.

    pedido ingresado  →  DTE emitido  →  sale a ruta  →  entregada
     (vendedor)          (logística)     (logística)     (terreno)

Las metas se editan acá mismo (rol gerencia) y quedan guardadas por mes, igual
que los objetivos de venta: así el histórico conserva contra qué se medía en
cada momento.
"""
import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.auth import es_gerencia
from app.data import (get_objetivos_maquinas, upsert_objetivos_maquinas,
                      get_dim_cliente_full)
from app.kpis_maquinas import (cargar_todo, calcular_kpis, calcular_flujo,
                               semanal)

_C = {"azul": "#C01E6E", "chart": "#E62984", "verde": "#1A7F4B",
      "amrl": "#D4881E", "rojo": "#C0392B", "slate": "#64748B"}

_RANGOS = {
    "Mes en curso": 1,
    "Últimos 3 meses": 3,
    "Últimos 6 meses": 6,
    "Últimos 12 meses": 12,
}

# Metas editables: (clave, etiqueta, tipo, ayuda)
_CAMPOS_META = [
    ("meta_gestiones_semana", "Gestiones con DTE por semana", "int",
     "Meta del equipo completo, no por vendedor. Instalación, cambio y retiro suman igual."),
    ("meta_pedidos_semana", "Pedidos ingresados por semana", "int",
     "Lo único que el vendedor controla entero. Déjalo en 0 si no quieres fijarle meta."),
    ("meta_pct_concretado", "% Concretado (pedido a DTE)", "pct",
     "De lo que se ingresa, cuánto termina con documento emitido."),
    ("meta_dias_gestion", "Días de ingreso a DTE (mediana)", "int",
     "Cuánto puede esperar un pedido antes de que se emita el documento."),
    ("meta_cola_vencida", "Cola vencida (más de 30 días)", "int",
     "Cuántos pedidos sin DTE de más de un mes se toleran."),
    ("meta_pct_entregado", "% Entregado", "pct",
     "De lo despachado, cuánto se confirma entregado en terreno."),
    ("meta_pct_rechazo", "% Rechazo", "pct",
     "Tope de despachos que vuelven rechazados."),
    ("meta_conversion_inst", "Conversión de instalación", "pct",
     "De las instalaciones gestionadas, cuántas se confirman en terreno."),
    ("meta_parque_neto", "Parque neto por mes", "int",
     "Instalaciones menos retiros. Cero significa 'que no siga cayendo'."),
]


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _rango(anio: int, mes: int):
    meses = _RANGOS.get(st.session_state.get("cm_rango", "Últimos 3 meses"), 3)
    fin_mes = datetime.date(anio + (mes // 12), (mes % 12) + 1, 1) - datetime.timedelta(days=1)
    ini_num = (anio * 12 + mes - 1) - (meses - 1)
    return datetime.date(ini_num // 12, ini_num % 12 + 1, 1), fin_mes


def _tarjeta(k: dict) -> str:
    """Tarjeta de indicador con el semáforo de su meta."""
    if k["cumple"] is True:
        color, chip, chip_cls = "verde", "en meta", "verde"
    elif k["cumple"] is False:
        color, chip, chip_cls = "rojo", f"meta {k['meta_txt']}", "rojo"
    else:
        color, chip, chip_cls = "", "sin meta", "slate"
    return (
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{k["nombre"]}</div>'
        f'<div class="kpi-value {color}">{k["valor_txt"]}</div>'
        f'<div class="kpi-sub"><span style="color:var(--{chip_cls},#64748B)">'
        f'{chip}</span> · {k["detalle"]}</div>'
        f'<div class="kpi-sub" style="opacity:.65">{k["responsable"]}</div>'
        f'</div>'
    )


def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver el control de máquinas.")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        st.selectbox("Período", list(_RANGOS), key="cm_rango", index=1)
    f_ini, f_fin = _rango(anio, mes)
    with c2:
        st.caption(f"📅 {f_ini:%d/%m/%Y} → {f_fin:%d/%m/%Y}. Las metas son las "
                   f"vigentes para {mes:02d}/{anio}; se editan más abajo.")

    metas = get_objetivos_maquinas(client, anio, mes)
    with st.spinner("Cargando movimientos, pedidos y despachos…"):
        mov, ped, _ = cargar_todo(client, f_ini, f_fin)

    if mov is None or mov.empty:
        st.markdown('<div class="estado-vacio">Sin movimientos de máquinas en '
                    'el período.</div>', unsafe_allow_html=True)
        _editor_metas(client, anio, mes, metas)
        return

    kpis = calcular_kpis(mov, ped, f_ini, f_fin, metas)
    en_meta = sum(1 for k in kpis if k["cumple"] is True)
    con_meta = sum(1 for k in kpis if k["cumple"] is not None)

    if metas.get("_origen") == "default":
        st.info("Todavía no hay metas guardadas: se están usando las de "
                "referencia. Fíjalas abajo para que queden registradas.")
    elif metas.get("_origen") == "heredado":
        st.caption(f"Metas heredadas de {metas['mes']:02d}/{metas['anio']}: este "
                   "mes no tiene metas propias.")

    _sec(f"Indicadores · {en_meta} de {con_meta} en meta")
    for grupo in ("Volumen", "Gestión", "Terreno", "Resultado", "Control del dato"):
        del_grupo = [k for k in kpis if k["grupo"] == grupo]
        if not del_grupo:
            continue
        st.markdown(f'<p class="nav-section-label">{grupo}</p>',
                    unsafe_allow_html=True)
        st.markdown('<div class="kpi-grid">' + "".join(_tarjeta(k) for k in del_grupo)
                    + '</div>', unsafe_allow_html=True)

    # ── El recorrido ─────────────────────────────────────────────────────────
    st.divider()
    _sec("El recorrido de una máquina")
    flujo = calcular_flujo(mov, ped, f_ini, f_fin)
    fig = go.Figure(go.Bar(
        x=[f["valor"] for f in flujo],
        y=[f["etapa"] for f in flujo],
        orientation="h",
        marker_color=[_C["chart"], _C["azul"], _C["slate"], _C["verde"]],
        text=[f'{f["valor"]}' for f in flujo], textposition="auto",
        hovertext=[f'{f["responsable"]} · {f["detalle"]}' for f in flujo],
        hoverinfo="text",
    ))
    fig.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=10),
                      yaxis=dict(autorange="reversed"),
                      xaxis=dict(showgrid=False),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("La caída entre dos barras es dónde se está perdiendo: pedidos "
               "que no llegan a documento, documentos que no salen a ruta, "
               "despachos que no se entregan.")

    # ── Semana a semana ──────────────────────────────────────────────────────
    _sec("Gestiones por semana contra la meta")
    sem = semanal(mov, ped, f_ini, f_fin, metas.get("meta_gestiones_semana"))
    if not sem.empty:
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(x=sem["Semana"], y=sem["Gestiones con DTE"],
                              name="Gestiones con DTE", marker_color=_C["chart"]))
        fig2.add_trace(go.Bar(x=sem["Semana"], y=sem["Pedidos ingresados"],
                              name="Pedidos ingresados", marker_color=_C["slate"],
                              opacity=.55))
        meta_v = metas.get("meta_gestiones_semana")
        if meta_v:
            fig2.add_hline(y=meta_v, line_dash="dash", line_color=_C["verde"],
                           annotation_text=f"meta {meta_v}",
                           annotation_position="top left")
        fig2.update_layout(barmode="group", height=330,
                           margin=dict(l=0, r=0, t=10, b=0),
                           legend=dict(orientation="h", y=-0.25),
                           yaxis=dict(showgrid=False),
                           plot_bgcolor="rgba(0,0,0,0)",
                           paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig2, use_container_width=True)

    # ── Lo que está trabado ──────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        _sec("Sin gestionar, por antigüedad")
        if ped is not None and not ped.empty:
            cola = ped[ped["_sin_dte"] & ~ped["_fantasma"]].copy()
            if cola.empty:
                st.success("No hay pedidos esperando documento.")
            else:
                cola["Días"] = (pd.Timestamp(datetime.date.today())
                                - cola["_ingreso"]).dt.days
                tramos = pd.cut(cola["Días"], [-1, 7, 30, 90, 10**6],
                                labels=["Hasta 7 días", "8 a 30", "31 a 90",
                                        "Más de 90"])
                res = tramos.value_counts().reindex(
                    ["Hasta 7 días", "8 a 30", "31 a 90", "Más de 90"]).fillna(0)
                fig3 = go.Figure(go.Bar(
                    x=res.index.tolist(), y=res.values.tolist(),
                    marker_color=[_C["verde"], _C["amrl"], _C["rojo"], "#7B241C"],
                    text=res.values.tolist(), textposition="auto"))
                fig3.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0),
                                   yaxis=dict(showgrid=False),
                                   plot_bgcolor="rgba(0,0,0,0)",
                                   paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig3, use_container_width=True)
                st.caption(f"{len(cola)} pedidos ingresados esperando que se "
                           "emita el documento.")
    with col2:
        _sec("Por qué vuelven rechazados")
        rech = mov[mov["_rechazada"]]
        if rech.empty:
            st.success("Sin rechazos en el período.")
        else:
            mot = rech["Motivo del rechazo"].value_counts().head(7)
            fig4 = go.Figure(go.Bar(
                x=mot.values.tolist(), y=mot.index.tolist(), orientation="h",
                marker_color=_C["rojo"], text=mot.values.tolist(),
                textposition="auto"))
            fig4.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0),
                               yaxis=dict(autorange="reversed"),
                               xaxis=dict(showgrid=False),
                               plot_bgcolor="rgba(0,0,0,0)",
                               paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig4, use_container_width=True)
            st.caption("Sale del comentario del repartidor: el campo de motivo "
                       "del ERP llega vacío.")

    # ── Informe ──────────────────────────────────────────────────────────────
    st.divider()
    _sec("Informe para gerencia")
    st.caption("Ocho hojas: el tablero con las metas, el flujo mes a mes y "
               "semana a semana, lo que sigue sin gestionar, el estado de los "
               "despachos y los rechazos. El informe completo de 19 hojas sigue "
               "en Análisis → Máquinas.")
    if st.button("📘 Generar informe de gerencia", type="primary",
                 key="btn_informe_gerencia"):
        with st.spinner("Armando el informe…"):
            from app.export_maquinas_gerencia import libro_gerencia
            try:
                cli_dim = get_dim_cliente_full(client)
            except Exception:
                cli_dim = None
            data = libro_gerencia(mov, ped, f_ini, f_fin, metas,
                                  st.session_state.get("cm_soc", "Ambas"),
                                  cli_dim)
        st.session_state["_cm_libro"] = ((str(f_ini), str(f_fin)), data)
    guardado = st.session_state.get("_cm_libro")
    if guardado and guardado[0] == (str(f_ini), str(f_fin)):
        st.download_button(
            "⬇️ Descargar informe de gerencia", guardado[1],
            f"maquinas_gerencia_{f_ini:%Y%m%d}_{f_fin:%Y%m%d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, key="dl_cm")

    _editor_metas(client, anio, mes, metas)


def _editor_metas(client, anio: int, mes: int, metas: dict):
    st.divider()
    _sec(f"Metas de {mes:02d}/{anio}")
    st.caption("Se guardan por mes, igual que los objetivos de venta: cambiar "
               "la meta de este mes no reescribe contra qué se midieron los "
               "meses anteriores.")
    with st.form("form_metas_maquinas"):
        cols = st.columns(3)
        nuevos = {}
        for i, (clave, etiqueta, tipo, ayuda) in enumerate(_CAMPOS_META):
            actual = metas.get(clave)
            with cols[i % 3]:
                if tipo == "pct":
                    nuevos[clave] = st.number_input(
                        etiqueta, min_value=0.0, max_value=100.0,
                        value=float((actual or 0) * 100), step=1.0,
                        help=ayuda, key=f"meta_{clave}") / 100
                else:
                    nuevos[clave] = st.number_input(
                        etiqueta, value=int(actual or 0), step=1,
                        help=ayuda, key=f"meta_{clave}")
        guardar = st.form_submit_button("💾 Guardar metas", type="primary",
                                        use_container_width=True)
    if guardar:
        # 0 en una meta opcional significa "sin meta", no "meta cero": para los
        # porcentajes y los conteos que sí admiten cero (cola vencida, parque
        # neto) el cero es un valor legítimo y se guarda.
        opcionales = {"meta_pedidos_semana"}
        valores = {k: (None if (k in opcionales and not v) else v)
                   for k, v in nuevos.items()}
        try:
            upsert_objetivos_maquinas(client, anio, mes, valores)
            st.success(f"Metas de {mes:02d}/{anio} guardadas.")
            st.rerun()
        except Exception as exc:
            st.error(f"No se pudieron guardar: {exc}")
