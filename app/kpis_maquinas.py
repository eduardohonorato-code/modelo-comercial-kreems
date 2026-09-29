"""
Los indicadores del control de máquinas, calculados en UN solo lugar.

La sección Control de Máquinas y el informe de gerencia leen de aquí. Si cada
uno calculara lo suyo, tarde o temprano la pantalla y el Excel dirían números
distintos del mismo mes, que es la manera más rápida de que nadie le crea a
ninguno de los dos.

Todo se cuenta sobre las gestiones del período (fletes con DTE emitido), y de
cada una se sigue qué pasó: si se entregó, si volvió rechazada y, si volvió,
si alguien la reingresó y si el cliente siguió comprando. Los indicadores del
recorrido anterior (% concretado, días de gestión, cola vencida, parque neto)
salieron en sep-2026 junto con su código.
"""
from datetime import date

import pandas as pd

from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA, SIN_DESPACHO,
                                 preparar_movimientos, _prep_pedidos)


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


def conteo_semana(w: pd.DataFrame) -> dict:
    """
    Los números de un grupo de gestiones, todos sobre ese mismo grupo.

    Lo usan la página Control de Máquinas y el Excel de gerencia, para que
    nunca muestren cifras distintas de la misma semana. `pct` se calcula sobre
    las gestiones con información de despacho (Acuña nunca la tiene).
    """
    from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA,
                                     SIN_DESPACHO, SIN_INFO)
    est = (w["Estado entrega"] if w is not None and not w.empty
           else pd.Series(dtype=str))
    n = len(est)
    ent = int((est == ENTREGADA).sum())
    rech = int((est == RECHAZADA).sum())
    ruta = int((est == EN_RUTA).sum())
    sin_desp = int((est == SIN_DESPACHO).sum())
    sin_info = int((est == SIN_INFO).sum())
    base = n - sin_info
    # Las tres maneras de no saber cómo terminó una gestión. Se suman aquí y no
    # en cada tarjeta: mientras cada pantalla elegía su propia combinación, la
    # tarjeta decía 2 y la tabla decía otra cosa.
    sin_confirmar = ruta + sin_desp + sin_info
    return dict(n=n, ent=ent, rech=rech, ruta=ruta, sin_desp=sin_desp,
                sin_info=sin_info, sin_confirmar=sin_confirmar, base=base,
                pct=(ent / base) if base else None)


# ── Qué se movió: la mezcla por tipo de movimiento ───────────────────────────

_MOV_LBL = {"nueva": "Instalación", "cambio": "Cambio", "retiro": "Retiro"}
_ORDEN_MOV = ["nueva", "cambio", "retiro"]
_MOV_SG = {"nueva": "instalación", "cambio": "cambio", "retiro": "retiro"}
_MOV_PL = {"nueva": "instalaciones", "cambio": "cambios", "retiro": "retiros"}


def etiqueta_mov(tipo: str, n: int) -> str:
    """«3 retiros» / «1 retiro»: la mezcla se escribe en prosa en varios lados."""
    return f"{n} {(_MOV_SG if n == 1 else _MOV_PL).get(tipo, tipo)}"


def mezcla_movimientos(w: pd.DataFrame) -> pd.DataFrame:
    """
    Las gestiones del período partidas por tipo de movimiento.

    "22 gestiones" no dice lo mismo si son 18 retiros que si son 12
    instalaciones: la meta se puede cumplir desinstalando el parque. Cada fila
    trae además cómo terminó ese tipo, porque una instalación rechazada y un
    retiro rechazado son problemas distintos —uno es un cliente que se
    arrepintió, el otro uno que no devuelve la máquina.
    """
    cols = ["Movimiento", "Gestiones", "% de las gestiones", "Entregadas",
            "En ruta", "Sin despacho", "Sin información", "Rechazadas",
            "% de entrega"]
    if w is None or w.empty:
        return pd.DataFrame(columns=cols)

    def fila(nombre, g):
        c = conteo_semana(g)
        # Sin despacho y Sin información van SEPARADOS: el primero es un
        # documento que debería aparecer en el Excel de despachos y no está
        # (hay que ir a buscarlo), el segundo es Acuña o un mes sin cargar, que
        # nunca se va a poder confirmar. Estuvieron fundidos bajo un solo
        # rótulo y la tarjeta contaba uno y la tabla los dos.
        return {"Movimiento": nombre, "Gestiones": c["n"],
                "% de las gestiones": c["n"] / len(w),
                "Entregadas": c["ent"], "En ruta": c["ruta"],
                "Sin despacho": c["sin_desp"], "Sin información": c["sin_info"],
                "Rechazadas": c["rech"], "% de entrega": c["pct"]}

    filas = [fila(_MOV_LBL[mv], w[w["tipo_mov"] == mv])
             for mv in _ORDEN_MOV if (w["tipo_mov"] == mv).any()]
    otros = w[~w["tipo_mov"].isin(_ORDEN_MOV)]
    if not otros.empty:
        filas.append(fila("(otro)", otros))
    return pd.DataFrame(filas, columns=cols)


def mezcla_texto(w: pd.DataFrame) -> str:
    """La mezcla en una línea, para el subtítulo de una tarjeta."""
    if w is None or w.empty:
        return ""
    n = w["tipo_mov"].value_counts()
    return " · ".join(etiqueta_mov(mv, int(n[mv]))
                      for mv in _ORDEN_MOV if n.get(mv))


# ── Seguimiento de los rechazos ──────────────────────────────────────────────

# Cuánto se le da a un rechazo para volver a aparecer antes de que la página lo
# reclame. Una semana: la ruta es semanal, así que un rechazo del lunes pasado
# ya debería estar reingresado, o descartado a conciencia.
DIAS_PARA_REINTENTAR = 7

REINTENTO_OK = "Reingresado y entregado"
REINTENTO_RUTA = "Reingresado, va en camino"
REINTENTO_SININFO = "Reingresado, sin información"
REINTENTO_RECHAZO = "Se volvió a rechazar"
REINTENTO_PEDIDO = "Pedido reingresado, esperando DTE"
REINTENTO_OTRO = "Otro movimiento del cliente"
SIN_REINTENTO = "Sin reintento"
# Logística registra sus anulaciones como un "rechazo" en una ruta ficticia
# («Nulas mes en curso»): pedido duplicado, cliente que desiste, cambio que no
# hacía falta. No es un rechazo en terreno ni hay nada que reintentar, y sin
# esta marca el pedido duplicado de Farmacia Economik (5607, sep-2026) salía
# como «Sin reintento» y disparaba la advertencia.
ANULADA = "Anulada por logística"
_RUTA_ANULACION = "nula"

# Los que siguen pidiendo que alguien haga algo. Solo «Sin reintento»: el
# entregado está cerrado, el pedido reingresado es de logística (falta el DTE), y
# «Se volvió a rechazar» NO va aquí aunque suene urgente, porque ese segundo
# documento rechazado tiene su propia fila en esta misma tabla y aparecería como
# «Sin reintento» si nadie lo retomó. Contar los dos pondría el mismo caso dos
# veces en la lista que se le manda al vendedor.
ABIERTOS = (SIN_REINTENTO,)

_COLS_SEG = ["Fecha rechazo", "Fecha documento", "Documento", "Cliente", "RUT",
             "Comuna", "Vendedor", "Movimiento", "Motivo",
             "Lo que dijo el repartidor", "Transportista",
             "Estado post-rechazo", "Reintento", "Días desde el rechazo",
             "_abierto"]

_POST = {ENTREGADA: REINTENTO_OK, RECHAZADA: REINTENTO_RECHAZO,
         EN_RUTA: REINTENTO_RUTA, SIN_DESPACHO: REINTENTO_RUTA}


def seguimiento_rechazos(rech: pd.DataFrame, mov: pd.DataFrame,
                         ped: pd.DataFrame, hoy: date | None = None
                         ) -> pd.DataFrame:
    """
    Qué pasó DESPUÉS de cada rechazo: si se volvió a ingresar y cómo terminó
    esa segunda vuelta.

    **El cruce.** Ninguna de las dos fuentes trae número de serie de la máquina,
    así que un reintento no se puede emparejar con su rechazo por identidad del
    equipo. Lo que sí identifica el caso es **cliente + tipo de movimiento +
    fecha posterior al rechazo**, y se busca en este orden:

      1. ¿Hay otro documento de flete del mismo cliente y del mismo tipo con
         fecha igual o posterior a la del rechazo? Ese es el reintento, y su
         estado de entrega es el estado post-rechazo. Esto es lo que responde
         "de los que se rechazaron, cuáles ya están entregados".
      2. Si no, ¿hay un pedido FL del mismo cliente y tipo ingresado después del
         rechazo y todavía sin DTE? El vendedor ya lo reingresó y la pelota está
         en logística.
      3. Si no, ¿hubo cualquier otro movimiento del cliente después? Se informa
         aparte: un retiro rechazado que reaparece como cambio es una decisión
         comercial, no un reintento.
      4. Si no hay nada, queda «Sin reintento» con los días que lleva así.

    Lo que el cruce NO puede distinguir: un cliente con dos máquinas del mismo
    tipo de movimiento en la misma ventana. Son pocos casos, y la fila muestra
    el documento con que se emparejó para poder verificarlo a mano.

    Una trampa que conviene tener clara: un documento rechazado que DESPUÉS se
    entregó en un segundo intento del mismo camión ya no llega hasta aquí
    —`preparar_movimientos` resuelve cada documento por prioridad
    Entregada > Rechazada—, así que toda fila de esta tabla es un documento que
    nunca se entregó.

    `mov` tiene que venir hasta hoy, no hasta el fin del período mirado: el
    reintento de un rechazo de hace tres semanas ocurre después de esa semana.
    """
    hoy_ts = pd.Timestamp(hoy or date.today())
    if rech is None or rech.empty:
        return pd.DataFrame(columns=_COLS_SEG)

    otros = mov if mov is not None and not mov.empty else pd.DataFrame()
    cola = (ped[ped["_sin_dte"] & ~ped["_fantasma"]]
            if ped is not None and not ped.empty else pd.DataFrame())

    filas = []
    for _, r in rech.iterrows():
        ref = r["Fecha ruta"] if pd.notna(r.get("Fecha ruta")) else r["fecha"]
        estado, detalle = SIN_REINTENTO, ""
        anulada = _RUTA_ANULACION in str(r.get("Transportista") or "").lower()

        cand = (otros[(otros["cliente_rut"] == r["cliente_rut"])
                      & (otros["_doc"] != r["_doc"])
                      & (otros["fecha"] >= ref)]
                if not otros.empty else pd.DataFrame())
        mismo = cand[cand["tipo_mov"] == r["tipo_mov"]] if not cand.empty else cand

        if anulada:
            estado = ANULADA
            detalle = str(r.get("Comentario de entrega") or "").strip() or "sin detalle"
        elif not mismo.empty:
            n = mismo.sort_values("fecha").iloc[0]
            estado = _POST.get(n["Estado entrega"], REINTENTO_SININFO)
            detalle = f"doc {n['_doc']} del {n['fecha']:%d/%m}"
            if pd.notna(n["Fecha ruta"]):
                detalle += f", ruta {n['Fecha ruta']:%d/%m}"
        else:
            pv = (cola[(cola["cliente_rut"] == r["cliente_rut"])
                       & (cola["_mov"] == r["tipo_mov"])
                       & (cola["_ingreso"] >= ref)]
                  if not cola.empty else pd.DataFrame())
            if not pv.empty:
                p = pv.sort_values("_ingreso").iloc[0]
                estado = REINTENTO_PEDIDO
                detalle = (f"pedido {p['n_pedido']} del {p['_ingreso']:%d/%m}, "
                           f"sin DTE hace {(hoy_ts - p['_ingreso']).days} días")
            elif not cand.empty:
                n = cand.sort_values("fecha").iloc[0]
                estado = REINTENTO_OTRO
                detalle = (f"{_MOV_LBL.get(n['tipo_mov'], n['tipo_mov'])} "
                           f"doc {n['_doc']} del {n['fecha']:%d/%m} "
                           f"({n['Estado entrega']})")

        dias = int((hoy_ts - ref).days)
        if estado == SIN_REINTENTO:
            detalle = f"{dias} días sin volver a ingresarlo"
        # Cuando dim_cliente no se pudo leer, `preparar_movimientos` deja
        # "(sin dato)" en todas las filas: ahí el RUT es más útil que nada.
        cliente = str(r.get("Cliente") or "").strip()
        if not cliente or cliente == "(sin dato)":
            cliente = r["cliente_rut"]
        filas.append({
            "Fecha rechazo": ref.date() if pd.notna(ref) else None,
            "Fecha documento": r["fecha"].date() if pd.notna(r["fecha"]) else None,
            "Documento": r["_doc"],
            "Cliente": cliente,
            "RUT": r["cliente_rut"],
            "Comuna": r.get("Comuna", "(sin dato)"),
            "Vendedor": r.get("Vendedor", "Sin asignar"),
            "Movimiento": _MOV_LBL.get(r["tipo_mov"], r["tipo_mov"]),
            "Motivo": r.get("Motivo del rechazo", ""),
            "Lo que dijo el repartidor": r.get("Comentario de entrega", ""),
            "Transportista": r.get("Transportista", ""),
            "Estado post-rechazo": estado,
            "Reintento": detalle,
            "Días desde el rechazo": dias,
            "_abierto": estado in ABIERTOS,
        })
    return (pd.DataFrame(filas, columns=_COLS_SEG)
            .sort_values(["_abierto", "Días desde el rechazo"],
                         ascending=[False, False])
            .reset_index(drop=True))


def resumen_rechazos(seg: pd.DataFrame) -> dict:
    """
    Los números del seguimiento. `ok + en_curso + abiertos + anuladas = n`,
    siempre: en_curso es todo lo que se reingresó y todavía no cerró, incluido
    lo que se volvió a rechazar; anuladas son las que logística dio de baja.
    """
    if seg is None or seg.empty:
        return dict(n=0, ok=0, en_curso=0, abiertos=0, anuladas=0, vencidos=0,
                    pct=None)
    est = seg["Estado post-rechazo"]
    ok = int((est == REINTENTO_OK).sum())
    anuladas = int((est == ANULADA).sum())
    abiertos = int(seg["_abierto"].sum())
    vencidos = int((seg["_abierto"]
                    & (seg["Días desde el rechazo"] > DIAS_PARA_REINTENTAR)).sum())
    return dict(n=len(seg), ok=ok, en_curso=len(seg) - ok - abiertos - anuladas,
                abiertos=abiertos, anuladas=anuladas, vencidos=vencidos,
                pct=ok / len(seg))


def texto_sin_confirmar(c: dict) -> str:
    """«3 en ruta · 2 sin despacho», nombrando solo lo que existe."""
    partes = [(c["ruta"], "en ruta"), (c["sin_desp"], "sin despacho"),
              (c["sin_info"], "sin información")]
    return " · ".join(f"{n} {lbl}" for n, lbl in partes if n) or "ninguna"


# ── ¿Se le sigue vendiendo al cliente rechazado? ─────────────────────────────

# Un rechazo sin reintento no siempre es un pedido perdido. Un retiro que el
# cliente rechazó porque quería renegociar (Valhalla, mar-2026) puede haber
# terminado en que se le siguió vendiendo: la máquina se quedó y está bien que
# nadie reingrese el retiro. La compra posterior es la única señal que hay de
# eso, porque la renegociación no queda registrada en ningún sistema.
LECT_NO = "No ha vuelto a comprar"
LECT_RECIENTE = "Sin compras aún (rechazo reciente)"
LECT_IGUAL = "Sigue comprando igual o más"
LECT_MENOS = "Compra menos que antes"
LECT_NUEVO = "Compra, y antes no compraba"
LECT_SIN_BASE = "Sigue comprando (sin historia previa)"
LECT_DEJO = "Compró después, pero dejó de comprar"

# Gran Natural tiene ventas en la base desde feb-2026, y los rechazos de máquina
# son todos de Gran Natural (Acuña no tiene despachos). Antes de esa fecha el
# "antes" del rechazo no es cero: es desconocido. Sin esto, un rechazo del 5 de
# marzo decía "antes no compraba" solo porque faltaba historia (Valhalla).
_INICIO_VENTAS = pd.Timestamp("2026-02-01")
# Con menos días de historia previa que esto no se compara la intensidad.
_DIAS_BASE_MIN = 45
# Una última compra más vieja que esto es un cliente que se cortó, aunque haya
# comprado algo después del rechazo (Don Héctor: compró hasta abril y nada más).
_DIAS_CORTE = 60

# Por debajo de esto, "después" no es comparable con "antes": un mes a medias
# siempre parece una caída.
_DIAS_COMPARABLE = 30
# "Igual" admite una caída de hasta 20%: la venta de helado varía mucho de un
# mes a otro por temporada.
_TOLERANCIA = 0.8
_DIAS_MES = 30.44
_VENTANA_ANTES = 90

_COLS_VTA = ["Lectura de la venta", "Última compra", "Monto última compra",
             "Compras después del rechazo", "Facturas después del rechazo",
             "Promedio mensual antes", "Promedio mensual después", "Variación"]


def _sin_fletes(ventas: pd.DataFrame) -> pd.DataFrame:
    """Las líneas FL-x son fletes de máquina a $1: no son venta de helado."""
    if ventas is None or ventas.empty:
        return pd.DataFrame(columns=["cliente_rut", "fecha", "neto", "n_dcto",
                                     "tipo_dcto", "producto_codigo"])
    v = ventas[~ventas["producto_codigo"].astype(str).str.upper()
               .str.startswith("FL-")].copy()
    v["fecha"] = pd.to_datetime(v["fecha"], errors="coerce")
    v["neto"] = pd.to_numeric(v["neto"], errors="coerce").fillna(0)
    v["_fact"] = v["tipo_dcto"].astype(str).str.upper().str.contains("FACTURA")
    return v


def desde_ventas(seg: pd.DataFrame):
    """Desde qué fecha hay que traer ventas para comparar antes y después."""
    if seg is None or seg.empty:
        return None
    return (pd.to_datetime(seg["Fecha rechazo"]).min()
            - pd.Timedelta(days=_VENTANA_ANTES)).date()


def ventas_post_rechazo(seg: pd.DataFrame, ventas: pd.DataFrame,
                        hoy: date | None = None) -> pd.DataFrame:
    """
    Por cada rechazo, qué le compró el cliente antes y después.

    Devuelve un DataFrame con el mismo índice que `seg` y las columnas de
    `_COLS_VTA`. `ventas` son líneas de fact_ventas de esos clientes desde
    `desde_ventas(seg)`; los fletes FL-x se descartan. Las NC entran con su signo
    (Fact-NC), pero la "última compra" y las "facturas" miran solo facturas.

    - Antes: promedio mensual de los 90 días previos al rechazo.
    - Después: lo comprado DESDE EL DÍA SIGUIENTE al rechazo, dividido por los
      meses transcurridos (mínimo uno). El mismo día no cuenta: el camión que
      volvió con la máquina pudo haber dejado helado igual.
    - Con menos de 30 días desde el rechazo no se compara la intensidad, solo
      si compró o no.
    - Si compró después pero su última compra tiene más de 60 días, "dejó de
      comprar": se cortó igual, solo que un poco más tarde.
    - Si la base no cubre al menos 45 días antes del rechazo (datos desde
      feb-2026), "antes" queda vacío en vez de cero.
    """
    hoy_ts = pd.Timestamp(hoy or date.today())
    out = pd.DataFrame(index=seg.index if seg is not None else None,
                       columns=_COLS_VTA)
    if seg is None or seg.empty:
        return out
    v = _sin_fletes(ventas)
    por_cli = dict(tuple(v.groupby("cliente_rut"))) if not v.empty else {}

    for i, r in seg.iterrows():
        ref = pd.Timestamp(r["Fecha rechazo"])
        vc = por_cli.get(r["RUT"])
        if vc is None:
            vc = v.iloc[0:0]
        fac = vc[vc["_fact"]]
        ult = fac["fecha"].max() if not fac.empty else pd.NaT
        monto_ult = (float(fac.loc[fac["fecha"] == ult, "neto"].sum())
                     if pd.notna(ult) else None)
        desp = vc[vc["fecha"] > ref]
        compras = float(desp["neto"].sum())
        n_fact = int(desp.loc[desp["_fact"], "n_dcto"].nunique())
        # "Antes" solo sobre los días que la base cubre: si el rechazo es de
        # marzo, los 90 días previos caen en parte antes de que existan datos.
        ini_antes = max(ref - pd.Timedelta(days=_VENTANA_ANTES), _INICIO_VENTAS)
        dias_base = (ref - ini_antes).days
        antes = (float(vc[(vc["fecha"] >= ini_antes) & (vc["fecha"] < ref)]
                       ["neto"].sum()) / (dias_base / _DIAS_MES)
                 if dias_base >= _DIAS_BASE_MIN else None)
        dias = (hoy_ts - ref).days
        meses = max(dias / _DIAS_MES, 1)
        despues = compras / meses
        var = (despues / antes - 1) if antes else None
        dias_ult = (hoy_ts - ult).days if pd.notna(ult) else None

        if n_fact == 0:
            lect = LECT_RECIENTE if dias < _DIAS_COMPARABLE else LECT_NO
        elif dias_ult is not None and dias_ult > _DIAS_CORTE:
            lect = LECT_DEJO
        elif antes is None:
            lect = LECT_SIN_BASE
        elif dias < _DIAS_COMPARABLE:
            lect = LECT_IGUAL if antes <= 0 or compras >= antes * _TOLERANCIA \
                else LECT_MENOS
        elif antes <= 0:
            lect = LECT_NUEVO
        elif despues >= antes * _TOLERANCIA:
            lect = LECT_IGUAL
        else:
            lect = LECT_MENOS

        out.loc[i] = [lect, ult.date() if pd.notna(ult) else None, monto_ult,
                      compras, n_fact, antes, despues, var]
    return out


def ventas_por_mes(seg: pd.DataFrame, ventas: pd.DataFrame) -> pd.DataFrame:
    """
    Una fila por rechazo y una columna por mes con lo que compró el cliente
    (Fact-NC, sin fletes), desde 3 meses antes del primer rechazo. Para ver de
    un vistazo si la venta siguió, bajó o se cortó en el mes del rechazo.
    """
    if seg is None or seg.empty:
        return pd.DataFrame()
    v = _sin_fletes(ventas)
    ini = pd.Timestamp(desde_ventas(seg)).to_period("M")
    fin = pd.Timestamp(date.today()).to_period("M")
    meses = pd.period_range(ini, fin, freq="M")
    piv = (v.assign(_m=v["fecha"].dt.to_period("M"))
           .pivot_table(index="cliente_rut", columns="_m", values="neto",
                        aggfunc="sum", fill_value=0)
           if not v.empty else pd.DataFrame())
    base = seg[["Fecha rechazo", "Cliente", "RUT", "Vendedor", "Movimiento"]].copy()
    for p in meses:
        col = piv[p] if p in getattr(piv, "columns", []) else pd.Series(dtype=float)
        base[f"{p.strftime('%m/%Y')}"] = base["RUT"].map(col).fillna(0.0)
    return base.reset_index(drop=True)
