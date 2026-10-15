"""
Control de Entregas: cuánto se demora un pedido desde que el vendedor lo
ingresa hasta que el camión lo deja (o vuelve con él), calculado en UN solo
lugar para la página y sus descargas.

El recorrido de un pedido tiene tres fechas, de tres fuentes:

    INGRESO  → `fact_pedidos.fecha_pedido` (created_at de la línea en Autoventa)
    DTE      → `fact_pedidos.fecha` cuando ya tiene documento (num_documento)
    RUTA     → `fact_despachos.fecha_ruta`, cruzado por (documento, cliente)

Y un resultado: Entregado / Rechazado (cierran el pedido), En ruta, Facturado
sin ruta o Sin DTE (lo dejan abierto). No hay hora de entrega en la base: el
Excel de despachos trae «Hora entrega» pero casi siempre vacía, así que el día
de la ruta ES el día de la entrega.

REGLA DEL GRUPO (la misma de Control de Máquinas): todo se cuenta sobre los
pedidos INGRESADOS en el período. El tiempo de entrega, el % a tiempo, el % de
rechazo y los que siguen abiertos son esos mismos pedidos, no otro grupo.

Los días se cuentan HÁBILES (lunes a viernes, sin feriados): un pedido del
viernes entregado el lunes es 1 día, no 3. Las rutas casi no salen sábado
(355 de 6.287) ni domingo, y los pedidos no se ingresan en fin de semana.

Qué NO entra:
  · pedidos fantasma (Autoventa ya no los tiene: anulados o reingresados con
    otro número) — ver `_prep_pedidos` en export_maquinas;
  · pedidos facturados en la otra serie (Obuma): tienen documento, pero no el
    nuestro, así que no se pueden cruzar con su despacho;
  · los anulados por logística (ruta ficticia «Nulas mes en curso»): no son
    entregas ni rechazos en terreno.
"""
from datetime import date

import numpy as np
import pandas as pd

from app.export_maquinas import FL_A_MOV, _norm_doc, clasificar_motivo

ENTREGADO = "Entregado"
RECHAZADO = "Rechazado"
EN_RUTA = "En ruta"
SIN_RUTA = "Facturado sin ruta"
SIN_DTE = "Sin DTE"
SIN_INFO = "Sin información"
ANULADO = "Anulado por logística"
OTRA_SERIE = "Facturado en otra serie"
CERRADOS = (ENTREGADO, RECHAZADO)
ABIERTOS = (EN_RUTA, SIN_RUTA, SIN_DTE)
ESTADOS = [ENTREGADO, RECHAZADO, EN_RUTA, SIN_RUTA, SIN_DTE, SIN_INFO]

# Plazo comprometido por defecto, en días hábiles desde el ingreso hasta la
# entrega. La página lo deja cambiar; 2 días es lo que cumple hoy la mayoría
# (feb–oct 2026: mediana 2 días corridos, 61% en 2 o menos).
SLA_DIAS_DEFAULT = 2
# Máquinas: un flete pasa por coordinación (DTE, camión de máquinas) y hoy la
# mediana es 7,5 días hábiles, casi todos esperando el DTE. 5 días es la vara
# propuesta; se cambia en la página.
SLA_MAQ_DEFAULT = 5

# Ventana para aceptar un despacho como el de un documento: los folios se
# reutilizan entre meses, así que un despacho con el mismo número y cliente
# pero meses de distancia es OTRO documento.
_DIAS_RUTA_ANTES = 30
_DIAS_RUTA_DESPUES = 60
_RUTA_ANULACION = "nula"

# Rechazos de producto: cuándo se acredita (NC) y cuándo el cliente vuelve a
# recibir. La NC del mismo monto es la que anula la factura rechazada; si no
# hay una exacta, la primera NC del cliente desde 2 días antes del rechazo (a
# veces logística acredita antes de cerrar la ruta).
_NC_DIAS_ANTES = 2
_NC_TOLERANCIA = 2           # pesos de diferencia para llamar "exacta" a la NC
DIAS_ALERTA_NC = 10          # un rechazo sin NC pasado esto se marca


def _habiles(ini: pd.Series, fin: pd.Series, feriados=()) -> pd.Series:
    """Días hábiles entre dos fechas (lun–vie sin feriados); NaN si falta una."""
    ok = ini.notna() & fin.notna()
    out = pd.Series(np.nan, index=ini.index)
    if ok.any():
        a = ini[ok].values.astype("datetime64[D]")
        b = fin[ok].values.astype("datetime64[D]")
        hol = np.array(sorted(feriados), dtype="datetime64[D]") if feriados else None
        n = (np.busday_count(a, b, holidays=hol) if hol is not None
             else np.busday_count(a, b))
        out[ok] = n
    return out


def preparar_pedidos(lineas: pd.DataFrame, despachos: pd.DataFrame | None,
                     feriados=(), hoy: date | None = None) -> pd.DataFrame:
    """
    Una fila por pedido con sus tres fechas, su resultado y los días entre
    cada etapa.

    `lineas` son líneas de fact_pedidos (todas, helados y fletes FL);
    `despachos` son filas de fact_despachos de una ventana que cubra las rutas
    de esos pedidos.
    """
    hoy_ts = pd.Timestamp(hoy or date.today())
    cols = ["n_pedido", "Tipo", "Movimiento", "vendedor_id", "cliente_rut",
            "sociedad_id", "Ingreso", "Documento", "Fecha DTE", "Fecha ruta",
            "Estado", "Monto", "Transportista", "Motivo", "Comentario",
            "Días a DTE", "Días DTE a ruta", "Días a cierre",
            "Días corridos a cierre", "Días abierto"]
    if lineas is None or lineas.empty:
        return pd.DataFrame(columns=cols)

    p = lineas.copy()
    p["fecha"] = pd.to_datetime(p["fecha"], errors="coerce")
    p["fecha_pedido"] = pd.to_datetime(p.get("fecha_pedido"), errors="coerce")
    p["_cod"] = p["producto_codigo"].astype(str).str.upper().str.strip()
    p["_fl"] = p["_cod"].str.startswith("FL-")
    p["neto"] = pd.to_numeric(p.get("neto"), errors="coerce").fillna(0)
    est = p.get("estado_pedido")
    est = (est.astype(str).str.strip().str.lower() if est is not None
           else pd.Series("", index=p.index))
    p["_fantasma"] = (p["estado_pedido"].isna() | (est == "no_existe_en_api")
                      if "estado_pedido" in p.columns
                      else pd.Series(False, index=p.index))
    sin_dte_txt = p["doc_venta"].astype(str).str.strip().str.lower() == "sin dte"
    p["_otra_serie"] = sin_dte_txt & (est == "invoiced")
    p["_doc"] = _norm_doc(p["num_documento"]).where(p["num_documento"].notna())

    g = (p.groupby("n_pedido", sort=False)
         .agg(vendedor_id=("vendedor_id", "first"),
              cliente_rut=("cliente_rut", "first"),
              sociedad_id=("sociedad_id", "first"),
              Ingreso=("fecha_pedido", "min"),
              _fecha=("fecha", "max"),
              Documento=("_doc", "first"),
              Monto=("neto", "sum"),
              _fl=("_fl", "any"),
              _cod_fl=("_cod", lambda s: next((c for c in s if c.startswith("FL-")),
                                               None)),
              _fantasma=("_fantasma", "all"),
              _otra_serie=("_otra_serie", "any"))
         .reset_index())
    # Sin fecha de ingreso (meses cargados antes de sql/036) se usa la fecha
    # del pedido: mejor una aproximación que dejar la fila fuera.
    g["Ingreso"] = g["Ingreso"].fillna(g["_fecha"])
    g = g[~g["_fantasma"]].copy()
    g["Tipo"] = np.where(g["_fl"], "Máquina", "Helados")
    g["Movimiento"] = g["_cod_fl"].map(FL_A_MOV).map(
        {"nueva": "Instalación", "cambio": "Cambio", "retiro": "Retiro"})
    g["Fecha DTE"] = g["_fecha"].where(g["Documento"].notna())

    # ── Despacho del documento ──────────────────────────────────────────────
    g["Fecha ruta"] = pd.NaT
    for c in ("_est", "Transportista", "Motivo", "Comentario"):
        g[c] = None
    cobertura = set()
    if despachos is not None and not despachos.empty:
        d = despachos.copy()
        d["fecha_ruta"] = pd.to_datetime(d["fecha_ruta"], errors="coerce")
        d["_doc"] = _norm_doc(d["documento"])
        d["_est"] = d["estado"].astype(str).str.strip().str.lower()
        d["_motivo"] = clasificar_motivo(d.get("motivo_rechazo"),
                                         d.get("comentario_entrega"))
        cobertura = set(zip(pd.to_numeric(d["sociedad_id"], errors="coerce"),
                            d["fecha_ruta"].dt.to_period("M").astype(str)))
        con = g[g["Documento"].notna()][["n_pedido", "Documento", "cliente_rut",
                                         "Fecha DTE"]]
        m = con.merge(d[["_doc", "cliente_rut", "fecha_ruta", "_est",
                         "transportista", "_motivo", "comentario_entrega"]],
                      left_on=["Documento", "cliente_rut"],
                      right_on=["_doc", "cliente_rut"], how="inner")
        dif = (m["fecha_ruta"] - m["Fecha DTE"]).dt.days
        m = m[dif.between(-_DIAS_RUTA_ANTES, _DIAS_RUTA_DESPUES)].copy()
        m["_dist"] = (m["fecha_ruta"] - m["Fecha DTE"]).dt.days.abs()
        prio = {"entregada": 0, "rechazada": 1, "pendiente": 2}
        m["_prio"] = m["_est"].map(prio).fillna(9)
        m = m.sort_values(["_prio", "_dist"]).drop_duplicates("n_pedido")
        m = m.set_index("n_pedido")
        idx = g["n_pedido"]
        g["Fecha ruta"] = idx.map(m["fecha_ruta"]).values
        g["_est"] = idx.map(m["_est"]).values
        g["Transportista"] = idx.map(m["transportista"]).values
        g["Motivo"] = idx.map(m["_motivo"]).values
        g["Comentario"] = idx.map(m["comentario_entrega"]).values

    # ── Resultado ───────────────────────────────────────────────────────────
    anul = g["Transportista"].astype(str).str.lower().str.contains(_RUTA_ANULACION)
    mes_dte = g["Fecha DTE"].dt.to_period("M").astype(str)
    cubierto = pd.Series([(s, mm) in cobertura for s, mm in
                          zip(pd.to_numeric(g["sociedad_id"], errors="coerce"),
                              mes_dte)], index=g.index)
    estado = pd.Series(SIN_DTE, index=g.index)
    estado[g["Documento"].notna()] = SIN_RUTA
    estado[g["Documento"].notna() & ~cubierto] = SIN_INFO
    estado[g["_est"] == "pendiente"] = EN_RUTA
    estado[g["_est"] == "entregada"] = ENTREGADO
    estado[g["_est"] == "rechazada"] = RECHAZADO
    estado[anul] = ANULADO
    estado[g["_otra_serie"] & g["Documento"].isna()] = OTRA_SERIE
    g["Estado"] = estado

    cierre = g["Fecha ruta"].where(g["Estado"].isin(CERRADOS))
    g["Días a DTE"] = _habiles(g["Ingreso"], g["Fecha DTE"], feriados)
    g["Días DTE a ruta"] = _habiles(g["Fecha DTE"], g["Fecha ruta"], feriados)
    g["Días a cierre"] = _habiles(g["Ingreso"], cierre, feriados)
    g["Días corridos a cierre"] = (cierre - g["Ingreso"]).dt.days
    abierto = g["Estado"].isin(ABIERTOS)
    g["Días abierto"] = _habiles(g["Ingreso"].where(abierto),
                                 pd.Series(hoy_ts, index=g.index).where(abierto),
                                 feriados)
    # Una ruta anterior al ingreso es un folio mal cruzado o un pedido
    # reingresado después de despachado: no es un tiempo real.
    for c in ("Días a cierre", "Días corridos a cierre"):
        g.loc[g[c] < 0, c] = np.nan
    return g[cols].reset_index(drop=True)


def resumen(g: pd.DataFrame, sla: int = SLA_DIAS_DEFAULT) -> dict:
    """Los números de un grupo de pedidos, todos sobre ese mismo grupo."""
    if g is None or g.empty:
        return dict(n=0, monto=0.0, cerrados=0, ent=0, rech=0, abiertos=0,
                    sin_dte=0, sin_ruta=0, ruta=0, sin_info=0, mediana=None,
                    promedio=None, p90=None, a_tiempo=None, pct_rech=None,
                    pct_rech_monto=None, med_dte=None, med_ruta=None)
    est = g["Estado"]
    base = g[est.isin(CERRADOS + ABIERTOS)]
    ent = base[base["Estado"] == ENTREGADO]
    cerr = base[base["Estado"].isin(CERRADOS)]
    d = ent["Días a cierre"].dropna()
    monto_cerr = float(cerr["Monto"].sum())
    return dict(
        n=len(base), monto=float(base["Monto"].sum()),
        cerrados=len(cerr), ent=len(ent),
        rech=int((base["Estado"] == RECHAZADO).sum()),
        abiertos=int(base["Estado"].isin(ABIERTOS).sum()),
        sin_dte=int((est == SIN_DTE).sum()),
        sin_ruta=int((est == SIN_RUTA).sum()),
        ruta=int((est == EN_RUTA).sum()),
        sin_info=int((est == SIN_INFO).sum()),
        mediana=float(d.median()) if len(d) else None,
        promedio=float(d.mean()) if len(d) else None,
        p90=float(d.quantile(.9)) if len(d) else None,
        a_tiempo=float((d <= sla).mean()) if len(d) else None,
        pct_rech=(int((cerr["Estado"] == RECHAZADO).sum()) / len(cerr)
                  if len(cerr) else None),
        pct_rech_monto=(float(cerr.loc[cerr["Estado"] == RECHAZADO,
                                       "Monto"].sum()) / monto_cerr
                        if monto_cerr else None),
        med_dte=_med(base["Días a DTE"]),
        med_ruta=_med(base["Días DTE a ruta"].where(base["Fecha ruta"].notna())),
    )


def _med(s: pd.Series):
    s = s.dropna()
    return float(s.median()) if len(s) else None


def tabla_por(g: pd.DataFrame, col: str, sla: int = SLA_DIAS_DEFAULT,
              etiqueta: str | None = None) -> pd.DataFrame:
    """Los mismos indicadores de `resumen`, partidos por una columna."""
    if g is None or g.empty:
        return pd.DataFrame()
    filas = []
    for k, sub in g.groupby(g[col].fillna("(sin dato)")):
        r = resumen(sub, sla)
        if not r["n"]:
            continue
        filas.append({etiqueta or col: k, "Pedidos": r["n"],
                      "Entregados": r["ent"], "Rechazados": r["rech"],
                      "Abiertos": r["abiertos"],
                      "Días hábiles (mediana)": r["mediana"],
                      "Días hábiles (promedio)": r["promedio"],
                      f"% en {sla} días o menos": r["a_tiempo"],
                      "% rechazo": r["pct_rech"],
                      "Monto": r["monto"]})
    return (pd.DataFrame(filas).sort_values("Pedidos", ascending=False)
            .reset_index(drop=True))


def distribucion(g: pd.DataFrame) -> pd.DataFrame:
    """Cuántos entregados tardaron 0, 1, 2… días hábiles (6 o más juntos)."""
    d = g.loc[g["Estado"] == ENTREGADO, "Días a cierre"].dropna()
    if d.empty:
        return pd.DataFrame(columns=["Días hábiles", "Pedidos", "%"])
    tramo = d.clip(upper=6).astype(int).map(lambda x: "6 o más" if x >= 6 else str(x))
    orden = ["0", "1", "2", "3", "4", "5", "6 o más"]
    c = tramo.value_counts().reindex(orden, fill_value=0)
    return pd.DataFrame({"Días hábiles": orden, "Pedidos": c.values,
                         "%": c.values / c.sum()})


def semanal(g: pd.DataFrame, fin, semanas: int = 8,
            sla: int = SLA_DIAS_DEFAULT) -> pd.DataFrame:
    """Indicadores por semana de INGRESO (lunes a domingo)."""
    per = pd.period_range(end=pd.Timestamp(fin).to_period("W-SUN"),
                          periods=semanas, freq="W-SUN")
    if g is None or g.empty:
        return pd.DataFrame()
    s = g.assign(_sem=g["Ingreso"].dt.to_period("W-SUN"))
    filas = []
    for p in per:
        r = resumen(s[s["_sem"] == p], sla)
        filas.append({"Semana": f"{p.start_time:%d/%m}", "Pedidos": r["n"],
                      "Mediana": r["mediana"], "A tiempo": r["a_tiempo"],
                      "% rechazo": r["pct_rech"], "Abiertos": r["abiertos"]})
    return pd.DataFrame(filas)


# ── Rechazos de producto: qué pasa después ───────────────────────────────────

NC_EXACTA = "NC por el monto"
NC_OTRA = "NC (otro monto)"
SIN_NC = "Sin NC"


def seguimiento_rechazos_productos(rech: pd.DataFrame, ventas: pd.DataFrame,
                                   despachos: pd.DataFrame,
                                   hoy: date | None = None) -> pd.DataFrame:
    """
    Por cada pedido de helados rechazado: cuándo se acreditó y cuándo el
    cliente volvió a recibir.

    Un rechazo de producto deja dos cosas pendientes, de dos áreas distintas:
      · la NOTA DE CRÉDITO que anula la factura (administración/logística).
        Sin ella, el cliente queda debiendo una factura que no recibió.
      · la SIGUIENTE ENTREGA al cliente (comercial): si no vuelve a recibir, el
        rechazo fue una venta perdida.
    Medido feb–oct 2026: 94% se acredita (86% por el monto exacto, mediana 7
    días) y 85% vuelve a recibir (mediana 13 días).

    `ventas` son líneas de fact_ventas de esos clientes (facturas y NC);
    `despachos` son despachos con fecha posterior a los rechazos.
    """
    hoy_ts = pd.Timestamp(hoy or date.today())
    if rech is None or rech.empty:
        return pd.DataFrame()
    v = ventas.copy() if ventas is not None else pd.DataFrame()
    nc = pd.DataFrame(columns=["cliente_rut", "fecha", "n_dcto", "neto"])
    if not v.empty:
        v["fecha"] = pd.to_datetime(v["fecha"], errors="coerce")
        es_nc = v["tipo_dcto"].astype(str).str.upper().str.contains("CREDITO")
        nc = (v[es_nc].groupby(["cliente_rut", "n_dcto", "fecha"])["neto"]
              .sum().reset_index())
    d = despachos.copy() if despachos is not None else pd.DataFrame()
    if not d.empty:
        d["fecha_ruta"] = pd.to_datetime(d["fecha_ruta"], errors="coerce")
        d = d[d["estado"].astype(str).str.lower() == "entregada"]

    filas = []
    for i, r in rech.iterrows():
        f = r["Fecha ruta"]
        cand = nc[(nc["cliente_rut"] == r["cliente_rut"])
                  & (nc["fecha"] >= f - pd.Timedelta(days=_NC_DIAS_ANTES))]
        exacta = cand[(cand["neto"] + r["Monto"]).abs() <= _NC_TOLERANCIA]
        usada = exacta if not exacta.empty else cand
        if not usada.empty:
            n = usada.sort_values("fecha").iloc[0]
            tipo_nc = NC_EXACTA if not exacta.empty else NC_OTRA
            f_nc, doc_nc, monto_nc = n["fecha"], n["n_dcto"], float(n["neto"])
        else:
            tipo_nc, f_nc, doc_nc, monto_nc = SIN_NC, pd.NaT, None, None
        sig = (d[(d["cliente_rut"] == r["cliente_rut"]) & (d["fecha_ruta"] > f)]
               .sort_values("fecha_ruta") if not d.empty else pd.DataFrame())
        f_sig = sig["fecha_ruta"].iloc[0] if not sig.empty else pd.NaT
        dias = int((hoy_ts - f).days)
        filas.append({
            "Acreditación": tipo_nc,
            "Fecha NC": f_nc, "N° NC": doc_nc, "Monto NC": monto_nc,
            "Días a la NC": (f_nc - f).days if pd.notna(f_nc) else None,
            "Siguiente entrega": f_sig,
            "Días a la siguiente entrega": (f_sig - f).days if pd.notna(f_sig) else None,
            "Días desde el rechazo": dias,
            "_sin_nc_vencido": tipo_nc == SIN_NC and dias > DIAS_ALERTA_NC,
            "_sin_volver": pd.isna(f_sig),
        })
    return pd.DataFrame(filas, index=rech.index)
