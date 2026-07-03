"""Panel Gerencia: tabla de todos los vendedores, ranking y edición de objetivos."""
import streamlit as st
import plotly.express as px
import pandas as pd

from app.styles import fmt_clp, fmt_pct, fmt_num, color_pct
from app.data import (get_resumen, get_pedidos_resumen, get_calendario,
                      get_todos_vendedores, get_objetivos, upsert_objetivo,
                      get_ultima_factura, get_maquinas_sin_factura)
from app.export import (color_hex, bloque_descarga,
                        GRP_AZUL, GRP_VERDE, GRP_NARANJO)

# Encabezados cortos + banda de grupos para el PNG (el grupo da el contexto, así
# "Objetivo" puede repetirse en Facturación / Máquinas / Efectividad sin ambigüedad).
_PNG_LABELS = [
    "Vendedor", "OBJETIVO", "FACTURACIÓN-NC", "% CUMPL", "PROYECCIÓN",
    "INGRESADOS", "FACTURADOS", "No Fact.", "% Fact.", "NC",
    "OBJETIVO", "INGRESADA AV", "ENTREGADA", "OBJETIVO", "DOC", "% EFECT",
]
_PNG_GRUPOS = [
    ("FACTURACIÓN", GRP_AZUL, 1, 4),
    ("PEDIDOS", GRP_AZUL, 5, 8),
    ("MÁQUINAS", GRP_VERDE, 10, 12),
    ("EFECTIVIDAD VISITAS", GRP_NARANJO, 13, 15),
]

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
    total_proy  = df["proyeccion_cierre"].sum() if "proyeccion_cierre" in df else 0
    pct_global  = total_fnc / total_obj if total_obj else None
    pct_proy    = total_proy / total_obj if total_obj else None

    cls      = color_pct(pct_global)
    cls_proy = color_pct(pct_proy)

    # ── KPIs financieros (4×2 = rectángulo simétrico para pantallazos) ───────
    st.markdown(f"""
    <div class="kpi-grid-4">
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
        <div class="kpi-label">Proyección Cierre</div>
        <div class="kpi-value {cls_proy}">{fmt_clp(total_proy)}</div>
        <div class="kpi-sub">% Proy: <strong>{fmt_pct(pct_proy)}</strong></div>
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
        <div class="kpi-label">Maq. Ingresadas AV</div>
        <div class="kpi-value">{fmt_num(total_mgst)}</div>
        <div class="kpi-sub">Maq. Entregada: {fmt_num(total_menv)}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Tabla principal (réplica mejorada del Power BI) ──────────────────────
    st.markdown('<div class="seccion-titulo">Seguimiento por vendedor</div>',
                unsafe_allow_html=True)

    # Tira de contexto (días del mes + última factura) justo arriba del cuadro,
    # para que un solo pantallazo del seguimiento incluya el período de referencia.
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

    # UNA sola tabla con TODOS los vendedores (con y sin objetivo juntos). A quien
    # aún no tiene objetivo del mes, los % dependientes (% Cumpl, % Efec) le salen
    # "—". "Sin asignar" (facturación sin vendedor mapeado en Obuma) se fija al
    # final de la tabla (ver _tabla_gerencia), no se cuenta como vendedor.
    sin_obj = df[(df["obj_venta"] == 0) & (df["nombre_canonico"] != "Sin asignar")]
    if not sin_obj.empty:
        st.markdown(
            f'<div class="estado-vacio" style="margin-bottom:.75rem">'
            f'ℹ️ {len(sin_obj)} vendedor(es) aún sin objetivo del período — aparecen '
            f'en la tabla con "—" en los % que dependen del objetivo. Asígnalos en '
            f'<strong>Editar objetivos</strong>.</div>',
            unsafe_allow_html=True,
        )
    _tabla_gerencia(df)

    # Export discreto (PNG para WhatsApp / CSV) con el contexto del período.
    _subt = (f"{MESES[mes]} {anio}  ·  Días del mes: {cal['dias_totales']}  ·  "
             f"Días trabajados: {cal['dias_trabajados']}  ·  Última factura: {ultima_factura}")
    _disp, _col = _export_seguimiento(df)
    bloque_descarga(_disp, _col, "REPORTE DE SEGUIMIENTO DE OBJETIVOS",
                    _subt, f"seguimiento_{anio}_{mes:02d}",
                    col_labels=_PNG_LABELS, grupos=_PNG_GRUPOS)

    # Aviso: máquinas FL-4 ingresadas en Autoventa que aún NO tienen factura.
    # No cuentan en "Maq. Ingresadas AV" hasta facturarse; aquí quedan visibles.
    df_maq_sf = get_maquinas_sin_factura(client, anio, mes)
    if not df_maq_sf.empty:
        filas = "".join(
            f"<li><strong>{r['vendedor']}</strong> — cliente {r['cliente_rut']} "
            f"(pedido {r['n_pedido']}, {str(r['fecha'])[:10]})</li>"
            for _, r in df_maq_sf.iterrows()
        )
        st.markdown(f"""
        <div class="aviso-maq">
          <strong>⚠️ {len(df_maq_sf)} máquina(s) ingresada(s) en Autoventa aún sin factura</strong>
          <div class="aviso-maq-sub">Instalación a cliente nuevo (FL-4) registrada en Autoventa pero
          sin DTE emitido. NO cuenta en <em>Maq. Ingresadas AV</em> hasta que se facture el flete;
          aparecerá automáticamente cuando salga su factura.</div>
          <ul>{filas}</ul>
        </div>
        """, unsafe_allow_html=True)

    # Nota explicativa: de dónde sale cada columna (colapsable)
    with st.expander("ℹ️ Cómo leer la tabla y de dónde sale cada columna", expanded=False):
        st.markdown(
            """
            <div class="nota-embudo">
              <p><strong>Dos ERP alimentan esta tabla:</strong> <strong>Obuma</strong> (facturación
                 oficial — DTE, notas de crédito, máquinas) y <strong>Autoventa</strong> (pedidos y
                 logística). Los <strong>objetivos</strong> los edita gerencia. Las dos sociedades,
                 <strong>Acuña</strong> y <strong>Gran Natural</strong>, están en Obuma;
                 <strong>Autoventa cubre solo Gran Natural.</strong></p>

              <p><strong>De dónde sale cada columna</strong></p>
              <ul>
                <li><strong>Vendedor</strong> — dimensión de vendedores (se mapea el nombre de cada
                    ERP a un id único, tolerando variaciones de escritura).</li>
                <li><strong>Objetivo</strong> — objetivo de venta del mes, <em>cargado por gerencia</em>
                    (editable abajo). Igual para <em>Obj Maq</em> y <em>Obj Visitas</em>.</li>
                <li><strong>Fact-NC</strong> — <strong>Obuma</strong>. Es la <em>venta neta oficial</em>
                    del vendedor y el número contra el que se mide el objetivo. Detalle:
                    <ul>
                      <li><strong>Fórmula:</strong> suma de <em>facturas</em> − suma de <em>notas de
                          crédito</em> del mes. Las NC entran con <strong>signo negativo</strong>
                          (devoluciones/anulaciones que restan venta).</li>
                      <li><strong>Qué documentos cuenta:</strong> solo DTE reales —
                          <em>factura electrónica</em>, <em>factura exenta</em> y <em>nota de
                          crédito</em>. Se <strong>excluyen</strong> notas de venta/pedidos internos y
                          guías de despacho (no son venta facturada).</li>
                      <li><strong>Cubre las dos sociedades:</strong> Acuña + Gran Natural (Obuma es el
                          único ERP con la facturación de ambas).</li>
                      <li><strong>Atribución por documento:</strong> toda la factura suma al vendedor
                          que figura en el DTE (por eso es la fuente de verdad para repartir las
                          ventas entre vendedores).</li>
                      <li>Se suma a <strong>nivel de línea de producto</strong> (cada factura puede
                          traer varias líneas); el monto usado es el <em>neto</em>, sin IVA.</li>
                    </ul></li>
                <li><strong>% Cumpl</strong> — Fact-NC / Objetivo.</li>
                <li><strong>Proyección</strong> — venta estimada al cierre del mes si el vendedor
                    mantiene su ritmo actual: <em>(Fact-NC / días hábiles transcurridos) × días
                    hábiles del mes</em> (descontando feriados). El color compara la proyección
                    contra el objetivo: verde ≥100%, amarillo ≥70%, rojo &lt;70%. En meses cerrados
                    coincide con el Fact-NC final.</li>
                <li><strong>Pedidos</strong> — <strong>Autoventa</strong>: neto total de pedidos del
                    mes = <em>Ped. Fact. + No Fact.</em></li>
                <li><strong>Ped. Fact.</strong> — <strong>Autoventa</strong>: pedidos que ya tienen
                    folio (DTE emitido) = <em>Pedidos − No Fact.</em> <strong>El vendedor de un pedido
                    facturado se hereda del documento en Obuma</strong> (el DTE manda), no de quién
                    cargó la línea en Autoventa — así esta columna usa el mismo criterio que Fact-NC.</li>
                <li><strong>No Fact.</strong> — <strong>Autoventa</strong>: pedidos marcados
                    <em>Sin DTE</em> (despachados pero aún sin factura emitida).</li>
                <li><strong>% Fact.</strong> — Ped. Fact. / Pedidos: qué parte de lo pedido llegó a
                    factura. <strong>"—"</strong> = vendedor sin pedidos en Autoventa (ej. solo Acuña).</li>
                <li><strong>NC</strong> — <strong>Obuma</strong>: suma de notas de crédito del mes.</li>
                <li><strong>Maq. Ingresadas AV</strong> <em>(AV = Autoventa)</em> — máquinas de
                    <strong>instalación a cliente nuevo</strong> (código <strong>FL-4</strong>), tal
                    como se ingresan en <strong>Autoventa</strong>. Es lo que el vendedor colocó en el
                    mes. El vendedor se toma de Autoventa (quien gestionó la máquina en terreno), no de
                    Obuma (que suele dejar estos documentos en "Sin asignar"). NO incluye cambios
                    (FL-1/3/5) ni retiros (FL-2).</li>
                <li><strong>Maq. Entregada</strong> — de esas máquinas ingresadas, las que figuran como
                    <strong>"Entregada"</strong> en el <em>Detalle de despachos</em>, cruzando por N° de
                    documento. Mide la conversión ingresada → entregada.</li>
                <li><em>Ojo:</em> la máquina se cuenta cuando su flete FL-4 <strong>se factura</strong>.
                    Si ya se ingresó en Autoventa pero aún no tiene factura (Sin DTE), NO cuenta todavía
                    — pero queda <strong>listada en el aviso amarillo</strong> bajo la tabla, y entra
                    sola cuando se emita el DTE.</li>
                <li><strong>N° Docs</strong> — <strong>Obuma</strong>: nº de facturas distintas del
                    vendedor en el mes.</li>
                <li><strong>% Efec</strong> — N° Docs / Obj Visitas.</li>
              </ul>

              <p><strong>Cómo leer el embudo Pedidos → Fact-NC</strong></p>
              <ul>
                <li>La identidad que se cumple es <strong>Ped. Fact. − NC = Fact-NC</strong>
                    (no "Pedidos total − NC"). Para vendedores <strong>100% Gran Natural cuadra al
                    peso</strong> (las diferencias de unos pesos son redondeo de la API de Autoventa).</li>
                <li><strong>Por qué a un vendedor puede NO cuadrarle, sin que sea error:</strong></li>
                <li>① <strong>Tiene venta en Acuña.</strong> Acuña no pasa por Autoventa, así que su
                    facturación entra en Fact-NC pero no tiene pedidos que la respalden → Fact-NC &gt;
                    Ped. Fact. (ej.: un vendedor con Pedidos = 0 y Fact-NC &gt; 0 es 100% Acuña).</li>
                <li>② <strong>"No Facturado" inflado por cruce de sociedades.</strong> Si un pedido se
                    ingresa en Autoventa (Gran Natural) pero termina facturándose por <strong>Acuña</strong>,
                    Autoventa nunca ve el DTE y lo deja <em>Sin DTE</em> para siempre — aunque la venta
                    sí existe (ya está en Fact-NC por Acuña).</li>
                <li>③ <strong>Documento sin vendedor en Obuma.</strong> Si Obuma dejó la factura en
                    "Sin asignar" pero Autoventa sí sabe de quién es, los montos quedan en filas
                    distintas. Se corrige mapeando ese vendedor en Obuma.</li>
                <li>Pedido y factura caen en el <strong>mismo mes</strong> (sin arrastre, verificado
                    por folio).</li>
              </ul>

              <p><strong>Sobre máquinas:</strong> esta tabla muestra solo <em>Maq. Ingresadas AV</em> y
                 <em>Maq. Entregada</em>. Los <strong>retiros</strong> (FL-2), los <em>cambios</em>
                 (FL-1/3/5) y el detalle por estado están en <strong>Análisis → Máquinas</strong>.
                 El movimiento de máquina se toma de <strong>Obuma</strong> (cubre las dos sociedades y
                 los 5 códigos FL) y el <strong>vendedor se atribuye según Autoventa</strong>; el estado
                 <em>entregada/rechazada</em> se completa al cargar los despachos — sin despacho, la
                 máquina ingresada queda pendiente de entrega.</p>

              <p><strong>Tarjetas de arriba (totales del equipo):</strong> son la suma de todos los
                 vendedores. <strong>Proyección Cierre</strong> = venta estimada a fin de mes con ritmo
                 lineal: <em>(Fact-NC / días trabajados) × días del mes</em> (descontando feriados);
                 <em>% Proy</em> = Proyección / Objetivo. Las demás tarjetas son los mismos conceptos
                 de la tabla, sumados.</p>

              <p><strong>Cálculo:</strong> casi todo se lee de la vista
                 <code>v_resumen_vendedor_mes</code> (Fact-NC, NC, máquinas, objetivos, N° Docs y sus
                 %); <em>Pedidos</em> sale de <code>fact_pedidos</code> y <em>Ped. Fact.</em> se deriva
                 en la app como Pedidos − No Fact. Ningún número se recalcula a mano: salen del ETL.</p>
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
    # Ordenar por fact_nc desc, pero "Sin asignar" (residual, no es vendedor) va
    # SIEMPRE al final, para no aparecer arriba en el ranking del reporte.
    df_sorted = df.assign(
        _sa=(df["nombre_canonico"] == "Sin asignar").astype(int)
    ).sort_values(["_sa", "fact_nc"], ascending=[True, False], na_position="last")

    header = (
        "<th style='text-align:left'>Vendedor</th>"
        "<th title='Objetivo de venta mensual'>Objetivo</th>"
        "<th title='Facturación neta de notas de crédito'>Fact-NC</th>"
        "<th title='Fact-NC / Objetivo'>% Cumpl</th>"
        "<th title='Venta proyectada al cierre del mes al ritmo actual: (Fact-NC / días trabajados) × días del mes. Color según % proyectado vs objetivo'>Proyección</th>"
        "<th title='Pedidos neto total (Autoventa) = facturados + no facturado'>Pedidos</th>"
        "<th title='Pedidos con folio emitido (Autoventa). El vendedor se hereda del documento en Obuma (el DTE manda). En Gran Natural, Ped. Fact. − NC = Fact-NC al peso; no iguala Fact-NC cuando hay venta Acuña (no pasa por Autoventa)'>Ped. Fact.</th>"
        "<th title='Monto no facturado (Sin DTE): pedidos cargados que aún no se facturan'>No Fact.</th>"
        "<th title='% de pedidos que llegaron a factura (Ped. Fact. / Pedidos). “—” = vendedor sin pedidos en Autoventa (ej. solo Acuña)'>% Fact.</th>"
        "<th title='Suma notas de crédito'>NC</th>"
        "<th title='Objetivo de máquinas'>Obj Maq</th>"
        "<th title='Máquinas de instalación a cliente nuevo (FL-4), ingresadas en Autoventa. Vendedor según Autoventa'>Maq. Ingresadas AV</th>"
        "<th title='De las ingresadas (FL-4), las que figuran como Entregada en los despachos'>Maq. Entregada</th>"
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
        # Proyección a cierre: color según % proyectado vs objetivo (ritmo)
        proy   = r.get("proyeccion_cierre")
        pct_p  = pd.to_numeric(r.get("pct_proyeccion"), errors="coerce")
        cls_p  = color_pct(None if pd.isna(pct_p) else float(pct_p))
        rows += f"""<tr>
          <td style='text-align:left'>{r['nombre_canonico']}</td>
          <td>{fmt_clp(r.get('obj_venta'))}</td>
          <td>{fmt_clp(r.get('fact_nc'))}</td>
          <td class='{cls_c}'>{fmt_pct(pct_c)}</td>
          <td class='{cls_p}'>{fmt_clp(proy)}</td>
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
    pct_proy_tot = tot_proy / tot_obj if tot_obj else None
    cls_proy_tot = color_pct(pct_proy_tot)
    rows += f"""<tr class='total-row'>
      <td style='text-align:left'>TOTAL</td>
      <td>{fmt_clp(tot_obj)}</td>
      <td>{fmt_clp(tot_fnc)}</td>
      <td class='{cls_tot}'>{fmt_pct(pct_tot)}</td>
      <td class='{cls_proy_tot}'>{fmt_clp(tot_proy)}</td>
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


def _export_seguimiento(df: pd.DataFrame):
    """
    Arma el DataFrame de STRINGS para exportar la tabla de seguimiento (mismo
    orden y formato que la tabla en pantalla) + el mapa de colores de los %.
    Devuelve (df_display, color_celdas).
    """
    d = (df.assign(_sa=(df["nombre_canonico"] == "Sin asignar").astype(int))
           .sort_values(["_sa", "fact_nc"], ascending=[True, False], na_position="last"))
    filas, colores = [], {}
    for i, (_, r) in enumerate(d.iterrows()):
        pct_c = r.get("pct_cumplimiento")
        pct_e = r.get("pct_efectividad")
        pct_p = pd.to_numeric(r.get("pct_proyeccion"), errors="coerce")
        ped_tot = r.get("pedidos_neto") or 0
        pct_f = (r.get("pedidos_facturado") / ped_tot) if ped_tot else None
        filas.append({
            "Vendedor": r["nombre_canonico"], "Objetivo": fmt_clp(r.get("obj_venta")),
            "Fact-NC": fmt_clp(r.get("fact_nc")), "%Cumpl": fmt_pct(pct_c),
            "Proy.": fmt_clp(r.get("proyeccion_cierre")), "Pedidos": fmt_clp(r.get("pedidos_neto")),
            "Ped.Fact": fmt_clp(r.get("pedidos_facturado")), "No Fact": fmt_clp(r.get("no_facturado_monto")),
            "%Fact": fmt_pct(pct_f) if pct_f is not None else "—", "NC": fmt_clp(r.get("monto_notas_credito")),
            "ObjMaq": fmt_num(r.get("obj_maquinas")), "MaqIng": fmt_num(r.get("maquinas_gestionadas")),
            "MaqEnt": fmt_num(r.get("maquinas_entregadas")), "ObjVis": fmt_num(r.get("obj_visitas")),
            "Docs": fmt_num(r.get("n_documentos")), "%Efec": fmt_pct(pct_e),
        })
        for col, val, kw in [("%Cumpl", pct_c, {}), ("%Efec", pct_e, {"ok": 0.5, "warn": 0.3})]:
            h = color_hex(val, **kw)
            if h:
                colores[(i, col)] = h
        if pd.notna(pct_p):
            h = color_hex(float(pct_p))
            if h:
                colores[(i, "Proy.")] = h

    # Fila TOTAL
    n = len(df)
    tot_obj = df["obj_venta"].sum()
    tot_fnc = df["fact_nc"].sum()
    tot_ped = df["pedidos_neto"].sum() if "pedidos_neto" in df else 0
    tot_pedf = df["pedidos_facturado"].sum() if "pedidos_facturado" in df else 0
    pf = (tot_pedf / tot_ped) if tot_ped else None
    filas.append({
        "Vendedor": "TOTAL", "Objetivo": fmt_clp(tot_obj), "Fact-NC": fmt_clp(tot_fnc),
        "%Cumpl": fmt_pct(tot_fnc / tot_obj if tot_obj else None),
        "Proy.": fmt_clp(df["proyeccion_cierre"].sum()), "Pedidos": fmt_clp(tot_ped),
        "Ped.Fact": fmt_clp(tot_pedf), "No Fact": fmt_clp(df["no_facturado_monto"].sum()),
        "%Fact": fmt_pct(pf) if pf is not None else "—", "NC": fmt_clp(df["monto_notas_credito"].sum()),
        "ObjMaq": fmt_num(df["obj_maquinas"].sum()), "MaqIng": fmt_num(df["maquinas_gestionadas"].sum()),
        "MaqEnt": fmt_num(df["maquinas_entregadas"].sum()), "ObjVis": fmt_num(df["obj_visitas"].sum()),
        "Docs": fmt_num(df["n_documentos"].sum()), "%Efec": "",
    })
    return pd.DataFrame(filas), colores


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
