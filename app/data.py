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


def get_maquinas_sin_factura(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """
    Máquinas de instalación cliente nuevo (FL-4) ingresadas en Autoventa pero aún
    SIN factura (doc_venta='Sin DTE'). No cuentan en "Maq. Ingresadas AV" hasta
    que se facture su flete; esta lista las hace visibles para no perderlas de
    vista. RLS aplica: un vendedor ve solo las suyas. Fail-soft: si la lectura de
    fact_pedidos falla (grant/JWT), devuelve vacío sin romper la página.
    """
    cols = ["vendedor_id", "cliente_rut", "fecha", "n_pedido", "vendedor"]
    fecha_ini = f"{anio:04d}-{mes:02d}-01"
    fecha_fin = f"{anio + (mes // 12):04d}-{(mes % 12) + 1:02d}-01"
    try:
        r = (client.table("fact_pedidos")
             .select("vendedor_id,cliente_rut,fecha,n_pedido")
             .eq("producto_codigo", "FL-4")
             .eq("doc_venta", "Sin DTE")
             .gte("fecha", fecha_ini).lt("fecha", fecha_fin)
             .order("fecha").range(0, 999).execute())
    except Exception:
        return pd.DataFrame(columns=cols)
    if not r.data:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(r.data)
    try:
        vd = client.table("dim_vendedor").select("id,nombre_canonico").execute().data
        nom = {v["id"]: v["nombre_canonico"] for v in (vd or [])}
        df["vendedor"] = df["vendedor_id"].map(nom).fillna("Sin asignar")
    except Exception:
        df["vendedor"] = df["vendedor_id"].astype(str)
    return df


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


# ── Presupuesto de venta (sección gerencia) ──────────────────────────────────
# Tablas livianas (sql/017): presupuesto_venta y ventas_historicas guardan SOLO
# el monto mensual total de la empresa (sin detalle de facturación). Fail-soft:
# si las tablas aún no existen (falta correr sql/017), devuelven vacío.

def get_presupuesto(client: Client, anio: int) -> pd.DataFrame:
    """Presupuesto mensual del año → DataFrame(mes, monto)."""
    try:
        r = (client.table("presupuesto_venta").select("mes,monto")
             .eq("anio", anio).order("mes").execute())
        return pd.DataFrame(r.data) if r.data else pd.DataFrame(columns=["mes", "monto"])
    except Exception:
        return pd.DataFrame(columns=["mes", "monto"])


def upsert_presupuesto(client: Client, anio: int, mes: int, monto: float):
    """Guarda (crea o actualiza) el presupuesto de un mes."""
    client.table("presupuesto_venta").upsert(
        {"anio": anio, "mes": mes, "monto": monto},
        on_conflict="anio,mes").execute()


def get_ventas_historicas(client: Client) -> pd.DataFrame:
    """Ventas totales mensuales de años anteriores → DataFrame(anio, mes, monto)."""
    try:
        r = (client.table("ventas_historicas").select("anio,mes,monto")
             .order("anio").order("mes").execute())
        return pd.DataFrame(r.data) if r.data else pd.DataFrame(columns=["anio", "mes", "monto"])
    except Exception:
        return pd.DataFrame(columns=["anio", "mes", "monto"])


def upsert_venta_historica(client: Client, anio: int, mes: int, monto: float):
    """Guarda (crea o actualiza) la venta total de un mes histórico."""
    client.table("ventas_historicas").upsert(
        {"anio": anio, "mes": mes, "monto": monto},
        on_conflict="anio,mes").execute()


def get_real_mensual(client: Client, anio: int) -> pd.DataFrame:
    """
    Venta real (Fact-NC) y proyección de cierre por MES del año, sumando todos
    los vendedores desde la vista v_resumen_vendedor_mes (misma fuente que el
    Panel Gerencia → cuadra exacto con lo que ya se reporta).
    Retorna DataFrame(mes, fact_nc, proyeccion).
    """
    r = (client.table("v_resumen_vendedor_mes")
         .select("mes,fact_nc,proyeccion_cierre")
         .eq("anio", anio).execute())
    if not r.data:
        return pd.DataFrame(columns=["mes", "fact_nc", "proyeccion"])
    df = pd.DataFrame(r.data)
    for c in ["fact_nc", "proyeccion_cierre"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    out = (df.groupby("mes")
             .agg(fact_nc=("fact_nc", "sum"), proyeccion=("proyeccion_cierre", "sum"))
             .reset_index())
    return out


def get_participacion_vendedores(client: Client, anio: int, meses: list) -> pd.DataFrame:
    """
    Participación de cada vendedor en el Fact-NC de los meses dados (para el
    reparto sugerido del objetivo). Excluye 'Sin asignar'.
    Retorna DataFrame(vendedor_id, nombre_canonico, fact_nc, share) orden desc.
    """
    r = (client.table("v_resumen_vendedor_mes")
         .select("vendedor_id,nombre_canonico,mes,fact_nc")
         .eq("anio", anio).in_("mes", meses).execute())
    if not r.data:
        return pd.DataFrame(columns=["vendedor_id", "nombre_canonico", "fact_nc", "share"])
    df = pd.DataFrame(r.data)
    df["fact_nc"] = pd.to_numeric(df["fact_nc"], errors="coerce").fillna(0)
    df = df[df["nombre_canonico"] != "Sin asignar"]
    g = (df.groupby(["vendedor_id", "nombre_canonico"])["fact_nc"].sum()
           .reset_index().sort_values("fact_nc", ascending=False))
    total = g["fact_nc"].sum()
    g["share"] = g["fact_nc"] / total if total else 0
    return g


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


def get_dim_cliente_full(client: Client) -> pd.DataFrame:
    """dim_cliente con datos descriptivos (para enriquecer el ranking de clientes).
    Paginado: dim_cliente ya supera el límite de 1000 filas de PostgREST."""
    _PAGE, offset, rows = 1000, 0, []
    while True:
        r = (client.table("dim_cliente")
             .select("rut,razon_social,comuna,region,tipo")
             .order("rut")
             .range(offset, offset + _PAGE - 1).execute())
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_top_clientes(client: Client, anio: int, mes: int,
                     sociedad_ids=None) -> pd.DataFrame:
    """
    Ranking de clientes por Fact-NC en el mes. RLS aplica: un vendedor solo
    ve sus clientes; gerencia ve todos. Reusa get_ventas_rango (paginado) para
    no quedar corto por el límite de 1000 filas de PostgREST.

    Devuelve, por cliente_rut: fact_nc (neto, NC ya negativas), n_facturas
    (facturas distintas) y los datos descriptivos de dim_cliente. Ordenado
    de mayor a menor Fact-NC.
    """
    import calendar as _cal
    ultimo = _cal.monthrange(anio, mes)[1]
    fini, ffin = f"{anio}-{mes:02d}-01", f"{anio}-{mes:02d}-{ultimo:02d}"
    df = get_ventas_rango(client, fini, ffin, sociedad_ids)
    if df.empty:
        return pd.DataFrame()

    df["neto"] = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    es_factura = df["tipo_dcto"].str.contains("factura", case=False, na=False)

    agg = (df.groupby("cliente_rut", dropna=False)
             .agg(fact_nc=("neto", "sum"))
             .reset_index())
    nfac = (df[es_factura].groupby("cliente_rut")["n_dcto"]
              .nunique().reset_index(name="n_facturas"))
    agg = agg.merge(nfac, on="cliente_rut", how="left")
    agg["n_facturas"] = agg["n_facturas"].fillna(0).astype(int)

    # Enriquecer con datos del cliente
    dfc = get_dim_cliente_full(client)
    if not dfc.empty:
        agg = agg.merge(dfc.rename(columns={"rut": "cliente_rut"}),
                        on="cliente_rut", how="left")
    for col in ["razon_social", "comuna", "region", "tipo"]:
        if col not in agg.columns:
            agg[col] = None
    agg["razon_social"] = agg["razon_social"].fillna(agg["cliente_rut"])

    return agg.sort_values("fact_nc", ascending=False).reset_index(drop=True)


def get_dim_direccion_full(client: Client) -> pd.DataFrame:
    """Catálogo completo de sucursales (dim_direccion), paginado."""
    _PAGE, offset, rows = 1000, 0, []
    while True:
        r = (client.table("dim_direccion")
             .select("id,cliente_rut,nombre,direccion,comuna,ciudad,ruta,"
                     "es_principal,activa,origen")
             .order("id").range(offset, offset + _PAGE - 1).execute())
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    return pd.DataFrame(rows)


def _leer_vista_mes(client: Client, vista: str, sociedad_ids=None) -> pd.DataFrame:
    """
    Lee una vista de agregación mensual (v_cliente_mes / v_sucursal_mes /
    v_sin_sucursal_mes), paginando. Devuelve ~10K filas ya agregadas en Postgres,
    en vez de traerse fact_ventas entera: es lo que permite que el histórico crezca
    sin que la app se degrade (ver sql/029).
    """
    _PAGE, offset, rows = 1000, 0, []
    while True:
        q = client.table(vista).select("*").range(offset, offset + _PAGE - 1)
        if sociedad_ids:
            q = q.in_("sociedad_id", sociedad_ids)
        r = q.execute()
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    df = pd.DataFrame(rows)
    if not df.empty:
        df["fact_nc"] = pd.to_numeric(df["fact_nc"], errors="coerce").fillna(0)
        if "n_facturas" in df.columns:
            df["n_facturas"] = pd.to_numeric(
                df["n_facturas"], errors="coerce").fillna(0).astype(int)
    return df


def get_sucursales_historia(client: Client, sociedad_ids=None) -> pd.DataFrame:
    """
    Matriz sucursal × mes (base de la pestaña Sucursales y de su export).

    Una fila por (dirección, mes) con actividad:
      direccion_id, cliente_rut, ym, fact_nc, n_facturas + atributos de la
      sucursal (nombre, direccion, comuna, ciudad, ruta, es_principal, origen) y
      la razón social del cliente.

    Lee v_sucursal_mes (agregación en Postgres). Si la vista todavía no existe
    (sql/029 sin correr), cae al camino antiguo —traerse fact_ventas entera y
    agregar en pandas— y lo DECLARA en df.attrs["fuente"]="fallback" para que la
    página lo muestre: es correcto pero lento, y con el histórico grande no sirve.

    Lo no atribuible (NC de anulación, ventas sin dirección en el ERP) viaja en
    df.attrs["sin_sucursal"], para declararlo en vez de perderlo.
    """
    fuente = "vista"
    try:
        out = _leer_vista_mes(client, "v_sucursal_mes", sociedad_ids)
        sin_df = _leer_vista_mes(client, "v_sin_sucursal_mes", sociedad_ids)
        if out.empty:
            return pd.DataFrame()
        out = (out.groupby(["direccion_id", "cliente_rut", "ym"], as_index=False)
               .agg(fact_nc=("fact_nc", "sum"), n_facturas=("n_facturas", "sum")))
        sin_df = (sin_df.groupby(["cliente_rut", "ym"], as_index=False)["fact_nc"].sum()
                  if not sin_df.empty
                  else pd.DataFrame(columns=["cliente_rut", "ym", "fact_nc"]))
    except Exception:
        fuente = "fallback"
        out, sin_df = _sucursales_historia_lento(client, sociedad_ids)
        if out.empty:
            return pd.DataFrame()

    out["direccion_id"] = out["direccion_id"].astype("int64")
    dd = get_dim_direccion_full(client)
    if not dd.empty:
        out = out.merge(dd.rename(columns={"id": "direccion_id",
                                           "cliente_rut": "_rut_dir"}),
                        on="direccion_id", how="left")
        out = out.drop(columns=[c for c in ["_rut_dir"] if c in out.columns])

    dfc = get_dim_cliente_full(client)
    if not dfc.empty:
        out = out.merge(dfc[["rut", "razon_social", "region"]]
                        .rename(columns={"rut": "cliente_rut",
                                         "region": "region_cliente"}),
                        on="cliente_rut", how="left")
    out["razon_social"] = out.get("razon_social", pd.Series(dtype=object)).fillna(
        out["cliente_rut"])

    out.attrs["sin_sucursal"] = sin_df
    out.attrs["fuente"] = fuente
    return out


def _sucursales_historia_lento(client: Client, sociedad_ids=None):
    """
    Camino antiguo: trae fact_ventas entera y agrega en pandas. Solo se usa si
    v_sucursal_mes no existe todavía. Correcto pero lento (~8 s con 64K líneas,
    y crece linealmente con el histórico) → correr sql/029.
    """
    _PAGE, offset, rows = 1000, 0, []
    while True:
        q = (client.table("fact_ventas")
             .select("fecha,tipo_dcto,n_dcto,cliente_rut,neto,direccion_id,sociedad_id")
             .order("id").range(offset, offset + _PAGE - 1))
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
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)
    df["neto"] = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["ym"] = df["fecha"].dt.strftime("%Y-%m")
    df["es_fac"] = df["tipo_dcto"].str.contains("factura", case=False, na=False)

    sin = df[df["direccion_id"].isna()]
    con = df[df["direccion_id"].notna()].copy()
    if con.empty:
        return pd.DataFrame(), pd.DataFrame()

    monto = (con.groupby(["direccion_id", "cliente_rut", "ym"], dropna=False)["neto"]
               .sum().reset_index(name="fact_nc"))
    nfac = (con[con["es_fac"]].groupby(["direccion_id", "ym"])["n_dcto"]
              .nunique().reset_index(name="n_facturas"))
    out = monto.merge(nfac, on=["direccion_id", "ym"], how="left")
    out["n_facturas"] = out["n_facturas"].fillna(0).astype(int)

    sin_df = (sin.groupby(["cliente_rut", "ym"])["neto"].sum()
              .reset_index(name="fact_nc") if not sin.empty
              else pd.DataFrame(columns=["cliente_rut", "ym", "fact_nc"]))
    return out, sin_df


def get_clientes_historia(client: Client, sociedad_ids=None) -> pd.DataFrame:
    """
    Matriz cliente × mes para toda la historia disponible (base del CRM de
    Clientes). RLS aplica: vendedor ve solo sus clientes. Pagina para no
    quedar corto con el límite de 1000 filas de PostgREST.

    Devuelve formato largo (una fila por cliente-mes con actividad):
      cliente_rut, ym ('YYYY-MM'), fact_nc (SUM neto), n_facturas (folios
      distintos), razon_social, comuna, region, tipo.

    Lee v_cliente_mes (agregación en Postgres, sql/029). Si la vista no existe,
    cae al camino antiguo y lo declara en df.attrs["fuente"]="fallback".
    """
    fuente = "vista"
    try:
        out = _leer_vista_mes(client, "v_cliente_mes", sociedad_ids)
        if out.empty:
            return pd.DataFrame()
        out = (out.groupby(["cliente_rut", "ym"], as_index=False)
               .agg(fact_nc=("fact_nc", "sum"), n_facturas=("n_facturas", "sum")))
    except Exception:
        fuente = "fallback"
        out = _clientes_historia_lento(client, sociedad_ids)
        if out.empty:
            return pd.DataFrame()

    dfc = get_dim_cliente_full(client)
    if not dfc.empty:
        out = out.merge(dfc.rename(columns={"rut": "cliente_rut"}),
                        on="cliente_rut", how="left")
    for col in ["razon_social", "comuna", "region", "tipo"]:
        if col not in out.columns:
            out[col] = None
    out["razon_social"] = out["razon_social"].fillna(out["cliente_rut"])
    out.attrs["fuente"] = fuente
    return out


def _clientes_historia_lento(client: Client, sociedad_ids=None) -> pd.DataFrame:
    """
    Camino antiguo (solo si v_cliente_mes no existe): trae fact_ventas entera y
    agrega en pandas. Correcto pero lento y no escala con el histórico.
    """
    _PAGE, offset, rows = 1000, 0, []
    while True:
        q = (client.table("fact_ventas")
             .select("fecha,tipo_dcto,n_dcto,cliente_rut,neto")
             .order("id").range(offset, offset + _PAGE - 1))
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
    df["neto"] = pd.to_numeric(df["neto"], errors="coerce").fillna(0)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["ym"] = df["fecha"].dt.strftime("%Y-%m")
    df["es_fac"] = df["tipo_dcto"].str.contains("factura", case=False, na=False)

    monto = (df.groupby(["cliente_rut", "ym"], dropna=False)["neto"]
               .sum().reset_index(name="fact_nc"))
    nfac = (df[df["es_fac"]].groupby(["cliente_rut", "ym"])["n_dcto"]
              .nunique().reset_index(name="n_facturas"))
    out = monto.merge(nfac, on=["cliente_rut", "ym"], how="left")
    out["n_facturas"] = out["n_facturas"].fillna(0).astype(int)
    return out


def get_direcciones_cliente(client: Client, cliente_rut: str,
                            ids: list | None = None) -> pd.DataFrame:
    """
    Sucursales/direcciones de un cliente (dim_direccion). Un RUT puede comprar en
    varias (una casa matriz y sus locales: casinos, sucursales, puntos de venta).

    `ids`: direcciones que aparecen en sus ventas. Se consultan además del RUT
    porque una dirección dada de baja en Autoventa se reconstruye del pedido y
    puede quedar sin cliente_rut (ver etl/direcciones.py).

    Fail-soft: si la tabla aún no existe (sql/027 sin correr), devuelve vacío y la
    ficha simplemente no muestra el desglose por sucursal.
    """
    _COLS = "id,cliente_rut,nombre,direccion,comuna,ciudad,ruta,es_principal"
    r = (client.table("dim_direccion").select(_COLS)
         .eq("cliente_rut", cliente_rut).order("id").range(0, 999).execute())
    df = pd.DataFrame(r.data) if r.data else pd.DataFrame()
    faltan = [i for i in (ids or []) if df.empty or i not in set(df["id"])]
    if faltan:
        r2 = (client.table("dim_direccion").select(_COLS)
              .in_("id", faltan).order("id").range(0, 999).execute())
        if r2.data:
            df = pd.concat([df, pd.DataFrame(r2.data)], ignore_index=True)
    return df


def get_cliente_detalle(client: Client, cliente_rut: str):
    """
    Detalle de un cliente para su ficha: (df_ventas, df_pedidos).
    - df_ventas: líneas de fact_ventas (fecha, neto, n_dcto, tipo_dcto,
      producto_codigo, cantidad, direccion_id, categoria, nombre_producto).
    - df_pedidos: líneas de fact_pedidos (fecha, neto, facturado).
    Pagina ambas tablas. RLS aplica.
    """
    def _paginar(tabla, cols):
        _PAGE, offset, rows = 1000, 0, []
        while True:
            r = (client.table(tabla).select(cols)
                 .eq("cliente_rut", cliente_rut)
                 .order("id").range(offset, offset + _PAGE - 1).execute())
            if not r.data:
                break
            rows.extend(r.data)
            if len(r.data) < _PAGE:
                break
            offset += _PAGE
        return pd.DataFrame(rows)

    _COLS_V = "fecha,neto,n_dcto,tipo_dcto,producto_codigo,cantidad"
    try:
        dfv = _paginar("fact_ventas", _COLS_V + ",direccion_id")
    except Exception:
        # sql/027 aún no corrido: la ficha funciona igual, sin sucursales.
        dfv = _paginar("fact_ventas", _COLS_V)
    if not dfv.empty:
        for c in ["neto", "cantidad"]:
            dfv[c] = pd.to_numeric(dfv[c], errors="coerce").fillna(0)
        dfv["fecha"] = pd.to_datetime(dfv["fecha"], errors="coerce")
        dp = get_dim_producto_all(client)
        if not dp.empty:
            dp = dp.rename(columns={"codigo": "producto_codigo",
                                    "nombre": "nombre_producto"})
            dfv = dfv.merge(dp[["producto_codigo", "nombre_producto", "categoria"]],
                            on="producto_codigo", how="left")
        # Servicios (SER-*) traen Cantidad basura: neutralizar solo la cantidad.
        if "categoria" in dfv.columns:
            mask_serv = dfv["categoria"].astype(str).str.upper().str.contains(
                "SERVICIO", na=False)
            dfv.loc[mask_serv, "cantidad"] = 0

    dfp = _paginar("fact_pedidos", "fecha,neto,facturado")
    if not dfp.empty:
        dfp["neto"] = pd.to_numeric(dfp["neto"], errors="coerce").fillna(0)
        dfp["fecha"] = pd.to_datetime(dfp["fecha"], errors="coerce")

    return dfv, dfp


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


# ── Cartera oficial de clientes por vendedor ────────────────────────────────

def get_cartera_map(client: Client) -> pd.DataFrame:
    """Cartera oficial (tabla cartera_cliente, cargada del reporte Autoventa):
    cliente_rut → vendedor_id (+ ruta, n_sucursales). RLS: vendedor ve solo
    la suya. Fail-soft (tabla aún no creada → DataFrame vacío)."""
    try:
        _PAGE, offset, rows = 1000, 0, []
        while True:
            r = (client.table("cartera_cliente")
                 .select("cliente_rut,vendedor_id,nombre,ruta,n_sucursales")
                 .order("cliente_rut")
                 .range(offset, offset + _PAGE - 1).execute())
            if not r.data:
                break
            rows.extend(r.data)
            if len(r.data) < _PAGE:
                break
            offset += _PAGE
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ── Propuesta de Comisiones v1 (scorecard 5 KPIs) ───────────────────────────

def get_comision_v1_parametros(client: Client) -> dict:
    """Parámetros del modelo v1 (umbrales de pago por KPI) como dict
    clave→valor. Fail-soft: tabla inexistente → {} (la app usa defaults)."""
    try:
        r = client.table("comision_v1_parametro").select("clave,valor").execute()
        return {x["clave"]: float(x["valor"]) for x in (r.data or [])}
    except Exception:
        return {}


def upsert_comision_v1_parametros(client: Client, valores: dict):
    """Actualiza parámetros v1: valores = {clave: valor}."""
    regs = [{"clave": k, "valor": float(v)} for k, v in valores.items()]
    if regs:
        client.table("comision_v1_parametro").upsert(
            regs, on_conflict="clave").execute()


def get_comision_v1_meta(client: Client, anio: int, mes: int) -> pd.DataFrame:
    """Metas editables del modelo v1 por vendedor/mes (tabla comision_v1_meta).
    Columnas NULL caen al default de otras tablas en el cálculo de la página."""
    try:
        # select("*") tolera esquemas sin meta_lineas (sql/023 sin correr aún).
        r = (client.table("comision_v1_meta")
             .select("*")
             .eq("anio", anio).eq("mes", mes)
             .execute())
        return pd.DataFrame(r.data) if r.data else pd.DataFrame()
    except Exception:
        # Tabla aún no creada (sql/022 sin correr): fail-soft.
        return pd.DataFrame()


def upsert_comision_v1_meta(client: Client, vendedor_id: int, anio: int, mes: int,
                            meta_venta=None, meta_nuevos_react=None,
                            meta_cobertura=None, meta_amplitud=None,
                            meta_visitas=None, meta_lineas=None, meta_skus=None):
    """Crea/actualiza las metas v1 de un vendedor. Valores None quedan NULL
    (el cálculo usa el default: objetivos, cartera o meta automática).
    meta_lineas = amplitud de categorías (líneas x cliente); meta_skus =
    profundidad SKU (SKUs x categoría). meta_amplitud/meta_visitas son legacy
    (v1.0: % Galletas NY y efectividad) y quedan sin uso en v1.1."""
    def _i(v):
        return int(v) if v is not None and pd.notna(v) else None
    def _f(v):
        return float(v) if v is not None and pd.notna(v) else None
    client.table("comision_v1_meta").upsert({
        "vendedor_id": int(vendedor_id),
        "anio": int(anio),
        "mes": int(mes),
        "meta_venta": _f(meta_venta),
        "meta_nuevos_react": _i(meta_nuevos_react),
        "meta_cobertura": _i(meta_cobertura),
        "meta_amplitud": _f(meta_amplitud),
        "meta_visitas": _i(meta_visitas),
        "meta_lineas": _f(meta_lineas),
        "meta_skus": _f(meta_skus),
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
    # Dato cosmético del header: nunca debe tumbar la página de Inicio.
    # Si la query falla (JWT expirado, permiso sobre fact_ventas, etc.) se
    # degrada a "—" en vez de propagar el APIError.
    try:
        r = (client.table("fact_ventas")
             .select("fecha")
             .ilike("tipo_dcto", "%factura%")
             .gte("fecha", f"{anio}-{mes:02d}-01")
             .lte("fecha", f"{anio}-{mes:02d}-{ultimo_dia:02d}")
             .order("fecha", desc=True)
             .limit(1)
             .execute())
    except Exception:
        return "—"
    if r.data:
        import datetime
        return datetime.date.fromisoformat(r.data[0]["fecha"][:10]).strftime("%d-%m-%Y")
    return "—"
