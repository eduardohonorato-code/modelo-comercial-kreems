"""
Página "Estado de Máquinas" (solo rol gerencia/admin).

Cruza las compras del cliente (fact_ventas) con los movimientos de máquina
(fact_maquinas: FL-4 instalación, cambio, FL-2 retiro) en los últimos 12 meses
cerrados para responder dos preguntas comerciales:

  1. ¿Qué clientes ya NO tienen nuestra máquina?  → hubo retiro FL-2.
  2. ¿Qué clientes RETIENEN la máquina (comodato) pero dejaron de comprarnos?
     → sin retiro FL-2 + sin compras en el último trimestre. Riesgo: la máquina
     (inversión de la empresa) puede estar ociosa o usándose para otros productos.

Criterio maestro de retiro = línea FL-2 facturada en Obuma (excluye 'rechazada').
El estado 'entregada' del despacho confirma el retiro FÍSICO pero solo existe
desde feb-2026, así que se usa como columna de CONFIANZA, no como filtro.
Todo con datos de la base; RLS aplica (gerencia ve todo).
"""
from datetime import date

import io
import pandas as pd
import streamlit as st

from app.auth import es_gerencia
from app.styles import fmt_clp, fmt_num
from app.data import get_clientes_historia, get_maquinas_rango


# ─── Ventana de 12 meses cerrados (igual que Cartera Directa) ────────────────────
def _ventana_12m(hoy: date):
    anio, mes = hoy.year, hoy.month
    yms = []
    for i in range(12, 0, -1):
        m = mes - i
        a = anio + (m - 1) // 12
        m = (m - 1) % 12 + 1
        yms.append(f"{a}-{m:02d}")
    ini = date(int(yms[0][:4]), int(yms[0][5:7]), 1)
    t4 = date(int(yms[9][:4]), int(yms[9][5:7]), 1)   # inicio del último trimestre
    fin1 = date(anio, mes, 1)                           # 1er día del mes en curso (exclusivo)
    return yms, ini, t4, fin1


# ─── Carga cacheada por usuario ─────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _datos_cached(scope: str):
    from app.auth import get_client_auth
    cli = get_client_auth()
    hoy = date.today()
    yms, ini, t4, fin1 = _ventana_12m(hoy)
    hist = get_clientes_historia(cli, None)
    maq = get_maquinas_rango(cli, ini, fin1)
    return hist, maq, yms, ini.isoformat(), t4.isoformat(), fin1.isoformat()


def _construir(hist: pd.DataFrame, maq: pd.DataFrame, yms, t4_iso: str) -> pd.DataFrame:
    hist = hist[hist["ym"].isin(yms)].copy()
    # compras por trimestre
    tri_de = {ym: f"t{i // 3 + 1}" for i, ym in enumerate(yms)}
    hist["tri"] = hist["ym"].map(tri_de)
    piv = (hist.pivot_table(index="cliente_rut", columns="tri", values="fact_nc",
                            aggfunc="sum", fill_value=0.0)
               .reindex(columns=["t1", "t2", "t3", "t4"], fill_value=0.0))
    piv["compra_12m"] = piv.sum(axis=1)
    meta = (hist.sort_values("ym").groupby("cliente_rut")[["razon_social", "comuna"]].last())
    ult_ym = hist.groupby("cliente_rut")["ym"].max().rename("ultima_compra")

    base = piv.join(meta).join(ult_ym)

    # movimientos de máquina por cliente
    if maq is None or maq.empty:
        maq = pd.DataFrame(columns=["cliente_rut", "tipo_mov", "estado", "fecha"])
    m = maq.copy()
    m["fecha"] = pd.to_datetime(m["fecha"], errors="coerce")

    def agg_cli(rut):
        mm = m[m["cliente_rut"] == rut]
        ret = mm[(mm["tipo_mov"] == "retiro") & (mm["estado"].isin(["entregada", "gestionada"]))]
        return pd.Series({
            "nuevas": int(((mm["tipo_mov"] == "nueva") & (mm["estado"] != "rechazada")).sum()),
            "cambios": int((mm["tipo_mov"] == "cambio").sum()),
            "retiros": int(len(ret)),
            "retiros_conf": int(((mm["tipo_mov"] == "retiro") & (mm["estado"] == "entregada")).sum()),
            "retiros_rech": int(((mm["tipo_mov"] == "retiro") & (mm["estado"] == "rechazada")).sum()),
            "ult_retiro": ret["fecha"].max() if len(ret) else pd.NaT,
        })

    universo = sorted(set(base.index) | set(m["cliente_rut"].dropna()))
    df = base.reindex(universo)
    mv = pd.DataFrame([agg_cli(r) for r in universo], index=universo)
    df = df.join(mv)
    for c in ["t1", "t2", "t3", "t4", "compra_12m", "nuevas", "cambios",
              "retiros", "retiros_conf", "retiros_rech"]:
        df[c] = df[c].fillna(0)
    df["razon_social"] = df["razon_social"].fillna(pd.Series(df.index, index=df.index))
    df["comuna"] = df["comuna"].fillna("(sin comuna)").replace("", "(sin comuna)")

    # compra estrictamente posterior al último retiro
    def compra_post(rut):
        r = df.loc[rut, "ult_retiro"]
        if pd.isna(r):
            return df.loc[rut, "compra_12m"]
        sub = hist[(hist["cliente_rut"] == rut) & (hist["ym"] > pd.Timestamp(r).strftime("%Y-%m"))]
        return sub["fact_nc"].sum()
    df["compra_post_retiro"] = [compra_post(r) for r in df.index]
    df["activo_t4"] = df["t4"] > 0

    def estado(r):
        tiene_ret = r["retiros"] > 0
        if r["activo_t4"]:
            return "Activo (sigue comprando)"
        if tiene_ret and r["compra_post_retiro"] <= 0:
            if r["retiros_conf"] >= 1:
                return "Máquina recuperada (retiro confirmado)"
            return "Máquina retirada (FL-2 sin confirmar) — cliente ido"
        if tiene_ret and r["compra_post_retiro"] > 0:
            return "Retiro parcial / repuso — dejó de comprar"
        if r["compra_12m"] > 0:
            extra = " (retiro RECHAZADO)" if r["retiros_rech"] > 0 else ""
            return "⚠ RIESGO: retiene máquina y dejó de comprar" + extra
        return "Otro (mov. máquina sin compras 12M)"
    df["estado_maquina"] = df.apply(estado, axis=1)

    def conf(r):
        if r["retiros"] == 0 and r["retiros_rech"] == 0:
            return "Sin retiro"
        if r["retiros_conf"] >= 1:
            return "Confirmado despacho (Entregada)"
        if pd.notna(r["ult_retiro"]) and pd.Timestamp(r["ult_retiro"]) < pd.Timestamp("2026-02-01"):
            return "Sin despacho disponible (pre feb-2026)"
        if r["retiros_rech"] >= 1:
            return "Retiro rechazado (máquina puede seguir)"
        return "Con despacho, retiro no marcado Entregada"
    df["confianza"] = df.apply(conf, axis=1)
    df["ult_retiro"] = pd.to_datetime(df["ult_retiro"]).dt.date
    return df.reset_index(drop=True).sort_values("compra_12m", ascending=False)


# ════════════════════════════════════════════════════════════════════════════════
def render(client, anio: int, mes: int):
    if not es_gerencia():
        st.warning("Solo el rol **gerencia/admin** puede ver el estado de máquinas.")
        return

    st.caption("Cruce de compras × movimientos de máquina (FL-4 / cambio / FL-2 retiro) en los "
               "últimos 12 meses cerrados. Responde: quién ya no tiene máquina y quién la **retiene** "
               "pero dejó de comprarnos.")

    scope = f"{st.session_state.get('user_id', '')}:{st.session_state.get('vendedor_id', '')}"
    with st.spinner("Cargando estado de máquinas…"):
        hist, maq, yms, ini, t4, fin1 = _datos_cached(scope)
    if hist is None or hist.empty:
        st.info("No hay ventas en la base.")
        return
    df = _construir(hist, maq, yms, t4)

    # ── KPIs por estado ──
    riesgo = df[df["estado_maquina"].str.startswith("⚠ RIESGO")]
    retirada = df[df["estado_maquina"].str.startswith("Máquina")]
    activo = df[df["estado_maquina"].str.startswith("Activo")]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Clientes analizados", fmt_num(len(df)))
    k2.metric("✅ Activos", fmt_num(len(activo)))
    k3.metric("⚠️ Retienen máquina y no compran", fmt_num(len(riesgo)),
              help="Sin retiro FL-2 y sin compras en el último trimestre → máquina posiblemente ociosa o en uso para otros productos.")
    k4.metric("Máquina retirada / recuperada", fmt_num(len(retirada)))

    st.markdown(f"**Venta 12M en riesgo (grupo que retiene máquina y dejó de comprar): "
                f"{fmt_clp(riesgo['compra_12m'].sum())}**")

    # ── Filtros ──
    c1, c2 = st.columns([2, 2])
    estados = sorted(df["estado_maquina"].unique())
    f_estado = c1.multiselect("Estado de máquina", estados, placeholder="Todos los estados")
    comunas = sorted(df["comuna"].unique())
    f_com = c2.multiselect("Comuna", comunas, placeholder="Todas las comunas")

    vista = df
    if f_estado:
        vista = vista[vista["estado_maquina"].isin(f_estado)]
    if f_com:
        vista = vista[vista["comuna"].isin(f_com)]

    et = ["Jul-Sep 25", "Oct-Dic 25", "Ene-Mar 26", "Abr-Jun 26"]
    cols = ["razon_social", "cliente_rut" if "cliente_rut" in vista.columns else None]
    tabla = vista[["razon_social", "comuna", "t1", "t2", "t3", "t4", "compra_12m",
                   "ultima_compra", "nuevas", "cambios", "retiros", "retiros_conf",
                   "retiros_rech", "ult_retiro", "compra_post_retiro",
                   "estado_maquina", "confianza"]].copy()

    st.markdown('<div class="seccion-titulo">Estado de máquina por cliente</div>',
                unsafe_allow_html=True)
    st.dataframe(
        tabla, use_container_width=True, hide_index=True, height=520,
        column_config={
            "razon_social": st.column_config.TextColumn("Cliente", width="medium"),
            "comuna": st.column_config.TextColumn("Comuna", width="small"),
            "t1": st.column_config.NumberColumn(et[0], format="$%d"),
            "t2": st.column_config.NumberColumn(et[1], format="$%d"),
            "t3": st.column_config.NumberColumn(et[2], format="$%d"),
            "t4": st.column_config.NumberColumn(et[3], format="$%d"),
            "compra_12m": st.column_config.NumberColumn("Compra 12M", format="$%d"),
            "ultima_compra": st.column_config.TextColumn("Últ. compra", width="small"),
            "nuevas": st.column_config.NumberColumn("Instal FL-4", width="small"),
            "cambios": st.column_config.NumberColumn("Cambios", width="small"),
            "retiros": st.column_config.NumberColumn("Retiros FL-2", width="small"),
            "retiros_conf": st.column_config.NumberColumn("Ret. conf.", width="small",
                help="Retiros con despacho 'Entregada' (confirma retiro físico; solo desde feb-2026)."),
            "retiros_rech": st.column_config.NumberColumn("Ret. rechaz.", width="small",
                help="Retiro rechazado: el despacho falló → la máquina probablemente sigue con el cliente."),
            "ult_retiro": st.column_config.TextColumn("Últ. retiro", width="small"),
            "compra_post_retiro": st.column_config.NumberColumn("Compra post-retiro", format="$%d",
                help="Ventas después del último retiro. Si es >0, el cliente siguió comprando (no es baja)."),
            "estado_maquina": st.column_config.TextColumn("Estado de máquina", width="large"),
            "confianza": st.column_config.TextColumn("Confianza (despacho)", width="medium"),
        })
    st.caption(f"{len(tabla)} clientes en la vista.")

    # ── Descarga ──
    buf = io.BytesIO()
    tabla.rename(columns={
        "razon_social": "Cliente", "comuna": "Comuna", "t1": et[0], "t2": et[1],
        "t3": et[2], "t4": et[3], "compra_12m": "Compra 12M",
        "ultima_compra": "Ultima compra", "nuevas": "Instal FL-4", "cambios": "Cambios",
        "retiros": "Retiros FL-2", "retiros_conf": "Retiros conf",
        "retiros_rech": "Retiros rechaz", "ult_retiro": "Ult retiro",
        "compra_post_retiro": "Compra post-retiro", "estado_maquina": "Estado maquina",
        "confianza": "Confianza despacho",
    }).to_excel(buf, index=False, sheet_name="Estado maquinas")
    st.download_button("⬇️ Descargar Excel (vista actual)", buf.getvalue(),
                       file_name="estado_maquinas_clientes_12m.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ── Insights ──
    st.markdown('<div class="seccion-titulo">Lectura</div>', unsafe_allow_html=True)
    st.markdown(
        f"- **⚠️ {len(riesgo)} clientes retienen nuestra máquina y dejaron de comprar** "
        f"({fmt_clp(riesgo['compra_12m'].sum())} de venta 12M que se secó). Sin retiro FL-2 de por medio → "
        f"la máquina sigue en su local. **Prioridad de terreno:** verificar si está ociosa o usándose para "
        f"otros productos, y recuperar o reactivar.\n"
        f"- **{len(retirada)} clientes** ya tuvieron retiro FL-2 (máquina recuperada o en devolución) — ya no operan con nosotros.\n"
        f"- **{len(activo)} clientes activos** siguen comprando: sin acción.")
    st.caption("Retiro = FL-2 facturado (excluye 'rechazada'). 'Retiene máquina' se infiere del modelo comodato "
               "(cada cliente tiene máquina) + ausencia de retiro; conviene verificar en terreno. El despacho "
               "'Entregada' (confirma retiro físico) solo existe desde feb-2026 → es columna de confianza, no filtro.")
