"""Exportación de comisiones: Excel (consolidado + detalle) y PDF (Anexo Cierre
de Ventas, por trabajador). El PDF usa reportlab; si no está instalado, Excel
sigue funcionando y la UI oculta el botón PDF.
"""
import io
import pandas as pd

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, PageBreak)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

# Columnas legibles para el Excel de resumen
_RESUMEN_COLS = [
    ("nombre_canonico", "Vendedor"),
    ("plan_nombre", "Plan"),
    ("fact_nc", "Fact-NC"),
    ("obj_venta", "Objetivo PNV"),
    ("logro_pnv", "% Logro PNV"),
    ("com_pnv", "Comisión PNV"),
    ("bono_4pct", "Bono 4%"),
    ("obj_maquinas", "Obj. Máquinas"),
    ("maquinas_entregadas", "Máq. Entregadas"),
    ("logro_maquinas", "% Logro Máq."),
    ("com_maquinas", "Comisión Máquinas"),
    ("obj_visitas", "Obj. Visitas"),
    ("n_facturas", "N° Facturas"),
    ("cartera_clientes", "Cartera Clientes"),
    ("logro_efectividad", "% Efectividad"),
    ("com_efectividad", "Comisión Efectividad"),
    ("total_comision", "Total Comisión"),
    ("dias_trabajados", "Días Trab."),
    ("inab", "INAB"),
    ("semana_corrida", "Semana Corrida"),
    ("salas_ganga", "Salas Ganga"),
    ("bono_reposicion", "Bono Reposición"),
    ("total_variable", "Total Variable"),
    ("total_a_pagar", "Total a Pagar"),
]


def _clp(n):
    try:
        if n is None or pd.isna(n):
            return "0"
        return f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "0"


def _pct(n):
    try:
        if n is None or pd.isna(n):
            return "—"
        return f"{float(n) * 100:.0f}%"
    except Exception:
        return "—"


# ── EXCEL ────────────────────────────────────────────────────────────────────

def comisiones_a_excel(df: pd.DataFrame, anio: int, mes: int,
                       detalle: pd.DataFrame | None = None) -> bytes:
    """Excel con hoja Resumen (todos los componentes) y, si se entrega, hoja
    Detalle (facturas/NC fila a fila del mes)."""
    buf = io.BytesIO()
    cols = [c for c, _ in _RESUMEN_COLS if c in df.columns]
    resumen = df[cols].copy()
    resumen.columns = [lbl for c, lbl in _RESUMEN_COLS if c in df.columns]
    resumen = resumen.sort_values("Total a Pagar", ascending=False, na_position="last")

    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        resumen.to_excel(xw, sheet_name=f"Resumen {mes:02d}-{anio}", index=False)
        if detalle is not None and not detalle.empty:
            det = detalle.rename(columns={
                "n_dcto": "N° Doc", "tipo_dcto": "Tipo", "fecha": "Fecha",
                "razon_social": "Cliente", "comuna": "Comuna", "region": "Región",
                "neto": "Neto",
            })
            keep = [c for c in ["N° Doc", "Tipo", "Fecha", "Cliente", "Comuna",
                                "Región", "Neto"] if c in det.columns]
            det[keep].to_excel(xw, sheet_name="Detalle facturas-NC", index=False)
    return buf.getvalue()


# ── PDF (Anexo Cierre de Ventas) ─────────────────────────────────────────────

def comisiones_a_pdf(df: pd.DataFrame, anio: int, mes: int,
                     detalle: pd.DataFrame | None = None) -> bytes:
    """Un Anexo por vendedor (réplica del formato de liquidación de comisiones)."""
    if not REPORTLAB_OK:
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=18 * mm, bottomMargin=15 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("t", parent=styles["Title"], fontSize=15, spaceAfter=2)
    h_sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=10,
                           alignment=1, textColor=colors.HexColor("#1B3A6B"), spaceAfter=8)
    normal = styles["Normal"]
    small = ParagraphStyle("sm", parent=styles["Normal"], fontSize=8)

    elems = []
    df_sorted = df.sort_values("nombre_canonico")
    for idx, (_, r) in enumerate(df_sorted.iterrows()):
        if idx > 0:
            elems.append(PageBreak())
        elems += _anexo_vendedor(r, anio, mes, detalle, h_title, h_sub, normal, small)

    doc.build(elems)
    return buf.getvalue()


def _kv_table(rows, col_widths):
    """Tabla clave-valor estilo formulario (bordes grises, etiqueta izq.)."""
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0B0B0")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F5FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _anexo_vendedor(r, anio, mes, detalle, h_title, h_sub, normal, small):
    el = []
    el.append(Paragraph("Anexo Cierre de Ventas", h_title))
    el.append(Paragraph(f"{MESES[mes]} {anio}", h_sub))

    # Datos del trabajador
    el.append(_kv_table([
        ["Nombre:", str(r.get("nombre_canonico", ""))],
        ["Período:", f"{MESES[mes]} {anio}"],
    ], [45 * mm, 120 * mm]))
    el.append(Spacer(1, 6))
    el.append(Paragraph(
        "En el presente documento se informa el detalle de las "
        "<b>comisiones devengadas</b> del período:", normal))
    el.append(Spacer(1, 6))

    # Bloque PNV
    el.append(_kv_table([
        ["Objetivo PNV:", _clp(r.get("obj_venta"))],
        ["Efectivo PNV (Fact-NC):", _clp(r.get("fact_nc"))],
        ["% Logro PNV:", _pct(r.get("logro_pnv"))],
        ["$ Comisión por Facturación:", _clp(r.get("com_pnv"))],
        ["$ Comisión 4% sobre el 110%:", _clp(r.get("bono_4pct"))],
    ], [80 * mm, 85 * mm]))
    el.append(Spacer(1, 5))

    # Bloque Máquinas
    el.append(_kv_table([
        ["Objetivo Máquinas:", _clp(r.get("obj_maquinas"))],
        ["Efectivo Máquinas (entregadas):", _clp(r.get("maquinas_entregadas"))],
        ["% Logro Máquinas:", _pct(r.get("logro_maquinas"))],
        ["$ Comisión por Colocación:", _clp(r.get("com_maquinas"))],
    ], [80 * mm, 85 * mm]))
    el.append(Spacer(1, 5))

    # Bloque Efectividad
    el.append(_kv_table([
        ["Objetivo Efectividad (visitas):", _clp(r.get("obj_visitas"))],
        ["Efectivo Efectividad (N° facturas):", _clp(r.get("n_facturas"))],
        ["Cartera de clientes:", _clp(r.get("cartera_clientes"))],
        ["% Efectividad:", _pct(r.get("logro_efectividad"))],
        ["$ Comisión por Efectividad:", _clp(r.get("com_efectividad"))],
    ], [80 * mm, 85 * mm]))
    el.append(Spacer(1, 8))

    # INCENTIVOS / TOTALES
    el.append(Paragraph("<b>INCENTIVOS Y TOTALES</b>", normal))
    el.append(Spacer(1, 3))
    tot = _kv_table([
        ["$ Comisión por Facturación", _clp(r.get("com_pnv"))],
        ["$ Comisión 4% sobre el 110%", _clp(r.get("bono_4pct"))],
        ["$ Comisión por Colocación", _clp(r.get("com_maquinas"))],
        ["$ Comisión por Efectividad", _clp(r.get("com_efectividad"))],
        ["Total Comisión", _clp(r.get("total_comision"))],
        ["Semana Corrida", _clp(r.get("semana_corrida"))],
        ["Total Variable", _clp(r.get("total_variable"))],
        ["Bono Reposición (salas Ganga)", _clp(r.get("bono_reposicion"))],
        ["TOTAL A PAGAR", _clp(r.get("total_a_pagar"))],
    ], [100 * mm, 65 * mm])
    tot.setStyle(TableStyle([
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#1B3A6B")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
        ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold"),
    ]))
    el.append(tot)

    # Detalle factura/NC del vendedor
    if detalle is not None and not detalle.empty:
        sub = detalle[detalle["vendedor_id"] == r.get("vendedor_id")]
        if not sub.empty:
            el.append(Spacer(1, 8))
            el.append(Paragraph("<b>Detalle de facturas y notas de crédito</b>", normal))
            el.append(Spacer(1, 3))
            data = [["N° Doc", "Tipo", "Fecha", "Cliente", "Neto"]]
            for _, d in sub.iterrows():
                tipo = str(d.get("tipo_dcto", ""))
                tipo_corto = "NC" if "NOTA" in tipo.upper() else "FAC"
                data.append([
                    str(d.get("n_dcto", "")), tipo_corto,
                    str(d.get("fecha", ""))[:10],
                    str(d.get("razon_social", ""))[:38],
                    _clp(d.get("neto")),
                ])
            dt = Table(data, colWidths=[20 * mm, 14 * mm, 24 * mm, 80 * mm, 27 * mm],
                       repeatRows=1)
            dt.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D0D0")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B3A6B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (4, 1), (4, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]))
            el.append(dt)

    el.append(Spacer(1, 10))
    el.append(Paragraph(
        "<i>Al firmar este documento, declara conocer y aceptar el detalle de "
        "las comisiones devengadas.</i>", small))
    return el
