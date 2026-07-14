"""
Sucursales / direcciones de cliente (Autoventa) → dim_direccion + direccion_id.

Un RUT puede comprar en varias direcciones (Sodexo: casa matriz + "Hospital
Naval" + "Clínica Alemana de Temuco"...). Este módulo trae esas direcciones y
atribuye cada factura y cada pedido a la suya.

Cadena de atribución (verificada 2026-07-14 sobre junio 2026):
    /clients   → direcciones del cliente (id estable, nombre, ruta, GPS).
    /requests  → dispatch_address_id del pedido (100% poblado).
    /invoices  → sus líneas traen request_id → dirección del pedido.
                 correlative de la factura = n_dcto de Obuma (fact_ventas).
    En junio: 518/528 facturas resueltas (100% del neto) y ninguna factura mezcla
    dos direcciones → la atribución documento→sucursal es 1 a 1.

Cobertura: solo Gran Natural (Acuña no pasa por Autoventa) y solo facturas (las
NC se emiten en Obuma sin pedido) → el resto queda con direccion_id NULL.
"""
import logging
import re
from collections import Counter

import pandas as pd

from etl.cleaners import normalizar_rut
from etl.config import SOCIEDAD_ID
from etl.loaders.autoventa_api import _get, _dias

logger = logging.getLogger(__name__)

SOCIEDAD_AUTOVENTA = SOCIEDAD_ID["grannatural"]

_EXPANDS_CLIENTES = "&".join(
    f"expand[]={g}" for g in
    ["client_detail", "r_client_addresses", "address_detail"]
)
_EXPANDS_REQ = "&".join(
    f"expand[]={g}" for g in ["request_detail", "r_request_client", "client_detail"]
)
_EXPANDS_INV = "&".join(
    f"expand[]={g}" for g in
    ["invoice_detail", "r_invoice_client", "client_detail",
     "r_invoice_lines", "line_detail"]
)


def ruts_dim_cliente(client) -> set[str]:
    """RUT ya presentes en dim_cliente (paginado: límite 1000 de PostgREST)."""
    ruts, off = set(), 0
    while True:
        r = (client.table("dim_cliente").select("rut")
             .order("rut").range(off, off + 999).execute())
        ruts |= {x["rut"] for x in (r.data or [])}
        if not r.data or len(r.data) < 1000:
            break
        off += 1000
    return ruts


def cargar_dim_direccion(ruts_validos: set[str] | None = None) -> pd.DataFrame:
    """
    Todas las direcciones de todos los clientes (1 llamada a /clients).

    `ruts_validos`: RUT existentes en dim_cliente. Las direcciones de un RUT que
    todavía no está en dim_cliente se descartan (violarían la FK) y se registran
    en el log — no se descartan en silencio (regla 10 de CLAUDE.md).
    """
    clientes = _get(f"/clients?{_EXPANDS_CLIENTES}") or []
    filas, sin_cliente = [], Counter()
    for c in clientes:
        rut = normalizar_rut(pd.Series([c.get("rut")])).iloc[0]
        if not rut:
            continue
        for a in (c.get("addresses") or []):
            if not a.get("id"):
                continue
            if ruts_validos is not None and rut not in ruts_validos:
                sin_cliente[rut] += 1
                continue
            filas.append({
                "id": int(a["id"]),
                "cliente_rut": rut,
                "nombre": (a.get("name") or "").strip() or None,
                "direccion": (a.get("address") or "").strip() or None,
                "comuna": a.get("locality"),
                "ciudad": a.get("city"),
                "ruta": a.get("route"),
                "latitud": a.get("latitude"),
                "longitud": a.get("longitude"),
                "es_principal": bool(a.get("main")),
                "activa": bool(a.get("state", True)),
            })
    if sin_cliente:
        logger.warning("  [dir] %d RUT con direcciones pero sin fila en dim_cliente "
                       "(se omiten): %s", len(sin_cliente),
                       list(sin_cliente)[:10])
    df = pd.DataFrame(filas).drop_duplicates(subset=["id"])
    logger.info("  [dir] direcciones: %d de %d clientes", len(df), len(clientes))
    return df


def _dias_ext(anio: int, mes: int, posterior: int = 5) -> list[str]:
    """Días del mes con margen previo (el de _dias) y además posterior: una factura
    de fin de mes puede tener su pedido despachado ya entrado el mes siguiente. Sin
    ese margen se pierden ~20 facturas al mes (medido en junio 2026)."""
    from datetime import date, timedelta
    dias = _dias(anio, mes)
    ultimo = date.fromisoformat(dias[-1])
    return dias + [(ultimo + timedelta(days=i)).isoformat()
                   for i in range(1, posterior + 1)]


def mapas_periodo(periodo: tuple) -> tuple[dict, dict, dict, dict]:
    """
    Recorre la API del mes y devuelve:
      · doc_dir    : n_dcto (str, = invoice.correlative) → direccion_id
      · pedido_dir : n_pedido (str, = request.correlative) → direccion_id
      · stats      : cobertura de la atribución
      · vistas     : direccion_id → datos crudos de la dirección tal como viene en
                     el pedido. Necesario porque /clients NO devuelve todas las
                     direcciones que aparecen despachadas (las de clientes dados de
                     baja, p.ej.), y sin ellas el FK de fact_ventas revienta.
    """
    anio, mes = periodo
    mes_prefijo = f"{anio}-{mes:02d}"

    # 1) Pedidos del mes → dirección de despacho.
    req_dir, pedido_dir, vistas = {}, {}, {}
    for dia in _dias_ext(anio, mes):
        for r in (_get(f"/requests?dispatch_date={dia}&{_EXPANDS_REQ}") or []):
            dir_id = r.get("dispatch_address_id")
            if not dir_id:
                continue
            dir_id = int(dir_id)
            req_dir[r["id"]] = dir_id
            if r.get("correlative"):
                pedido_dir[str(r["correlative"]).strip()] = dir_id
            vistas.setdefault(dir_id, {
                "texto": r.get("dispatch_address_address"),
                "rut": (r.get("client") or {}).get("rut"),
            })

    # 2) Facturas del mes → dirección vía el request de sus líneas.
    doc_dir, n_facturas, sin_dir, mezcladas = {}, 0, 0, 0
    for dia in _dias_ext(anio, mes):
        for i in (_get(f"/invoices?created_at={dia}&{_EXPANDS_INV}") or []):
            if str(i.get("invoice_date") or "")[:7] != mes_prefijo or i.get("voided"):
                continue
            n_facturas += 1
            dirs = {req_dir[l["request_id"]]
                    for l in (i.get("lines") or [])
                    if l.get("request_id") in req_dir}
            if not dirs:
                sin_dir += 1
                continue
            if len(dirs) > 1:
                # No observado en la validación; si algún día ocurre, no adivinamos.
                mezcladas += 1
                continue
            doc_dir[str(i["correlative"]).strip()] = dirs.pop()

    stats = {
        "dir_pedidos": len(pedido_dir),
        "dir_facturas": len(doc_dir),
        "facturas_periodo": n_facturas,
        "facturas_sin_direccion": sin_dir,
        "facturas_multi_direccion": mezcladas,
    }
    logger.info("  [dir] %d-%02d · pedidos con dirección=%d | facturas %d/%d "
                "(sin dir=%d, multi-dir=%d)", anio, mes, len(pedido_dir),
                len(doc_dir), n_facturas, sin_dir, mezcladas)
    return doc_dir, pedido_dir, stats, vistas


# "AV. INGLESA 98, LOCAL 4.(Concepción. Biobío. Ruta: CN25.)" → partes
_RE_DIR = re.compile(r"^(?P<calle>.*?)\.?\s*\((?P<resto>.*)\)\s*$", re.S)


def direcciones_faltantes(vistas: dict, conocidas: set[int],
                          ruts_validos: set[str]) -> pd.DataFrame:
    """
    Direcciones que aparecen despachadas pero que /clients no devuelve. Se
    reconstruyen del texto del pedido para no perder la venta ni romper el FK.
    El RUT solo se conserva si ya existe en dim_cliente (si no, queda NULL).
    """
    filas = []
    for dir_id, d in vistas.items():
        if dir_id in conocidas:
            continue
        texto = (d.get("texto") or "").strip()
        calle, comuna, ciudad, ruta = texto or None, None, None, None
        m = _RE_DIR.match(texto)
        if m:
            calle = m.group("calle").strip() or None
            partes = [p.strip(" .") for p in m.group("resto").split(".") if p.strip(" .")]
            for p in partes:
                if p.lower().startswith("ruta:"):
                    ruta = p.split(":", 1)[1].strip() or None
                elif comuna is None:
                    comuna = p
                elif ciudad is None:
                    ciudad = p
        rut = normalizar_rut(pd.Series([d.get("rut")])).iloc[0]
        filas.append({
            "id": dir_id,
            "cliente_rut": rut if rut in ruts_validos else None,
            "nombre": None, "direccion": calle, "comuna": comuna, "ciudad": ciudad,
            "ruta": ruta, "latitud": None, "longitud": None,
            "es_principal": False, "activa": False,   # no está en el catálogo vigente
        })
    if filas:
        logger.warning("  [dir] %d direcciones despachadas que /clients no devuelve "
                       "(se reconstruyen del pedido): %s", len(filas),
                       [f["id"] for f in filas][:10])
    return pd.DataFrame(filas)


def _leer_fact(client, tabla: str, cols: str, periodo: tuple) -> pd.DataFrame:
    """Lee las filas Gran Natural del período (paginado: ver memoria del límite 1000)."""
    import calendar
    anio, mes = periodo
    desde = f"{anio}-{mes:02d}-01"
    hasta = f"{anio}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}"
    rows, off = [], 0
    while True:
        r = (client.table(tabla).select(cols)
             .eq("sociedad_id", SOCIEDAD_AUTOVENTA)
             .gte("fecha", desde).lte("fecha", hasta)
             .order("id").range(off, off + 999).execute())
        rows += r.data or []
        if not r.data or len(r.data) < 1000:
            break
        off += 1000
    return pd.DataFrame(rows)


def _rango_mes(periodo: tuple) -> tuple[str, str]:
    import calendar
    anio, mes = periodo
    return (f"{anio}-{mes:02d}-01",
            f"{anio}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}")


def _patch_direcciones(client, tabla: str, campo: str, mapa: dict,
                       periodo: tuple, solo_facturas: bool = False) -> int:
    """
    UPDATE fact_*.direccion_id agrupando los documentos que van a la misma
    dirección (un PATCH por dirección, no uno por fila).

    No se puede usar upsert: `id` es GENERATED ALWAYS (PostgREST no lo acepta en
    el INSERT) y un upsert por la llave natural insertaría filas duplicadas cuando
    producto_codigo es NULL (en Postgres los NULL no colisionan en un UNIQUE).

    `solo_facturas`: las NC llevan su propia serie de folios, así que un n_dcto de
    NC puede repetir el folio de una factura. El mapa viene de las facturas de
    Autoventa → restringir el UPDATE a FACTURA* o le pondríamos a una NC la
    dirección de otra venta.
    """
    if not mapa:
        return 0
    por_dir: dict[int, list[str]] = {}
    for clave, dir_id in mapa.items():
        por_dir.setdefault(int(dir_id), []).append(str(clave))

    desde, hasta = _rango_mes(periodo)
    filas = 0
    for dir_id, claves in por_dir.items():
        for i in range(0, len(claves), 100):
            q = (client.table(tabla).update({"direccion_id": dir_id})
                 .eq("sociedad_id", SOCIEDAD_AUTOVENTA)
                 .gte("fecha", desde).lte("fecha", hasta)
                 .in_(campo, claves[i:i + 100]))
            if solo_facturas:
                q = q.ilike("tipo_dcto", "FACTURA%")
            filas += len(q.execute().data or [])
    return filas


def actualizar_direcciones(client, periodo: tuple, doc_dir: dict,
                           pedido_dir: dict, dry_run: bool = False) -> dict:
    """
    Escribe direccion_id en fact_ventas (por n_dcto, solo facturas) y fact_pedidos
    (por n_pedido) del período. Idempotente: re-correrlo reescribe el mismo valor.
    """
    ventas = _leer_fact(client, "fact_ventas",
                        "id,tipo_dcto,n_dcto,direccion_id", periodo)
    pedidos = _leer_fact(client, "fact_pedidos", "id,n_pedido,direccion_id", periodo)

    res = {}
    if not ventas.empty:
        es_fac = ventas["tipo_dcto"].str.upper().str.startswith("FACTURA", na=False)
        cubre = ventas.loc[es_fac, "n_dcto"].astype(str).str.strip().isin(doc_dir)
        res["ventas_cobertura"] = f"{int(cubre.sum())}/{int(es_fac.sum())} líneas de factura"
    if not pedidos.empty:
        cubre = pedidos["n_pedido"].astype(str).str.strip().isin(pedido_dir)
        res["pedidos_cobertura"] = f"{int(cubre.sum())}/{len(pedidos)} líneas"

    if dry_run:
        return res

    res["ventas_actualizadas"] = _patch_direcciones(
        client, "fact_ventas", "n_dcto", doc_dir, periodo, solo_facturas=True)
    res["pedidos_actualizados"] = _patch_direcciones(
        client, "fact_pedidos", "n_pedido", pedido_dir, periodo)
    return res
