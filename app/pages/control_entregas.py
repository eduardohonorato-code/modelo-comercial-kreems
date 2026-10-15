"""
Control de Entregas — cuánto se demora un pedido desde que el vendedor lo
ingresa hasta que el camión lo cierra, y qué pasa con los rechazos.

Nace en oct-2026 aparte de Control de Máquinas para no mezclar: allá la
pregunta es la gestión comercial del comodato (¿se movieron las máquinas?);
aquí es la operación logística (¿cuánto tarda y cuánto vuelve?). Cuatro vistas:

    🍦 Pedidos de helados  · tiempo ingreso → DTE → ruta, % a tiempo, abiertos
    🧊 Máquinas            · lo mismo para los pedidos de flete (FL-x)
    ↩️ Rechazos de helados · cuántos, por qué, y cuánto tardan la NC y la
                             siguiente entrega al cliente
    🚛 Por transportista   · % de entrega en pesos y en unidades (la tabla que
                             vivía plegada en Control de Máquinas)

Las tres primeras cuentan sobre los pedidos INGRESADOS en el período (un solo
grupo: el tiempo, el % a tiempo, los rechazos y los abiertos son esos mismos
pedidos). La de transportistas mide por FECHA DE RUTA, que es la definición
oficial del % de entrega (gerencia, sep-2026). El cálculo vive en
`app/kpis_entregas.py`.
"""
import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.frescos import refrescar

# Tras un deploy, Streamlit Cloud puede seguir con app.data viejo en memoria
# (ImportError de get_pedidos_rango el 15-10-2026). Ver app/frescos.py.
refrescar({"app.data": ("get_pedidos_rango", "get_feriados"),
           "app.kpis_entregas": ("SLA_MAQ_DEFAULT",)})

from app.auth import es_gerencia  # noqa: E402
from app.styles import fmt_clp  # noqa: E402
from app.kpis_maquinas import etiqueta_mov  # noqa: E402
from app.kpis_entregas import (ABIERTOS, CERRADOS, DIAS_ALERTA_NC, EN_RUTA, ENTREGADO,  # noqa: E402
                               NC_EXACTA, RECHAZADO, SIN_DTE, SIN_NC, SIN_RUTA,
                               SLA_DIAS_DEFAULT, SLA_MAQ_DEFAULT, distribucion,
                               preparar_pedidos, resumen, semanal,
                               seguimiento_rechazos_productos, tabla_por)

_C = {"verde": "#1A7F4B", "amrl": "#D4881E", "rojo": "#C0392B",
      "gris": "#9CA3AF", "rosa": "#E62984", "slate": "#64748B",
      "azul": "#1B3A6B"}

_RANGOS = {
    "Semana pasada": ("semana", 1),
    "Semana en curso": ("semana", 0),
    "Mes en curso": ("mes", 0),
    "Mes anterior": ("mes", 1),
    "Últimas 4 semanas": ("ult", 4),
    "Últimos 3 meses": ("ult", 13),
    "Rango personalizado": ("pers", 0),
}
# Primer mes con fecha de ingreso del pedido y despachos de Autoventa.
_INICIO = datetime.date(2026, 2, 1)
_SEMANAS_TENDENCIA = 8

_VISTAS = {
    "helados": "🍦 Pedidos de helados",
    "maquinas": "🧊 Máquinas",
    "rechazos": "↩️ Rechazos de helados",
    "transportista": "🚛 Por transportista",
}


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _rango():
    tipo, n = _RANGOS.get(st.session_state.get("ce_rango", "Semana pasada"),
                          ("semana", 1))
    hoy = datetime.date.today()
    un_dia = datetime.timedelta(days=1)
    if tipo == "pers":
        sel = st.session_state.get("ce_rango_pers")
        if isinstance(sel, (list, tuple)) and len(sel) == 2:
            return sel[0], sel[1]
        ini = (sel[0] if isinstance(sel, (list, tuple)) and sel
               else hoy - datetime.timedelta(days=27))
        return ini, max(ini, hoy)
    if tipo == "mes":
        primero = hoy.replace(day=1)
        for _ in range(n):
            primero = (primero - un_dia).replace(day=1)
        sgte = (primero.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return primero, sgte - un_dia
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    if tipo == "ult":
        return lunes - datetime.timedelta(weeks=n), hoy
    ini = lunes - datetime.timedelta(days=7 * n)
    return ini, ini + datetime.timedelta(days=6)


def _tarjeta(titulo, valor, color, linea1, linea2="", barra=None):
    barra_html = ""
    if barra is not None:
        ancho = max(min(barra, 1.0), 0) * 100
        barra_html = (
            '<div style="background:var(--gris-light);border-radius:99px;'
            'height:.5rem;overflow:hidden;margin:.45rem 0 .25rem">'
            f'<div style="width:{ancho:.0f}%;height:100%;background:{color};'
            'border-radius:99px"></div></div>')
    return (
        '<div class="kpi-card" style="text-align:left;padding:1.1rem 1.2rem">'
        f'<div class="kpi-label">{titulo}</div>'
        f'<div class="kpi-value" style="color:{color};font-size:2.2rem">{valor}</div>'
        f'{barra_html}'
        f'<div class="kpi-sub" style="font-size:.8rem">{linea1}</div>'
        + (f'<div class="kpi-sub" style="font-size:.74rem;opacity:.75">{linea2}</div>'
           if linea2 else "")
        + '</div>')


def _dias(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:.1f}".replace(".0", "").replace(".", ",")


def _pct(x, dec: int = 0) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{dec}f}%".replace(".", ",")


@st.cache_data(show_spinner=False, ttl=300)
def _cargar(_client, ini, fin):
    """
    Pedidos ingresados desde `ini` (con 8 semanas antes para la tendencia) y
    los despachos que pueden cerrarlos. Caché de 5 minutos, como Control de
    Máquinas; el botón «Recargar» la limpia.
    """
    from app.data import get_pedidos_rango, get_despachos_rango, get_feriados
    hoy = datetime.date.today()
    lineas = get_pedidos_rango(_client, ini, fin)
    desp = get_despachos_rango(_client, ini - datetime.timedelta(days=5),
                               max(fin, hoy) + datetime.timedelta(days=60))
    fer = get_feriados(_client)
    return preparar_pedidos(lineas, desp, fer), desp


@st.cache_data(show_spinner=False, ttl=600)
def _nombres(_client):
    from app.data import get_todos_vendedores, get_dim_cliente_full
    try:
        v = get_todos_vendedores(_client)
        vend = dict(zip(v["id"], v["nombre_canonico"]))
    except Exception:
        vend = {}
    try:
        c = get_dim_cliente_full(_client).drop_duplicates("rut")
        cli = dict(zip(c["rut"], c["razon_social"].astype(str).str.strip()))
        com = dict(zip(c["rut"], c["comuna"].astype(str).str.strip()))
    except Exception:
        cli, com = {}, {}
    return vend, cli, com


@st.cache_data(show_spinner=False, ttl=300)
def _ventas_clientes(_client, ruts: tuple, desde):
    from app.data import get_ventas_clientes
    return get_ventas_clientes(_client, ruts, desde)


def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver el control de entregas.")
        return

    hoy = datetime.date.today()
    c1, c2, c3, c4, c5 = st.columns([1.3, 2.2, 0.9, 0.9, 0.8])
    with c1:
        st.selectbox("Período (fecha de ingreso)", list(_RANGOS), key="ce_rango")
    pers = st.session_state.get("ce_rango") == "Rango personalizado"
    if pers:
        with c2:
            st.date_input("Desde / hasta",
                          value=(hoy - datetime.timedelta(days=27), hoy),
                          min_value=_INICIO, max_value=hoy, format="DD/MM/YYYY",
                          key="ce_rango_pers")
    f_ini, f_fin = _rango()
    f_ini = max(f_ini, _INICIO)
    if not pers:
        with c2:
            st.markdown(
                '<div style="padding-top:1.9rem;color:var(--gris);font-size:.88rem">'
                f'📅 <b>{f_ini:%d/%m/%Y} → {f_fin:%d/%m/%Y}</b>'
                + (" · en curso, todavía se está llenando" if f_fin >= hoy else "")
                + '</div>', unsafe_allow_html=True)
    with c3:
        sla = st.number_input("Plazo helados", 1, 10, SLA_DIAS_DEFAULT,
                              key="ce_sla",
                              help="En días hábiles. Plazo comprometido desde que el "
                                   "vendedor ingresa el pedido hasta la entrega. "
                                   "Define el «% a tiempo».")
    with c4:
        sla_m = st.number_input("Plazo máquinas", 1, 30, SLA_MAQ_DEFAULT,
                                key="ce_sla_m",
                                help="En días hábiles. Lo mismo para los "
                                     "pedidos de flete de máquina, que pasan "
                                     "por coordinación.")
    with c5:
        st.markdown('<div style="height:1.75rem"></div>', unsafe_allow_html=True)
        if st.button("🔄 Recargar", key="ce_recargar"):
            _cargar.clear()

    ini_carga = max(f_ini - datetime.timedelta(weeks=_SEMANAS_TENDENCIA - 1),
                    _INICIO)
    with st.spinner("Cargando pedidos y despachos…"):
        todo, desp = _cargar(client, ini_carga, f_fin)
    vend, cli, com = _nombres(client)
    if not todo.empty:
        todo = todo.assign(
            Vendedor=todo["vendedor_id"].map(vend).fillna("Sin asignar"),
            Cliente=todo["cliente_rut"].map(cli).fillna(todo["cliente_rut"]),
            Comuna=todo["cliente_rut"].map(com).fillna("(sin dato)"))
    per = (todo[todo["Ingreso"].between(pd.Timestamp(f_ini), pd.Timestamp(f_fin))]
           if not todo.empty else todo)

    # Hasta dónde llega el Excel de despachos (carga manual): lo más reciente
    # con resultado en terreno. Un «Pendiente» puede venir con fecha futura.
    ult = None
    if desp is not None and not desp.empty:
        con_res = desp[desp["estado"].astype(str).str.lower()
                       .isin(["entregada", "rechazada"])]
        if not con_res.empty:
            ult = pd.to_datetime(con_res["fecha_ruta"]).max()
    st.info(
        "**Cómo leer esta página.** Todo se cuenta sobre los **pedidos "
        f"ingresados entre el {f_ini:%d/%m} y el {f_fin:%d/%m}** (fecha en que "
        "el vendedor lo ingresó en Autoventa). El tiempo de entrega va en "
        "**días hábiles** (lunes a viernes, sin feriados) desde el ingreso "
        "hasta el día de la ruta que lo cerró: el Excel de despachos no trae la "
        "hora de entrega, así que el día de la ruta es el día de la entrega. "
        "Los pedidos que siguen abiertos todavía no tienen tiempo: se cuentan "
        "aparte. La vista «Por transportista» mide por fecha de ruta."
        + (f"\n\n**Despachos cargados hasta el {ult:%d/%m/%Y}.** El estado de "
           "entrega sale del Excel de despachos de Autoventa, que se sube a "
           "mano en Carga: lo posterior a esa fecha figura «Facturado sin "
           "ruta» o «En ruta» aunque ya se haya entregado."
           if ult is not None else ""))

    vista = st.radio("Vista", list(_VISTAS), horizontal=True, key="ce_vista",
                     format_func=lambda k: _VISTAS[k],
                     label_visibility="collapsed")
    if vista == "helados":
        _vista_pedidos(per[per["Tipo"] == "Helados"] if not per.empty else per,
                       todo[todo["Tipo"] == "Helados"] if not todo.empty else todo,
                       f_fin, sla, "helados")
    elif vista == "maquinas":
        _vista_pedidos(per[per["Tipo"] == "Máquina"] if not per.empty else per,
                       todo[todo["Tipo"] == "Máquina"] if not todo.empty else todo,
                       f_fin, sla_m, "maquinas")
    elif vista == "rechazos":
        _vista_rechazos(client, per[per["Tipo"] == "Helados"]
                        if not per.empty else per, desp)
    else:
        _vista_transportista(client, desp)


# ── Pedidos (helados o máquinas) ─────────────────────────────────────────────

def _vista_pedidos(g: pd.DataFrame, todo: pd.DataFrame, f_fin, sla: int,
                   tipo: str) -> None:
    maq = tipo == "maquinas"
    if g is None or g.empty:
        st.markdown('<div class="estado-vacio">Sin pedidos ingresados en el '
                    'período.</div>', unsafe_allow_html=True)
        return
    r = resumen(g, sla)

    if maq:
        mz = g.loc[g["Estado"].isin(CERRADOS + ABIERTOS), "Movimiento"].value_counts()
        linea_n = " · ".join(etiqueta_mov(k, int(mz[m])) for m, k in
                             (("Instalación", "nueva"), ("Cambio", "cambio"),
                              ("Retiro", "retiro")) if mz.get(m))
    else:
        linea_n = fmt_clp(r["monto"]) + " en pedidos"
    color_t = (_C["verde"] if (r["a_tiempo"] or 0) >= .8 else
               _C["amrl"] if (r["a_tiempo"] or 0) >= .6 else _C["rojo"])
    color_r = (_C["verde"] if (r["pct_rech"] or 0) <= .05 else
               _C["amrl"] if (r["pct_rech"] or 0) <= .10 else _C["rojo"])
    st.markdown(
        '<div class="kpi-grid-4">'
        + _tarjeta("Pedidos ingresados", f"{r['n']:,}".replace(",", "."),
                   _C["azul"], linea_n,
                   f"{r['cerrados']} cerrados · {r['abiertos']} abiertos")
        + _tarjeta("Tiempo de entrega", f"{_dias(r['mediana'])} días",
                   _C["azul"], "mediana, ingreso → entrega (hábiles)",
                   f"promedio {_dias(r['promedio'])} · 9 de cada 10 en "
                   f"{_dias(r['p90'])} o menos")
        + _tarjeta(f"% a tiempo (≤ {sla} días)", _pct(r["a_tiempo"]), color_t,
                   f"de los {r['ent']} entregados", "", r["a_tiempo"])
        + _tarjeta("% de rechazo", _pct(r["pct_rech"], 1), color_r,
                   f"{r['rech']} de {r['cerrados']} cerrados",
                   "" if maq else f"{_pct(r['pct_rech_monto'], 1)} del monto")
        + '</div>', unsafe_allow_html=True)

    # ── Dónde se va el tiempo ────────────────────────────────────────────────
    _sec("Dónde se va el tiempo")
    k = st.columns(4)
    k[0].metric("Ingreso → DTE", f"{_dias(r['med_dte'])} días",
                help="Mediana de días hábiles desde que el vendedor ingresa el "
                     "pedido hasta que se emite el documento.")
    k[1].metric("DTE → ruta", f"{_dias(r['med_ruta'])} días",
                help="Mediana de días hábiles desde el documento hasta el día "
                     "de la ruta. Suele ser 0: logística factura con la fecha "
                     "de entrega.")
    k[2].metric("Ingreso → entrega", f"{_dias(r['mediana'])} días",
                help="Mediana del recorrido completo de los entregados.")
    k[3].metric("Abiertos hoy", r["abiertos"],
                help=f"{r['sin_dte']} sin DTE · {r['sin_ruta']} facturados sin "
                     f"ruta · {r['ruta']} en ruta.")
    col1, col2 = st.columns([1, 1.25])
    with col1:
        dist = distribucion(g)
        if not dist.empty:
            colores = [_C["verde"] if (d != "6 o más" and int(d) <= sla)
                       else _C["amrl"] for d in dist["Días hábiles"]]
            fig = go.Figure(go.Bar(
                x=dist["Días hábiles"], y=dist["Pedidos"], marker_color=colores,
                text=[_pct(x) for x in dist["%"]], textposition="outside",
                hovertemplate="%{x} días: %{y} pedidos<extra></extra>"))
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=24, b=0),
                              title=dict(text="Entregados según días hábiles",
                                         font=dict(size=13)),
                              xaxis=dict(title=None), yaxis=dict(visible=False),
                              plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        sem = semanal(todo, f_fin, _SEMANAS_TENDENCIA, sla)
        if not sem.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=sem["Semana"], y=sem["A tiempo"], name="% a tiempo",
                marker_color=_C["verde"],
                text=[_pct(x) for x in sem["A tiempo"]], textposition="outside",
                hovertemplate="%{x}: %{y:.0%} a tiempo<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=sem["Semana"], y=sem["% rechazo"], name="% rechazo",
                mode="lines+markers", line=dict(color=_C["rojo"]),
                hovertemplate="%{x}: %{y:.1%} rechazo<extra></extra>"))
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=24, b=0),
                              title=dict(text="Últimas 8 semanas (por semana de "
                                              "ingreso)", font=dict(size=13)),
                              yaxis=dict(tickformat=".0%", range=[0, 1.15],
                                         showgrid=False),
                              legend=dict(orientation="h", y=-0.2),
                              plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
    st.caption("Verde: dentro del plazo. La última semana casi siempre se ve "
               "mejor de lo que es: solo cuentan los que ya se entregaron, y "
               "los lentos todavía están abiertos.")

    # ── Por quién / dónde ────────────────────────────────────────────────────
    _sec("Por vendedor, transportista o comuna")
    opciones = (["Movimiento", "Vendedor", "Transportista", "Comuna"] if maq
                else ["Vendedor", "Transportista", "Comuna"])
    por = st.radio("Partir por", opciones, horizontal=True,
                   key=f"ce_por_{tipo}", label_visibility="collapsed")
    t = tabla_por(g, por, sla)
    if not t.empty:
        v = t.copy()
        for c in ("Días hábiles (mediana)", "Días hábiles (promedio)"):
            v[c] = v[c].map(_dias)
        for c in (f"% en {sla} días o menos", "% rechazo"):
            v[c] = v[c].map(lambda x: _pct(x, 0 if "días" in c else 1))
        v["Monto"] = v["Monto"].map(fmt_clp)
        if maq:
            v = v.drop(columns=["Monto"])
        st.dataframe(v, use_container_width=True, hide_index=True)
    if por == "Transportista":
        st.caption("«(sin dato)» son pedidos que todavía no salen a ruta.")

    # ── Abiertos ─────────────────────────────────────────────────────────────
    ab = g[g["Estado"].isin(ABIERTOS)].copy()
    _sec(f"Pedidos abiertos del período · {len(ab)}")
    if ab.empty:
        st.success("Todos los pedidos del período ya se entregaron o rechazaron.")
    else:
        cuenta = ab["Estado"].value_counts()
        st.markdown(" · ".join(f"**{int(cuenta[e])}** {e.lower()}"
                               for e in (SIN_DTE, SIN_RUTA, EN_RUTA)
                               if cuenta.get(e)))
        cols = ["Días abierto", "Estado", "Ingreso", "n_pedido",
                "Movimiento" if maq else "Monto", "Vendedor", "Cliente",
                "Comuna", "Documento", "Transportista"]
        v = ab.sort_values("Días abierto", ascending=False)[cols].rename(
            columns={"n_pedido": "N° pedido"})
        v["Ingreso"] = v["Ingreso"].dt.date
        if not maq:
            v["Monto"] = v["Monto"].map(fmt_clp)
        st.dataframe(v, use_container_width=True, hide_index=True)
        st.caption(
            "«Sin DTE»: el pedido no tiene documento (falta que logística lo "
            "emita). «Facturado sin ruta»: tiene documento y no aparece en el "
            "Excel de despachos (no ha salido, o falta subir ese Excel). «En "
            "ruta»: salió y no vuelve confirmado. Días abierto = hábiles desde "
            "el ingreso hasta hoy.")

    fuera = g.loc[~g["Estado"].isin(CERRADOS + ABIERTOS), "Estado"].value_counts()
    with st.expander(f"📋 Detalle de los {len(g)} pedidos"):
        if not fuera.empty:
            st.caption(
                "Incluye " + " · ".join(f"{n} {e.lower()}" for e, n in fuera.items())
                + ", que quedan fuera de los indicadores: no tienen un despacho "
                "nuestro con el que medir (anulados por logística, facturados "
                "en la serie de Obuma o de un mes sin despachos cargados).")
        det = g.drop(columns=["vendedor_id", "sociedad_id", "Tipo"]).rename(
            columns={"n_pedido": "N° pedido", "cliente_rut": "RUT"})
        if not maq:
            det = det.drop(columns=["Movimiento"])
        for c in ("Ingreso", "Fecha DTE", "Fecha ruta"):
            det[c] = pd.to_datetime(det[c]).dt.date
        st.dataframe(det, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Descargar en CSV", det.to_csv(index=False).encode("utf-8-sig"),
            f"entregas_{tipo}_{datetime.date.today():%Y%m%d}.csv", "text/csv",
            key=f"dl_ce_{tipo}")
    if maq:
        st.caption(
            "Esta vista cuenta **pedidos de flete ingresados**; Control de "
            "Máquinas cuenta **gestiones con DTE emitido** en la semana. Son "
            "grupos distintos a propósito (aquí se mide el tiempo desde que el "
            "vendedor pide, allá la gestión), así que los totales no cuadran "
            "entre sí.")


# ── Rechazos de helados ──────────────────────────────────────────────────────

def _vista_rechazos(client, g: pd.DataFrame, desp: pd.DataFrame) -> None:
    if g is None or g.empty:
        st.markdown('<div class="estado-vacio">Sin pedidos ingresados en el '
                    'período.</div>', unsafe_allow_html=True)
        return
    r = resumen(g)
    rech = g[g["Estado"] == RECHAZADO].copy()
    if rech.empty:
        st.success(f"Ninguno de los {r['cerrados']} pedidos cerrados del "
                   "período volvió rechazado.")
        return
    try:
        ventas = _ventas_clientes(
            client, tuple(sorted(set(rech["cliente_rut"]))),
            (rech["Fecha ruta"].min() - pd.Timedelta(days=5)).date())
    except Exception:
        ventas = None
        st.caption("No se pudieron leer las notas de crédito; la vista sale "
                   "sin la acreditación.")
    s = seguimiento_rechazos_productos(rech, ventas, desp)
    rech = rech.join(s)
    sin_nc = rech[rech["Acreditación"] == SIN_NC]
    sin_volver = rech[rech["_sin_volver"]]
    venc_nc = int(rech["_sin_nc_vencido"].sum())

    st.markdown(
        '<div class="kpi-grid-4">'
        + _tarjeta("Rechazos", str(len(rech)), _C["rojo"],
                   f"{_pct(r['pct_rech'], 1)} de los {r['cerrados']} cerrados",
                   "pedidos ingresados en el período")
        + _tarjeta("Monto rechazado", fmt_clp(rech["Monto"].sum()), _C["rojo"],
                   f"{_pct(r['pct_rech_monto'], 1)} del monto cerrado")
        + _tarjeta("Días a la nota de crédito",
                   f"{_dias(rech['Días a la NC'].median())}", _C["azul"],
                   "mediana, desde el rechazo (corridos)",
                   f"{len(sin_nc)} sin NC todavía"
                   + (f" · {venc_nc} con más de {DIAS_ALERTA_NC} días"
                      if venc_nc else ""))
        + _tarjeta("Días a volver a recibir",
                   f"{_dias(rech['Días a la siguiente entrega'].median())}",
                   _C["azul"], "mediana hasta la siguiente entrega al cliente",
                   f"{len(sin_volver)} no han vuelto a recibir")
        + '</div>', unsafe_allow_html=True)
    if venc_nc:
        st.warning(f"⚠️ **{venc_nc} rechazos llevan más de {DIAS_ALERTA_NC} "
                   "días sin nota de crédito.** El cliente queda debiendo una "
                   "factura que no recibió. Lista abajo, en «Sin NC».")

    col1, col2 = st.columns(2)
    with col1:
        _sec("Por qué se rechazaron")
        mot = rech["Motivo"].fillna("Sin dato").value_counts()
        fig = go.Figure(go.Bar(
            y=mot.index[::-1], x=mot.values[::-1], orientation="h",
            marker_color=_C["rojo"], text=mot.values[::-1],
            textposition="outside",
            hovertemplate="%{y}: %{x}<extra></extra>"))
        fig.update_layout(height=max(220, 28 * len(mot)),
                          margin=dict(l=0, r=20, t=8, b=0),
                          xaxis=dict(visible=False),
                          plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Clasificado por palabras clave sobre lo que escribe el "
                   "repartidor: el campo «Motivo rechazo» del ERP llega vacío.")
    with col2:
        _sec("Qué pasó después")
        acr = rech["Acreditación"].value_counts()
        st.markdown(
            f"- **{int(acr.get(NC_EXACTA, 0))}** acreditados con NC por el monto "
            f"exacto, **{int(acr.drop([NC_EXACTA, SIN_NC], errors='ignore').sum())}** "
            f"con NC de otro monto y **{len(sin_nc)}** sin NC.\n"
            f"- **{len(rech) - len(sin_volver)}** clientes volvieron a recibir "
            f"un pedido después del rechazo; **{len(sin_volver)}** todavía no.")
        por = st.radio("Partir por", ["Transportista", "Vendedor", "Comuna"],
                       horizontal=True, key="ce_rech_por",
                       label_visibility="collapsed")
        t = (rech.groupby(rech[por].fillna("(sin dato)"))
             .agg(Rechazos=("n_pedido", "size"), Monto=("Monto", "sum"),
                  **{"Días a la NC": ("Días a la NC", "median"),
                     "Sin NC": ("Acreditación", lambda x: int((x == SIN_NC).sum())),
                     "No volvió a recibir": ("_sin_volver", "sum")})
             .sort_values("Rechazos", ascending=False).reset_index())
        cerr = g[g["Estado"].isin((ENTREGADO, RECHAZADO))]
        base = cerr.groupby(cerr[por].fillna("(sin dato)")).size()
        t.insert(2, "% rechazo", (t["Rechazos"] / t[por].map(base)).map(
            lambda x: _pct(x, 1)))
        t["Monto"] = t["Monto"].map(fmt_clp)
        t["Días a la NC"] = t["Días a la NC"].map(_dias)
        t["No volvió a recibir"] = t["No volvió a recibir"].astype(int)
        st.dataframe(t, use_container_width=True, hide_index=True)

    lista = st.radio(
        "Lista", ["sin_nc", "sin_volver", "todos"], horizontal=True,
        key="ce_rech_lista", label_visibility="collapsed",
        format_func=lambda k: {"sin_nc": f"🔴 Sin NC · {len(sin_nc)}",
                               "sin_volver": f"🟡 No han vuelto a recibir · "
                                             f"{len(sin_volver)}",
                               "todos": f"Todos · {len(rech)}"}[k])
    v = {"sin_nc": sin_nc, "sin_volver": sin_volver, "todos": rech}[lista]
    v = v.sort_values("Días desde el rechazo", ascending=False)
    cols = ["Fecha ruta", "Días desde el rechazo", "Cliente", "Comuna",
            "Vendedor", "Transportista", "Monto", "Motivo", "Comentario",
            "Acreditación", "Días a la NC", "N° NC", "Siguiente entrega",
            "Días a la siguiente entrega", "Documento", "n_pedido"]
    det = v[cols].rename(columns={"Fecha ruta": "Fecha rechazo",
                                  "Comentario": "Lo que dijo el repartidor",
                                  "n_pedido": "N° pedido"})
    for c in ("Fecha rechazo", "Siguiente entrega"):
        det[c] = pd.to_datetime(det[c]).dt.date
    det_v = det.copy()
    det_v["Monto"] = det_v["Monto"].map(fmt_clp)
    st.dataframe(det_v, use_container_width=True, hide_index=True)
    st.caption(
        "**Cómo se cruza.** La NC no dice qué factura anula: se toma la primera "
        "NC del cliente por el mismo monto desde 2 días antes del rechazo (a "
        "veces se acredita antes de cerrar la ruta) y, si no hay una exacta, la "
        "primera NC del cliente. «Siguiente entrega» es la primera ruta "
        "entregada al mismo cliente después del rechazo, de cualquier pedido.")
    st.download_button(
        "⬇️ Descargar en CSV", det.to_csv(index=False).encode("utf-8-sig"),
        f"rechazos_helados_{datetime.date.today():%Y%m%d}.csv", "text/csv",
        key="dl_ce_rech")


# ── Por transportista (antes plegado en Control de Máquinas) ─────────────────

def _vista_transportista(client, desp_todo: pd.DataFrame) -> None:
    """
    % de entrega por transportista: helados en pesos, máquinas en unidades.

    Se mudó desde Control de Máquinas en oct-2026. Mide por FECHA DE RUTA del
    período (definición oficial, sep-2026), no por fecha de ingreso como las
    otras tres vistas.
    """
    from app.data import get_ventas_rango, get_maquinas_rango, get_despachos_rango
    from app.export_entregas import (preparar_entregas, _tabla_por,
                                     maquinas_despachadas)
    f_ini, f_fin = _rango()
    f_ini = max(f_ini, _INICIO)
    st.caption(f"Despachos con **fecha de ruta** entre el {f_ini:%d/%m/%Y} y el "
               f"{f_fin:%d/%m/%Y}. Las NC no se restan: un rechazo se termina "
               "acreditando y restarlas inflaría el %.")
    try:
        # La factura puede ser anterior a la ruta: se pide con margen.
        ventas = get_ventas_rango(client, f_ini - datetime.timedelta(days=45),
                                  f_fin + datetime.timedelta(days=14))
        maq = get_maquinas_rango(client, f_ini - datetime.timedelta(days=60),
                                 f_fin + datetime.timedelta(days=14))
        desp = get_despachos_rango(client, f_ini, f_fin)
    except Exception as exc:
        st.warning("No se pudieron leer las ventas o los despachos del período "
                   f"({type(exc).__name__}). Prueba con «Recargar».")
        return
    if desp is None or desp.empty:
        st.caption("Sin despachos cargados en el período.")
        return
    if ventas is None or ventas.empty:
        st.caption("Sin ventas facturadas en el período.")
        return
    d = preparar_entregas(ventas, desp, maq)
    d = d[d["fecha_ruta"].between(pd.Timestamp(f_ini), pd.Timestamp(f_fin))]
    if d.empty:
        st.caption("Sin despachos en el período.")
        return
    # Igual que el informe de entregas: en helados las anulaciones quedan en el
    # total (solo se sacan del % de máquinas).
    prods = d[~d["Es máquina"]]
    ent = float(prods.loc[prods["_est"] == "Entregada", "Monto facturado"].sum())
    tot = float(prods["Monto facturado"].sum())
    rech = float(prods.loc[prods["_est"] == "Rechazada", "Monto facturado"].sum())
    _sec("Helados · en pesos")
    k1, k2, k3 = st.columns(3)
    k1.metric("% de entrega · helados", f"{ent / tot * 100:.1f}%" if tot else "—",
              help="Plata entregada sobre la plata que salió a ruta en el período.")
    k2.metric("Despachado", fmt_clp(tot))
    k3.metric("Rechazado", fmt_clp(rech))
    t = _tabla_por(prods, "Transportista", "monto", "Transportista")
    if not t.empty:
        v = t.copy()
        for col in [x for x in v.columns if x not in ("Transportista", "% de entrega")]:
            v[col] = v[col].apply(lambda x: fmt_clp(x) if pd.notna(x) and x != "" else "")
        v["% de entrega"] = v["% de entrega"].apply(
            lambda x: f"{x * 100:.1f}%" if pd.notna(x) and x != "" else "")
        st.dataframe(v, use_container_width=True, hide_index=True)

    _sec("Máquinas · en unidades")
    maqs = maquinas_despachadas(d)
    if maqs.empty:
        st.caption("Sin máquinas despachadas en el período.")
        return
    est = maqs["_est"]
    n_ent, n_rech = int((est == "Entregada").sum()), int((est == "Rechazada").sum())
    n_pend, n_tot = int((est == "Pendiente").sum()), len(maqs)
    k1, k2, k3 = st.columns(3)
    k1.metric("% de entrega · máquinas", f"{n_ent / n_tot * 100:.0f}%",
              help="Máquinas entregadas sobre las que salieron a ruta en el "
                   "período. Las que siguen en ruta cuentan en el total.")
    k2.metric("Entregadas", f"{n_ent} de {n_tot}")
    k3.metric("Rechazadas", n_rech, f"{n_pend} en ruta" if n_pend else None,
              delta_color="off")
    t = _tabla_por(maqs, "Transportista", "docs", "Transportista")
    if not t.empty:
        v = t.copy()
        for col in [x for x in v.columns if x not in ("Transportista", "% de entrega")]:
            v[col] = v[col].apply(lambda x: f"{int(x)}" if pd.notna(x) and x != "" else "")
        v["% de entrega"] = v["% de entrega"].apply(
            lambda x: f"{x * 100:.0f}%" if pd.notna(x) and x != "" else "")
        st.dataframe(v, use_container_width=True, hide_index=True)
    n_anul = int((d["Es máquina"] & d["Es anulación"]).sum())
    st.caption(
        "Cuenta cada documento despachado: todo flete de máquina (FL) y todo lo "
        "de $0 que llevó un camión de máquinas, porque ese camión hace retiros "
        "que no pasan por factura FL. Las máquinas que viajan en camión de "
        "helados también cuentan."
        + (f" Quedan fuera {n_anul} de la ruta «Nulas mes en curso»: son "
           "anulaciones de logística, no entregas ni rechazos."
           if n_anul else ""))
