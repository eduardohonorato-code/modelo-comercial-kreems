"""
Informe de máquinas para gerencia: la misma lógica que la página.

Todo se cuenta sobre UN grupo: las gestiones del período, es decir, fletes de
máquina con documento (DTE) emitido en esas fechas. Un pedido ingresado antes
cuenta en la semana en que se emitió su documento; las entregas, los rechazos y
los que siguen en ruta son esas mismas gestiones. La versión anterior tenía diez
indicadores con denominadores distintos (% concretado sobre pedidos ingresados,
cola vencida sobre todo lo abierto, parque neto…) y gerencia se enredaba igual
que en la pantalla, así que se alinea con ella.

Hojas:
  1. Resumen              · las respuestas de la semana, contra la meta
  2. Semana a semana      · las últimas 8 semanas partidas por resultado
  3. Gestiones por tipo   · instalaciones, cambios y retiros, y cómo terminó c/u
  4. Rechazos             · cuántos por motivo y el detalle de cada uno
  5. Rechazos · seguimiento · si se volvieron a ingresar y cómo terminaron
  6. Sin confirmar        · lo que todavía no tiene resultado
  7. Gestiones · detalle  · todas las gestiones del período con su estado
  8. Pedidos sin documento· aparte y rotulado: todavía NO son gestiones
  9. Facturados sin despacho · la otra cola de logística, toda la ventana
"""
import io
from datetime import date

import pandas as pd

from app.export_analisis import _escribir, _con_total, _FMT_NUM, _FMT_PCT
from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA, SIN_DESPACHO,
                                 SIN_INFO, _desc)
from app.kpis_maquinas import (DIAS_PARA_REINTENTAR, conteo_semana,
                               etiqueta_mov, mezcla_movimientos,
                               resumen_rechazos, seguimiento_rechazos,
                               texto_sin_confirmar)

_FMT_FECHA = "dd/mm/yyyy"
_MOV = {"nueva": "Instalación", "cambio": "Cambio", "retiro": "Retiro"}
SEMANAS_TENDENCIA = 8


def libro_gerencia(mov: pd.DataFrame, ped: pd.DataFrame, f_ini, f_fin,
                   metas: dict, soc_lbl: str = "Ambas",
                   clientes: pd.DataFrame | None = None,
                   hoy: date | None = None) -> bytes:
    """
    `mov` puede traer semanas anteriores al período —se usan para la hoja de
    tendencia— y también posteriores: el seguimiento de rechazos necesita ver lo
    que pasó DESPUÉS del período para saber si un rechazo se volvió a ingresar.
    Las demás hojas se filtran al período.
    """
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    hoy = hoy or date.today()
    ini, fin = pd.Timestamp(f_ini), pd.Timestamp(f_fin)

    if mov is None or mov.empty:
        _escribir(wb, "Resumen", pd.DataFrame(),
                  nota="Sin gestiones de máquinas en el período elegido.")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    w = mov[mov["fecha"].between(ini, fin)].copy()
    c = conteo_semana(w)
    meta_g = metas.get("meta_gestiones_semana")
    meta_e = metas.get("meta_pct_entregado")
    semanas_periodo = max(((fin - ini).days + 1) / 7, 1)
    meta_periodo = meta_g * semanas_periodo if meta_g else None

    def cli(ruts):
        return _desc(ruts, clientes, "razon_social")

    # ── 1. Resumen ───────────────────────────────────────────────────────────
    rech = w[w["Estado entrega"] == RECHAZADA]
    motivo_top = (rech["Motivo del rechazo"].value_counts().index[0]
                  if not rech.empty else "—")
    # El seguimiento mira TODOS los rechazos de `mov`, no solo los del período:
    # un rechazo de hace tres semanas sin retomar sigue siendo un pedido perdido,
    # y su reintento cae después del período. Igual que en la página.
    seg = seguimiento_rechazos(mov[mov["Estado entrega"] == RECHAZADA], mov, ped,
                               hoy=hoy)
    res_seg = resumen_rechazos(seg)
    mz = mezcla_movimientos(w)
    n_mov = dict(zip(mz["Movimiento"], mz["Gestiones"])) if not mz.empty else {}

    filas = [
        ("Período", f"{f_ini:%d/%m/%Y} a {f_fin:%d/%m/%Y}", ""),
        ("Generado el", f"{hoy:%d/%m/%Y}", ""),
        ("", "", ""),
        ("1 · GESTIONES", c["n"],
         "Fletes de máquina con documento emitido en el período"),
        ("   Meta", round(meta_periodo) if meta_periodo else "—",
         "Meta semanal × semanas del período" if meta_g else "Sin meta fijada"),
        ("   % de la meta", (c["n"] / meta_periodo) if meta_periodo else "—", ""),
        ("   Instalaciones (FL-4)", n_mov.get("Instalación", 0),
         "Cliente nuevo: la máquina entra al parque"),
        ("   Cambios (FL-1/3/5)", n_mov.get("Cambio", 0),
         "El cliente sigue con máquina"),
        ("   Retiros (FL-2)", n_mov.get("Retiro", 0),
         "La máquina sale del parque. La meta no distingue: los tres suman igual"),
        ("", "", ""),
        ("2 · % DE ENTREGA", c["pct"] if c["pct"] is not None else "—",
         f"{c['ent']} entregadas de {c['base']} gestiones con despacho"),
        ("   Meta", meta_e if meta_e else "—", ""),
        ("", "", ""),
        ("3 · SIN CONFIRMAR", c["sin_confirmar"], texto_sin_confirmar(c)),
        ("", "", ""),
        ("4 · RECHAZADAS", c["rech"], f"Motivo principal: {motivo_top}"),
        ("", "", ""),
        ("CUADRE", f"{c['ent']} + {c['sin_confirmar']} + {c['rech']} = {c['n']}",
         "Entregadas + sin confirmar + rechazadas = gestiones"),
        ("", "", ""),
        ("5 · RECHAZOS RETOMADOS", res_seg["n"],
         "Todos los rechazos cargados, no solo los del período: el trabajo que "
         "deja un rechazo no vence el domingo"),
        ("   Ya entregados", res_seg["ok"],
         "Se volvieron a ingresar y el segundo intento llegó"),
        ("   Reingresados sin cerrar", res_seg["en_curso"],
         "Van en camino, esperan documento, o el segundo intento también se "
         "rechazó"),
        ("   Sin retomar", res_seg["abiertos"],
         f"Nadie los volvió a ingresar · {res_seg['vencidos']} llevan más de "
         f"{DIAS_PARA_REINTENTAR} días"),
        ("   % recuperado", res_seg["pct"] if res_seg["pct"] is not None else "—",
         "Ya entregados sobre el total de rechazos"),
    ]
    # Qué celdas van en formato porcentaje. Se marcan por etiqueta al construir
    # la lista: cualquier regla por número de fila se rompe al agregar una.
    _PCT = {"   % de la meta", "2 · % DE ENTREGA", "   % recuperado"}
    pct_filas = {i for i, f in enumerate(filas)
                 if f[0] in _PCT or (f[0].strip() == "Meta"
                                     and isinstance(f[1], float))}
    res = pd.DataFrame(filas, columns=["Pregunta", "Resultado", "Detalle"])
    ws = _escribir(wb, "Resumen", res,
                   nota=("CÓMO LEER: todo se cuenta sobre las gestiones del "
                         "período (documentos de flete emitidos en esas fechas). "
                         "Una gestión puede venir de un pedido ingresado antes: "
                         "lo que manda es la fecha del documento. Entregas, "
                         "rechazos y en ruta son esas mismas gestiones."))
    for i in pct_filas:
        celda = ws.cell(row=4 + i, column=2)
        if isinstance(celda.value, float):
            celda.number_format = _FMT_PCT

    # ── 2. Semana a semana ───────────────────────────────────────────────────
    m = mov.copy()
    m["_sem"] = m["fecha"].dt.to_period("W-SUN")
    semanas = pd.period_range(end=fin.to_period("W-SUN"),
                              periods=SEMANAS_TENDENCIA, freq="W-SUN")
    tend = []
    for p in semanas:
        g = conteo_semana(m[m["_sem"] == p])
        tend.append({
            "Semana": f"{p.start_time:%d/%m} al {p.end_time:%d/%m}",
            "Gestiones": g["n"],
            "Meta": meta_g,
            "Entregadas": g["ent"],
            "En ruta": g["ruta"],
            "Sin despacho": g["sin_desp"],
            "Sin información": g["sin_info"],
            "Rechazadas": g["rech"],
            "% de entrega": g["pct"],
        })
    tend = pd.DataFrame(tend)
    tot = _con_total(tend, "Semana", ("% de entrega", "Meta"))
    if not tot.empty:
        f = tot.index[-1]
        base = tend["Gestiones"].sum() - sum(
            conteo_semana(m[m["_sem"] == p])["sin_info"] for p in semanas)
        tot.loc[f, "% de entrega"] = (tend["Entregadas"].sum() / base) if base else None
    _escribir(wb, "Semana a semana", tot,
              {c_: _FMT_NUM for c_ in ("Gestiones", "Meta", "Entregadas",
                                       "En ruta", "Sin despacho",
                                       "Sin información", "Rechazadas")}
              | {"% de entrega": _FMT_PCT},
              nota=("Las últimas semanas siempre tienen más 'En ruta' y un % de "
                    "entrega más bajo: todavía no se confirman. Se completan solas "
                    "con los días."),
              total_ultima=not tot.empty)

    # ── 3. Gestiones por tipo ────────────────────────────────────────────────
    _escribir(wb, "Gestiones por tipo", mz,
              {c_: _FMT_NUM for c_ in ("Gestiones", "Entregadas", "En ruta",
                                       "Sin despacho", "Sin información",
                                       "Rechazadas")}
              | {"% de las gestiones": _FMT_PCT, "% de entrega": _FMT_PCT},
              nota=("Las mismas gestiones del Resumen, partidas por tipo. "
                    "Instalación es FL-4 (cliente nuevo), cambio es FL-1/3/5 y "
                    "retiro es FL-2. Una instalación rechazada y un retiro "
                    "rechazado no son el mismo problema: la primera es un "
                    "cliente que se arrepintió, el segundo uno que no devuelve "
                    "la máquina."))

    # ── 4. Rechazos ──────────────────────────────────────────────────────────
    if rech.empty:
        _escribir(wb, "Rechazos", pd.DataFrame(),
                  nota="Ninguna gestión del período volvió rechazada.")
    else:
        mot = (rech.groupby("Motivo del rechazo").size().rename("Rechazos")
               .reset_index().sort_values("Rechazos", ascending=False))
        mot["% de los rechazos"] = mot["Rechazos"] / mot["Rechazos"].sum()
        mot = _con_total(mot, "Motivo del rechazo")
        _escribir(wb, "Rechazos", mot,
                  {"Rechazos": _FMT_NUM, "% de los rechazos": _FMT_PCT},
                  nota=("El motivo se lee del comentario del repartidor: el campo "
                        "de motivo del ERP llega vacío. El detalle, en la hoja "
                        "siguiente."),
                  total_ultima=True)
        _escribir(wb, "Rechazos · detalle", pd.DataFrame({
            "Fecha rechazo": rech["Fecha ruta"].dt.date,
            "Fecha documento": rech["fecha"].dt.date,
            "Documento": rech["_doc"],
            "Cliente": cli(rech["cliente_rut"]),
            "Comuna": _desc(rech["cliente_rut"], clientes, "comuna"),
            "Movimiento": rech["tipo_mov"].map(_MOV),
            "Motivo": rech["Motivo del rechazo"],
            "Lo que dijo el repartidor": rech["Comentario de entrega"],
            "Transportista": rech["Transportista"],
            "Vendedor": rech["Vendedor"],
        }).sort_values("Fecha documento"),
            {"Fecha documento": _FMT_FECHA, "Fecha rechazo": _FMT_FECHA},
            nota=("Una fila por gestión rechazada, con lo que escribió el "
                  "repartidor. La fecha del rechazo es la de la ruta, no la del "
                  "documento. Qué pasó con cada una, en la hoja siguiente."))

    # ── 5. Rechazos · seguimiento ────────────────────────────────────────────
    if seg.empty:
        _escribir(wb, "Rechazos · seguimiento", pd.DataFrame(),
                  nota="Sin rechazos en la ventana cargada.")
    else:
        _escribir(wb, "Rechazos · seguimiento",
                  seg.drop(columns=["_abierto"]),
                  {"Fecha rechazo": _FMT_FECHA, "Fecha documento": _FMT_FECHA,
                   "Días desde el rechazo": _FMT_NUM},
                  nota=("QUÉ PASÓ DESPUÉS DE CADA RECHAZO. Ordenada con los que "
                        "nadie retomó arriba: esa es la lista para los "
                        "vendedores. Como ningún sistema guarda el número de "
                        "serie de la máquina, el reintento se reconoce por mismo "
                        "cliente + mismo tipo de movimiento + fecha posterior al "
                        "rechazo; la columna «Reintento» dice con qué documento "
                        "o pedido se emparejó, para poder verificarlo. Incluye "
                        "todos los rechazos cargados, no solo los del período."))

    # ── 6. Siguen en ruta ────────────────────────────────────────────────────
    ruta = w[w["Estado entrega"].isin([EN_RUTA, SIN_DESPACHO, SIN_INFO])].copy()
    if ruta.empty:
        _escribir(wb, "Sin confirmar", pd.DataFrame(),
                  nota="Todas las gestiones del período tienen resultado.")
    else:
        desde = ruta["Fecha ruta"].fillna(ruta["fecha"])
        _escribir(wb, "Sin confirmar", pd.DataFrame({
            "Días": (pd.Timestamp(hoy) - desde).dt.days,
            "Estado": ruta["Estado entrega"].map(
                {EN_RUTA: "En camino", SIN_DESPACHO: "Sin despacho aún",
                 SIN_INFO: "Sin información"}),
            "Fecha documento": ruta["fecha"].dt.date,
            "Documento": ruta["_doc"],
            "Cliente": cli(ruta["cliente_rut"]),
            "Movimiento": ruta["tipo_mov"].map(_MOV),
            "Transportista": ruta["Transportista"].fillna("—"),
            "Vendedor": ruta["Vendedor"],
        }).sort_values("Días", ascending=False),
            {"Fecha documento": _FMT_FECHA, "Días": _FMT_NUM},
            nota=("Los tres estados que suma «3 · SIN CONFIRMAR» del Resumen. "
                  "'En camino' salió a ruta y no vuelve confirmada todavía. "
                  "'Sin despacho aún' es un documento emitido que no aparece en "
                  "el Excel de despachos del mes, aunque ese mes sí está "
                  "cargado. 'Sin información' es Acuña o un mes sin despachos "
                  "cargados: esas no se pueden confirmar nunca y quedan fuera "
                  "del % de entrega."))

    # ── 7. Gestiones · detalle ───────────────────────────────────────────────
    _escribir(wb, "Gestiones · detalle", pd.DataFrame({
        "Fecha documento": w["fecha"].dt.date,
        "Documento": w["_doc"],
        "Movimiento": w["tipo_mov"].map(_MOV),
        "Cliente": cli(w["cliente_rut"]),
        "Comuna": _desc(w["cliente_rut"], clientes, "comuna"),
        "Vendedor": w["Vendedor"],
        "Resultado": w["Estado entrega"],
        "Fecha ruta": w["Fecha ruta"].dt.date,
        "Transportista": w["Transportista"],
    }).sort_values("Fecha documento"),
        {"Fecha documento": _FMT_FECHA, "Fecha ruta": _FMT_FECHA},
        nota="Una fila por gestión del período. Es la base de todas las demás hojas.")

    # ── 8. Pedidos sin documento ─────────────────────────────────────────────
    if ped is not None and not ped.empty:
        cola = ped[ped["_sin_dte"] & ~ped["_fantasma"]].copy()
        if not cola.empty:
            vmap = dict(mov.drop_duplicates("vendedor_id")
                        .set_index("vendedor_id")["Vendedor"])
            # Veinte pedidos en cola no son lo mismo si son retiros que si son
            # instalaciones: los primeros son parque que sigue en la calle, los
            # segundos venta que todavía no empieza.
            n_c = cola["_mov"].value_counts()
            mezcla_cola = " · ".join(
                etiqueta_mov(mv, int(n_c[mv]))
                for mv in ("nueva", "cambio", "retiro") if n_c.get(mv))
            _escribir(wb, "Pedidos sin documento", pd.DataFrame({
                "Días esperando": (pd.Timestamp(hoy) - cola["_ingreso"]).dt.days,
                "Fecha pedido": cola["_ingreso"].dt.date,
                "N° pedido": cola["n_pedido"],
                "Movimiento": cola["_mov"].map(_MOV).fillna("(otro)"),
                "Vendedor": cola["vendedor_id"].map(vmap).fillna("—"),
                "Cliente": cli(cola["cliente_rut"]),
            }).sort_values("Días esperando", ascending=False),
                {"Fecha pedido": _FMT_FECHA, "Días esperando": _FMT_NUM},
                nota=(f"APARTE: pedidos que el vendedor ya ingresó y todavía "
                      f"no tienen documento. Aún NO son gestiones y no cuentan "
                      f"en ninguna otra hoja; cuando se emita el documento pasan "
                      f"a contar en esa semana. Salen todos los abiertos hoy. "
                      f"De los {len(cola)}: {mezcla_cola}."))

    # ── 9. Facturados sin despacho ───────────────────────────────────────────
    # Toda la ventana de `mov`, no el período: la hermana de la cola de pedidos
    # sin documento, un paso más adelante del recorrido. Ahí falta emitir el
    # DTE, aquí falta subirlo a un camión; las dos son de logística y las dos
    # envejecen, así que las dos se miran completas.
    sin_ruta = mov[mov["Estado entrega"] == SIN_DESPACHO].copy()
    if not sin_ruta.empty:
        _escribir(wb, "Facturados sin despacho", pd.DataFrame({
            "Días desde la factura": (pd.Timestamp(hoy) - sin_ruta["fecha"]).dt.days,
            "Fecha factura": sin_ruta["fecha"].dt.date,
            "Documento": sin_ruta["_doc"],
            "Movimiento": sin_ruta["tipo_mov"].map(_MOV),
            "Vendedor": sin_ruta["Vendedor"],
            "Cliente": cli(sin_ruta["cliente_rut"]),
            "Comuna": _desc(sin_ruta["cliente_rut"], clientes, "comuna"),
            "Sociedad": sin_ruta["Sociedad"],
        }).sort_values("Días desde la factura", ascending=False),
            {"Fecha factura": _FMT_FECHA, "Días desde la factura": _FMT_NUM},
            nota=("APARTE, y de toda la ventana cargada, no solo del período: "
                  "documentos de flete emitidos que no aparecen en ninguna ruta "
                  "—ni entregada, ni rechazada, ni pendiente— en un mes que SÍ "
                  "tiene despachos cargados. O sea, no falta el archivo: falta "
                  "programar el flete. Es la otra cola de logística, después de "
                  "«Pedidos sin documento»."))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
