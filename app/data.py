"""Capa de acceso a datos — todas las queries en un lugar.
El cliente ya trae el JWT del usuario → RLS se aplica automáticamente.
No se recalcula ninguna métrica aquí: se leen desde las vistas de Postgres.
"""
import pandas as pd
from datetime import date, timedelta
from supabase import Client


# ── Resumen principal (v_resumen_vendedor_mes) ───────────────────────────────

def get_resumen(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """
    Lee la vista de métricas para el período dado.
    RLS filtra automáticamente: vendedor ve solo sus filas.
    """
    r = (client.table("v_resumen_vendedor_mes")
         .select("*")
         .eq("anio", anio)
         .eq("mes", mes)
         .execute())
    if not r.data:
        return pd.DataFrame()
    return pd.DataFrame(r.data)


def get_resumen_anio(client: Client, anio: int) -> pd.DataFrame:
    """Devuelve todos los meses de un año (para tendencias)."""
    r = (client.table("v_resumen_vendedor_mes")
         .select("*")
         .eq("anio", anio)
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


# ── Pedidos Autoventa (columna "Pedidos" del Power BI) ──────────────────────

def get_pedidos_resumen(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Neto total de pedidos por vendedor (incluye facturados y Sin DTE).

    Filtra por fecha EN LA QUERY y pagina: fact_pedidos supera el límite de
    1000 filas de PostgREST, así que un .execute() sin rango devolvía solo las
    primeras 1000 (≈ el primer mes cargado) y el filtro en pandas dejaba el mes
    consultado en 0 → la columna "Pedidos" salía vacía.
    """
    fecha_ini = f"{anio:04d}-{mes:02d}-01"
    fecha_fin = f"{anio + (mes // 12):04d}-{(mes % 12) + 1:02d}-01"  # 1er día del mes siguiente
    _PAGE = 1000
    all_rows: list = []
    offset = 0
    while True:
        r = (client.table("fact_pedidos")
             .select("vendedor_id,neto,doc_venta,fecha")
             .gte("fecha", fecha_ini)
             .lt("fecha", fecha_fin)
             .order("id")
             .range(offset, offset + _PAGE - 1)
             .execute())
        if not r.data:
            break
        all_rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE

    if not all_rows:
        return pd.DataFrame(columns=["vendedor_id", "pedidos_neto", "no_facturado_neto"])
    df = pd.DataFrame(all_rows)
    df["neto"] = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    agg = df.groupby("vendedor_id").agg(
        pedidos_neto=("neto", "sum"),
        no_facturado_neto=("neto", lambda x: x[df.loc[x.index, "doc_venta"] == "Sin DTE"].sum()),
    ).reset_index()
    return agg


# ── Análisis de ventas por producto / categoría ──────────────────────────────

def get_ventas_producto(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """fact_ventas unido con dim_producto para análisis de categoría/fabricante."""
    rv = (client.table("fact_ventas")
          .select("producto_codigo,neto,margen,cantidad,tipo_dcto,fecha")
          .execute())
    rp = client.table("dim_producto").select("codigo,nombre,categoria,subcategoria,fabricante").execute()

    if not rv.data:
        return pd.DataFrame()

    dfv = pd.DataFrame(rv.data)
    dfp = pd.DataFrame(rp.data) if rp.data else pd.DataFrame()

    dfv["fecha"] = pd.to_datetime(dfv["fecha"], errors="coerce")
    dfv = dfv[(dfv["fecha"].dt.year == anio) & (dfv["fecha"].dt.month == mes)]

    for col in ["neto", "margen", "cantidad"]:
        dfv[col] = pd.to_numeric(dfv[col], errors="coerce").fillna(0)

    if not dfp.empty:
        # Normalizar categoria
        dfp["categoria"] = dfp["categoria"].str.upper().str.strip()
        dfv = dfv.merge(dfp.rename(columns={"codigo": "producto_codigo"}),
                        on="producto_codigo", how="left")
    return dfv


def get_ventas_region(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """fact_ventas unido con dim_cliente para análisis por región/comuna."""
    rv = (client.table("fact_ventas")
          .select("cliente_rut,neto,margen,cantidad,tipo_dcto,fecha,sociedad_id")
          .execute())
    rc = client.table("dim_cliente").select("rut,region,comuna,sociedad_id").execute()

    if not rv.data:
        return pd.DataFrame()

    dfv = pd.DataFrame(rv.data)
    dfc = pd.DataFrame(rc.data) if rc.data else pd.DataFrame()

    dfv["fecha"] = pd.to_datetime(dfv["fecha"], errors="coerce")
    dfv = dfv[(dfv["fecha"].dt.year == anio) & (dfv["fecha"].dt.month == mes)]

    for col in ["neto", "margen"]:
        dfv[col] = pd.to_numeric(dfv[col], errors="coerce").fillna(0)

    if not dfc.empty:
        # Normalizar region (quitar duplicados con/sin tilde)
        dfc["region"] = dfc["region"].str.strip()
        dfv = dfv.merge(dfc.rename(columns={"rut": "cliente_rut", "sociedad_id": "soc_cliente"}),
                        on="cliente_rut", how="left")
    return dfv


# ── Máquinas ─────────────────────────────────────────────────────────────────

def get_maquinas(client: Client, anio: int, mes: int) -> pd.DataFrame:
    r = (client.table("fact_maquinas")
         .select("vendedor_id,tipo_mov,estado,fecha,cliente_rut")
         .execute())
    if not r.data:
        return pd.DataFrame()
    df = pd.DataFrame(r.data)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df[(df["fecha"].dt.year == anio) & (df["fecha"].dt.month == mes)]
    return df


def get_maquinas_rango(client: Client, fecha_ini, fecha_fin,
                       sociedad_ids=None) -> pd.DataFrame:
    """
    fact_maquinas filtrado por rango de fechas, paginado (bypass límite 1000 de
    PostgREST). RLS aplica: el vendedor ve solo sus máquinas.
    """
    fi = fecha_ini.isoformat() if hasattr(fecha_ini, "isoformat") else str(fecha_ini)
    ff = fecha_fin.isoformat() if hasattr(fecha_fin, "isoformat") else str(fecha_fin)
    _PAGE, offset, rows = 1000, 0, []
    while True:
        q = (client.table("fact_maquinas")
             .select("vendedor_id,tipo_mov,estado,sociedad_id,fecha,cliente_rut,documento")
             .gte("fecha", fi).lte("fecha", ff)
             .order("id")
             .range(offset, offset + _PAGE - 1))
        if sociedad_ids:
            q = q.in_("sociedad_id", sociedad_ids)
        r = q.execute()
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def get_maquinas_historico(client: Client, anio: int) -> pd.DataFrame:
    r = (client.table("fact_maquinas")
         .select("vendedor_id,tipo_mov,estado,fecha")
         .execute())
    if not r.data:
        return pd.DataFrame()
    df = pd.DataFrame(r.data)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df[df["fecha"].dt.year == anio]
    df["mes"] = df["fecha"].dt.month
    return df


# ── Calendario laboral ───────────────────────────────────────────────────────

def get_calendario(client: Client, anio: int, mes: int) -> dict:
    r = (client.table("calendario_laboral")
         .select("dias_totales,dias_trabajados")
         .eq("anio", anio).eq("mes", mes)
         .execute())
    row = r.data[0] if r.data else {"dias_totales": 30, "dias_trabajados": 20}

    # Para el mes en curso, recalcular días hábiles dinámicamente descontando
    # feriados (consistente con la vista v_resumen_vendedor_mes). El valor en BD
    # queda desactualizado entre cargas del ETL.
    hoy = date.today()
    if anio == hoy.year and mes == hoy.month:
        inicio = date(anio, mes, 1)
        ultimo = (date(anio + 1, 1, 1) if mes == 12
                  else date(anio, mes + 1, 1)) - timedelta(days=1)
        # Feriados del mes (si la tabla aún no existe, se ignora sin romper)
        feriados: set = set()
        try:
            fr = (client.table("feriados").select("fecha")
                  .gte("fecha", inicio.isoformat())
                  .lte("fecha", ultimo.isoformat()).execute())
            feriados = {f["fecha"] for f in (fr.data or [])}
        except Exception:
            pass

        def _habiles(desde: date, hasta: date) -> int:
            return sum(
                1 for n in range((hasta - desde).days + 1)
                if (x := desde + timedelta(days=n)).weekday() < 5
                and x.isoformat() not in feriados
            )

        row = {**row,
               "dias_trabajados": _habiles(inicio, hoy),
               "dias_totales":    _habiles(inicio, ultimo)}

    return row


# ── Objetivos (edición por gerencia) ────────────────────────────────────────

def get_objetivos(client: Client, anio: int, mes: int) -> pd.DataFrame:
    r = (client.table("objetivos_mensuales")
         .select("vendedor_id,obj_venta,obj_maquinas,obj_visitas")
         .eq("anio", anio).eq("mes", mes)
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def upsert_objetivo(client: Client, vendedor_id: int, anio: int, mes: int,
                    obj_venta: float, obj_maquinas: int, obj_visitas: int):
    """Guarda (crea o actualiza) el objetivo de un vendedor."""
    client.table("objetivos_mensuales").upsert({
        "vendedor_id": vendedor_id,
        "anio": anio,
        "mes": mes,
        "obj_venta": obj_venta,
        "obj_maquinas": obj_maquinas,
        "obj_visitas": obj_visitas,
    }, on_conflict="vendedor_id,anio,mes").execute()


def get_todos_vendedores(client: Client) -> pd.DataFrame:
    r = client.table("dim_vendedor").select("id,nombre_canonico,activo").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_ventas_diarias(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """
    Suma de neto por fecha del mes (facturas + NC ya firmadas negativas).
    Filtro server-side por rango de fecha; agrupación en Python.
    Usada para la evolución diaria acumulada (Sección 4 del dashboard).
    """
    import calendar as _cal
    ultimo_dia = _cal.monthrange(anio, mes)[1]
    r = (client.table("fact_ventas")
         .select("fecha,neto")
         .gte("fecha", f"{anio}-{mes:02d}-01")
         .lte("fecha", f"{anio}-{mes:02d}-{ultimo_dia:02d}")
         .execute())
    if not r.data:
        return pd.DataFrame(columns=["fecha", "neto_dia"])
    df = pd.DataFrame(r.data)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["neto"]  = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    agg = df.groupby("fecha")["neto"].sum().reset_index()
    agg.columns = ["fecha", "neto_dia"]
    return agg.sort_values("fecha").reset_index(drop=True)


# ── Análisis de ventas v2 (rango de fechas, todas las columnas) ──────────────

def get_ventas_rango(client: Client, fecha_ini, fecha_fin, sociedad_ids=None) -> pd.DataFrame:
    """
    fact_ventas filtrado por rango de fechas con paginación completa.
    PostgREST tiene un max-rows por request (típicamente 1000). Usamos
    .range(offset, offset+PAGE-1) para traer todas las páginas sin perder filas.
    """
    _PAGE = 1000
    all_rows: list = []
    offset = 0

    while True:
        q = (client.table("fact_ventas")
             .select("fecha,tipo_dcto,n_dcto,producto_codigo,cliente_rut,"
                     "sociedad_id,sucursal,vendedor_id,cantidad,neto,costo,margen")
             .gte("fecha", str(fecha_ini))
             .lte("fecha", str(fecha_fin))
             .order("id")
             .range(offset, offset + _PAGE - 1))
        if sociedad_ids:
            q = q.in_("sociedad_id", sociedad_ids)
        r = q.execute()
        if not r.data:
            break
        all_rows.extend(r.data)
        if len(r.data) < _PAGE:   # última página
            break
        offset += _PAGE

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    for col in ["cantidad", "neto", "costo", "margen"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def get_dim_producto_all(client: Client) -> pd.DataFrame:
    """dim_producto completo (para multiselect de categorías y enriquecimiento)."""
    r = client.table("dim_producto").select(
        "codigo,nombre,categoria,subcategoria,fabricante"
    ).execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_dim_cliente_geo(client: Client) -> pd.DataFrame:
    """dim_cliente con rut, region, comuna (para análisis geográfico)."""
    r = client.table("dim_cliente").select("rut,region,comuna").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_dim_sociedad(client: Client) -> pd.DataFrame:
    """dim_sociedad para mapeo id → nombre en filtro de sociedad."""
    r = client.table("dim_sociedad").select("id,nombre").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


# ── Comisiones (sección Sueldos y Comisiones — solo gerencia) ───────────────

def get_comisiones(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Lee la vista de cálculo de comisiones en vivo (v_comision_vendedor_mes).
    RLS: la vista filtra por es_gerencia(); un vendedor recibe 0 filas."""
    r = (client.table("v_comision_vendedor_mes")
         .select("*")
         .eq("anio", anio).eq("mes", mes)
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_comision_entradas(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Entradas editables (cartera, salas Ganga, overrides) del período."""
    r = (client.table("comision_entrada_mensual")
         .select("vendedor_id,cartera_clientes,salas_ganga,"
                 "efectividad_override,pnv_logro_override,maq_logro_override")
         .eq("anio", anio).eq("mes", mes)
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def upsert_comision_entrada(client: Client, vendedor_id: int, anio: int, mes: int,
                            cartera_clientes: int, salas_ganga: int,
                            efectividad_override=None,
                            pnv_logro_override=None,
                            maq_logro_override=None):
    """Crea/actualiza la entrada mensual de comisión de un vendedor."""
    client.table("comision_entrada_mensual").upsert({
        "vendedor_id": vendedor_id,
        "anio": anio,
        "mes": mes,
        "cartera_clientes": int(cartera_clientes),
        "salas_ganga": int(salas_ganga),
        "efectividad_override": efectividad_override,
        "pnv_logro_override": pnv_logro_override,
        "maq_logro_override": maq_logro_override,
    }, on_conflict="vendedor_id,anio,mes").execute()


def get_planes_comision(client: Client) -> pd.DataFrame:
    r = client.table("comision_plan").select("id,codigo,nombre").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_vendedores_plan(client: Client) -> pd.DataFrame:
    """Vendedores con su plan de comisión asignado."""
    r = (client.table("dim_vendedor")
         .select("id,nombre_canonico,plan_comision_id,activo")
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def update_vendedor_plan(client: Client, vendedor_id: int, plan_id: int):
    client.table("dim_vendedor").update(
        {"plan_comision_id": int(plan_id)}
    ).eq("id", vendedor_id).execute()


# Columnas que se guardan en el snapshot comision_calculo (orden del modelo).
_COMISION_SNAPSHOT_COLS = [
    "vendedor_id", "plan_id", "fact_nc", "obj_venta", "logro_pnv", "pnv_aj",
    "com_pnv", "bono_4pct", "obj_maquinas", "maquinas_entregadas", "logro_maquinas",
    "maq_aj", "com_maquinas", "obj_visitas", "n_facturas", "cartera_clientes",
    "logro_efectividad", "efect_aj", "com_efectividad", "total_comision",
    "dias_trabajados", "inab", "semana_corrida", "salas_ganga", "bono_reposicion",
    "total_variable", "total_a_pagar",
]


def cerrar_mes_comisiones(client: Client, anio: int, mes: int) -> int:
    """Congela el cálculo del período: lee la vista en vivo y hace upsert del
    snapshot en comision_calculo. Re-ejecutable (idempotente por vendedor/mes).
    Devuelve el nº de filas congeladas."""
    df = get_comisiones(client, anio, mes)
    if df.empty:
        return 0
    registros = []
    for _, r in df.iterrows():
        fila = {c: r.get(c) for c in _COMISION_SNAPSHOT_COLS}
        fila["anio"] = anio
        fila["mes"] = mes
        fila["cerrado"] = True
        # NaN/None → null
        fila = {k: (None if pd.isna(v) else v) for k, v in fila.items()}
        registros.append(fila)
    client.table("comision_calculo").upsert(
        registros, on_conflict="vendedor_id,anio,mes"
    ).execute()
    return len(registros)


def get_comision_calculo(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Snapshot congelado (historial) del período."""
    r = (client.table("comision_calculo")
         .select("*")
         .eq("anio", anio).eq("mes", mes)
         .execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


# ── Escalas y parámetros de comisión (config editable por gerencia) ─────────

def get_tramos_pnv(client: Client) -> pd.DataFrame:
    r = client.table("comision_tramo_pnv").select("*").order("plan_id").order("logro_pct").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_tramos_maquinas(client: Client) -> pd.DataFrame:
    r = client.table("comision_tramo_maquinas").select("*").order("plan_id").order("logro_pct").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_tramos_efectividad(client: Client) -> pd.DataFrame:
    r = (client.table("comision_tramo_efectividad").select("*")
         .order("plan_id").order("cartera_min").order("efectividad_pct").execute())
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def get_parametros(client: Client) -> pd.DataFrame:
    r = client.table("comision_parametro").select("*").order("clave").execute()
    return pd.DataFrame(r.data) if r.data else pd.DataFrame()


def replace_tramos_plan(client: Client, tabla: str, plan_id: int, registros: list):
    """Reemplaza TODA la escala de un plan: borra las filas del plan e inserta las
    nuevas. Es la forma simple y consistente de guardar una tabla editada (permite
    agregar/quitar tramos). Solo gerencia (RLS)."""
    client.table(tabla).delete().eq("plan_id", int(plan_id)).execute()
    if registros:
        client.table(tabla).insert(registros).execute()


def upsert_parametros(client: Client, registros: list):
    client.table("comision_parametro").upsert(registros, on_conflict="clave").execute()


def get_ventas_detalle_doc(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Detalle de respaldo para el Anexo: una fila por DOCUMENTO (factura/NC) del
    mes, con vendedor y cliente. fact_ventas es nivel línea → se agrega a documento.
    Pagina (bypass del límite 1000 de PostgREST)."""
    import calendar as _cal
    ultimo = _cal.monthrange(anio, mes)[1]
    fini, ffin = f"{anio}-{mes:02d}-01", f"{anio}-{mes:02d}-{ultimo:02d}"
    _PAGE, offset, rows = 1000, 0, []
    while True:
        r = (client.table("fact_ventas")
             .select("vendedor_id,n_dcto,tipo_dcto,fecha,cliente_rut,neto")
             .gte("fecha", fini).lte("fecha", ffin)
             .order("id")
             .range(offset, offset + _PAGE - 1).execute())
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["neto"] = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    doc = (df.groupby(["vendedor_id", "n_dcto", "tipo_dcto", "fecha", "cliente_rut"],
                      dropna=False)["neto"].sum().reset_index())
    # Enriquecer con datos de cliente
    rc = client.table("dim_cliente").select("rut,razon_social,comuna,region").execute()
    if rc.data:
        dfc = pd.DataFrame(rc.data).rename(columns={"rut": "cliente_rut"})
        doc = doc.merge(dfc, on="cliente_rut", how="left")
    return doc.sort_values(["vendedor_id", "fecha", "n_dcto"]).reset_index(drop=True)


def get_ultima_factura(client: Client, anio: int, mes: int) -> str:
    """Fecha de la última factura emitida en el período (dd-mm-yyyy)."""
    import calendar as _cal
    ultimo_dia = _cal.monthrange(anio, mes)[1]
    r = (client.table("fact_ventas")
         .select("fecha")
         .ilike("tipo_dcto", "%factura%")
         .gte("fecha", f"{anio}-{mes:02d}-01")
         .lte("fecha", f"{anio}-{mes:02d}-{ultimo_dia:02d}")
         .order("fecha", desc=True)
         .limit(1)
         .execute())
    if r.data:
        import datetime
        return datetime.date.fromisoformat(r.data[0]["fecha"][:10]).strftime("%d-%m-%Y")
    return "—"
