"""
Página "Carga de archivos" (solo rol gerencia/admin).

Permite alimentar la base de datos subiendo los exports del mes desde el
navegador, sin tocar la línea de comandos. Reutiliza el MISMO núcleo de ETL que
`run_historico` (`procesar_carga`), de modo que el resultado es idéntico a cargar
desde las carpetas. Idempotente: volver a subir el mismo mes actualiza, no duplica.

Flujo de datos (igual que el ETL):
  Obuma (Acuña + Gran Natural)  → ventas, márgenes, clientes, productos, MÁQUINAS
  Autoventa pedidos (detalle)   → N° de pedidos, $ pedido, "No facturado" (Sin DTE)
  Autoventa despachos           → entrega de máquinas, logística, devoluciones
  Cruce: Obuma.N°DCTO = Autoventa.Num documento = Despachos.Documento
"""
import shutil
import tempfile
from pathlib import Path

import streamlit as st

from app.auth import es_gerencia, MESES

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_MENSUAL = ROOT / "data" / "mensual"

# Carpeta destino y plantilla de nombre estándar (numérico AAAA_MM, escalable).
DESTINO = {
    "acuna":     ("acuña",        "obuma_ventas_acuña_{a}_{m:02d}.xls"),
    "gn":        ("gran_natural", "obuma_ventas_grannatural_{a}_{m:02d}.xls"),
    "pedidos":   ("autoventa",    "pedidos_detalle_productos_{a}_{m:02d}.csv"),
    "despachos": ("autoventa",    "detalle_despachos_{a}_{m:02d}.xlsx"),
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

    av_pares = []
    if uploads.get("pedidos") and uploads.get("despachos"):
        pp = _save("pedidos", uploads["pedidos"])
        dp = _save("despachos", uploads["despachos"])
        av_pares.append((mes, pp, dp))

    client = get_client()
    rows = client.table("dim_vendedor").select("id,nombre_canonico").execute().data
    mapeo = construir_mapeo_vendedor(rows or [])
    fallback = _asegurar_vendedor_sin_asignar(client)

    rep = procesar_carga(client, obuma_files, av_pares, mapeo, fallback)
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
duplica). Puedes subir solo lo que tengas; lo mínimo útil es al menos un Obuma.

| Archivo | ERP | Alimenta | Para qué sirve |
|---|---|---|---|
| **Obuma Acuña** (`.xls`) | Obuma | `fact_ventas` (Acuña) + **máquinas** | facturación, margen, %Cumplimiento, máquinas (FL-1/2/3/4/5) |
| **Obuma Gran Natural** (`.xls`) | Obuma | `fact_ventas` (Gran Natural) + **máquinas** | ídem, sociedad Gran Natural |
| **Pedidos detalle** (`.csv ;`) | Autoventa | `fact_pedidos` | N° de pedidos, $ pedido vs facturado, *No facturado* (Sin DTE) |
| **Despachos detalle** (`.xlsx`) | Autoventa | `fact_despachos` | entrega de máquinas, efectividad logística, devoluciones |

**Cruce:** `Obuma.N° DCTO = Autoventa.Num documento = Despachos.Documento`.
La máquina se marca *entregada* cuando su documento aparece como **Entregada**
en despachos. Los vendedores nuevos que no estén en el sistema se reportan al
final (sus ventas no se pierden: van a *Sin asignar* hasta registrarlos).

> **Nombres:** no te preocupes por el nombre del archivo — al subirlo se guarda
> con el formato estándar `…_{AAAA}_{MM}` según el período que elijas aquí.
        """)

    st.markdown('<div class="seccion-titulo">Período a cargar</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    anio_sel = c1.selectbox("Año", [2026, 2025, 2027],
                            index=[2026, 2025, 2027].index(anio) if anio in (2026, 2025, 2027) else 0)
    mes_sel = c2.selectbox("Mes", list(MESES.keys()),
                           format_func=lambda m: MESES[m],
                           index=list(MESES.keys()).index(mes) if mes in MESES else 0)

    st.markdown('<div class="seccion-titulo">Archivos</div>', unsafe_allow_html=True)
    cobu1, cobu2 = st.columns(2)
    up_acuna = cobu1.file_uploader("Obuma · Acuña (.xls)", type=["xls"], key="up_acuna")
    up_gn    = cobu2.file_uploader("Obuma · Gran Natural (.xls)", type=["xls"], key="up_gn")
    cav1, cav2 = st.columns(2)
    up_ped = cav1.file_uploader("Autoventa · Pedidos detalle (.csv)", type=["csv"], key="up_ped")
    up_des = cav2.file_uploader("Autoventa · Despachos (.xlsx)", type=["xlsx"], key="up_des")

    uploads = {"acuna": up_acuna, "gn": up_gn, "pedidos": up_ped, "despachos": up_des}
    hay_obuma = bool(up_acuna or up_gn)
    solo_uno_av = bool(up_ped) ^ bool(up_des)

    if solo_uno_av:
        st.info("Autoventa necesita **pedidos y despachos juntos**; sube ambos o ninguno.")

    deshabilitado = not hay_obuma
    if deshabilitado:
        st.caption("Sube al menos un archivo de Obuma (Acuña o Gran Natural) para habilitar la carga.")

    if st.button("⬆️  Cargar a la base de datos", type="primary",
                 use_container_width=True, disabled=deshabilitado):
        with st.spinner(f"Cargando {MESES[mes_sel]} {anio_sel} a Supabase…"):
            try:
                rep = _ejecutar_carga(anio_sel, mes_sel, uploads)
                st.session_state["carga_reporte"] = rep
            except Exception as exc:
                st.session_state["carga_reporte"] = {"ok": False, "error": str(exc)}

    rep = st.session_state.get("carga_reporte")
    if rep:
        _mostrar_reporte(rep)


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
