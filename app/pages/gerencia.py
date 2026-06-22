"""Panel Gerencia: tabla de todos los vendedores, ranking y edición de objetivos."""
import streamlit as st
import plotly.express as px
import pandas as pd

from app.styles import fmt_clp, fmt_pct, fmt_num, color_pct
from app.data import (get_resumen, get_pedidos_resumen, get_calendario,
                      get_todos_vendedores, get_objetivos, upsert_objetivo,
                      get_ultima_factura)

MESES = {
    1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
    7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"
}


def render(client, anio: int, mes: int):
    # CSS ya inyectado en main.py

    df = get_resumen(client, anio, mes)
    cal = get_calendario(client, anio, mes)
    ultima_factura = get_ultima_factura(client, anio, mes)

    if df.empty:
        st.info("Sin datos para el período seleccionado.")
        return

    # Excluir filas de demo del seed
    df = df[~df["nombre_canonico"].str.startswith("Vendedor ", na=False)].copy()

    # Merge con pedidos neto
    dfped = get_pedidos_resumen(client, anio, mes)
    if not dfped.empty:
        df = df.merge(dfped[["vendedor_id", "pedidos_neto"]], on="vendedor_id", how="left")
    else:
        df["pedidos_neto"] = None

    # Convertir tipos
    for col in ["fact_nc","monto_facturas","monto_notas_credito","proyeccion_cierre",
                "obj_venta","no_facturado_monto","pedidos_neto",
                "maquinas_gestionadas","maquinas_entregadas","maquinas_retiros",
                "obj_maquinas","obj_visitas","n_documentos","n_facturas","n_notas_credito"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Pedidos facturados = pedidos con folio (Pedidos − No facturado).
    # Embudo: Pedidos $ = Ped. Facturados + No Facturado. OJO: no debe igualar
    # a Fact-NC (hay ventas sin pedido en Autoventa, atribución y timing distintos).
    df["pedidos_facturado"] = df["pedidos_neto"] - df["no_facturado_monto"]

    # ── KPIs globales ────────────────────────────────────────────────────────
    total_obj   = df["obj_venta"].sum()
    total_fnc   = df["fact_nc"].sum()
    total_fact  = df["monto_facturas"].sum()
    total_nc    = df["monto_notas_credito"].sum()
    total_docs  = df["n_documentos"].sum()
    total_mgst  = df["maquinas_gestionadas"].sum()
    total_menv  = df["maquinas_entregadas"].sum()
    total_ped   = df["pedidos_neto"].sum() if "pedidos_neto" in df else 0
    total_nofac = df["no_facturado_monto"].sum() if "no_facturado_monto" in df else 0
    pct_global  = total_fnc / total_obj if total_obj else None

    cls = color_pct(pct_global)

    # ── Strip de contexto ────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="kpi-strip">
      <div class="kpi-strip-card">
        <div class="kpi-strip-value">{cal['dias_totales']}</div>
        <div class="kpi-strip-label">Total días mes</div>
      </div>
      <div class="kpi-strip-card">
        <div class="kpi-strip-value">{cal['dias_trabajados']}</div>
        <div class="kpi-strip-label">Días trabajados</div>
      </div>
      <div class="kpi-strip-card">
        <div class="kpi-strip-value">{ultima_factura}</div>
        <div class="kpi-strip-label">Última factura</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPIs financieros (3×2) ───────────────────────────────────────────────
    st.markdown(f"""
    <div class="kpi-grid-3">
      <div class="kpi-card">
        <div class="kpi-label">Objetivo total</div>
        <div class="kpi-value">{fmt_clp(total_obj)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact Total</div>
        <div class="kpi-value">{fmt_clp(total_fact)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Notas de Crédito</div>
        <div class="kpi-value rojo-bg">{fmt_clp(total_nc)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fact-NC</div>
        <div class="kpi-value {cls}">{fmt_clp(total_fnc)}</div>
        <div class="kpi-sub">% Cumpl: <strong>{fmt_pct(pct_global)}</strong></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Pedidos</div>
        <div class="kpi-value">{fmt_clp(total_ped)}</div>
        <div class="kpi-sub">No fact.: {fmt_clp(total_nofac)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">N° Documentos</div>
        <div class="kpi-value">{fmt_num(total_docs)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Máq. Gestionadas</div>
        <div class="kpi-value">{fmt_num(total_mgst)}</div>
        <div class="kpi-sub">Entregadas: {fmt_num(total_menv)}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Tabla principal (réplica mejorada del Power BI) ──────────────────────
    st.markdown('<div class="seccion-titulo">Seguimiento por vendedor</div>',
                unsafe_allow_html=True)

    # Separar vendedores CON y SIN objetivo
    df_con_obj = df[df["obj_venta"] > 0].copy()
    df_sin_obj = df[df["obj_venta"] == 0].copy()

    _tabla_gerencia(df_con_obj)

    if not df_sin_obj.empty:
        with st.expander(f"{len(df_sin_obj)} vendedor(es) sin objetivo asignado"):
            st.markdown(
                '<div class="estado-vacio" style="margin-bottom:.75rem">'
                'Estos vendedores aún no tienen objetivo definido para el período. '
                'Asígnalos en la sección <strong>Editar objetivos</strong>.'
                '</div>',
                unsafe_allow_html=True,
            )
            _tabla_gerencia(df_sin_obj, mostrar_total=False)

    # Nota explicativa: de dónde sale cada columna (colapsable)
    with st.expander("ℹ️ Cómo leer la tabla y de dónde sale cada columna", expanded=False):
        st.markdown(
            """
            <div class="nota-embudo">
              <p><strong>Pedidos vs Fact-NC</strong></p>
              <ul>
                <li><strong>Pedidos = Ped. Fact. + No Fact.</strong> El total de pedidos (Autoventa)
                    se divide entre los que ya tienen factura y los que aún no (Sin DTE).</li>
                <li><strong>Ped. Fact. ≈ Facturas de Gran Natural.</strong> A nivel sociedad cuadra
                    al peso (mayo: $55,1M = $55,1M). La fórmula que sí se cumple es
                    <em>Ped. Fact. − NC = Fact-NC</em>, no "Pedidos total − NC".</li>
                <li><strong>No iguala a Fact-NC por dos razones, no por error:</strong>
                    (1) <em>Pedidos total</em> incluye el No Facturado, que todavía no es venta;
                    (2) <em>Acuña no pasa por Autoventa</em> (Autoventa = solo Gran Natural), así que
                    su facturación entra en Fact-NC pero no en Pedidos.</li>
                <li><strong>% Fact. = Ped. Fact. / Pedidos</strong>: qué parte de lo pedido llegó a
                    factura. <strong>"—"</strong> = vendedor sin pedidos en Autoventa (ej. solo Acuña).</li>
                <li>Pedido y factura caen en el <strong>mismo mes</strong> (sin arrastre de meses
                    anteriores, verificado por folio).</li>
              </ul>
              <p><strong>Máquinas — de dónde sale cada columna</strong></p>
              <ul>
                <li><strong>Gestionadas</strong> = instalaciones a cliente nuevo: líneas con código
                    <strong>FL-4</strong> en <strong>Obuma</strong> (categoría "Maquinas"). Es lo que
                    el vendedor colocó en el mes.</li>
                <li><strong>Entregadas</strong> = de esas máquinas, las que figuran como
                    <strong>"Entregada"</strong> en el <em>Detalle de despachos</em> (Autoventa),
                    cruzando por N° de documento. Mide la conversión gestionada → entregada.</li>
                <li>Esta tabla muestra solo <strong>Gestionadas</strong> y <strong>Entregadas</strong>.
                    Los <strong>retiros</strong> (FL-2), los <em>cambios</em> (FL-1/3/5) y el detalle
                    por estado se ven en <strong>Análisis → Máquinas</strong>.</li>
                <li><strong>Fuente única = Obuma</strong> (cubre Acuña y Gran Natural y los 5 códigos
                    FL). El estado <em>entregada/rechazada</em> se completa al cargar los despachos;
                    sin despacho, la máquina queda <em>gestionada</em>. Si un mes muestra 0
                    gestionadas, es que no hubo líneas FL-4 en Obuma ese mes.</li>
              </ul>
              <p><strong>Resto de columnas:</strong> Fact-NC, N° docs, NC, % Cumpl. y % Efec. salen de
                 las facturas/NC de <strong>Obuma</strong>; Pedidos, No Fact. y % Fact. de
                 <strong>Autoventa</strong>; los objetivos los edita gerencia. Todo se calcula en la
                 vista <code>v_resumen_vendedor_mes</code>, no en la app.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Ranking ──────────────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Ranking — Fact-NC</div>',
                unsafe_allow_html=True)
    _grafico_ranking(df)

    # ── Edición de objetivos ─────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Editar objetivos del período</div>',
                unsafe_allow_html=True)
    _editor_objetivos(client, df, anio, mes)


def _tabla_gerencia(df: pd.DataFrame, mostrar_total: bool = True):
    """Tabla completa de vendedores con colores por cumplimiento."""
    # Ordenar por fact_nc desc, poner totales al final
    df_sorted = df.sort_values("fact_nc", ascending=False, na_position="last")

    header = (
        "<th style='text-align:left'>Vendedor</th>"
        "<th title='Objetivo de venta mensual'>Objetivo</th>"
        "<th title='Facturación neta de notas de crédito'>Fact-NC</th>"
        "<th title='Fact-NC / Objetivo'>% Cumpl</th>"
        "<th title='Pedidos neto total (Autoventa) = facturados + no facturado'>Pedidos</th>"
        "<th title='Pedidos con folio emitido. A nivel Gran Natural = Facturas Obuma exacto. No iguala Fact-NC porque Acuña no pasa por Autoventa'>Ped. Fact.</th>"
        "<th title='Monto no facturado (Sin DTE): pedidos cargados que aún no se facturan'>No Fact.</th>"
        "<th title='% de pedidos que llegaron a factura (Ped. Fact. / Pedidos). “—” = vendedor sin pedidos en Autoventa (ej. solo Acuña)'>% Fact.</th>"
        "<th title='Suma notas de crédito'>NC</th>"
        "<th title='Objetivo de máquinas'>Obj Maq</th>"
        "<th title='Máquinas gestionadas (FL-4)'>Gestionadas</th>"
        "<th title='Máquinas entregadas'>Entregadas</th>"
        "<th title='Objetivo de visitas'>Obj Visitas</th>"
        "<th title='Número de documentos emitidos'>N° Docs</th>"
        "<th title='% Efectividad (docs / obj visitas)'>% Efec</th>"
    )
    rows = ""
    for _, r in df_sorted.iterrows():
        pct_c = r.get("pct_cumplimiento")
        pct_e = r.get("pct_efectividad")
        cls_c = color_pct(pct_c)
        cls_e = color_pct(pct_e, umbral_ok=0.5, umbral_warn=0.3)
        ped_tot = r.get("pedidos_neto") or 0
        pct_fact = (r.get("pedidos_facturado") / ped_tot) if ped_tot else None
        rows += f"""<tr>
          <td style='text-align:left'>{r['nombre_canonico']}</td>
          <td>{fmt_clp(r.get('obj_venta'))}</td>
          <td>{fmt_clp(r.get('fact_nc'))}</td>
          <td class='{cls_c}'>{fmt_pct(pct_c)}</td>
          <td>{fmt_clp(r.get('pedidos_neto'))}</td>
          <td>{fmt_clp(r.get('pedidos_facturado'))}</td>
          <td>{fmt_clp(r.get('no_facturado_monto'))}</td>
          <td>{fmt_pct(pct_fact) if pct_fact is not None else '—'}</td>
          <td class='rojo-bg'>{fmt_clp(r.get('monto_notas_credito'))}</td>
          <td>{fmt_num(r.get('obj_maquinas'))}</td>
          <td>{fmt_num(r.get('maquinas_gestionadas'))}</td>
          <td>{fmt_num(r.get('maquinas_entregadas'))}</td>
          <td>{fmt_num(r.get('obj_visitas'))}</td>
          <td>{fmt_num(r.get('n_documentos'))}</td>
          <td class='{cls_e}'>{fmt_pct(pct_e)}</td>
        </tr>"""

    if not mostrar_total:
        st.markdown(f"""
        <div class="tabla-container">
        <table class="kreems"><thead><tr>{header}</tr></thead>
        <tbody>{rows}</tbody></table></div>
        """, unsafe_allow_html=True)
        return

    # Fila de totales
    tot_fnc  = df["fact_nc"].sum()
    tot_proy = df["proyeccion_cierre"].sum()
    tot_obj  = df["obj_venta"].sum()
    pct_tot = tot_fnc / tot_obj if tot_obj else None
    cls_tot = color_pct(pct_tot)
    tot_ped  = df.get('pedidos_neto', pd.Series()).sum() if 'pedidos_neto' in df else 0
    tot_pedf = df.get('pedidos_facturado', pd.Series()).sum() if 'pedidos_facturado' in df else 0
    pct_fact_tot = (tot_pedf / tot_ped) if tot_ped else None
    rows += f"""<tr class='total-row'>
      <td style='text-align:left'>TOTAL</td>
      <td>{fmt_clp(tot_obj)}</td>
      <td>{fmt_clp(tot_fnc)}</td>
      <td class='{cls_tot}'>{fmt_pct(pct_tot)}</td>
      <td>{fmt_clp(tot_ped)}</td>
      <td>{fmt_clp(tot_pedf)}</td>
      <td>{fmt_clp(df['no_facturado_monto'].sum())}</td>
      <td>{fmt_pct(pct_fact_tot) if pct_fact_tot is not None else '—'}</td>
      <td class='rojo-bg'>{fmt_clp(df['monto_notas_credito'].sum())}</td>
      <td>{fmt_num(df['obj_maquinas'].sum())}</td>
      <td>{fmt_num(df['maquinas_gestionadas'].sum())}</td>
      <td>{fmt_num(df['maquinas_entregadas'].sum())}</td>
      <td>{fmt_num(df['obj_visitas'].sum())}</td>
      <td>{fmt_num(df['n_documentos'].sum())}</td>
      <td></td>
    </tr>"""

    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems">
      <thead><tr>{header}</tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>
    """, unsafe_allow_html=True)


def _grafico_ranking(df: pd.DataFrame):
    df_r = df[df["fact_nc"].notna()].copy()
    df_r = df_r.sort_values("fact_nc", ascending=True)
    df_r["color"] = df_r["pct_proyeccion"].apply(
        lambda x: "#1A7F4B" if (x or 0) >= 1 else "#D4881E" if (x or 0) >= 0.7 else "#C0392B"
    )
    fig = px.bar(df_r, x="fact_nc", y="nombre_canonico", orientation="h",
                 color="color", color_discrete_map="identity",
                 text=df_r["fact_nc"].apply(lambda x: fmt_clp(x)),
                 labels={"fact_nc": "Fact-NC", "nombre_canonico": ""})
    if "obj_venta" in df_r.columns:
        for _, row in df_r.iterrows():
            if pd.notna(row.get("obj_venta")) and row["obj_venta"] > 0:
                fig.add_vline(x=float(row["obj_venta"]),
                              line_dash="dot", line_color="gray",
                              annotation_text="Obj", annotation_font_size=9)
    fig.update_layout(
        height=max(280, len(df_r) * 32),
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False, plot_bgcolor="white", paper_bgcolor="white",
        xaxis_title="", yaxis_title="",
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


def _safe_float(val, default=0.0) -> float:
    """Convierte val a float de forma segura; devuelve default si es NaN/None."""
    try:
        v = float(val)
        return default if pd.isna(v) else v
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0) -> int:
    return int(_safe_float(val, default))


def _editor_objetivos(client, df: pd.DataFrame, anio: int, mes: int):
    """Formulario de edición de objetivos para gerencia."""
    vendedores = df[["vendedor_id", "nombre_canonico",
                     "obj_venta", "obj_maquinas", "obj_visitas"]].copy()
    vendedores = vendedores.sort_values("nombre_canonico")

    nombre_sel = st.selectbox(
        "Seleccionar vendedor",
        vendedores["nombre_canonico"].tolist(),
        key="sel_vend_obj"
    )
    fila = vendedores[vendedores["nombre_canonico"] == nombre_sel].iloc[0]

    with st.form("form_objetivo", clear_on_submit=False):
        st.markdown(f"**Editando objetivos de: {nombre_sel}** — {anio}/{mes:02d}")
        c1, c2, c3 = st.columns(3)
        obj_v = c1.number_input(
            "Objetivo venta ($)",
            value=_safe_float(fila.get("obj_venta")),
            step=500000.0, format="%.0f", min_value=0.0,
        )
        obj_m = c2.number_input(
            "Objetivo máquinas",
            value=_safe_int(fila.get("obj_maquinas")),
            step=1, min_value=0,
        )
        obj_vis = c3.number_input(
            "Objetivo visitas",
            value=_safe_int(fila.get("obj_visitas")),
            step=10, min_value=0,
        )
        submitted = st.form_submit_button("💾 Guardar objetivo", type="primary",
                                          use_container_width=True)

    if submitted:
        try:
            upsert_objetivo(client, int(fila["vendedor_id"]),
                            anio, mes, obj_v, obj_m, obj_vis)
            st.success(f"✅ Objetivo de **{nombre_sel}** actualizado correctamente.")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")
