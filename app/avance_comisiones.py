"""Avance diario de comisiones (modelo nuevo) para el Panel Gerencia.

Reproduce la planilla "Avance_Diario_Comisiones_Vendedores_Kreems.xlsx" sin
ingreso manual: lo acumulado del mes sale del mismo motor de la Propuesta de
Comisiones (`comisiones_v1._calcular`), así el panel y la comisión que se paga
usan las mismas definiciones y metas. Lo que esa planilla pedía a mano se
calcula acá:

  · Clientes en cartera   → cartera oficial (tabla cartera_cliente).
  · Visitas programadas   → en este orden: (1) reporte de Autoventa cargado en
                            Comisiones → Cobertura de ruta; (2) "Obj. visitas"
                            del Panel Gerencia (en sep-2026 gerencia cargó ahí
                            los agendamientos del reporte); (3) estimadas desde
                            el código de ruta de la casa matriz de cada cliente
                            de la cartera: semanal = 1 por semana del mes,
                            quincenal (-Q) = la mitad, mensual (-M) = 1.
                            Autoventa agenda por SEMANA. Validado contra el
                            reporte de sep-2026: 4% de error promedio (sumar las
                            sucursales lo sube a 11%).
  · Visitas hechas        → reporte de Autoventa si tiene visitas cargadas; si
                            no, GPS de Autoventa (fact_visitas) contando UNA
                            visita por cliente por semana. Validado contra el
                            reporte: 6,8% de error en jul-2026 y 3,5% en sep-2026.

Proyección al cierre: lineal por días hábiles (lo acumulado ÷ días hábiles
transcurridos × días hábiles del mes). Amplitud de SKU es un promedio, así que
se proyecta igual a lo que lleva; la efectividad se topa en el 100% de la
cartera. Cada indicador paga desde su piso, proporcional y con tope 100%.
"""
from __future__ import annotations

import calendar
import math
import re
from datetime import date, timedelta

import pandas as pd

from app.data import get_cartera_map, get_objetivos, get_visitas_mes, habiles_e_inab

_RE_RUTA = re.compile(r"^[A-Z]{2}\d\d(?:-([QM])\d)?$")

# Orden y etiquetas de los indicadores en el panel (claves del motor v1).
INDICADORES = [
    ("cuota", "Cuota de venta"),
    ("nuevos", "Clientes nuevos"),
    ("ruta", "Cobertura de ruta"),
    ("cobertura", "Efectividad de cartera"),
    ("amplitud", "Amplitud de SKU"),
]


def semanas_del_mes(anio: int, mes: int) -> int:
    """Semanas calendario (lunes a domingo) que tocan el mes."""
    ini = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    return (fin - (ini - timedelta(days=ini.weekday()))).days // 7 + 1


def frecuencia_ruta(ruta) -> str | None:
    """'W' semanal (CN15), 'Q' quincenal (CN12-Q1), 'M' mensual (CN32-M2).
    Rutas que no siguen el patrón (CENTRALIZADA, CIERRE…) no agendan visitas."""
    m = _RE_RUTA.match(str(ruta or "").upper().strip())
    if not m:
        return None
    return m.group(1) or "W"


def agenda_estimada(cart_map: pd.DataFrame, anio: int, mes: int) -> pd.DataFrame:
    """Visitas programadas del mes por vendedor, desde las rutas de su cartera."""
    if cart_map is None or cart_map.empty:
        return pd.DataFrame(columns=["vendedor_id", "agend_est"])
    sem = semanas_del_mes(anio, mes)
    veces = {"W": sem, "Q": sem / 2, "M": 1}
    c = cart_map.dropna(subset=["vendedor_id"]).copy()
    c["veces"] = c["ruta"].map(frecuencia_ruta).map(veces).fillna(0)
    out = c.groupby("vendedor_id")["veces"].sum().round().astype(int)
    return out.rename("agend_est").reset_index()


def visitas_estimadas(vis: pd.DataFrame | None) -> pd.DataFrame:
    """Visitas hechas por vendedor: una por cliente por semana (así agenda
    Autoventa; una segunda visita en la misma semana no cubre otra agenda)."""
    if vis is None or vis.empty:
        return pd.DataFrame(columns=["vendedor_id", "vis_est"])
    v = vis.dropna(subset=["vendedor_id", "cliente_rut"]).copy()
    iso = pd.to_datetime(v["fecha"]).dt.isocalendar()
    v["semana"] = iso["year"].astype(str) + "-" + iso["week"].astype(str)
    v = v.drop_duplicates(["vendedor_id", "cliente_rut", "semana"])
    out = v.groupby("vendedor_id").size().rename("vis_est").reset_index()
    out["vendedor_id"] = out["vendedor_id"].astype(int)
    return out


def corte_por_defecto(anio: int, mes: int, hoy: date | None = None) -> date | None:
    """Último día con datos cargados: ayer en el mes en curso (la carga diaria
    corre de madrugada), el último día del mes si ya terminó, None si es futuro."""
    hoy = hoy or date.today()
    ini = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    if hoy <= ini:
        return None
    return min(fin, hoy - timedelta(days=1))


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _paga(cumpl, umbral: float, pct: float) -> float:
    if cumpl is None:
        return 0.0
    if umbral > 0 and cumpl < umbral:
        return 0.0
    return max(0.0, min(1.0, cumpl)) * pct


def _ceil(x) -> int:
    return max(0, math.ceil(round(x, 6)))


def calcular_avance(client, anio: int, mes: int, corte: date,
                    base: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """`base` = primer elemento de comisiones_v1._calcular(client, anio, mes).
    Devuelve (una fila por vendedor con lo que lleva, metas, proyección, pago y
    faltantes por indicador; contexto del corte)."""
    from app.pages.comisiones_v1 import PCT, TASA_MAX

    ini = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    dm, _ = habiles_e_inab(client, ini, fin)
    dt, _ = habiles_e_inab(client, ini, corte) if corte >= ini else (0, 0)
    f = (dm / dt) if dt else None
    ctx = {"corte": corte, "dias_mes": dm, "dias_transcurridos": dt,
           "dias_restantes": max(0, dm - dt), "semanas": semanas_del_mes(anio, mes),
           "visitas_habilitadas": True}

    if base is None or base.empty:
        return pd.DataFrame(), ctx

    vis = get_visitas_mes(client, anio, mes, hasta=corte)
    ctx["visitas_habilitadas"] = vis is not None
    df = (base.merge(agenda_estimada(get_cartera_map(client), anio, mes),
                     on="vendedor_id", how="left")
              .merge(visitas_estimadas(vis), on="vendedor_id", how="left"))
    obj = get_objetivos(client, anio, mes)
    if not obj.empty and "obj_visitas" in obj.columns:
        df = df.merge(obj[["vendedor_id", "obj_visitas"]].rename(
            columns={"obj_visitas": "agend_obj"}), on="vendedor_id", how="left")
    else:
        df["agend_obj"] = None

    filas = []
    for _, r in df.iterrows():
        o = {"vendedor_id": int(r["vendedor_id"]), "vendedor": r["nombre_canonico"]}
        umb = {k: float(_num(r.get(f"{k}_umbral")) or 0.0) for k in PCT}

        # ── Cuota de venta ──────────────────────────────────────────────────
        venta = _num(r.get("fact_nc")) or 0.0
        meta_v = _num(r.get("cuota_meta"))
        venta_proy = venta * f if f else venta
        o.update(cuota_llevas=venta, cuota_meta=meta_v, cuota_proy=venta_proy,
                 cuota_cumpl=(venta_proy / meta_v) if meta_v else None,
                 cuota_falta=max(0.0, meta_v - venta) if meta_v else None,
                 cuota_min=(umb["cuota"] * meta_v - venta) if meta_v else None)

        # ── Clientes nuevos (+ reactivados) ─────────────────────────────────
        nv = _num(r.get("nuevos_real")) or 0.0
        meta_n = _num(r.get("nuevos_meta"))
        proy_n = nv * f if f else nv
        o.update(nuevos_llevas=nv, nuevos_meta=meta_n, nuevos_proy=proy_n,
                 nuevos_cumpl=(proy_n / meta_n) if meta_n else None,
                 nuevos_falta=_ceil(meta_n - nv) if meta_n else None,
                 nuevos_min=_ceil(umb["nuevos"] * meta_n - nv) if meta_n else None)

        # ── Cobertura de ruta ───────────────────────────────────────────────
        # Programadas: reporte → "Obj. visitas" del panel → estimación por ruta.
        # Hechas: reporte si ya trae visitas → GPS (una por cliente por semana).
        ag_of, vi_of = _num(r.get("agendamientos")), _num(r.get("visitas"))
        ag_obj = _num(r.get("agend_obj"))
        if ag_of:
            agend, fuente = ag_of, "reporte"
        elif ag_obj:
            agend, fuente = ag_obj, "objetivo"
        else:
            agend, fuente = _num(r.get("agend_est")), "estimado"
        if vi_of:
            visitas, fuente_v = vi_of, "reporte"
        else:
            visitas, fuente_v = (_num(r.get("vis_est")) or 0.0), "gps"
        meta_r = _num(r.get("meta_ruta_pct")) or 0.0
        if agend:
            pct_r = visitas / agend
            pct_r_proy = min(1.0, (visitas * f if f else visitas) / agend)
            o.update(ruta_cumpl=(pct_r_proy / meta_r) if meta_r else None,
                     ruta_falta=_ceil(meta_r * agend - visitas),
                     ruta_min=_ceil(umb["ruta"] * meta_r * agend - visitas))
        else:
            pct_r = pct_r_proy = None
            o.update(ruta_cumpl=None, ruta_falta=None, ruta_min=None)
        o.update(ruta_llevas=visitas, ruta_agend=agend, ruta_meta=meta_r,
                 ruta_pct=pct_r, ruta_proy=pct_r_proy, ruta_fuente=fuente,
                 ruta_fuente_vis=fuente_v)

        # ── Efectividad de cartera ──────────────────────────────────────────
        cart = _num(r.get("cartera"))
        compr = _num(r.get("cobertura_real")) or 0.0
        meta_e = _num(r.get("meta_efec_pct")) or 0.0
        if cart:
            pct_e = compr / cart
            pct_e_proy = min(cart, compr * f if f else compr) / cart
            o.update(cobertura_cumpl=(pct_e_proy / meta_e) if meta_e else None,
                     cobertura_falta=_ceil(meta_e * cart - compr),
                     cobertura_min=_ceil(umb["cobertura"] * meta_e * cart - compr))
        else:
            pct_e = pct_e_proy = None
            o.update(cobertura_cumpl=None, cobertura_falta=None, cobertura_min=None)
        o.update(cobertura_llevas=compr, cobertura_cartera=cart,
                 cobertura_meta=meta_e, cobertura_pct=pct_e,
                 cobertura_proy=pct_e_proy)

        # ── Amplitud de SKU (promedio: se proyecta igual) ───────────────────
        sku = _num(r.get("amplitud_real")) or 0.0
        meta_s = _num(r.get("amplitud_meta"))
        o.update(amplitud_llevas=sku, amplitud_meta=meta_s, amplitud_proy=sku,
                 amplitud_cumpl=(sku / meta_s) if (meta_s and compr) else None,
                 amplitud_falta=max(0.0, round(meta_s - sku, 1)) if meta_s else None,
                 amplitud_min=(umb["amplitud"] * meta_s - sku) if meta_s else None)

        # ── Pago y comisión proyectada ──────────────────────────────────────
        tasa = 0.0
        for k in PCT:
            pago = _paga(o.get(f"{k}_cumpl"), umb[k], PCT[k])
            o[f"{k}_pago"] = pago
            o[f"{k}_umbral"] = umb[k]
            o[f"{k}_dejando"] = (PCT[k] - pago) * venta_proy
            tasa += pago
        o["tasa_proy"] = tasa
        o["venta_proy"] = venta_proy
        o["comision_proy"] = tasa * venta_proy
        o["si_todo"] = TASA_MAX * max(meta_v or 0.0, venta_proy)
        o["dejando"] = max(0.0, o["si_todo"] - o["comision_proy"])
        foco = max(PCT, key=lambda k: o[f"{k}_dejando"])
        o["foco"] = foco if o[f"{foco}_dejando"] > 0.5 else None
        filas.append(o)

    out = pd.DataFrame(filas)
    return out, ctx


def estado(cumpl, umbral: float) -> str:
    """'ok' (≥100%), 'parcial' (desde el piso), 'no' (bajo el piso), '' sin dato."""
    if cumpl is None or (isinstance(cumpl, float) and math.isnan(cumpl)):
        return ""
    if cumpl >= 1:
        return "ok"
    if cumpl >= umbral:
        return "parcial"
    return "no"
