"""
Control de Máquinas — la semana de gestiones, contada de una sola manera.

Gerencia viene a responder cuatro preguntas, siempre sobre la misma semana:

    1. ¿Cuántas gestiones hubo, y de qué tipo?   (contra la meta)
    2. ¿Cuántas se entregaron?                   (% de entrega)
    3. ¿Cuántas volvieron y por qué?             (motivos de rechazo)
    4. ¿Cuáles siguen sin confirmar? (en ruta, sin despacho, sin información)
    5. ¿Qué pasó con las que volvieron?          (se reingresaron o se perdieron)

La quinta se agregó en septiembre de 2026: gerencia veía los rechazos de la
semana pero nadie sabía si el vendedor los volvía a ingresar. El cruce que lo
responde vive en `kpis_maquinas.seguimiento_rechazos`.

La regla que evita el enredo: TODO se cuenta sobre las gestiones de la semana,
es decir, fletes de máquina con documento (DTE) emitido en esas fechas. Un
pedido ingresado antes cuenta en la semana en que se emitió su documento, y el
% de entrega, los rechazos y los que siguen en ruta son esas mismas gestiones,
no otro grupo. Por eso se sacaron de la vista los indicadores que miraban otra
cosa (% concretado, mediana de días, cola vencida, parque neto): cada uno tenía
su propio denominador y obligaba a explicar cuál era cuál.

Lo demás —la cola de pedidos sin documento, las entregas en pesos, los Excel y
las metas— queda abajo, plegado.
"""
import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.auth import es_gerencia
from app.styles import fmt_clp
from app.data import (get_objetivos_maquinas, upsert_objetivos_maquinas,
                      get_dim_cliente_full)
from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA, SIN_DESPACHO,
                                 SIN_INFO)
from app.kpis_maquinas import (DIAS_PARA_REINTENTAR, cargar_todo,
                               conteo_semana, mezcla_movimientos, mezcla_texto,
                               resumen_rechazos, seguimiento_rechazos,
                               texto_sin_confirmar)

_C = {"verde": "#1A7F4B", "amrl": "#D4881E", "rojo": "#C0392B",
      "gris": "#9CA3AF", "gris2": "#CBD5E1", "rosa": "#E62984",
      "slate": "#64748B"}

# Un identificador por tarjeta: el documento que se emite, el visto de la
# entrega, el camión que va en ruta y la equis del que volvió. El color es el
# del concepto y no cambia con el resultado (ver `_tarjeta`).
_ICO = {
    # 📄 y no 🧾: la boleta ya es el ícono de la sección Clientes en el menú.
    "gestiones": ("📄", _C["slate"]),
    "entrega": ("✔", _C["verde"]),
    "ruta": ("🚚", _C["amrl"]),
    "rechazo": ("✖", _C["rojo"]),
}

# Semanas de lunes a domingo, relativas a hoy. Gerencia mide por semana.
_RANGOS = {
    "Semana pasada": ("semana", 1),
    "Semana en curso": ("semana", 0),
    "Próxima semana": ("semana", -1),
    "Hace 2 semanas": ("semana", 2),
    "Hace 3 semanas": ("semana", 3),
    "Mes en curso": ("mes", 0),
    "Rango personalizado": ("pers", 0),
}

# Primer despacho de Autoventa que hay en la base. Antes de esta fecha no hay
# con qué confirmar si una máquina se entregó o volvió, así que tampoco existen
# rechazos: es el límite real hacia atrás de todo lo que mira esta página.
# Acuña no pasa por Autoventa y nunca tiene despacho, en ninguna fecha.
_PRIMER_DESPACHO = datetime.date(2026, 2, 20)
# Desde dónde se cargan los movimientos: un mes antes del primer despacho,
# porque una factura puede preceder a su ruta en varias semanas.
_INI_HISTORIA = _PRIMER_DESPACHO - datetime.timedelta(days=31)

# Logística factura con la fecha de ENTREGA, no la de emisión: un jueves ya hay
# documentos fechados el sábado y programados en ruta el viernes (24-09-2026:
# 12 gestiones, entre ellas Full Market). Se carga esta holgura hacia adelante
# para que esos documentos existan en la página.
_DIAS_ADELANTE = 14

# Cuántas semanas muestra el gráfico de tendencia.
_SEMANAS_TENDENCIA = 8

_MOV = {"nueva": "Instalación", "cambio": "Cambio", "retiro": "Retiro"}
_MOV_PL = {"nueva": "Instalaciones", "cambio": "Cambios", "retiro": "Retiros"}

# Solo las metas que esta página usa. El resto de las columnas de
# objetivos_maquinas se conserva en la base y lo sigue usando el Excel.
_CAMPOS_META = [
    ("meta_gestiones_semana", "Gestiones por semana", "int",
     "Meta del equipo completo. Instalación, cambio y retiro suman igual."),
    ("meta_pct_entregado", "% de entrega", "pct",
     "De las gestiones de la semana, cuántas se confirman entregadas."),
]


def _sec(title: str):
    st.markdown(f'<div class="seccion-titulo">{title}</div>',
                unsafe_allow_html=True)


def _rango():
    """
    (inicio, fin) del período elegido.

    La semana y el mes en curso llegan hasta su ÚLTIMO día, no hasta hoy. Antes
    se cortaban en hoy, y como logística factura con la fecha de entrega, el
    jueves 24-09-2026 quedaban fuera 12 gestiones fechadas el viernes y el
    sábado que ya estaban en ruta —las de Full Market entre ellas—. Un documento
    con fecha futura no es una predicción: ya se emitió, solo que con esa fecha.
    """
    tipo, n = _RANGOS.get(st.session_state.get("cm_rango", "Semana pasada"),
                          ("semana", 1))
    hoy = datetime.date.today()
    un_dia = datetime.timedelta(days=1)
    if tipo == "pers":
        sel = st.session_state.get("cm_rango_pers")
        if isinstance(sel, (list, tuple)) and len(sel) == 2:
            return sel[0], sel[1]
        # El calendario devuelve una sola fecha entre el primer y el segundo
        # clic: mientras tanto se mira desde esa fecha hasta hoy.
        ini = (sel[0] if isinstance(sel, (list, tuple)) and sel
               else hoy - datetime.timedelta(days=27))
        return ini, max(ini, hoy)
    if tipo == "mes":
        primero_sgte = (hoy.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return hoy.replace(day=1), primero_sgte - un_dia
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    ini = lunes - datetime.timedelta(days=7 * n)
    return ini, ini + datetime.timedelta(days=6)


def _tarjeta(titulo: str, valor: str, color: str, linea1: str,
             linea2: str = "", barra: float | None = None,
             icono: tuple[str, str] | None = None) -> str:
    """
    Tarjeta grande con un número, su contexto y (opcional) una barra.

    `icono` es `(glifo, color)` y va chico, arriba a la derecha, en un chip del
    mismo color al 10%. Su color es FIJO por tarjeta, no el del semáforo: el
    ícono dice de qué se está hablando y el número grande dice cómo va. Un tick
    que se pone rojo cuando el % de entrega baja confunde las dos cosas.
    """
    barra_html = ""
    if barra is not None:
        ancho = max(min(barra, 1.0), 0) * 100
        barra_html = (
            '<div style="background:var(--gris-light);border-radius:99px;'
            'height:.5rem;overflow:hidden;margin:.45rem 0 .25rem">'
            f'<div style="width:{ancho:.0f}%;height:100%;background:{color};'
            'border-radius:99px"></div></div>')
    chip = ""
    if icono:
        glifo, ci = icono
        chip = (f'<span style="font-size:.85rem;line-height:1;color:{ci};'
                f'background:{ci}1A;border-radius:7px;padding:.25rem .35rem;'
                'flex:none">' + glifo + '</span>')
    return (
        '<div class="kpi-card" style="text-align:left;padding:1.1rem 1.2rem">'
        '<div style="display:flex;align-items:center;justify-content:space-'
        'between;gap:.4rem;margin-bottom:.22rem">'
        f'<div class="kpi-label" style="margin-bottom:0">{titulo}</div>'
        f'{chip}</div>'
        f'<div class="kpi-value" style="color:{color};font-size:2.3rem">{valor}</div>'
        f'{barra_html}'
        f'<div class="kpi-sub" style="font-size:.8rem">{linea1}</div>'
        + (f'<div class="kpi-sub" style="font-size:.74rem;opacity:.75">{linea2}</div>'
           if linea2 else "")
        + '</div>')


@st.cache_data(show_spinner=False, ttl=300)
def _cargar(_client, ini, fin):
    """
    `cargar_todo` con caché de 5 minutos.

    Sin caché, cada clic en un filtro volvía a bajar movimientos, despachos y
    pedidos de la base: con el filtro de fechas de los rechazos yendo hasta
    febrero son ~5 segundos por clic. La página es solo de gerencia y gerencia
    ve todo, así que la caché compartida no filtra nada que no debiera. El botón
    «Recargar» la limpia cuando se acaba de correr el ETL.
    """
    return cargar_todo(_client, ini, fin)


@st.cache_data(show_spinner=False, ttl=600)
def _nombres_cliente(_client) -> dict:
    try:
        d = get_dim_cliente_full(_client)
        return dict(zip(d["rut"], d["razon_social"].astype(str).str.strip()))
    except Exception:
        return {}


def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver el control de máquinas.")
        return

    hoy = datetime.date.today()
    c1, c2, c3 = st.columns([1.2, 3, 0.8])
    with c1:
        st.selectbox("Período", list(_RANGOS), key="cm_rango")
    pers = st.session_state.get("cm_rango") == "Rango personalizado"
    if pers:
        with c2:
            st.date_input(
                "Desde / hasta", value=(hoy - datetime.timedelta(days=27), hoy),
                min_value=_PRIMER_DESPACHO,
                max_value=hoy + datetime.timedelta(days=_DIAS_ADELANTE),
                format="DD/MM/YYYY", key="cm_rango_pers",
                help=f"Desde el {_PRIMER_DESPACHO:%d/%m/%Y}, el primer despacho "
                     "cargado: antes no hay con qué confirmar entregas. Hacia "
                     "adelante, hasta donde ya hay documentos fechados.")
    f_ini, f_fin = _rango()
    if f_ini > hoy:
        estado_per = " · período futuro: solo documentos ya emitidos con esas fechas"
    elif f_fin >= hoy:
        estado_per = " · período en curso, todavía se está llenando"
    else:
        estado_per = ""
    linea_per = (f'<div style="padding-top:{".2rem" if pers else "1.9rem"};'
                 'color:var(--gris);font-size:.88rem">'
                 f'📅 <b>{f_ini:%d/%m/%Y} → {f_fin:%d/%m/%Y}</b>{estado_per}</div>')
    if pers:
        st.markdown(linea_per, unsafe_allow_html=True)
    else:
        with c2:
            st.markdown(linea_per, unsafe_allow_html=True)
    with c3:
        st.markdown('<div style="height:1.75rem"></div>', unsafe_allow_html=True)
        if st.button("🔄 Recargar", key="cm_recargar",
                     help="Vuelve a leer la base. Los datos se guardan 5 minutos "
                          "para que los filtros respondan rápido."):
            _cargar.clear()

    metas = get_objetivos_maquinas(client, f_ini.year, f_ini.month)
    meta_g = metas.get("meta_gestiones_semana")
    meta_e = metas.get("meta_pct_entregado")

    # UNA sola carga, desde la historia completa hasta unos días después de hoy,
    # y cada sección filtra su propia ventana de forma explícita:
    #   · tarjetas, barra y «Qué se movió» → `w`, las gestiones del período
    #   · tendencia → las 8 semanas que terminan en `f_fin`
    #   · rechazos → su propio filtro de fechas (por defecto, últimas 8 semanas)
    #   · facturados sin despacho → toda la historia hasta hoy
    # Antes la carga partía 8 semanas antes del período elegido, y la cola de
    # despacho, que no filtraba, cambiaba de tamaño según el selector: 0 con la
    # semana en curso, 1 con el mes, cuando en la base había 9. Traer desde
    # febrero casi no cuesta más: lo pesado son los despachos, y esos ya se
    # pedían con 180 días de margen hacia atrás.
    # Hacia adelante, `_DIAS_ADELANTE`: logística factura con la fecha de
    # entrega, así que un reintento reingresado hoy puede venir fechado el sábado.
    ini_tend = f_ini - datetime.timedelta(weeks=_SEMANAS_TENDENCIA - 1)
    fin_carga = max(f_fin, hoy) + datetime.timedelta(days=_DIAS_ADELANTE)
    with st.spinner("Cargando gestiones y despachos…"):
        mov, ped, desp = _cargar(client, min(ini_tend, _INI_HISTORIA), fin_carga)

    if mov is None or mov.empty:
        st.markdown('<div class="estado-vacio">Sin gestiones en el período.</div>',
                    unsafe_allow_html=True)
        _seccion_plegada(client, mov, ped, desp, f_ini, f_fin, metas)
        return

    w = mov[mov["fecha"].between(pd.Timestamp(f_ini), pd.Timestamp(f_fin))].copy()
    c = conteo_semana(w)
    n_futuro = int((w["fecha"] > pd.Timestamp(hoy)).sum())

    st.info(
        f"**Cómo leer esta página.** Todo se cuenta sobre las **gestiones de la "
        f"semana**: fletes de máquina con documento emitido entre el "
        f"{f_ini:%d/%m} y el {f_fin:%d/%m}. Una gestión puede venir de un pedido "
        f"ingresado semanas antes — lo que cuenta es la fecha del documento. "
        f"Las entregas, los rechazos y los que van en ruta son **esas mismas "
        f"{c['n']} gestiones**, no otro grupo."
        + (f"\n\nIncluye **{n_futuro} con documento fechado después de hoy**: "
           f"logística factura con la fecha de entrega, así que ya están "
           f"emitidas y, casi siempre, programadas en ruta."
           if n_futuro else ""))

    # ── 1-4. Las cuatro respuestas ───────────────────────────────────────────
    # La meta es SEMANAL. En un período de más de una semana (mes, rango
    # personalizado) se compara contra meta × semanas, igual que el Excel:
    # comparar el total del mes con 22 daba "340% de la meta".
    semanas_per = max(((f_fin - f_ini).days + 1) / 7, 1)
    meta_per = meta_g * semanas_per if meta_g else None
    if meta_per:
        linea_g = (f"meta {meta_per:.0f}"
                   + (f" ({meta_g}/semana)" if semanas_per > 1 else "")
                   + f" · {c['n'] / meta_per * 100:.0f}% de la meta")
        color_g = _C["verde"] if c["n"] >= meta_per else (
            _C["amrl"] if c["n"] >= meta_per * 0.8 else _C["rojo"])
        barra_g = c["n"] / meta_per
    else:
        linea_g, color_g, barra_g = "sin meta fijada", _C["slate"], None

    if c["pct"] is not None:
        valor_e = f"{c['pct'] * 100:.0f}%"
        if meta_e:
            color_e = _C["verde"] if c["pct"] >= meta_e else (
                _C["amrl"] if c["pct"] >= meta_e * 0.8 else _C["rojo"])
            linea_e = f"{c['ent']} de {c['base']} · meta {meta_e * 100:.0f}%"
        else:
            color_e, linea_e = _C["slate"], f"{c['ent']} de {c['base']}"
    else:
        valor_e, color_e, linea_e = "—", _C["slate"], "sin despachos cargados"
    nota_e = ("sube a medida que se confirmen las que van en ruta"
              if c["ruta"] else "")

    rech = w[w["Estado entrega"] == RECHAZADA]
    motivo_top = (rech["Motivo del rechazo"].value_counts().index[0]
                  if not rech.empty else "")

    # El seguimiento se calcula UNA vez sobre todos los rechazos de la historia,
    # y de esa misma tabla salen las tres vistas, para que nunca se contradigan:
    #   · los de la semana (tabla de rechazos de arriba y su tarjeta)
    #   · las últimas 8 semanas (la advertencia y el filtro por defecto)
    #   · lo que se elija en el filtro de fechas de la sección
    seg_todo = seguimiento_rechazos(mov[mov["Estado entrega"] == RECHAZADA],
                                    mov, ped)
    # Por documento Y fecha: el número de documento se reutiliza entre meses, y
    # con la historia completa cargada un 5424 de marzo calzaría con uno de hoy.
    llave_w = set(zip(rech["_doc"], rech["fecha"].dt.date))
    seg_w = (seg_todo[[k in llave_w for k in zip(seg_todo["Documento"],
                                                 seg_todo["Fecha documento"])]]
             if not seg_todo.empty else seg_todo)
    desde_def = hoy - datetime.timedelta(weeks=_SEMANAS_TENDENCIA)
    seg = _entre(seg_todo, desde_def, hoy)
    res_seg = resumen_rechazos(seg)
    abiertos_w = int(seg_w["_abierto"].sum()) if not seg_w.empty else 0
    _advertencias(res_seg, w)

    st.markdown(
        '<div class="kpi-grid-4">'
        + _tarjeta("Gestiones de la semana" if semanas_per <= 1
                   else "Gestiones del período", str(c["n"]), color_g, linea_g,
                   mezcla_texto(w) or "documentos de flete emitidos", barra_g,
                   _ICO["gestiones"])
        + _tarjeta("% de entrega", valor_e, color_e, linea_e, nota_e, c["pct"],
                   _ICO["entrega"])
        # "Siguen en ruta" era un título mentiroso: contaba también las que
        # nunca salieron ("Sin despacho"), así que una semana con 0 camiones
        # andando mostraba 2. El título ahora cubre los tres estados y el
        # subtítulo los nombra uno por uno, con las mismas palabras que la
        # tabla «Qué se movió» y los mismos tramos que la barra.
        + _tarjeta("Sin confirmar", str(c["sin_confirmar"]),
                   _C["amrl"] if c["sin_confirmar"] else _C["verde"],
                   texto_sin_confirmar(c),
                   "gestiones sin resultado todavía", None, _ICO["ruta"])
        + _tarjeta("Rechazadas", str(c["rech"]),
                   _C["rojo"] if c["rech"] else _C["verde"],
                   (f"motivo principal: {motivo_top}" if motivo_top
                    else "ninguna volvió"),
                   # Un rechazo solo termina cuando vuelve a ingresarse: el
                   # número que importa no es cuántas volvieron sino cuántas
                   # de esas siguen sin que nadie las retome.
                   (f"{abiertos_w} sin retomar todavía" if abiertos_w
                    else "todas retomadas" if c["rech"] else ""),
                   None, _ICO["rechazo"])
        + '</div>', unsafe_allow_html=True)

    # ── La semana en una barra ───────────────────────────────────────────────
    if c["n"]:
        partes = [("Entregadas", c["ent"], _C["verde"]),
                  ("En ruta", c["ruta"], _C["amrl"]),
                  ("Sin despacho", c["sin_desp"], _C["gris"]),
                  ("Sin información", c["sin_info"], _C["gris2"]),
                  ("Rechazadas", c["rech"], _C["rojo"])]
        fig = go.Figure()
        for nombre, val, color in partes:
            if not val:
                continue
            fig.add_trace(go.Bar(
                y=[""], x=[val], name=nombre, orientation="h",
                marker_color=color, text=f"{nombre} {val}",
                textposition="inside", insidetextanchor="middle",
                hovertemplate=f"{nombre}: {val}<extra></extra>"))
        fig.update_layout(barmode="stack", height=110, showlegend=False,
                          margin=dict(l=0, r=0, t=8, b=0),
                          xaxis=dict(visible=False), yaxis=dict(visible=False),
                          plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    _tabla_mezcla(w)

    # ── Rechazos y en ruta, lado a lado ──────────────────────────────────────
    nombres = _nombres_cliente(client)
    col1, col2 = st.columns(2)
    with col1:
        _sec(f"Por qué se rechazaron · {c['rech']}")
        if rech.empty:
            st.success("Ninguna gestión de la semana volvió rechazada.")
        else:
            motivos = rech["Motivo del rechazo"].value_counts()
            st.markdown(" · ".join(f"**{m}** ({n})" for m, n in motivos.items()))
            # Vendedor, comuna y fecha van en la tabla porque el rechazo se
            # persigue llamando a alguien: sin el nombre y el lugar, la fila no
            # se puede accionar. La última columna dice si ya se retomó.
            st.dataframe(seg_w[[
                "Fecha rechazo", "Cliente", "Comuna", "Vendedor", "Movimiento",
                "Motivo", "Estado post-rechazo", "Reintento",
                "Lo que dijo el repartidor", "Transportista"]],
                use_container_width=True, hide_index=True)
    with col2:
        # El mismo grupo que la tarjeta «Sin confirmar», incluidas las
        # «Sin información»: si la tabla mostrara menos filas que el número de
        # arriba, volvemos al enredo que esto vino a arreglar.
        ruta = w[w["Estado entrega"].isin(
            [EN_RUTA, SIN_DESPACHO, SIN_INFO])].copy()
        _sec(f"Sin confirmar · {len(ruta)}")
        if ruta.empty:
            st.success("Todas las gestiones de la semana tienen resultado.")
        else:
            hoy = pd.Timestamp(datetime.date.today())
            ruta["_desde"] = ruta["Fecha ruta"].fillna(ruta["fecha"])
            st.dataframe(pd.DataFrame({
                "Cliente": ruta["cliente_rut"].map(nombres).fillna(ruta["cliente_rut"]),
                "Movimiento": ruta["tipo_mov"].map(_MOV),
                "Estado": ruta["Estado entrega"].map(
                    {EN_RUTA: "En camino", SIN_DESPACHO: "Sin despacho aún",
                     SIN_INFO: "Sin información"}),
                "Días": (hoy - ruta["_desde"]).dt.days,
                "Transportista": ruta["Transportista"].fillna("—"),
                "Vendedor": ruta["Vendedor"],
            }).sort_values("Días", ascending=False),
                use_container_width=True, hide_index=True)
            st.caption(
                "«En camino» salió a ruta y no vuelve confirmada todavía. "
                "«Sin despacho aún» es un documento emitido que no aparece en "
                "el Excel de despachos del mes, aunque ese mes sí está cargado: "
                "o no ha salido, o falta esa fila. «Sin información» es Acuña o "
                "un mes sin despachos cargados — esas no se van a poder "
                "confirmar nunca, y por eso quedan fuera del % de entrega.")

    _seguimiento(seg_todo, desde_def)

    # ── Tendencia ────────────────────────────────────────────────────────────
    st.divider()
    _sec(f"Últimas {_SEMANAS_TENDENCIA} semanas")
    _grafico_tendencia(mov, f_fin, meta_g)

    _seccion_plegada(client, mov, ped, desp, f_ini, f_fin, metas, w)


def _advertencias(res: dict, w: pd.DataFrame) -> None:
    """
    Lo que hay que empujar esta semana, y solo eso.

    Dos cosas ameritan interrumpir la lectura de la página: un rechazo que nadie
    volvió a ingresar —el pedido se perdió y no se enteró nadie— y una semana
    que cumple la meta retirando máquinas, que es cumplirla al revés. Todo lo
    demás tiene su propia tabla; un panel de alertas con diez líneas ya se probó
    y no lo leía nadie.
    """
    avisos = []
    if res["vencidos"]:
        avisos.append(
            f"**{res['vencidos']} rechazos de las últimas {_SEMANAS_TENDENCIA} "
            f"semanas llevan más de {DIAS_PARA_REINTENTAR} días sin volver a "
            f"ingresarse.** Cada uno es una máquina que salió "
            f"a ruta, volvió al camión y nadie la retomó. El detalle, con "
            f"vendedor y comuna, está en «Qué pasó con los rechazos».")
    if len(w):
        retiros = int((w["tipo_mov"] == "retiro").sum())
        nuevas = int((w["tipo_mov"] == "nueva").sum())
        if retiros > len(w) / 2 and retiros > nuevas:
            avisos.append(
                f"**{retiros} de las {len(w)} gestiones de la semana son "
                f"retiros** y solo {nuevas} son instalaciones: la meta se está "
                f"cumpliendo desinstalando parque.")
    if avisos:
        st.warning("\n\n".join("⚠️ " + a for a in avisos))


def _tabla_mezcla(w: pd.DataFrame) -> None:
    """De las gestiones de la semana, cuántas de cada tipo y cómo terminó cada tipo."""
    mz = mezcla_movimientos(w)
    if mz.empty:
        return
    _sec("Qué se movió")
    v = mz.copy()
    # «Sin información» solo aparece cuando hay: en un período de pura Gran
    # Natural es siempre cero y una columna de ceros distrae.
    if not v["Sin información"].any():
        v = v.drop(columns=["Sin información"])
    v["% de las gestiones"] = v["% de las gestiones"].map(lambda x: f"{x * 100:.0f}%")
    v["% de entrega"] = v["% de entrega"].map(
        lambda x: "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%")
    st.dataframe(v, use_container_width=True, hide_index=True)
    st.caption("Las mismas gestiones de la barra de arriba, partidas por tipo. "
               "Instalación es FL-4 (cliente nuevo), cambio es FL-1/3/5 y retiro "
               "es FL-2. La meta semanal no distingue: los tres suman igual. "
               "«En ruta», «sin despacho» y «sin información» son los tres "
               "estados que suma la tarjeta «Sin confirmar».")


# Columnas del seguimiento que se muestran en pantalla, en el orden en que se
# leen: primero cuánto lleva, después quién y dónde, y al final el texto largo.
# El CSV se baja completo.
_COLS_SEG_VISTA = ["Días desde el rechazo", "Fecha rechazo",
                   "Estado post-rechazo", "Vendedor", "Cliente", "Comuna",
                   "Movimiento", "Motivo", "Reintento",
                   "Lo que dijo el repartidor", "Documento"]


def _entre(seg: pd.DataFrame, desde, hasta) -> pd.DataFrame:
    """Los rechazos cuya fecha de rechazo (la de la ruta) cae entre dos fechas."""
    if seg is None or seg.empty:
        return seg
    f = pd.to_datetime(seg["Fecha rechazo"])
    return seg[f.between(pd.Timestamp(desde), pd.Timestamp(hasta))]


def _seguimiento(seg_todo: pd.DataFrame, desde_def) -> None:
    """
    Qué pasó con los rechazos: si volvieron a ingresarse y cómo terminaron.

    Mira una ventana más ancha que el resto de la página a propósito: el trabajo
    que deja un rechazo no vence el domingo, y la pregunta de gerencia es
    "¿cuáles siguen sueltos?", no "¿cuáles se rechazaron esa semana?".

    Por defecto, las últimas 8 semanas (lo mismo que cuenta la advertencia de
    arriba). El filtro deja ir hasta el primer despacho cargado. `seg_todo` ya
    trae todos los rechazos de la historia con su reintento resuelto, así que
    mover las fechas solo filtra: no vuelve a la base.
    """
    st.divider()
    _sec("Qué pasó con los rechazos")
    hoy = datetime.date.today()
    c1, c2 = st.columns([1.3, 3])
    with c1:
        sel = st.date_input(
            "Rechazados entre", value=(desde_def, hoy),
            min_value=_PRIMER_DESPACHO, max_value=hoy,
            format="DD/MM/YYYY", key="cm_seg_fechas",
            help=f"Se filtra por la fecha del rechazo (la de la ruta). El límite "
                 f"es el {_PRIMER_DESPACHO:%d/%m/%Y}: el primer despacho cargado "
                 f"de Autoventa. Antes no hay con qué saber si una máquina se "
                 f"entregó o volvió, y Acuña no tiene despachos en ninguna fecha.")
    desde, hasta = (sel if isinstance(sel, (list, tuple)) and len(sel) == 2
                    else (desde_def, hoy))
    with c2:
        st.markdown(
            '<div style="padding-top:1.9rem;color:var(--gris);font-size:.85rem">'
            f'Por defecto, las últimas {_SEMANAS_TENDENCIA} semanas. Se puede ir '
            f'hasta el <b>{_PRIMER_DESPACHO:%d/%m/%Y}</b>, el primer despacho '
            'cargado: antes de eso no existen rechazos registrados.'
            '</div>', unsafe_allow_html=True)

    seg = _entre(seg_todo, desde, hasta)
    res = resumen_rechazos(seg)
    if seg is None or seg.empty:
        st.success("Ningún rechazo en esas fechas.")
        return

    k = st.columns(4)
    k[0].metric("Rechazos", res["n"],
                help=f"Rechazados entre el {desde:%d/%m/%Y} y el {hasta:%d/%m/%Y}, "
                     "no solo los de la semana elegida.")
    k[1].metric("Ya entregados", res["ok"],
                f"{res['pct'] * 100:.0f}% recuperado" if res["pct"] else None,
                help="Se volvieron a ingresar y el segundo intento llegó.")
    k[2].metric("Reingresados sin cerrar", res["en_curso"],
                help="Volvieron a ingresarse y todavía no hay resultado: van en "
                     "camino, esperan documento, o el segundo intento también "
                     "se rechazó (ese segundo documento tiene su propia fila).")
    k[3].metric("Sin retomar", res["abiertos"],
                f"{res['vencidos']} sobre {DIAS_PARA_REINTENTAR} días"
                if res["vencidos"] else None, delta_color="inverse",
                help="Nadie los volvió a ingresar. Esta es la lista que hay que "
                     "mandarle a los vendedores.")

    st.caption(
        "**Cómo se cruza.** Ningún sistema guarda el número de serie de la "
        "máquina, así que un reintento se reconoce por **mismo cliente + mismo "
        "tipo de movimiento + fecha posterior al rechazo**: primero se busca "
        "otro documento de flete que cumpla eso (y su estado de entrega es el "
        "estado post-rechazo), y si no hay, un pedido ingresado después y "
        "todavía sin DTE. La columna «Reintento» muestra con qué documento o "
        "pedido se emparejó, para poder verificarlo.")

    # Los que nadie retomó van en la tabla principal y el resto en un desplegable
    # —no en un checkbox— a propósito: un checkbox reejecuta la página entera y
    # vuelve a bajar las ocho semanas de la base para mostrar filas que ya están
    # calculadas. El expander es puro layout y no cuesta una consulta.
    abiertos = seg[seg["_abierto"]]
    if abiertos.empty:
        st.success("Todos los rechazos de esas fechas ya se retomaron.")
    else:
        st.dataframe(abiertos[_COLS_SEG_VISTA], use_container_width=True,
                     hide_index=True)
    resto = seg[~seg["_abierto"]]
    if not resto.empty:
        with st.expander(f"Ver los que ya se retomaron · {len(resto)}"):
            st.dataframe(resto[_COLS_SEG_VISTA], use_container_width=True,
                         hide_index=True)
    st.download_button(
        "⬇️ Descargar el seguimiento en CSV",
        seg.drop(columns=["_abierto"]).to_csv(index=False).encode("utf-8-sig"),
        f"rechazos_seguimiento_{datetime.date.today():%Y%m%d}.csv",
        "text/csv", key="dl_seg")


def _grafico_tendencia(mov: pd.DataFrame, f_fin, meta_g):
    """Barras apiladas por estado, semana a semana, con la meta y el % encima."""
    m = mov.copy()
    m["_sem"] = m["fecha"].dt.to_period("W-SUN")
    fin = pd.Timestamp(f_fin).to_period("W-SUN")
    semanas = pd.period_range(end=fin, periods=_SEMANAS_TENDENCIA, freq="W-SUN")
    etq = [f"{p.start_time:%d/%m}" for p in semanas]

    def serie(estados):
        g = m[m["Estado entrega"].isin(estados)].groupby("_sem").size()
        return [int(g.get(p, 0)) for p in semanas]

    fig = go.Figure()
    for nombre, estados, color in [
            ("Entregadas", [ENTREGADA], _C["verde"]),
            ("En ruta", [EN_RUTA], _C["amrl"]),
            ("Sin despacho", [SIN_DESPACHO], _C["gris"]),
            ("Sin información", [SIN_INFO], _C["gris2"]),
            ("Rechazadas", [RECHAZADA], _C["rojo"])]:
        fig.add_trace(go.Bar(x=etq, y=serie(estados), name=nombre,
                             marker_color=color))
    tot = [int((m["_sem"] == p).sum()) for p in semanas]
    ent = serie([ENTREGADA])
    base = [t - s for t, s in zip(tot, serie([SIN_INFO]))]
    fig.add_trace(go.Scatter(
        x=etq, y=tot, mode="text", showlegend=False,
        text=[f"{e / b * 100:.0f}%" if b else "" for e, b in zip(ent, base)],
        textposition="top center", textfont=dict(size=11, color=_C["slate"]),
        hoverinfo="skip"))
    if meta_g:
        fig.add_hline(y=meta_g, line_dash="dash", line_color=_C["rosa"],
                      annotation_text=f"meta {meta_g}",
                      annotation_position="top left")
    fig.update_layout(barmode="stack", height=340,
                      margin=dict(l=0, r=0, t=20, b=0),
                      legend=dict(orientation="h", y=-0.18),
                      xaxis=dict(title="semana que empieza el"),
                      yaxis=dict(showgrid=False, title="gestiones"),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Cada barra es una semana de gestiones, partida según cómo "
               "terminó. El número de arriba es su % de entrega. Las últimas "
               "semanas siempre tienen más amarillo: todavía van en ruta.")


def _seccion_plegada(client, mov, ped, desp, f_ini, f_fin, metas, w=None):
    """Todo lo que no es la pregunta de la semana, fuera de la vista."""
    st.divider()

    # Pedidos que el vendedor ya ingresó y aún no tienen documento: todavía no
    # son gestiones, por eso van aparte y sin mezclarse con los números de arriba.
    cola = (ped[ped["_sin_dte"] & ~ped["_fantasma"]].copy()
            if ped is not None and not ped.empty else pd.DataFrame())
    with st.expander(f"📥 Pedidos esperando documento · {len(cola)}"):
        st.caption("Pedidos de flete que el vendedor ya ingresó y que todavía no "
                   "tienen DTE. Aún no son gestiones: cuando se emita el "
                   "documento, pasan a contar arriba en esa semana. Salen todos "
                   "los abiertos, sin importar la semana elegida.")
        if cola.empty:
            st.success("No hay pedidos esperando documento.")
        else:
            # Veinte pedidos en cola no son lo mismo si son retiros que si son
            # instalaciones: los primeros son parque que sigue en la calle, los
            # segundos venta que todavía no empieza.
            cuenta = cola["_mov"].value_counts()
            cols_m = st.columns(4)
            for col, mv in zip(cols_m, ("nueva", "cambio", "retiro")):
                col.metric(_MOV_PL[mv], int(cuenta.get(mv, 0)))
            cols_m[3].metric("Sin clasificar", int(cola["_mov"].isna().sum()),
                             help="Líneas de flete con un código FL que no es "
                                  "FL-1/2/3/4/5.")
            vmap = (dict(mov.drop_duplicates("vendedor_id")
                         .set_index("vendedor_id")["Vendedor"])
                    if mov is not None and not mov.empty else {})
            nombres = _nombres_cliente(client)
            det = pd.DataFrame({
                "Días esperando": (pd.Timestamp(datetime.date.today())
                                   - cola["_ingreso"]).dt.days,
                "Fecha pedido": cola["_ingreso"].dt.date,
                "N° pedido": cola["n_pedido"],
                "Movimiento": cola["_mov"].map(_MOV).fillna("(otro)"),
                "Vendedor": cola["vendedor_id"].map(vmap).fillna("—"),
                "Cliente": cola["cliente_rut"].map(nombres).fillna(cola["cliente_rut"]),
            }).sort_values("Días esperando", ascending=False)
            st.dataframe(det, use_container_width=True, hide_index=True)
            st.download_button("⬇️ Descargar en CSV",
                               det.to_csv(index=False).encode("utf-8-sig"),
                               f"sin_dte_{datetime.date.today():%Y%m%d}.csv",
                               "text/csv", key="dl_cola")

    _cola_despacho(mov, f_ini, f_fin)

    with st.expander("🚚 Entregas en pesos por transportista (helados y máquinas)"):
        _entregas_pesos(client, f_ini, f_fin, mov, desp)

    with st.expander("📘 Informes Excel"):
        st.caption("Mismos números que esta página, con el detalle de cada "
                   "gestión, rechazo y pendiente. El informe completo de 19 hojas "
                   "sigue en Análisis → Máquinas.")
        if st.button("Generar informe de gerencia", key="btn_informe_gerencia"):
            with st.spinner("Armando el informe…"):
                from app.export_maquinas_gerencia import libro_gerencia
                try:
                    cli_dim = get_dim_cliente_full(client)
                except Exception:
                    cli_dim = None
                # Se pasan las 8 semanas: el Excel filtra el período y usa las
                # anteriores para su hoja de tendencia, igual que la página.
                data = libro_gerencia(
                    mov, ped, f_ini, f_fin, metas, "Ambas", cli_dim,
                    seg_desde=datetime.date.today()
                    - datetime.timedelta(weeks=_SEMANAS_TENDENCIA))
            st.session_state["_cm_libro"] = ((str(f_ini), str(f_fin)), data)
        g = st.session_state.get("_cm_libro")
        if g and g[0] == (str(f_ini), str(f_fin)):
            st.download_button(
                "⬇️ Descargar informe de gerencia", g[1],
                f"maquinas_gerencia_{f_ini:%Y%m%d}_{f_fin:%Y%m%d}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key="dl_cm")

    with st.expander(f"🎯 Metas de {f_ini.month:02d}/{f_ini.year}"):
        _form_metas(client, f_ini.year, f_ini.month, metas)


def _cola_despacho(mov: pd.DataFrame, f_ini, f_fin) -> None:
    """
    Facturados que todavía no tienen ruta, de TODA la historia hasta hoy.

    Es la hermana de la cola de pedidos sin documento, un paso más adelante del
    recorrido: ahí falta que logística emita el DTE, aquí falta que lo suba a un
    camión. Las dos son de logística y las dos envejecen, por eso van juntas y
    fuera del período: un documento facturado hace tres semanas sin ruta es peor
    que uno del jueves pasado, y mirando solo la semana elegida desaparece de la
    vista apenas se cambia de semana.

    No incluye «Sin información» (Acuña, o un mes sin despachos cargados): de
    esas no se puede afirmar que falte la ruta, simplemente no hay con qué
    saberlo.
    """
    if mov is None or mov.empty:
        return
    # Un documento fechado mañana sin ruta todavía no está atrasado: logística
    # factura con la fecha de entrega y la ruta se arma después.
    hoy_ts = pd.Timestamp(datetime.date.today())
    cola = mov[(mov["Estado entrega"] == SIN_DESPACHO)
               & (mov["fecha"] <= hoy_ts)].copy()
    with st.expander(f"🚛 Facturados esperando despacho · {len(cola)}"):
        st.caption(
            "Documentos de flete emitidos que no aparecen en ninguna ruta, ni "
            "entregada ni rechazada ni pendiente — y en un mes que SÍ tiene "
            "despachos cargados, así que no es un archivo que falte: es un "
            "flete que no se ha programado. Salen todos, desde el primer "
            f"despacho cargado ({_PRIMER_DESPACHO:%d/%m/%Y}), sin importar el "
            "período elegido arriba.")
        if cola.empty:
            st.success("Todo lo facturado tiene ruta.")
            return
        cuenta = cola["tipo_mov"].value_counts()
        cols_m = st.columns(3)
        for col, mv in zip(cols_m, ("nueva", "cambio", "retiro")):
            col.metric(_MOV_PL[mv], int(cuenta.get(mv, 0)))
        hoy = pd.Timestamp(datetime.date.today())
        det = pd.DataFrame({
            "Días desde la factura": (hoy - cola["fecha"]).dt.days,
            "Fecha factura": cola["fecha"].dt.date,
            "Documento": cola["_doc"],
            "Movimiento": cola["tipo_mov"].map(_MOV),
            "Vendedor": cola["Vendedor"],
            "Cliente": cola["Cliente"],
            "Comuna": cola["Comuna"],
            "Sociedad": cola["Sociedad"],
        }).sort_values("Días desde la factura", ascending=False)
        st.dataframe(det, use_container_width=True, hide_index=True)
        st.caption("Los de 0 o 1 día son normales: se facturaron recién y la "
                   "ruta se programa después. Los que hay que mirar son los de "
                   "arriba de la tabla.")
        st.download_button(
            "⬇️ Descargar en CSV",
            det.to_csv(index=False).encode("utf-8-sig"),
            f"facturados_sin_despacho_{datetime.date.today():%Y%m%d}.csv",
            "text/csv", key="dl_sin_desp")


def _entregas_pesos(client, f_ini, f_fin, mov, desp):
    """Efectividad del despacho en pesos (productos) y unidades (máquinas)."""
    if desp is None or desp.empty:
        st.caption("Sin despachos cargados en el período.")
        return
    from app.data import get_ventas_rango
    from app.export_entregas import preparar_entregas, _tabla_por
    try:
        ventas = get_ventas_rango(client, f_ini, f_fin)
    except Exception:
        st.warning("No se pudieron leer las ventas del período.")
        return
    # Un período sin ventas (la próxima semana, casi siempre) llega como un
    # DataFrame sin columnas y `preparar_entregas` se caía buscando n_dcto.
    if ventas is None or ventas.empty:
        st.caption("Sin ventas facturadas en el período.")
        return
    d = preparar_entregas(ventas, desp, mov)
    d = d[d["fecha_ruta"].between(pd.Timestamp(f_ini), pd.Timestamp(f_fin))]
    if d.empty:
        st.caption("Sin despachos en el período.")
        return
    prods = d[~d["Es máquina"]]
    ent = float(prods.loc[prods["_est"] == "Entregada", "Monto facturado"].sum())
    tot = float(prods["Monto facturado"].sum())
    rech = float(prods.loc[prods["_est"] == "Rechazada", "Monto facturado"].sum())
    k1, k2 = st.columns(2)
    k1.metric("% de entrega · helados", f"{ent / tot * 100:.1f}%" if tot else "—",
              help="Plata entregada sobre la plata que salió a ruta en el período.")
    k2.metric("Rechazado · helados", fmt_clp(rech))
    t = _tabla_por(prods, "Transportista", "monto", "Transportista")
    if not t.empty:
        v = t.copy()
        for col in [x for x in v.columns if x not in ("Transportista", "% de entrega")]:
            v[col] = v[col].apply(lambda x: fmt_clp(x) if pd.notna(x) and x != "" else "")
        v["% de entrega"] = v["% de entrega"].apply(
            lambda x: f"{x * 100:.1f}%" if pd.notna(x) and x != "" else "")
        st.dataframe(v, use_container_width=True, hide_index=True)
    st.caption("Se mide sobre lo que salió a ruta en el período (fecha de ruta), "
               "no sobre lo facturado. Las máquinas no van en pesos: su flete "
               "se factura a $1.")


def _form_metas(client, anio: int, mes: int, metas: dict):
    st.caption("Se guardan por mes: cambiar la meta de este mes no reescribe "
               "contra qué se midieron los anteriores.")
    with st.form("form_metas_maquinas"):
        cols = st.columns(len(_CAMPOS_META))
        nuevos = {}
        for col, (clave, etiqueta, tipo, ayuda) in zip(cols, _CAMPOS_META):
            actual = metas.get(clave)
            with col:
                if tipo == "pct":
                    nuevos[clave] = st.number_input(
                        etiqueta, min_value=0.0, max_value=100.0,
                        value=float((actual or 0) * 100), step=1.0,
                        help=ayuda, key=f"meta_{clave}") / 100
                else:
                    nuevos[clave] = st.number_input(
                        etiqueta, min_value=0, value=int(actual or 0), step=1,
                        help=ayuda, key=f"meta_{clave}")
        guardar = st.form_submit_button("💾 Guardar metas", type="primary")
    if guardar:
        # Solo se mandan estas dos columnas: las demás metas del mes quedan
        # como estaban en la base (el Excel de gerencia todavía las usa).
        try:
            upsert_objetivos_maquinas(client, anio, mes, nuevos)
            st.success(f"Metas de {mes:02d}/{anio} guardadas.")
            st.rerun()
        except Exception as exc:
            st.error(f"No se pudieron guardar: {exc}")
