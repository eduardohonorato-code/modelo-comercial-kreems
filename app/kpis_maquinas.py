"""
Los indicadores del control de máquinas, calculados en UN solo lugar.

La sección Control de Máquinas y el informe de gerencia leen de aquí. Si cada
uno calculara lo suyo, tarde o temprano la pantalla y el Excel dirían números
distintos del mismo mes, que es la manera más rápida de que nadie le crea a
ninguno de los dos.

El recorrido que se mide es siempre el mismo, y en ese orden:

    pedido ingresado  →  DTE emitido  →  sale a ruta  →  entregada
     (vendedor)          (logística)     (logística)     (terreno)

Cada indicador cuelga de una de esas etapas y sabe de quién es.
"""
from datetime import date

import pandas as pd

from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA, SIN_DESPACHO,
                                 preparar_movimientos, _prep_pedidos,
                                 _semanas_del_rango, _pct)

# Quién responde por cada etapa (definición de gerencia, ago-2026): el pedido es
# del vendedor; emitir el DTE y despachar son de logística.
VENDEDOR, LOGISTICA, AMBOS = "Vendedor", "Logística", "Comercial + Logística"


def _fmt(valor, formato: str) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "—"
    if formato == "pct":
        return f"{valor * 100:.0f}%"
    if formato == "dec":
        return f"{valor:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{valor:,.0f}".replace(",", ".")


def _cumple(valor, meta, mejor: str):
    """None cuando no hay meta o no hay dato: mejor un guion que un semáforo falso."""
    if meta is None or valor is None or pd.isna(valor):
        return None
    return valor >= meta if mejor == "alto" else valor <= meta


def _logro(valor, meta, mejor: str):
    """
    Cumplimiento como fracción de la meta, comparable entre indicadores.

    Sin esto no se puede leer una tabla de KPIs de un vistazo: quedarse en 11 de
    22 gestiones y en 83% de un 90% se ven igual de rojos, cuando el primero es
    la mitad de la meta y el segundo está a un suspiro. Donde menos es mejor
    (rechazo, días, cola) se invierte la razón, así que 1,0 siempre significa
    "en meta", para arriba es mejor y para abajo peor.
    """
    if meta is None or valor is None or pd.isna(valor):
        return None
    if mejor == "alto":
        if meta == 0:
            return 1.0 if valor >= 0 else 0.0
        return max(valor / meta, 0.0)
    if meta == 0:                      # tolerancia cero (cola vencida)
        return 1.0 if valor == 0 else 0.0
    if valor == 0:                     # cero rechazos, cero días: inmejorable
        return 1.5
    return max(meta / valor, 0.0)


def _severidad(logro, cumple):
    if cumple is None or logro is None:
        return "sin_meta"
    if cumple:
        return "ok"
    return "alerta" if logro >= 0.8 else "critico"


def calcular_flujo(mov: pd.DataFrame, ped: pd.DataFrame, f_ini, f_fin) -> list[dict]:
    """Las cuatro etapas del recorrido, con lo que se pierde en cada salto."""
    ini, fin = pd.Timestamp(f_ini), pd.Timestamp(f_fin)
    vivos = ped[~ped["_fantasma"]] if not ped.empty else ped
    ped_per = vivos[vivos["_ingreso"].between(ini, fin)] if not vivos.empty else vivos
    ingresados = len(ped_per)
    sin_dte = int(ped_per["_sin_dte"].sum()) if ingresados else 0
    gestiones = len(mov)
    con_info = int((~mov["_sin_info"]).sum()) if gestiones else 0
    entregadas = int(mov["_entregada"].sum()) if gestiones else 0
    rechazadas = int(mov["_rechazada"].sum()) if gestiones else 0
    pendientes = int(mov["_pendiente"].sum()) if gestiones else 0
    return [
        {"etapa": "1 · Pedidos ingresados", "responsable": VENDEDOR,
         "valor": ingresados,
         "detalle": f"{sin_dte} siguen sin gestionar",
         "salto": "de estos, los que llegan a DTE"},
        {"etapa": "2 · Gestiones con DTE", "responsable": LOGISTICA,
         "valor": gestiones,
         "detalle": f"{con_info} con información de despacho",
         "salto": "de estos, los que salen a ruta"},
        {"etapa": "3 · Despachados", "responsable": LOGISTICA,
         "valor": con_info,
         "detalle": f"{pendientes} sin confirmar todavía",
         "salto": "de estos, los que se entregan"},
        {"etapa": "4 · Entregados", "responsable": LOGISTICA,
         "valor": entregadas,
         "detalle": f"{rechazadas} volvieron rechazados",
         "salto": ""},
    ]


def calcular_kpis(mov: pd.DataFrame, ped: pd.DataFrame, f_ini, f_fin,
                  metas: dict, hoy: date | None = None) -> list[dict]:
    """
    Lista de indicadores, cada uno con su valor, su meta y de quién es.

    `mov` viene de `preparar_movimientos` y `ped` de `_prep_pedidos`, así que
    esto no vuelve a tocar la base: solo mide.
    """
    hoy = hoy or date.today()
    ini, fin = pd.Timestamp(f_ini), pd.Timestamp(f_fin)
    dias = (fin - ini).days + 1
    # Un rango de una semana o menos se compara CONTRA LA META SEMANAL tal cual.
    # Dividir por los días transcurridos extrapolaría: un miércoles con 6
    # gestiones diría "14 por semana" y daría por cumplido algo que todavía no
    # pasó. Con la semana a medias, el aviso va en el detalle.
    semana_unica = dias <= 7
    semanas = 1 if semana_unica else dias / 7
    nota_semana = (f" · semana a medias: {dias} de 7 días"
                   if semana_unica and dias < 7 else "")
    # En un período de varias semanas, el indicador es el PROMEDIO semanal
    # contra la meta semanal, no la suma contra la meta: decirlo en el detalle
    # evita la pregunta de si las 22 son de la semana o del período entero.
    nota_prom = ("" if semana_unica
                 else f" · promedio de {semanas:.0f} semanas")

    vacio = mov.empty
    tot = len(mov)
    ent = int(mov["_entregada"].sum()) if not vacio else 0
    rech = int(mov["_rechazada"].sum()) if not vacio else 0
    sin_info = int(mov["_sin_info"].sum()) if not vacio else 0
    con_info = tot - sin_info
    nuevas = int((mov["tipo_mov"] == "nueva").sum()) if not vacio else 0
    retiros = int((mov["tipo_mov"] == "retiro").sum()) if not vacio else 0
    nue_ent = int(((mov["tipo_mov"] == "nueva") & mov["_entregada"]).sum()) if not vacio else 0
    nue_info = int(((mov["tipo_mov"] == "nueva") & ~mov["_sin_info"]).sum()) if not vacio else 0

    # Los pedidos fantasma (anulados o reingresados con otro número: ya no vienen
    # en la API) quedan fuera del numerador Y del denominador. Contarlos como
    # "no concretados" castiga por un pedido que dejó de existir, y era lo que
    # hundía el % concretado de agosto a 29% cuando la verdad era 33%.
    ped_vivos = ped[~ped["_fantasma"]] if not ped.empty else ped
    ped_per = (ped_vivos[ped_vivos["_ingreso"].between(ini, fin)]
               if not ped_vivos.empty else ped_vivos)
    ingresados = len(ped_per)
    if not ped.empty:
        cola = ped[ped["_sin_dte"] & ~ped["_fantasma"]]
        edad = (pd.Timestamp(hoy) - cola["_ingreso"]).dt.days
        cola_vencida = int((edad > 30).sum())
        concretado = float((~ped_per["_sin_dte"]).mean()) if ingresados else None
        dias_gestion = ped_vivos["_dias_a_dte"].median()
        n_cola = len(cola)
    else:
        cola_vencida, concretado, dias_gestion, n_cola = 0, None, None, 0

    # Meses del rango, para las metas que se fijan por mes y no por período
    n_meses = len(pd.period_range(ini, fin, freq="M"))
    meta_parque = metas.get("meta_parque_neto")

    k = [
        dict(clave="pedidos_semana", grupo="Volumen",
             nombre="Pedidos ingresados por semana",
             valor=ingresados / semanas if semanas else None,
             meta=metas.get("meta_pedidos_semana"), formato="dec", mejor="alto",
             responsable=VENDEDOR,
             detalle=(f"{ingresados} pedidos en el período"
                      f"{nota_semana}{nota_prom}")),
        dict(clave="gestiones_semana", grupo="Volumen",
             nombre="Gestiones con DTE por semana",
             valor=tot / semanas if semanas else None,
             meta=metas.get("meta_gestiones_semana"), formato="dec", mejor="alto",
             responsable=AMBOS,
             detalle=(f"{tot} gestiones · meta semanal del equipo, no por "
                      f"vendedor{nota_semana}{nota_prom}")),
        dict(clave="pct_concretado", grupo="Gestión",
             nombre="% Concretado (pedido a DTE)",
             valor=concretado, meta=metas.get("meta_pct_concretado"),
             formato="pct", mejor="alto", responsable=LOGISTICA,
             detalle=f"{n_cola} pedidos en cola hoy"),
        dict(clave="dias_gestion", grupo="Gestión",
             nombre="Días de ingreso a DTE (mediana)",
             valor=float(dias_gestion) if dias_gestion is not None
             and not pd.isna(dias_gestion) else None,
             meta=metas.get("meta_dias_gestion"), formato="dec", mejor="bajo",
             responsable=LOGISTICA, detalle="cuánto espera un pedido"),
        dict(clave="cola_vencida", grupo="Gestión",
             nombre="Cola vencida (más de 30 días)",
             valor=cola_vencida, meta=metas.get("meta_cola_vencida"),
             formato="num", mejor="bajo", responsable=LOGISTICA,
             detalle="pedidos sin DTE que ya llevan un mes"),
        dict(clave="pct_entregado", grupo="Terreno",
             nombre="% Entregado",
             valor=_pct(ent, con_info) if con_info else None,
             meta=metas.get("meta_pct_entregado"), formato="pct", mejor="alto",
             responsable=LOGISTICA,
             detalle=f"{ent} de {con_info} con información"),
        dict(clave="pct_rechazo", grupo="Terreno",
             nombre="% Rechazo",
             valor=_pct(rech, con_info) if con_info else None,
             meta=metas.get("meta_pct_rechazo"), formato="pct", mejor="bajo",
             responsable=LOGISTICA, detalle=f"{rech} despachos rechazados"),
        dict(clave="conversion_inst", grupo="Terreno",
             nombre="Conversión de instalación",
             valor=_pct(nue_ent, nue_info) if nue_info else None,
             meta=metas.get("meta_conversion_inst"), formato="pct", mejor="alto",
             responsable=AMBOS,
             detalle=f"{nue_ent} de {nue_info} instalaciones confirmadas"),
        dict(clave="parque_neto", grupo="Resultado",
             nombre="Parque neto del período",
             valor=nuevas - retiros,
             meta=(meta_parque * n_meses if meta_parque is not None else None),
             formato="num", mejor="alto", responsable=AMBOS,
             detalle=(f"{nuevas} instalaciones contra {retiros} retiros"
                      + (f" · meta de {_fmt(meta_parque, 'num')} por mes × "
                         f"{n_meses} meses" if meta_parque is not None
                         and n_meses > 1 else ""))),
        dict(clave="cobertura", grupo="Control del dato",
             nombre="Cobertura del cruce",
             valor=_pct(con_info, tot) if tot else None,
             meta=0.95, formato="pct", mejor="alto", responsable=LOGISTICA,
             detalle="sin esto, lo de terreno no se puede leer"),
    ]
    for x in k:
        x["cumple"] = _cumple(x["valor"], x["meta"], x["mejor"])
        x["logro"] = _logro(x["valor"], x["meta"], x["mejor"])
        x["severidad"] = _severidad(x["logro"], x["cumple"])
        x["logro_txt"] = ("—" if x["logro"] is None
                          else f'{min(x["logro"], 9.99) * 100:.0f}%')
        x["valor_txt"] = _fmt(x["valor"], x["formato"])
        x["meta_txt"] = _fmt(x["meta"], x["formato"])
    return k


def generar_alertas(mov: pd.DataFrame, ped: pd.DataFrame, kpis: list,
                    hoy: date | None = None) -> list[dict]:
    """
    Lo que hay que hacer algo al respecto, en orden de urgencia.

    Un tablero en rojo no dice qué hacer el lunes. Esto traduce cada indicador
    fuera de meta a una frase con el número concreto y de quién es, y deja fuera
    lo que está en meta: si todo aparece, nada resalta.
    """
    hoy = hoy or date.today()
    por_clave = {k["clave"]: k for k in kpis}
    out = []

    def _add(clave, texto, accion):
        k = por_clave.get(clave)
        if not k or k["severidad"] in ("ok", "sin_meta"):
            return
        out.append({"severidad": k["severidad"], "indicador": k["nombre"],
                    "texto": texto, "accion": accion,
                    "responsable": k["responsable"], "logro": k["logro"] or 0})

    if not ped.empty:
        cola = ped[ped["_sin_dte"] & ~ped["_fantasma"]]
        if len(cola):
            edad = (pd.Timestamp(hoy) - cola["_ingreso"]).dt.days
            viejos = int((edad > 30).sum())
            _add("cola_vencida",
                 f"{viejos} pedidos llevan más de 30 días sin documento "
                 f"(el más antiguo, {int(edad.max())} días)",
                 "Emitir o cerrar los más viejos: el vendedor ya hizo su parte")
        _add("pct_concretado",
             f"{len(cola)} pedidos ingresados siguen sin DTE",
             "Revisar qué los traba antes de pedir más volumen")
    _add("dias_gestion",
         f"un pedido espera {por_clave['dias_gestion']['valor_txt']} días de "
         "mediana hasta que se emite el documento",
         "Acortar el tiempo de emisión")

    if not mov.empty:
        rech = mov[mov["_rechazada"]]
        if len(rech):
            top = rech["Motivo del rechazo"].value_counts()
            motivo = top.index[0] if len(top) else "sin motivo"
            _add("pct_rechazo",
                 f"{len(rech)} despachos volvieron rechazados; el motivo más "
                 f"común es «{motivo}» ({int(top.iloc[0])} casos)",
                 "Coordinar con el cliente antes de subir la máquina al camión")
        nuevas = int((mov["tipo_mov"] == "nueva").sum())
        retiros = int((mov["tipo_mov"] == "retiro").sum())
        _add("parque_neto",
             f"el parque cayó {retiros - nuevas} máquinas: {nuevas} "
             f"instalaciones contra {retiros} retiros",
             "Sin instalaciones nuevas, la meta de gestiones se cumple retirando")
        nue_info = int(((mov["tipo_mov"] == "nueva") & ~mov["_sin_info"]).sum())
        nue_ent = int(((mov["tipo_mov"] == "nueva") & mov["_entregada"]).sum())
        if nue_info:
            _add("conversion_inst",
                 f"{nue_info - nue_ent} de {nue_info} instalaciones no se "
                 "confirmaron en terreno",
                 "Cada una es una máquina facturada que no está dando venta")
        _add("pct_entregado",
             f"{int(mov['_pendiente'].sum())} movimientos siguen sin confirmar "
             "entrega", "Cerrar los despachos pendientes")
        sin_info = int(mov["_sin_info"].sum())
        if sin_info:
            _add("cobertura",
                 f"{sin_info} movimientos no tienen despacho con que cruzarse",
                 "Cargar el Excel de despachos del mes; Acuña nunca lo tendrá")
    _add("gestiones_semana",
         f"el equipo va en {por_clave['gestiones_semana']['valor_txt']} "
         f"gestiones por semana contra una meta de "
         f"{por_clave['gestiones_semana']['meta_txt']}",
         "Es el volumen del que dependen los demás indicadores")

    orden = {"critico": 0, "alerta": 1}
    return sorted(out, key=lambda a: (orden.get(a["severidad"], 2), a["logro"]))


def semanal(mov: pd.DataFrame, ped: pd.DataFrame, f_ini, f_fin,
            meta_semana: int | None) -> pd.DataFrame:
    """Una fila por semana: pedidos ingresados, gestiones y cumplimiento."""
    grupos = (dict(tuple(mov.groupby(mov["fecha"].dt.to_period("W-SUN"))))
              if not mov.empty else {})
    ing = (ped["_ingreso"].dt.to_period("W-SUN").value_counts().to_dict()
           if not ped.empty else {})
    filas = []
    for per, dias_en_rango in _semanas_del_rango(f_ini, f_fin):
        g = grupos.get(per)
        n = len(g) if g is not None else 0
        filas.append({
            "Semana": f"{per.start_time:%d/%m} al {per.end_time:%d/%m}",
            "Días en el rango": dias_en_rango,
            "Pedidos ingresados": int(ing.get(per, 0)),
            "Gestiones con DTE": n,
            "Meta": meta_semana if dias_en_rango == 7 else None,
            "% Meta": (_pct(n, meta_semana)
                       if dias_en_rango == 7 and meta_semana else None),
            "Entregadas": int(g["_entregada"].sum()) if g is not None else 0,
            "Rechazadas": int(g["_rechazada"].sum()) if g is not None else 0,
        })
    return pd.DataFrame(filas)


def cargar_todo(client, f_ini, f_fin, soc_ids=None, dias_antes: int = 180,
                dias_despues: int = 90):
    """
    Trae de la base todo lo que necesitan los indicadores y lo deja preparado.

    Devuelve `(movimientos, pedidos, despachos)`. La ventana de despachos es más
    ancha que el período a propósito: la ruta de una máquina facturada a fin de
    mes cae en el mes siguiente.
    """
    from datetime import timedelta

    from app.data import (get_maquinas_rango, get_despachos_rango, get_lineas_fl,
                          get_pedidos_fl_todos, get_todos_vendedores,
                          get_dim_cliente_full, get_dim_sociedad)

    maq = get_maquinas_rango(client, f_ini, f_fin, soc_ids)
    desp = get_despachos_rango(client, f_ini - timedelta(days=dias_antes),
                               f_fin + timedelta(days=dias_despues), soc_ids)
    fl = get_lineas_fl(client, f_ini, f_fin, soc_ids)
    ped = get_pedidos_fl_todos(client, soc_ids)
    try:
        vend = get_todos_vendedores(client)
    except Exception:
        vend = None
    try:
        cli = get_dim_cliente_full(client)
    except Exception:
        cli = None
    try:
        df_soc = get_dim_sociedad(client)
        socs = dict(zip(df_soc["id"], df_soc["nombre"])) if not df_soc.empty else {}
    except Exception:
        socs = {}

    mov = (preparar_movimientos(maq, desp, fl, vend, cli, socs)
           if maq is not None and not maq.empty else pd.DataFrame())
    return mov, _prep_pedidos(ped), desp
