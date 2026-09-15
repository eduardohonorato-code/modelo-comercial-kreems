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
  1. Resumen              · las cuatro respuestas de la semana, contra la meta
  2. Semana a semana      · las últimas 8 semanas partidas por resultado
  3. Rechazos             · cuántos por motivo y el detalle de cada uno
  4. Siguen en ruta       · lo que todavía no tiene resultado
  5. Gestiones · detalle  · todas las gestiones del período con su estado
  6. Pedidos sin documento· aparte y rotulado: todavía NO son gestiones
"""
import io
from datetime import date

import pandas as pd

from app.export_analisis import _escribir, _con_total, _FMT_NUM, _FMT_PCT
from app.export_maquinas import (ENTREGADA, RECHAZADA, EN_RUTA, SIN_DESPACHO,
                                 SIN_INFO, _desc)
from app.kpis_maquinas import conteo_semana

_FMT_FECHA = "dd/mm/yyyy"
_MOV = {"nueva": "Instalación", "cambio": "Cambio", "retiro": "Retiro"}
SEMANAS_TENDENCIA = 8


def libro_gerencia(mov: pd.DataFrame, ped: pd.DataFrame, f_ini, f_fin,
                   metas: dict, soc_lbl: str = "Ambas",
                   clientes: pd.DataFrame | None = None,
                   hoy: date | None = None) -> bytes:
    """
    `mov` puede traer semanas anteriores al período: se usan para la hoja de
    tendencia. Las demás hojas se filtran al período.
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
    filas = [
        ("Período", f"{f_ini:%d/%m/%Y} a {f_fin:%d/%m/%Y}", ""),
        ("Generado el", f"{hoy:%d/%m/%Y}", ""),
        ("", "", ""),
        ("1 · GESTIONES", c["n"],
         "Fletes de máquina con documento emitido en el período"),
        ("   Meta", round(meta_periodo) if meta_periodo else "—",
         "Meta semanal × semanas del período" if meta_g else "Sin meta fijada"),
        ("   % de la meta", (c["n"] / meta_periodo) if meta_periodo else "—", ""),
        ("", "", ""),
        ("2 · % DE ENTREGA", c["pct"] if c["pct"] is not None else "—",
         f"{c['ent']} entregadas de {c['base']} gestiones con despacho"),
        ("   Meta", meta_e if meta_e else "—", ""),
        ("", "", ""),
        ("3 · SIGUEN EN RUTA", c["ruta"] + c["sin_desp"],
         f"{c['ruta']} en camino · {c['sin_desp']} sin despacho aún"),
        ("", "", ""),
        ("4 · RECHAZADAS", c["rech"], f"Motivo principal: {motivo_top}"),
        ("", "", ""),
        ("CUADRE", f"{c['ent']} + {c['ruta']} + {c['sin_desp'] + c['sin_info']} "
                   f"+ {c['rech']} = {c['n']}",
         "Entregadas + en ruta + sin despacho + rechazadas = gestiones"),
    ]
    res = pd.DataFrame(filas, columns=["Pregunta", "Resultado", "Detalle"])
    ws = _escribir(wb, "Resumen", res,
                   nota=("CÓMO LEER: todo se cuenta sobre las gestiones del "
                         "período (documentos de flete emitidos en esas fechas). "
                         "Una gestión puede venir de un pedido ingresado antes: "
                         "lo que manda es la fecha del documento. Entregas, "
                         "rechazos y en ruta son esas mismas gestiones."))
    for i in range(len(res)):
        etq = str(res.iloc[i, 0])
        celda = ws.cell(row=4 + i, column=2)
        if ("% " in etq or etq.startswith("2 ·") or
                (etq.strip() == "Meta" and i > 7)) and isinstance(celda.value, float):
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
            "Sin despacho": g["sin_desp"] + g["sin_info"],
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
                                       "En ruta", "Sin despacho", "Rechazadas")}
              | {"% de entrega": _FMT_PCT},
              nota=("Las últimas semanas siempre tienen más 'En ruta' y un % de "
                    "entrega más bajo: todavía no se confirman. Se completan solas "
                    "con los días."),
              total_ultima=not tot.empty)

    # ── 3. Rechazos ──────────────────────────────────────────────────────────
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
            "Fecha documento": rech["fecha"].dt.date,
            "Documento": rech["_doc"],
            "Cliente": cli(rech["cliente_rut"]),
            "Comuna": _desc(rech["cliente_rut"], clientes, "comuna"),
            "Movimiento": rech["tipo_mov"].map(_MOV),
            "Motivo": rech["Motivo del rechazo"],
            "Lo que dijo el repartidor": rech["Comentario de entrega"],
            "Transportista": rech["Transportista"],
            "Vendedor": rech["Vendedor"],
        }).sort_values("Fecha documento"), {"Fecha documento": _FMT_FECHA},
            nota="Una fila por gestión rechazada, con lo que escribió el repartidor.")

    # ── 4. Siguen en ruta ────────────────────────────────────────────────────
    ruta = w[w["Estado entrega"].isin([EN_RUTA, SIN_DESPACHO])].copy()
    if ruta.empty:
        _escribir(wb, "Siguen en ruta", pd.DataFrame(),
                  nota="Todas las gestiones del período tienen resultado.")
    else:
        desde = ruta["Fecha ruta"].fillna(ruta["fecha"])
        _escribir(wb, "Siguen en ruta", pd.DataFrame({
            "Días": (pd.Timestamp(hoy) - desde).dt.days,
            "Estado": ruta["Estado entrega"].map(
                {EN_RUTA: "En camino", SIN_DESPACHO: "Sin despacho aún"}),
            "Fecha documento": ruta["fecha"].dt.date,
            "Documento": ruta["_doc"],
            "Cliente": cli(ruta["cliente_rut"]),
            "Movimiento": ruta["tipo_mov"].map(_MOV),
            "Transportista": ruta["Transportista"].fillna("—"),
            "Vendedor": ruta["Vendedor"],
        }).sort_values("Días", ascending=False),
            {"Fecha documento": _FMT_FECHA, "Días": _FMT_NUM},
            nota=("'Sin despacho aún' es un documento emitido que todavía no "
                  "aparece en el Excel de despachos: no ha salido, o falta "
                  "cargar el archivo."))

    # ── 5. Gestiones · detalle ───────────────────────────────────────────────
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

    # ── 6. Pedidos sin documento ─────────────────────────────────────────────
    if ped is not None and not ped.empty:
        cola = ped[ped["_sin_dte"] & ~ped["_fantasma"]].copy()
        if not cola.empty:
            vmap = dict(mov.drop_duplicates("vendedor_id")
                        .set_index("vendedor_id")["Vendedor"])
            _escribir(wb, "Pedidos sin documento", pd.DataFrame({
                "Días esperando": (pd.Timestamp(hoy) - cola["_ingreso"]).dt.days,
                "Fecha pedido": cola["_ingreso"].dt.date,
                "N° pedido": cola["n_pedido"],
                "Movimiento": cola["_mov"].map(_MOV).fillna("(otro)"),
                "Vendedor": cola["vendedor_id"].map(vmap).fillna("—"),
                "Cliente": cli(cola["cliente_rut"]),
            }).sort_values("Días esperando", ascending=False),
                {"Fecha pedido": _FMT_FECHA, "Días esperando": _FMT_NUM},
                nota=("APARTE: pedidos que el vendedor ya ingresó y todavía no "
                      "tienen documento. Aún NO son gestiones y no cuentan en "
                      "ninguna otra hoja; cuando se emita el documento pasan a "
                      "contar en esa semana. Salen todos los abiertos hoy."))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
