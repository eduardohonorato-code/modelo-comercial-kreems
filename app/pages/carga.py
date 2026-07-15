"""
Página "Carga de archivos" (solo rol gerencia/admin).

Permite alimentar la base de datos subiendo los exports del mes desde el
navegador, sin tocar la línea de comandos. Reutiliza el MISMO núcleo de ETL que
`run_historico` (`procesar_carga`), de modo que el resultado es idéntico a cargar
desde las carpetas. Idempotente: volver a subir el mismo mes actualiza, no duplica.

Flujo vigente (2026-06):
  · Gran Natural (ventas+máquinas) y Pedidos → entran por API en el PC. Aquí solo
    se suben como RESPALDO si la API falló.
  · Acuña (Obuma) y Despachos (Autoventa) → NO tienen API: se suben aquí.
  Al subir despachos, el estado entregada/rechazada se sincroniza con TODAS las
  máquinas del mes que ya estén en la base (incluidas las de Gran Natural que
  cargó la API).
"""
import shutil
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st

from app.auth import es_gerencia, MESES

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_MENSUAL = ROOT / "data" / "mensual"

# Carpeta destino y plantilla de nombre estándar (esquema data/mensual/<fuente>/).
DESTINO = {
    "acuna":     ("acuna",        "acuna_{a}-{m:02d}.xls"),
    "gn":        ("gran_natural", "gran_natural_{a}-{m:02d}.xls"),
    "pedidos":   ("pedidos",      "pedidos_{a}-{m:02d}.csv"),
    "despachos": ("despachos",    "despachos_{a}-{m:02d}.xlsx"),
}


def _guardar_en_carpetas(anio: int, mes: int, temp_paths: dict):
    """Best-effort: copia los archivos subidos a data/mensual con nombre estándar.
    Da orden local. En despliegues sin disco persistente simplemente se omite."""
    copiados = []
    for clave, src in temp_paths.items():
        carpeta, plantilla = DESTINO[clave]
        destino_dir = DATA_MENSUAL / carpeta
        try:
            destino_dir.mkdir(parents=True, exist_ok=True)
            destino = destino_dir / plantilla.format(a=anio, m=mes)
            shutil.copyfile(src, destino)
            copiados.append(str(destino.relative_to(ROOT)))
        except Exception:
            pass  # entorno sin disco escribible: la verdad vive en Supabase
    return copiados


def _rango_mes(anio: int, mes: int) -> tuple[str, str]:
    """(primer día del mes, primer día del mes siguiente) en ISO, para filtrar."""
    ini = date(anio, mes, 1)
    fin = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return ini.isoformat(), fin.isoformat()


def _leer_periodo(client, tabla: str, col_fecha: str, ini: str, fin: str, select: str):
    """Lee una tabla fact filtrada por mes, paginando (bypass del límite 1000)."""
    import pandas as pd
    _PAGE, offset, rows = 1000, 0, []
    while True:
        r = (client.table(tabla).select(select)
             .gte(col_fecha, ini).lt(col_fecha, fin)
             .order("id")
             .range(offset, offset + _PAGE - 1).execute())
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < _PAGE:
            break
        offset += _PAGE
    return pd.DataFrame(rows)


def _sincronizar_estado_maquinas(client, anio: int, mes: int) -> dict | None:
    """
    Reconcilia el estado (entregada/rechazada/gestionada) de TODAS las máquinas
    del mes contra los despachos que ya están en la base. Necesario porque las
    máquinas de Gran Natural entran por API: al subir los despachos aquí, así
    toman su estado de entrega. Idempotente.
    """
    from etl.maquinas import aplicar_estado_despachos
    from etl.upsert import upsert_tabla

    ini, fin = _rango_mes(anio, mes)
    desp = _leer_periodo(client, "fact_despachos", "fecha_ruta", ini, fin,
                         "documento,estado,fecha_ruta")
    if desp.empty:
        return None
    maq = _leer_periodo(client, "fact_maquinas", "fecha", ini, fin,
                        "documento,fecha,vendedor_id,cliente_rut,tipo_mov,estado,sociedad_id")

    # Reconciliar es_maquina en los despachos del mes según las máquinas (Obuma):
    # un despacho es de máquina si su documento es una máquina derivada de Obuma.
    machine_docs = (sorted(set(maq["documento"].dropna().astype(str)))
                    if not maq.empty else [])
    n_maquina_desp = 0
    try:
        (client.table("fact_despachos").update({"es_maquina": False})
         .gte("fecha_ruta", ini).lt("fecha_ruta", fin).execute())
        if machine_docs:
            r = (client.table("fact_despachos").update({"es_maquina": True})
                 .gte("fecha_ruta", ini).lt("fecha_ruta", fin)
                 .in_("documento", machine_docs).execute())
            n_maquina_desp = len(r.data or [])
    except Exception:
        pass

    if maq.empty:
        return {"maquinas": 0, "entregadas": 0, "rechazadas": 0,
                "gestionadas": 0, "despachos_maquina": n_maquina_desp}

    actualizado = aplicar_estado_despachos(maq, desp)
    upsert_tabla(client, "fact_maquinas", actualizado,
                 on_conflict="sociedad_id,documento,cliente_rut,tipo_mov")
    return {
        "maquinas": len(actualizado),
        "entregadas": int((actualizado["estado"] == "entregada").sum()),
        "rechazadas": int((actualizado["estado"] == "rechazada").sum()),
        "gestionadas": int((actualizado["estado"] == "gestionada").sum()),
        "despachos_maquina": n_maquina_desp,
    }


def _atribuir_sucursales(client, path, sociedad: str) -> str:
    """
    Escribe fact_ventas.direccion_id con la dirección que el export de Obuma trae en
    cada documento (ver etl/direcciones_obuma.py). Devuelve un resumen para el
    reporte de la carga.
    """
    from etl.config import SOCIEDAD_ID
    from etl.upsert import upsert_tabla
    from etl.direcciones import ruts_dim_cliente
    from etl.direcciones_obuma import (leer_direcciones_excel, construir,
                                       actualizar_fact_ventas, ids_existentes)

    df = leer_direcciones_excel(Path(path), sociedad)
    dim, mapa = construir(df, ids_existentes(client),
                          ruts_validos=ruts_dim_cliente(client))
    if not dim.empty:
        upsert_tabla(client, "dim_direccion", dim, on_conflict="id")
    filas = actualizar_fact_ventas(client, mapa, SOCIEDAD_ID[sociedad])
    return (f"{sociedad}: {filas} líneas con sucursal "
            f"({len(dim)} direcciones nuevas)")


def _ejecutar_carga(anio: int, mes: int, uploads: dict) -> dict:
    """Guarda los archivos subidos en un temp, arma las listas y llama al núcleo."""
    # Import diferido: el ETL usa la service-role key (server-side, nunca al browser)
    from etl.db import get_client
    from etl.cleaners import construir_mapeo_vendedor
    from etl.run_historico import procesar_carga, _asegurar_vendedor_sin_asignar

    tmp = Path(tempfile.mkdtemp(prefix="kreems_carga_"))
    temp_paths: dict = {}

    def _save(clave, uploaded):
        _, plantilla = DESTINO[clave]
        p = tmp / plantilla.format(a=anio, m=mes)
        p.write_bytes(uploaded.getvalue())
        temp_paths[clave] = p
        return p

    obuma_files = []
    if uploads.get("acuna"):
        obuma_files.append((_save("acuna", uploads["acuna"]), "acuna"))
    if uploads.get("gn"):
        obuma_files.append((_save("gn", uploads["gn"]), "grannatural"))

    # Autoventa: pedidos y/o despachos (ya no es obligatorio subir ambos).
    av_pares = []
    if uploads.get("pedidos") or uploads.get("despachos"):
        pp = _save("pedidos", uploads["pedidos"]) if uploads.get("pedidos") else None
        dp = _save("despachos", uploads["despachos"]) if uploads.get("despachos") else None
        av_pares.append((mes, pp, dp))

    client = get_client()
    rows = client.table("dim_vendedor").select("id,nombre_canonico").execute().data
    mapeo = construir_mapeo_vendedor(rows or [])
    fallback = _asegurar_vendedor_sin_asignar(client)

    rep = procesar_carga(client, obuma_files, av_pares, mapeo, fallback)

    # Sucursal del cliente: el export de Obuma trae la dirección de cada documento.
    # Es la única fuente para Acuña y para las NC, y solo existe mientras el archivo
    # subido está en el temp (en Streamlit Cloud no hay disco persistente).
    rep["sucursales"] = []
    for path, sociedad in obuma_files:
        try:
            rep["sucursales"].append(_atribuir_sucursales(client, path, sociedad))
        except Exception as exc:
            rep["sucursales"].append(f"{sociedad}: sin sucursales ({exc})")

    # Si se subieron despachos, sincronizar el estado de las máquinas de TODO el
    # mes (incluye las de Gran Natural cargadas por API).
    if uploads.get("despachos"):
        rep["maquinas_sync"] = _sincronizar_estado_maquinas(client, anio, mes)

    rep["copiados"] = _guardar_en_carpetas(anio, mes, temp_paths)
    return rep


def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede cargar archivos.")
        return

    st.markdown('<div class="seccion-titulo">¿Cómo funciona?</div>',
                unsafe_allow_html=True)
    with st.expander("Flujo de carga y para qué sirve cada archivo", expanded=False):
        st.markdown("""
**Sube los exports del mes y se cargan a la base de datos (Supabase).** Es
**idempotente**: si vuelves a subir un mes ya cargado, se **actualiza** (no se
duplica). Puedes subir solo lo que tengas.

**Lo normal:** subir **Acuña** y **Despachos** (no tienen API). *Gran Natural* y
*Pedidos* entran solos por la API en el PC — solo súbelos aquí como **respaldo**
si la API falló.

| Archivo | ERP | Cómo entra | Para qué sirve |
|---|---|---|---|
| **Obuma Acuña** (`.xls`) | Obuma | **subir aquí** | facturación, margen, %Cumplimiento, máquinas (sociedad Acuña) |
| **Despachos** (`.xlsx`) | Autoventa | **subir aquí** | entrega de máquinas (entregada/rechazada), devoluciones |
| Obuma Gran Natural (`.xls`) | Obuma | API (respaldo aquí) | ventas + máquinas de Gran Natural |
| Pedidos detalle (`.csv ;`) | Autoventa | API (respaldo aquí) | N° de pedidos, *No facturado* (Sin DTE) |

**Cruce:** `Obuma.N° DCTO = Autoventa.Num documento = Despachos.Documento`.
Al subir los despachos, **todas** las máquinas del mes que ya estén en la base
—incluidas las de Gran Natural cargadas por API— toman su estado *entregada* /
*rechazada*. Los vendedores que no estén en el sistema van a *Sin asignar* hasta
registrarlos (se reportan al final).

> **Nombres:** da igual cómo se llame el archivo descargado — lo que importa es
> el recuadro donde lo sueltas y el período que elijas arriba.
        """)

    st.markdown('<div class="seccion-titulo">Período a cargar</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    anio_sel = c1.selectbox("Año", [2026, 2025, 2027],
                            index=[2026, 2025, 2027].index(anio) if anio in (2026, 2025, 2027) else 0)
    mes_sel = c2.selectbox("Mes", list(MESES.keys()),
                           format_func=lambda m: MESES[m],
                           index=list(MESES.keys()).index(mes) if mes in MESES else 0)

    st.markdown('<div class="seccion-titulo">Archivos del mes</div>', unsafe_allow_html=True)
    st.caption("Lo habitual: Acuña + Despachos. (Gran Natural y Pedidos solo si la API falló.)")
    cprin1, cprin2 = st.columns(2)
    up_acuna = cprin1.file_uploader("Obuma · Acuña (.xls)", type=["xls"], key="up_acuna")
    up_des   = cprin2.file_uploader("Autoventa · Despachos (.xlsx)", type=["xlsx"], key="up_des")

    with st.expander("Respaldo: Gran Natural y Pedidos (normalmente por API)", expanded=False):
        cresp1, cresp2 = st.columns(2)
        up_gn  = cresp1.file_uploader("Obuma · Gran Natural (.xls)", type=["xls"], key="up_gn")
        up_ped = cresp2.file_uploader("Autoventa · Pedidos detalle (.csv)", type=["csv"], key="up_ped")

    uploads = {"acuna": up_acuna, "gn": up_gn, "pedidos": up_ped, "despachos": up_des}
    hay_algo = any(uploads.values())

    if not hay_algo:
        st.caption("Sube al menos un archivo para habilitar la carga.")

    if st.button("⬆️  Cargar a la base de datos", type="primary",
                 use_container_width=True, disabled=not hay_algo):
        with st.spinner(f"Cargando {MESES[mes_sel]} {anio_sel} a Supabase…"):
            try:
                rep = _ejecutar_carga(anio_sel, mes_sel, uploads)
                st.session_state["carga_reporte"] = rep
            except Exception as exc:
                st.session_state["carga_reporte"] = {"ok": False, "error": str(exc)}

    rep = st.session_state.get("carga_reporte")
    if rep:
        _mostrar_reporte(rep)

    _seccion_estado_erp()


def _seccion_estado_erp():
    """Carga de la lista de clientes del ERP (flag activo/inactivo) → cliente_estado_erp."""
    st.divider()
    st.markdown('<div class="seccion-titulo">Lista de clientes (ERP: activo/inactivo)</div>',
                unsafe_allow_html=True)
    st.caption("Sube el export **lista_clientes.xlsx** del ERP (Autoventa). Usa las columnas "
               "`cliente_rut` y `cliente_activo` (1/0). Alimenta el flag Activo/Inactivo de la "
               "sección **Clientes**. Idempotente (upsert por RUT).")
    up = st.file_uploader("Lista de clientes (.xlsx)", type=["xlsx"], key="up_lista_erp")
    if st.button("⬆️  Cargar lista de clientes", disabled=up is None,
                 use_container_width=True):
        with st.spinner("Cargando lista de clientes a Supabase…"):
            try:
                import io
                from etl.db import get_client
                from etl.cargar_estado_erp import cargar_estado_erp
                r = cargar_estado_erp(get_client(), io.BytesIO(up.getvalue()))
                st.success(f"Lista cargada: {r['filas']} clientes "
                           f"({r['activos']} activos, {r['inactivos']} inactivos). ✅")
            except Exception as exc:
                st.error(f"No se pudo cargar la lista: {exc}")


def _mostrar_reporte(rep: dict):
    import pandas as pd

    if not rep.get("ok"):
        st.error(f"La carga falló: {rep.get('error', 'error desconocido')}")
        return

    st.success("Carga completada. ✅")

    c = rep["conteos"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Líneas de venta", f"{c['fact_ventas']:,}".replace(",", "."))
    m2.metric("Pedidos", f"{c['fact_pedidos']:,}".replace(",", "."))
    m3.metric("Despachos", f"{c['fact_despachos']:,}".replace(",", "."))
    m4.metric("Máquinas", f"{c['fact_maquinas']:,}".replace(",", "."))

    if rep.get("sucursales"):
        st.caption("Sucursales (dirección del cliente en cada documento): " +
                   " · ".join(rep["sucursales"]))

    sync = rep.get("maquinas_sync")
    if sync:
        st.caption(
            f"Estado de máquinas del mes sincronizado con despachos: "
            f"**{sync['entregadas']} entregadas**, {sync['rechazadas']} rechazadas, "
            f"{sync['gestionadas']} gestionadas (sobre {sync['maquinas']} máquinas, "
            f"incluye Gran Natural cargado por API). "
            f"{sync.get('despachos_maquina', 0)} despachos marcados como máquina.")

    if rep.get("por_sociedad_mes"):
        st.markdown('<div class="seccion-titulo">Detalle por sociedad y mes</div>',
                    unsafe_allow_html=True)
        df = pd.DataFrame(rep["por_sociedad_mes"]).rename(columns={
            "sociedad": "Sociedad", "mes": "Mes", "n_docs": "N° docs",
            "n_lineas": "Líneas", "despachos": "Despachos", "match": "Cruzados",
            "pct_match": "% match",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("**% match** = despachos que encuentran su factura en Obuma. "
                   "Es un chequeo de integridad del cruce (mide la confiabilidad de "
                   "máquinas *entregadas*); no altera los montos. Acuña aparece sin "
                   "match porque los despachos son solo de Gran Natural.")

    no_map = rep.get("no_mapeados") or []
    if no_map:
        st.warning(
            f"**{len(no_map)} vendedor(es) no mapeado(s)** — sus ventas se cargaron en "
            f"*Sin asignar*. Agrégalos en `dim_vendedor` y vuelve a cargar este mes "
            f"para reasignarlos:\n\n- " + "\n- ".join(no_map))

    if rep.get("copiados"):
        st.caption("Archivos guardados en orden: " + ", ".join(rep["copiados"]))
