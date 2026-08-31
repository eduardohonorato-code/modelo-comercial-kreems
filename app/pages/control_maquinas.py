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
                               generar_alertas, semanal)

_C = {"azul": "#C01E6E", "chart": "#E62984", "verde": "#1A7F4B",
      "amrl": "#D4881E", "rojo": "#C0392B", "slate": "#64748B"}

# La meta se mide por semana, así que las semanas van primero. Las opciones de
# semana son relativas a HOY (lunes a domingo); las de mes, al período del
# sidebar, que es como se mira el cierre.
_RANGOS = {
    "Semana en curso": ("semana", 0),
    "Semana pasada": ("semana", 1),
    "Últimas 4 semanas": ("semana", 4),
    "Mes en curso": ("mes", 1),
    "Últimos 3 meses": ("mes", 3),
    "Últimos 6 meses": ("mes", 6),
    "Últimos 12 meses": ("mes", 12),
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


# Qué contesta cada indicador y de dónde sale el número, para la revisión
# tarjeta por tarjeta.
_AYUDA = [
    ("Pedidos ingresados por semana",
     "Cuánto pidió el vendedor. Es lo único que controla entero.",
     "Pedidos de flete de Autoventa, contados por su fecha de INGRESO."),
    ("Gestiones con DTE por semana",
     "El volumen que llega a documento emitido: la meta de las 22.",
     "Líneas FL facturadas en Obuma, contadas en la semana del documento. "
     "Instalación, cambio y retiro suman igual."),
    ("% Concretado (pedido a DTE)",
     "De lo que se ingresa, cuánto termina con documento.",
     "Pedidos con DTE ÷ pedidos ingresados. Los anulados o reingresados con "
     "otro número no cuentan."),
    ("Días de ingreso a DTE",
     "Cuánto espera un pedido hasta que se emite el documento.",
     "Mediana de días entre la fecha del pedido y la del DTE."),
    ("Cola vencida",
     "El atraso acumulado: pedidos que ya llevan más de un mes.",
     "Pedidos sin DTE con más de 30 días desde que se ingresaron. Salen todos "
     "los abiertos, no solo los del período."),
    ("% Entregado",
     "De lo que salió a ruta, cuánto se confirmó entregado.",
     "Entregadas ÷ movimientos con información de despacho. Lo que no tiene "
     "despacho cargado queda fuera del cálculo, no cuenta como fallado."),
    ("% Rechazo",
     "Cuánto vuelve del camión sin entregar.",
     "Rechazadas ÷ movimientos con información de despacho."),
    ("Conversión de instalación",
     "De las máquinas nuevas gestionadas, cuántas quedaron instaladas.",
     "Instalaciones (FL-4) entregadas ÷ instalaciones con información."),
    ("Parque neto",
     "Si el parque en la calle crece o cae.",
     "Instalaciones (FL-4) menos retiros (FL-2). Es el crecimiento, NO el "
     "parque total: las máquinas puestas antes de 2026 no están registradas."),
    ("Cobertura del cruce",
     "Cuánto del período se puede juzgar. Si esto cae, lo de terreno miente.",
     "Movimientos con despacho cruzado ÷ total. Acuña no pasa por Autoventa, "
     "así que sus movimientos nunca tienen despacho."),
]


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _rango(anio: int, mes: int):
    """(inicio, fin) del período elegido. Semanas de lunes a domingo."""
    tipo, n = _RANGOS.get(st.session_state.get("cm_rango", "Semana pasada"),
                          ("semana", 1))
    if tipo == "semana":
        hoy = datetime.date.today()
        lunes = hoy - datetime.timedelta(days=hoy.weekday())
        if n == 0:                       # semana en curso: hasta hoy
            return lunes, hoy
        if n == 1:                       # la semana cerrada anterior
            fin = lunes - datetime.timedelta(days=1)
            return fin - datetime.timedelta(days=6), fin
        fin = lunes - datetime.timedelta(days=1)   # n semanas cerradas
        return fin - datetime.timedelta(days=7 * n - 1), fin
    fin_mes = (datetime.date(anio + (mes // 12), (mes % 12) + 1, 1)
               - datetime.timedelta(days=1))
    ini_num = (anio * 12 + mes - 1) - (n - 1)
    return datetime.date(ini_num // 12, ini_num % 12 + 1, 1), fin_mes


# Los cuatro que van arriba, grandes: uno por etapa del recorrido. El resto vive
# en la tabla — diez tarjetas iguales no dejan ver cuál importa.
_DESTACADOS = ["gestiones_semana", "pct_concretado", "pct_entregado", "parque_neto"]

_COLOR_SEV = {"ok": "verde", "alerta": "amarillo", "critico": "rojo",
              "sin_meta": "gris"}
_TXT_SEV = {"ok": "en meta", "alerta": "cerca", "critico": "lejos",
            "sin_meta": "sin meta"}


def _barra(logro, sev: str, alto: str = ".45rem") -> str:
    """
    Barra de cumplimiento: el 100% es la meta, no el máximo de la escala.

    Se corta a 130% para que un indicador muy sobrecumplido no aplaste
    visualmente a los demás y la comparación entre filas siga sirviendo.
    """
    if logro is None:
        return ""
    pct = max(min(logro, 1.3), 0) / 1.3 * 100
    color = "var(--%s)" % _COLOR_SEV.get(sev, "gris")
    return (
        '<div style="background:var(--gris-light);border-radius:99px;'
        'height:%s;overflow:hidden;margin:.35rem 0 .2rem">'
        '<div style="width:%.0f%%;height:100%%;background:%s;'
        'border-radius:99px"></div></div>' % (alto, pct, color)
    )


def _tarjeta_destacada(k: dict) -> str:
    """Tarjeta grande: el número, contra qué se compara y cuán lejos está."""
    color = _COLOR_SEV.get(k["severidad"], "")
    cls = ("kpi-value " + color if color in ("verde", "rojo", "amarillo")
           else "kpi-value")
    meta = ("meta %s · <b>%s</b> de la meta" % (k["meta_txt"], k["logro_txt"])
            if k["meta"] is not None else "sin meta fijada")
    return (
        '<div class="kpi-card" style="text-align:left">'
        '<div class="kpi-label">%s</div>'
        '<div class="%s">%s</div>'
        '%s'
        '<div class="kpi-sub">%s</div>'
        '<div class="kpi-sub" style="opacity:.7">%s</div>'
        '</div>' % (k["nombre"], cls, k["valor_txt"],
                    _barra(k["logro"], k["severidad"], ".5rem"),
                    meta, k["detalle"])
    )


def _tabla_cumplimiento(kpis: list) -> str:
    """Todos los indicadores en una tabla que se lee de un vistazo."""
    filas = []
    for k in kpis:
        sev = k["severidad"]
        color = _COLOR_SEV.get(sev, "gris")
        chip = ('<span style="background:var(--%s);color:#fff;border-radius:99px;'
                'padding:.12rem .5rem;font-size:.66rem;font-weight:700;'
                'white-space:nowrap">%s</span>' % (color, _TXT_SEV[sev]))
        filas.append(
            '<tr style="border-bottom:1px solid var(--gris-light)">'
            '<td style="padding:.55rem .7rem"><b>%s</b>'
            '<div style="color:var(--gris);font-size:.72rem">%s · %s</div></td>'
            '<td style="padding:.55rem .7rem;text-align:right;'
            'font-variant-numeric:tabular-nums;font-weight:700;'
            'font-size:1rem">%s</td>'
            '<td style="padding:.55rem .7rem;text-align:right;'
            'font-variant-numeric:tabular-nums;color:var(--gris)">%s</td>'
            '<td style="padding:.55rem .7rem;min-width:130px">%s'
            '<div style="font-size:.7rem;color:var(--gris)">%s de la meta</div>'
            '</td>'
            '<td style="padding:.55rem .7rem;text-align:center">%s</td>'
            '</tr>' % (k["nombre"], k["grupo"], k["responsable"],
                       k["valor_txt"], k["meta_txt"],
                       _barra(k["logro"], sev), k["logro_txt"], chip)
        )
    return (
        '<div style="overflow-x:auto;background:var(--bg-card);'
        'border-radius:12px;box-shadow:var(--sombra)">'
        '<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
        '<thead><tr style="background:var(--rosa-deep);color:#fff">'
        '<th style="text-align:left;padding:.55rem .7rem">Indicador</th>'
        '<th style="text-align:right;padding:.55rem .7rem">Resultado</th>'
        '<th style="text-align:right;padding:.55rem .7rem">Meta</th>'
        '<th style="text-align:left;padding:.55rem .7rem">Cumplimiento</th>'
        '<th style="text-align:center;padding:.55rem .7rem">Estado</th>'
        '</tr></thead><tbody>' + "".join(filas) + '</tbody></table></div>'
    )


def _mapa_vendedor(mov) -> dict:
    """id → nombre, reutilizando lo que ya trae el detalle de movimientos."""
    if mov is None or mov.empty or "Vendedor" not in mov.columns:
        return {}
    return dict(mov.drop_duplicates("vendedor_id")
                .set_index("vendedor_id")["Vendedor"])


@st.cache_data(show_spinner=False, ttl=600)
def _dim_cliente_nombres(_client) -> dict:
    """RUT → razón social. Cosmético: si falla, la tabla sale con el RUT."""
    try:
        from app.data import get_dim_cliente_full
        d = get_dim_cliente_full(_client)
        return dict(zip(d["rut"], d["razon_social"])) if not d.empty else {}
    except Exception:
        return {}


def _nombre_cliente(client, ruts):
    return ruts.map(_dim_cliente_nombres(client)).fillna("(sin nombre)")


def _panel_alertas(alertas: list) -> str:
    """Las alertas ya vienen ordenadas por urgencia; se muestran las cinco primeras."""
    if not alertas:
        return ('<div class="estado-vacio">Todos los indicadores con meta están '
                'en verde.</div>')
    filas = []
    for a in alertas[:5]:
        color = _COLOR_SEV.get(a["severidad"], "gris")
        texto = a["texto"][0].upper() + a["texto"][1:]
        filas.append(
            '<div style="display:flex;gap:.7rem;padding:.6rem 0;'
            'border-bottom:1px solid var(--gris-light)">'
            '<div style="width:4px;border-radius:99px;background:var(--%s);'
            'flex:none"></div><div>'
            '<div style="font-weight:600;font-size:.88rem">%s</div>'
            '<div style="color:var(--gris);font-size:.78rem">%s · <b>%s</b></div>'
            '</div></div>' % (color, texto, a["accion"], a["responsable"])
        )
    return "".join(filas)


def _seccion_sin_dte(client, mov, ped, f_ini=None, f_fin=None):
    """
    La cola de pedidos ingresados que todavía no tienen documento.

    Va arriba, junto a las alertas, y no al final entre los gráficos: es la
    lista con la que efectivamente se trabaja, y enterrarla equivale a no
    tenerla. Muestra todo lo abierto hoy, sin importar el período elegido,
    porque un pedido trabado hace tres meses hay que verlo igual aunque se esté
    mirando la semana pasada.
    """
    cola = (ped[ped["_sin_dte"] & ~ped["_fantasma"]].copy()
            if ped is not None and not ped.empty else pd.DataFrame())
    st.divider()
    _sec("Pedidos sin DTE · %d esperando documento" % len(cola))
    if cola.empty:
        st.success("No hay pedidos esperando documento.")
    else:
        cola["Días"] = (pd.Timestamp(datetime.date.today())
                        - cola["_ingreso"]).dt.days
        c1, c2 = st.columns([38, 62])
        with c1:
            tramos = pd.cut(cola["Días"], [-1, 7, 30, 90, 10 ** 6],
                            labels=["Hasta 7 días", "8 a 30", "31 a 90",
                                    "Más de 90"])
            res = tramos.value_counts().reindex(
                ["Hasta 7 días", "8 a 30", "31 a 90", "Más de 90"]).fillna(0)
            fig3 = go.Figure(go.Bar(
                x=res.index.tolist(), y=res.values.tolist(),
                marker_color=[_C["verde"], _C["amrl"], _C["rojo"], "#7B241C"],
                text=res.values.tolist(), textposition="auto"))
            fig3.update_layout(height=290, margin=dict(l=0, r=0, t=10, b=0),
                               yaxis=dict(showgrid=False),
                               plot_bgcolor="rgba(0,0,0,0)",
                               paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig3, use_container_width=True)
            del_periodo = (int(cola["_ingreso"].between(
                pd.Timestamp(f_ini), pd.Timestamp(f_fin)).sum())
                if f_ini and f_fin else None)
            extra = (f" **{del_periodo} de ellos se ingresaron en el período "
                     f"elegido**; el resto viene de antes."
                     if del_periodo is not None else "")
            st.caption(f"{len(cola)} pedidos ingresados esperando que se emita "
                       "el documento. Salen todos los abiertos, sin importar el "
                       f"período: el de hace tres meses es el que más urge.{extra}")
        with c2:
            vmap = _mapa_vendedor(mov)
            detalle = pd.DataFrame({
                "Días": cola["Días"],
                "Fecha pedido": cola["_ingreso"].dt.date,
                "N° pedido": cola["n_pedido"],
                "Movimiento": cola["_mov"].map(
                    {"nueva": "Instalación", "cambio": "Cambio",
                     "retiro": "Retiro"}).fillna("(otro)"),
                "Vendedor": cola["vendedor_id"].map(vmap).fillna("Sin asignar"),
                "Cliente": _nombre_cliente(client, cola["cliente_rut"]),
                "RUT": cola["cliente_rut"],
            }).sort_values("Días", ascending=False)
            st.dataframe(detalle, use_container_width=True, hide_index=True,
                         height=290)
            st.download_button(
                "⬇️ Descargar la cola en CSV",
                detalle.to_csv(index=False).encode("utf-8-sig"),
                f"sin_dte_{datetime.date.today():%Y%m%d}.csv", "text/csv",
                key="dl_cola")



def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver el control de máquinas.")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        st.selectbox("Período", list(_RANGOS), key="cm_rango", index=1)
    f_ini, f_fin = _rango(anio, mes)
    dias_rango = (f_fin - f_ini).days + 1
    with c2:
        extra = ""
        if dias_rango < 7:
            extra = (f" La semana va a medias ({dias_rango} de 7 días), así que "
                     "el volumen todavía no se puede juzgar contra la meta.")
        st.caption(f"📅 {f_ini:%d/%m/%Y} → {f_fin:%d/%m/%Y}. Las metas son las "
                   f"vigentes para {mes:02d}/{anio}; se editan más abajo.{extra}")

    metas = get_objetivos_maquinas(client, anio, mes)
    with st.spinner("Cargando movimientos, pedidos y despachos…"):
        mov, ped, _ = cargar_todo(client, f_ini, f_fin)

    if mov is None or mov.empty:
        st.markdown('<div class="estado-vacio">Sin movimientos de máquinas en '
                    'el período.</div>', unsafe_allow_html=True)
        _editor_metas(client, anio, mes, metas)
        return

    kpis = calcular_kpis(mov, ped, f_ini, f_fin, metas)
    alertas = generar_alertas(mov, ped, kpis)
    en_meta = sum(1 for k in kpis if k["cumple"] is True)
    con_meta = sum(1 for k in kpis if k["cumple"] is not None)
    criticos = sum(1 for k in kpis if k["severidad"] == "critico")

    if metas.get("_origen") == "default":
        st.info("Todavía no hay metas guardadas: se están usando las de "
                "referencia. Fíjalas abajo para que queden registradas.")
    elif metas.get("_origen") == "heredado":
        st.caption("Metas heredadas de %02d/%d: este mes no tiene metas propias."
                   % (metas["mes"], metas["anio"]))

    # ── Titulares: uno por etapa del recorrido ───────────────────────────────
    dest = [k for c in _DESTACADOS for k in kpis if k["clave"] == c]
    st.markdown('<div class="kpi-grid-4">'
                + "".join(_tarjeta_destacada(k) for k in dest)
                + '</div>', unsafe_allow_html=True)

    # ── Qué requiere acción ──────────────────────────────────────────────────
    st.divider()
    _sec("Qué requiere acción" + (f" · {criticos} indicadores lejos de la meta"
                                  if criticos else ""))
    st.markdown(_panel_alertas(alertas), unsafe_allow_html=True)

    # ── La cola de sin DTE, pegada a las alertas ─────────────────────────────
    _seccion_sin_dte(client, mov, ped, f_ini, f_fin)

    # ── Todos los indicadores ────────────────────────────────────────────────
    st.divider()
    _sec(f"Todos los indicadores · {en_meta} de {con_meta} en meta")
    st.markdown(_tabla_cumplimiento(kpis), unsafe_allow_html=True)
    st.caption("El cumplimiento es la fracción de la meta alcanzada: 100% es "
               "estar en meta. En los indicadores donde menos es mejor "
               "—rechazo, días de gestión, cola— la razón va invertida, así que "
               "100% siempre significa lo mismo.")
    with st.expander("ℹ️ Cómo se calcula cada indicador", expanded=False):
        st.dataframe(pd.DataFrame(_AYUDA, columns=["Indicador", "Qué mide",
                                                   "De dónde sale"]),
                     use_container_width=True, hide_index=True)

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
    st.divider()
    _sec("Por qué vuelven rechazados")
    rech = mov[mov["_rechazada"]]
    if rech.empty:
        st.success("Sin rechazos en el período.")
    else:
        mot = rech["Motivo del rechazo"].value_counts().head(8)
        fig4 = go.Figure(go.Bar(
            x=mot.values.tolist(), y=mot.index.tolist(), orientation="h",
            marker_color=_C["rojo"], text=mot.values.tolist(),
            textposition="auto"))
        fig4.update_layout(height=290, margin=dict(l=0, r=0, t=10, b=0),
                           yaxis=dict(autorange="reversed"),
                           xaxis=dict(showgrid=False),
                           plot_bgcolor="rgba(0,0,0,0)",
                           paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig4, use_container_width=True)
        st.caption("Sale del comentario del repartidor: el campo de motivo del "
                   "ERP llega vacío.")

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
    """El editor va plegado: se toca una vez al mes y compite con los datos."""
    st.divider()
    exp = st.expander(f"🎯 Metas de {mes:02d}/{anio} — editar", expanded=False)
    with exp:
        st.caption("Se guardan por mes, igual que los objetivos de venta: "
                   "cambiar la meta de este mes no reescribe contra qué se "
                   "midieron los meses anteriores.")
        _form_metas(client, anio, mes, metas)


def _form_metas(client, anio: int, mes: int, metas: dict):
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
