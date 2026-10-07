"""Imágenes PNG del avance de comisiones (modelo nuevo) para enviar por WhatsApp.

  · tablero_png — foto del equipo: cumplimiento proyectado por indicador y
    comisión proyectada, una fila por vendedor (mismo motor que tabla_png).
  · ficha_png   — "Mi avance del mes" de un vendedor, con el diseño de la hoja
    por vendedor de Avance_Diario_Comisiones_Vendedores_Kreems.xlsx: tarjetas de
    comisión, tabla por indicador con barra de avance, qué le falta y su foco.
  · fichas_zip  — todas las fichas del equipo en un .zip.

Se generan server-side con matplotlib (funciona en Streamlit Cloud).
"""
from __future__ import annotations

import io
import math
import textwrap
import zipfile

import pandas as pd

from app.avance_comisiones import INDICADORES, estado
from app.export import tabla_png

MESES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
         7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
         12: "Diciembre"}

VINO = "#3D1022"          # franja oscura del encabezado (como la planilla)
MAGENTA = "#E62984"
TINTA = "#22273A"
GRIS = "#5A6072"
LINEA = "#D9DCE6"
# Semáforo: (fondo, texto)
SEM = {"ok": ("#D7F0DD", "#1A7F4B"), "parcial": ("#FFEFC2", "#9C6500"),
       "no": ("#FBD5D5", "#B42318"), "": ("#F1F2F6", GRIS)}
ESTADO_TXT = {"ok": "✔ En meta", "parcial": "▲ Cobra parcial", "no": "✖ No cobra",
              "": "Sin dato"}


# ── Formato ─────────────────────────────────────────────────────────────────
def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def clp(v) -> str:
    return f"${v:,.0f}".replace(",", ".") if _ok(v) else "—"


def pct(v, dec: int = 0) -> str:
    if not _ok(v):
        return "—"
    return f"{v * 100:,.{dec}f}%".replace(",", "X").replace(".", ",").replace("X", ".")


def num(v, dec: int = 0) -> str:
    if not _ok(v):
        return "—"
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _plural(n, uno: str, varios: str) -> str:
    return f"{num(n)} {uno if _ok(n) and round(n) == 1 else varios}"


def faltan(n, uno: str, varios: str, extra: str = "") -> str:
    """'Te falta 1 visita' / 'Te faltan 9 visitas' (+ complemento opcional)."""
    return (f"Te falta{'' if round(n) == 1 else 'n'} "
            f"{_plural(n, uno, varios)}{extra}")


def subtitulo(ctx: dict, segunda_persona: bool = False) -> str:
    c = ctx["corte"]
    resta = ("Te quedan" if segunda_persona else "Quedan")
    return (f"Corte al {c.day}-{c.month}-{c.year}  ·  Día hábil "
            f"{ctx['dias_transcurridos']} de {ctx['dias_mes']}  ·  {resta} "
            f"{ctx['dias_restantes']} días hábiles")


# ── Textos por indicador (los usa la ficha y la tabla de la app) ────────────
def textos_indicador(r, k: str, ctx: dict) -> dict:
    """Llevas / meta / proyección / qué falta / mínimo para cobrar, en palabras."""
    quedan = ctx["dias_restantes"]
    cumpl = r.get(f"{k}_cumpl")
    est = estado(cumpl, r.get(f"{k}_umbral", 0.8))
    piso = pct(r.get(f"{k}_umbral", 0.8))
    if k == "cuota":
        meta = r.get("cuota_meta")
        falta = r.get("cuota_falta")
        if not _ok(meta):
            q, m = "Sin objetivo de venta cargado", "—"
        elif falta <= 0:
            q = "✔ Cuota cumplida: todo lo que vendas suma comisión"
            m = "✔ Ya cobras este indicador"
        else:
            q = f"Te faltan {clp(falta)} de venta"
            if quedan:
                q += f"  →  {clp(falta / quedan)} por día hábil"
            mn = r.get("cuota_min")
            m = "✔ Ya cobras este indicador" if mn <= 0 else f"Vende {clp(mn)} más"
        return dict(llevas=clp(r.get("cuota_llevas")), meta=clp(meta),
                    proy=clp(r.get("cuota_proy")), falta=q, minimo=m, estado=est)
    if k == "nuevos":
        f, mn = r.get("nuevos_falta"), r.get("nuevos_min")
        if not _ok(f):
            q = m = "—"
        else:
            q = ("✔ Meta cumplida" if f <= 0 else
                 faltan(f, "cliente nuevo", "clientes nuevos",
                        f" en {quedan} días hábiles" if quedan else ""))
            m = ("✔ Ya cobras este indicador" if mn <= 0 else
                 faltan(mn, "cliente nuevo", "clientes nuevos"))
        return dict(llevas=_plural(r.get("nuevos_llevas"), "cliente", "clientes"),
                    meta=_plural(r.get("nuevos_meta"), "cliente", "clientes"),
                    proy=f"{num(r.get('nuevos_proy'), 1)} clientes", falta=q, minimo=m,
                    estado=est)
    if k == "ruta":
        ag = r.get("ruta_agend")
        if not _ok(ag) or not ag:
            return dict(llevas=f"{num(r.get('ruta_llevas'))} visitas",
                        meta="Sin visitas programadas", proy="—",
                        falta="Sin ruta asignada en Autoventa", minimo="—", estado="")
        meta_v = math.ceil(round(r["ruta_meta"] * ag, 6))
        f, mn = r.get("ruta_falta"), r.get("ruta_min")
        q = ("✔ Cobertura cumplida" if f <= 0 else
             faltan(f, "visita", "visitas")
             + (f"  →  {math.ceil(f / quedan)} por día hábil" if quedan else ""))
        m = ("✔ Ya cobras este indicador" if mn <= 0 else
             faltan(mn, "visita", "visitas"))
        return dict(llevas=f"{num(r['ruta_llevas'])} de {num(ag)} visitas",
                    meta=f"{pct(r['ruta_meta'])}  ({num(meta_v)} visitas)",
                    proy=f"{pct(r.get('ruta_proy'))} de visitas", falta=q, minimo=m,
                    estado=est)
    if k == "cobertura":
        cart = r.get("cobertura_cartera")
        if not _ok(cart) or not cart:
            return dict(llevas=f"{num(r.get('cobertura_llevas'))} clientes",
                        meta="Sin cartera asignada", proy="—",
                        falta="Cargar la cartera del vendedor", minimo="—", estado="")
        meta_c = math.ceil(round(r["cobertura_meta"] * cart, 6))
        f, mn = r.get("cobertura_falta"), r.get("cobertura_min")
        q = ("✔ Meta cumplida" if f <= 0 else
             faltan(f, "cliente de tu cartera que te compre",
                    "clientes de tu cartera que te compren")
             + (f"  →  {math.ceil(f / quedan)} por día" if quedan else ""))
        m = ("✔ Ya cobras este indicador" if mn <= 0 else
             faltan(mn, "cliente que compre", "clientes que compren"))
        return dict(llevas=f"{num(r['cobertura_llevas'])} de {num(cart)} clientes",
                    meta=f"{pct(r['cobertura_meta'])}  ({num(meta_c)} clientes)",
                    proy=f"{pct(r.get('cobertura_proy'))} de tu cartera", falta=q,
                    minimo=m, estado=est)
    # amplitud
    f, mn = r.get("amplitud_falta"), r.get("amplitud_min")
    if not _ok(f):
        q = m = "—"
    elif r.get("cobertura_llevas", 0) <= 0:
        q, m = "Todavía no hay clientes con compra este mes", "—"
    else:
        extra = math.ceil(round(f * r.get("cobertura_llevas", 0), 6))
        q = ("✔ Meta cumplida" if f <= 0 else
             f"Sube {num(f, 1)} SKU por cliente  ≈  {extra} productos más "
             f"colocados en tus clientes")
        m = "✔ Ya cobras este indicador" if mn <= 0 else f"Sube {num(mn, 1)} SKU por cliente"
    return dict(llevas=f"{num(r.get('amplitud_llevas'), 1)} SKU por cliente",
                meta=f"{num(r.get('amplitud_meta'), 1)} SKU por cliente",
                proy=f"{num(r.get('amplitud_proy'), 1)} SKU por cliente",
                falta=q, minimo=m, estado=est)


def foco_texto(r, ctx: dict) -> str:
    k = r.get("foco")
    if not isinstance(k, str) or not k:   # en un DataFrame el None queda como NaN
        return ("Vas al 100% en todo. Sigue así: cada peso extra que vendas aumenta "
                "tu comisión.")
    nombre = dict(INDICADORES)[k]
    t = textos_indicador(r, k, ctx)["falta"]
    return (f"{nombre}:  {t}   →  hoy te cuesta ≈ {clp(r.get(f'{k}_dejando'))} "
            f"de comisión")


FUENTE_AGENDA = {"reporte": "reporte de Autoventa",
                 "objetivo": "Obj. visitas del Panel Gerencia",
                 "estimado": "estimadas por la ruta de cada cliente"}
FUENTE_VIS = {"reporte": "reporte de Autoventa", "gps": "GPS de Autoventa (1 por cliente por semana)"}


def fuentes_ruta(df: pd.DataFrame) -> str:
    """Una línea con de dónde salen las visitas programadas y las hechas."""
    ag = sorted({FUENTE_AGENDA.get(x, x) for x in df["ruta_fuente"].dropna()})
    vi = sorted({FUENTE_VIS.get(x, x) for x in df["ruta_fuente_vis"].dropna()})
    return (f"Visitas programadas: {' / '.join(ag)}.  Visitas hechas: {' / '.join(vi)}. "
            "Para pagar manda el reporte de Autoventa de fin de mes.")


# ── Tablero del equipo ──────────────────────────────────────────────────────
_GRUPOS_TABLERO = [
    ("CUOTA DE VENTA", "#1E5FA5", 1, 3),
    ("CLIENTES NUEVOS", "#1A7F4B", 4, 6),
    ("COBERTURA DE RUTA", "#C2185B", 7, 9),
    ("EFECTIVIDAD DE CARTERA", "#6A4C93", 10, 12),
    ("AMPLITUD DE SKU", "#7A8B2E", 13, 15),
    ("COMISIÓN PROYECTADA", VINO, 16, 19),
]
_LABELS_TABLERO = [
    "Vendedor",
    "Venta", "Meta", "Proy.",
    "Llevas", "Meta", "Proy.",
    "Visitas", "Program.", "Proy.",
    "Compraron", "Cartera", "Proy.",
    "SKU/cli.", "Meta", "Proy.",
    "Tasa", "Comisión", "Si cumple todo", "Se deja",
]


def tabla_tablero(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    """DataFrame de strings + colores (texto, fondo) de las columnas 'Proy.'."""
    filas, fondo, texto = [], {}, {}
    cols = ["Vendedor", "c_v", "c_m", "c_p", "n_l", "n_m", "n_p", "r_v", "r_a", "r_p",
            "e_c", "e_t", "e_p", "s_l", "s_m", "s_p", "tasa", "com", "todo", "deja"]
    proy_col = {"cuota": "c_p", "nuevos": "n_p", "ruta": "r_p", "cobertura": "e_p",
                "amplitud": "s_p"}
    for i, (_, r) in enumerate(df.iterrows()):
        filas.append([
            r["vendedor"],
            clp(r["cuota_llevas"]), clp(r["cuota_meta"]), pct(r["cuota_cumpl"]),
            num(r["nuevos_llevas"]), num(r["nuevos_meta"]), pct(r["nuevos_cumpl"]),
            num(r["ruta_llevas"]), num(r["ruta_agend"]), pct(r["ruta_cumpl"]),
            num(r["cobertura_llevas"]), num(r["cobertura_cartera"]), pct(r["cobertura_cumpl"]),
            num(r["amplitud_llevas"], 1), num(r["amplitud_meta"], 1), pct(r["amplitud_cumpl"]),
            pct(r["tasa_proy"], 2), clp(r["comision_proy"]), clp(r["si_todo"]),
            clp(r["dejando"]),
        ])
        for k, c in proy_col.items():
            bg, fg = SEM[estado(r.get(f"{k}_cumpl"), r.get(f"{k}_umbral", 0.8))]
            fondo[(i, c)], texto[(i, c)] = bg, fg
    filas.append(["TOTAL EQUIPO", clp(df["cuota_llevas"].sum()),
                  clp(df["cuota_meta"].sum(min_count=1)), "", "", "", "", "", "", "",
                  "", "", "", "", "", "",
                  pct(df["comision_proy"].sum() / df["venta_proy"].sum(), 2)
                  if df["venta_proy"].sum() else "—",
                  clp(df["comision_proy"].sum()), clp(df["si_todo"].sum()),
                  clp(df["dejando"].sum())])
    return pd.DataFrame(filas, columns=cols), texto, fondo


def tablero_png(df: pd.DataFrame, ctx: dict, anio: int, mes: int) -> bytes:
    disp, texto, fondo = tabla_tablero(df)
    r0 = df.iloc[0]
    notas = (
        f"Metas: cuota = objetivo de venta del mes · nuevos = meta automática por vendedor · "
        f"ruta = {pct(r0['ruta_meta'])} de las visitas programadas · efectividad = "
        f"{pct(r0['cobertura_meta'])} de la cartera · amplitud = SKU distintos por cliente.\n"
        "Proy. = cumplimiento proyectado al cierre al ritmo de hoy.   Verde ≥ 100%  ·  "
        "Amarillo = cobra parcial (desde el piso)  ·  Rojo = bajo el piso, no cobra ese "
        "indicador.\n" + fuentes_ruta(df))
    return tabla_png(disp, f"AVANCE DE COMISIONES · {MESES[mes].upper()} {anio}",
                     subtitulo(ctx), color_celdas=texto, fondo_celdas=fondo,
                     resaltar_ultima=True, col_labels=_LABELS_TABLERO,
                     grupos=_GRUPOS_TABLERO, notas=notas, dpi=220)


# ── Ficha del vendedor ──────────────────────────────────────────────────────
def ficha_png(r, ctx: dict, dpi: int = 220) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, FancyBboxPatch

    from app.styles import LOGO_PATH

    W, H = 13.6, 6.55
    fig = plt.figure(figsize=(W, H), dpi=dpi, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    X0, X1 = 0.3, W - 0.3

    def caja(x, y, w, h, color, ec="none", lw=0.0, z=1):
        ax.add_patch(Rectangle((x, y), w, h, facecolor=color, edgecolor=ec,
                               linewidth=lw, zorder=z))

    def txt(x, y, s, size=9, color=TINTA, w="normal", ha="left", va="center",
            style="normal", z=3):
        ax.text(x, y, s, fontsize=size, color=color, fontweight=w, ha=ha, va=va,
                fontstyle=style, zorder=z)

    # ── Encabezado ──────────────────────────────────────────────────────────
    caja(X0, 0.25, X1 - X0, 0.8, VINO)
    try:
        import matplotlib.image as mpimg
        logo = mpimg.imread(str(LOGO_PATH))
        lw = 1.55
        lh = lw * logo.shape[0] / logo.shape[1]
        ax.imshow(logo, extent=(X0 + 0.12, X0 + 0.12 + lw, 0.65 + lh / 2, 0.65 - lh / 2),
                  zorder=4)
    except Exception:
        txt(X0 + 0.15, 0.65, "kreems", 20, MAGENTA, "bold")
    txt(X0 + 2.0, 0.5, f"MI AVANCE DEL MES  ·  {str(r['vendedor']).upper()}", 15,
        "white", "bold")
    txt(X0 + 2.0, 0.84, subtitulo(ctx, segunda_persona=True), 9.5, MAGENTA, "bold")

    # ── Tarjetas ────────────────────────────────────────────────────────────
    from app.pages.comisiones_v1 import TASA_MAX
    tarjetas = [
        ("COMISIÓN PROYECTADA", clp(r["comision_proy"]), "si sigues al ritmo de hoy",
         VINO, "white", "white"),
        ("TU TASA PROYECTADA", pct(r["tasa_proy"], 2),
         f"de un máximo de {pct(TASA_MAX, 1)}", "#FBE3EE", VINO, GRIS),
        ("SI CUMPLES TODO AL 100%", clp(r["si_todo"]), "5% de tu venta",
         "#E3F2E3", "#2E6B2E", "#2E6B2E"),
        ("TE ESTÁS DEJANDO EN LA MESA", clp(r["dejando"]),
         "lo que ganas si llevas todo al 100%", "#FFF3CC", "#A36A10", "#A36A10"),
    ]
    anchos = [2.95, 2.95, 3.3, X1 - X0 - 9.2]
    x, y, h = X0, 1.22, 0.98
    for (lab, val, sub, bg, fg, fs), w in zip(tarjetas, anchos):
        caja(x, y, w, h, bg)
        txt(x + w / 2, y + 0.17, lab, 8, fg if bg != VINO else "white", "bold", "center")
        txt(x + w / 2, y + 0.5, val, 19, fg, "bold", "center")
        txt(x + w / 2, y + 0.82, sub, 7.5, fs, ha="center", style="italic")
        x += w

    # ── Tabla de indicadores ────────────────────────────────────────────────
    cols = [("Indicador", 1.78), ("Llevas", 1.26), ("Meta del mes\n(100%)", 1.4),
            ("Proyección\nal cierre", 1.26), ("Avance\nproyectado", 1.2), ("%", 0.6),
            ("Estado", 1.1), ("¿Qué te falta para el 100%?", 2.62),
            ("Mínimo para\nempezar a cobrar", 0)]
    cols[-1] = (cols[-1][0], X1 - X0 - sum(w for _, w in cols[:-1]))
    yh, hh, rh = 2.4, 0.38, 0.47
    x = X0
    for nom, w in cols:
        caja(x, yh, w, hh, VINO, ec="white", lw=0.8)
        txt(x + w / 2, yh + hh / 2, nom, 8.3, "white", "bold", "center")
        x += w
    for i, (k, nombre) in enumerate(INDICADORES):
        t = textos_indicador(r, k, ctx)
        y = yh + hh + i * rh
        cumpl = r.get(f"{k}_cumpl")
        bg, fg = SEM[t["estado"]]
        celdas = [nombre, t["llevas"], t["meta"], t["proy"], None, pct(cumpl),
                  ESTADO_TXT[t["estado"]], t["falta"], t["minimo"]]
        x = X0
        for j, ((_, w), val) in enumerate(zip(cols, celdas)):
            fondo_c = bg if j in (4, 5, 6) else "white"
            caja(x, y, w, rh, fondo_c, ec=LINEA, lw=0.8)
            if j == 0:
                txt(x + 0.08, y + rh / 2, val, 9, VINO, "bold")
            elif j == 4:
                frac = max(0.0, min(1.0, cumpl)) if _ok(cumpl) else 0.0
                bx, bw, bh = x + 0.1, w - 0.2, 0.2
                caja(bx, y + (rh - bh) / 2, bw, bh, "#E9EAF0", z=2)
                if frac:
                    caja(bx, y + (rh - bh) / 2, bw * frac, bh, fg, z=2)
            elif j in (5, 6):
                txt(x + w / 2, y + rh / 2, val, 10.5 if j == 5 else 8.3, fg, "bold", "center")
            elif j in (7, 8):
                ancho = int(w * 14.5) if j == 7 else int(w * 16)
                lineas = textwrap.wrap(str(val), width=max(10, ancho))[:2]
                bold = "bold" if j == 7 and not str(val).startswith("✔") else "normal"
                txt(x + 0.08, y + rh / 2, "\n".join(lineas), 7.9, TINTA, bold,
                    "left", "center")
            else:
                txt(x + w / 2, y + rh / 2, val, 8.6, TINTA, ha="center")
            x += w

    # ── Foco ────────────────────────────────────────────────────────────────
    yf = yh + hh + len(INDICADORES) * rh + 0.22
    caja(X0, yf, 2.5, 0.5, MAGENTA)
    txt(X0 + 1.25, yf + 0.25, "TU FOCO HOY", 11, "white", "bold", "center")
    caja(X0 + 2.5, yf, X1 - X0 - 2.5, 0.5, "#FBE3EE")
    txt(X0 + 2.65, yf + 0.25, foco_texto(r, ctx), 9.6, VINO, "bold")

    # ── Cómo leer ───────────────────────────────────────────────────────────
    yl = yf + 0.78
    txt(X0, yl, "Cómo leer:", 7.6, VINO, "bold")
    txt(X0 + 1.0, yl,
        "Verde = vas al 100% o más  ·  Amarillo = vas sobre el piso, cobras parte  ·  "
        "Rojo = vas bajo el piso, ese indicador no paga. La proyección supone que sigues "
        "al mismo ritmo el resto del mes.", 7.6, GRIS)
    txt(X0 + 1.0, yl + 0.22,
        f"Tu comisión = tasa (máx. {pct(TASA_MAX, 1)}) × tu venta neta del mes. Cada "
        f"indicador suma su parte de la tasa desde el {pct(r.get('cuota_umbral', 0.8))} "
        "de su meta, proporcional a lo que cumples.", 7.6, GRIS)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.08,
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def nombre_archivo(vendedor: str, ctx: dict) -> str:
    base = "_".join(str(vendedor).split()[:2])
    c = ctx["corte"]
    return f"avance_{base}_{c.year}-{c.month:02d}-{c.day:02d}.png"


def fichas_zip(df: pd.DataFrame, ctx: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for _, r in df.iterrows():
            z.writestr(nombre_archivo(r["vendedor"], ctx), ficha_png(r, ctx))
    return buf.getvalue()
