"""Panel Comisiones (solo gerencia): cálculo variable mensual por vendedor.

Lee v_comision_vendedor_mes (todo el cálculo se hace en Postgres). Permite
editar las entradas del período (cartera de clientes, salas Ganga, override de
efectividad y plan de comisión), cerrar el mes (snapshot) y exportar por
trabajador y consolidado (PDF/Excel).
"""
import streamlit as st
import pandas as pd

from app.styles import fmt_clp, fmt_pct, fmt_num, color_pct
from app.export import color_hex, bloque_descarga
from app.data import (
    get_comisiones, upsert_comision_entrada,
    get_planes_comision, update_vendedor_plan,
    cerrar_mes_comisiones, get_comision_calculo, get_ventas_detalle_doc,
    get_tramos_pnv, get_tramos_maquinas, get_tramos_efectividad,
    get_parametros, replace_tramos_plan, upsert_parametros,
)

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

_NUM_COLS = [
    "fact_nc", "obj_venta", "logro_pnv", "pnv_aj", "pnv_logro_override", "com_pnv", "bono_4pct",
    "obj_maquinas", "maquinas_entregadas", "logro_maquinas", "maq_aj", "maq_logro_override", "com_maquinas",
    "obj_visitas", "n_facturas", "cartera_clientes", "logro_efectividad", "efect_aj",
    "com_efectividad", "total_comision", "dias_trabajados", "inab", "semana_corrida",
    "salas_ganga", "bono_reposicion", "total_variable", "total_a_pagar", "plan_id",
]


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    for c in _NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def render(client, anio: int, mes: int):
    tab_mes, tab_cfg = st.tabs(["📅 Cálculo del mes", "⚙️ Escalas y parámetros"])
    with tab_mes:
        _render_mes(client, anio, mes)
    with tab_cfg:
        _render_escalas(client)


def _render_mes(client, anio: int, mes: int):
    df = get_comisiones(client, anio, mes)

    if df.empty:
        st.info("Sin datos de comisiones para el período seleccionado. "
                "Verifica que existan ventas/objetivos del mes y que tu usuario "
                "tenga rol gerencia.")
        return

    df = _coerce(df)
    # Ocultar vendedores demo del seed
    df = df[~df["nombre_canonico"].str.startswith("Vendedor ", na=False)].copy()

    # ── KPIs globales ────────────────────────────────────────────────────────
    tot_comision = df["total_comision"].fillna(0).sum()
    tot_sc       = df["semana_corrida"].fillna(0).sum()
    tot_repo     = df["bono_reposicion"].fillna(0).sum()
    tot_pagar    = df["total_a_pagar"].fillna(0).sum()
    n_con_com    = int((df["total_a_pagar"].fillna(0) > 0).sum())

    snapshot = get_comision_calculo(client, anio, mes)
    estado_cierre = (f"Cerrado · {len(snapshot)} vendedores congelados"
                     if not snapshot.empty else "Sin cerrar (cálculo en vivo)")

    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card destacado">
        <div class="kpi-label">Total a Pagar (variable)</div>
        <div class="kpi-value">{fmt_clp(tot_pagar)}</div>
        <div class="kpi-sub">{n_con_com} vendedores con comisión</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total Comisión</div>
        <div class="kpi-value">{fmt_clp(tot_comision)}</div>
        <div class="kpi-sub">PNV + bono + máq + efec</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Semana Corrida</div>
        <div class="kpi-value">{fmt_clp(tot_sc)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Bono Reposición</div>
        <div class="kpi-value">{fmt_clp(tot_repo)}</div>
        <div class="kpi-sub">{estado_cierre}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Tabla de comisiones ──────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Comisiones por vendedor</div>',
                unsafe_allow_html=True)
    _tabla_comisiones(df)

    _disp, _col, _csv = _export_comisiones(df)
    bloque_descarga(_disp, _col, f"Comisiones por vendedor — {MESES[mes]} {anio}",
                    f"{MESES[mes]} {anio}  ·  {estado_cierre}",
                    f"comisiones_{anio}_{mes:02d}", disp_csv=_csv)

    with st.expander("ℹ️ Cómo se calcula", expanded=False):
        st.markdown(
            """
            <div class="nota-embudo">
              <ul>
                <li><strong>PNV</strong>: logro = Fact-NC / objetivo → tramo más cercano (5%,
                    piso 80%, techo 110%) → monto de tabla. <strong>Bono 4%</strong> = 4% del
                    exceso sobre el 110% del objetivo (solo si llega al 110%).</li>
                <li><strong>Máquinas</strong>: entregadas (FL-4) / objetivo → tramo 5% (piso 40%, techo 140%).</li>
                <li><strong>Efectividad</strong>: (N°facturas / objetivo de visitas, tramo 10% 30–60%)
                    cruzado con el <strong>rango de cartera</strong> de clientes asignados.</li>
                <li><strong>Semana Corrida</strong> = Total Comisión / días trabajados × INAB.</li>
                <li><strong>Total a Pagar</strong> = Total Comisión + Semana Corrida + Bono Reposición
                    ($15.000 × salas Ganga).</li>
                <li>Plan <strong>Macarena</strong> usa su propia tabla (montos más altos).</li>
              </ul>
            </div>
            """, unsafe_allow_html=True,
        )

    # ── Edición de entradas del período ──────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Editar entradas del período</div>',
                unsafe_allow_html=True)
    _editor_entradas(client, df, anio, mes)

    # ── Cierre de mes + export ───────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Cierre y exportación</div>',
                unsafe_allow_html=True)
    _cierre_y_export(client, df, anio, mes, snapshot)


def _tabla_comisiones(df: pd.DataFrame):
    df_sorted = df.sort_values("total_a_pagar", ascending=False, na_position="last")

    header = (
        "<th style='text-align:left'>Vendedor</th>"
        "<th title='Plan de comisión'>Plan</th>"
        "<th title='Objetivo de venta mensual'>Objetivo</th>"
        "<th title='Facturación neta de NC'>Fact-NC</th>"
        "<th title='% logro PNV (real)'>%PNV</th>"
        "<th title='Comisión PNV (tramo)'>Com PNV</th>"
        "<th title='Bono 4% sobre exceso del 110%'>Bono 4%</th>"
        "<th title='Máquinas entregadas / objetivo'>Máq</th>"
        "<th title='Comisión máquinas'>Com Máq</th>"
        "<th title='% efectividad (real)'>%Efec</th>"
        "<th title='Clientes asignados (rango de cartera)'>Cartera</th>"
        "<th title='Comisión efectividad'>Com Efec</th>"
        "<th title='PNV + bono + máq + efec'>Total Com.</th>"
        "<th title='Total Comisión / días trab. × INAB'>Sem. Corrida</th>"
        "<th title='$15.000 × salas Ganga'>Reposición</th>"
        "<th title='Total Comisión + Sem. Corrida + Reposición'>Total a Pagar</th>"
    )

    plan_lbl = {1: "Normal", 2: "Macarena"}
    rows = ""
    for _, r in df_sorted.iterrows():
        pct_pnv  = r.get("logro_pnv")
        pct_ef   = r.get("logro_efectividad")
        cls_pnv  = color_pct(pct_pnv)
        cls_ef   = color_pct(pct_ef, umbral_ok=0.5, umbral_warn=0.3)
        maq_txt  = f"{fmt_num(r.get('maquinas_entregadas'))}/{fmt_num(r.get('obj_maquinas'))}"
        plan     = plan_lbl.get(int(r["plan_id"]) if pd.notna(r.get("plan_id")) else 1, "Normal")
        # Indicador de tramo forzado por gerencia
        pnv_over = pd.notna(r.get("pnv_logro_override"))
        maq_over = pd.notna(r.get("maq_logro_override"))
        _pnv_ov_txt = fmt_pct(r["pnv_logro_override"]) if pnv_over else ""
        _maq_ov_txt = fmt_pct(r["maq_logro_override"]) if maq_over else ""
        pnv_star = (f" <span title='Tramo forzado a {_pnv_ov_txt} por gerencia' "
                    f"style='color:#f59e0b'>★</span>") if pnv_over else ""
        maq_star = (f" <span title='Tramo forzado a {_maq_ov_txt} por gerencia' "
                    f"style='color:#f59e0b'>★</span>") if maq_over else ""
        rows += f"""<tr>
          <td style='text-align:left'>{r['nombre_canonico']}</td>
          <td>{plan}</td>
          <td>{fmt_clp(r.get('obj_venta'))}</td>
          <td>{fmt_clp(r.get('fact_nc'))}</td>
          <td class='{cls_pnv}'>{fmt_pct(pct_pnv)}{pnv_star}</td>
          <td>{fmt_clp(r.get('com_pnv'))}</td>
          <td>{fmt_clp(r.get('bono_4pct'))}</td>
          <td>{maq_txt}{maq_star}</td>
          <td>{fmt_clp(r.get('com_maquinas'))}</td>
          <td class='{cls_ef}'>{fmt_pct(pct_ef)}</td>
          <td>{fmt_num(r.get('cartera_clientes'))}</td>
          <td>{fmt_clp(r.get('com_efectividad'))}</td>
          <td><strong>{fmt_clp(r.get('total_comision'))}</strong></td>
          <td>{fmt_clp(r.get('semana_corrida'))}</td>
          <td>{fmt_clp(r.get('bono_reposicion'))}</td>
          <td><strong>{fmt_clp(r.get('total_a_pagar'))}</strong></td>
        </tr>"""

    # Fila de totales
    rows += f"""<tr class='total-row'>
      <td style='text-align:left'>TOTAL</td><td></td>
      <td>{fmt_clp(df['obj_venta'].sum())}</td>
      <td>{fmt_clp(df['fact_nc'].sum())}</td><td></td>
      <td>{fmt_clp(df['com_pnv'].sum())}</td>
      <td>{fmt_clp(df['bono_4pct'].sum())}</td><td></td>
      <td>{fmt_clp(df['com_maquinas'].sum())}</td><td></td><td></td>
      <td>{fmt_clp(df['com_efectividad'].sum())}</td>
      <td>{fmt_clp(df['total_comision'].sum())}</td>
      <td>{fmt_clp(df['semana_corrida'].sum())}</td>
      <td>{fmt_clp(df['bono_reposicion'].sum())}</td>
      <td>{fmt_clp(df['total_a_pagar'].sum())}</td>
    </tr>"""

    st.markdown(f"""
    <div class="tabla-container">
    <table class="kreems"><thead><tr>{header}</tr></thead>
    <tbody>{rows}</tbody></table></div>
    """, unsafe_allow_html=True)


def _export_comisiones(df: pd.DataFrame):
    """
    Exporta la tabla de comisiones. Devuelve (disp, colores, disp_csv):
      - disp: versión compacta para el PNG (imagen).
      - disp_csv: versión detallada para el CSV, con el tramo ORIGINAL (real) y el
        FORZADO por gerencia para PNV, Máquinas y Efectividad (para ver cuánto se
        forzó). La comisión ya se calcula sobre el forzado cuando aplica.
    """
    plan_lbl = {1: "Normal", 2: "Macarena"}
    d = df.sort_values("total_a_pagar", ascending=False, na_position="last")

    def _ov(x):  # override: número o None
        return float(x) if pd.notna(x) else None

    filas, filas_csv, colores = [], [], {}
    for i, (_, r) in enumerate(d.iterrows()):
        pct_pnv, pct_ef = r.get("logro_pnv"), r.get("logro_efectividad")
        plan = plan_lbl.get(int(r["plan_id"]) if pd.notna(r.get("plan_id")) else 1, "Normal")
        filas.append({
            "Vendedor": r["nombre_canonico"], "Plan": plan,
            "Objetivo": fmt_clp(r.get("obj_venta")),
            "Fact-NC": fmt_clp(r.get("fact_nc")), "%PNV": fmt_pct(pct_pnv),
            "Com PNV": fmt_clp(r.get("com_pnv")), "Bono 4%": fmt_clp(r.get("bono_4pct")),
            "Máq": f"{fmt_num(r.get('maquinas_entregadas'))}/{fmt_num(r.get('obj_maquinas'))}",
            "Com Máq": fmt_clp(r.get("com_maquinas")), "%Efec": fmt_pct(pct_ef),
            "Cartera": fmt_num(r.get("cartera_clientes")), "Com Efec": fmt_clp(r.get("com_efectividad")),
            "Total Com.": fmt_clp(r.get("total_comision")), "Sem.Corr": fmt_clp(r.get("semana_corrida")),
            "Repos": fmt_clp(r.get("bono_reposicion")), "Total Pagar": fmt_clp(r.get("total_a_pagar")),
        })
        h = color_hex(pct_pnv)
        if h:
            colores[(i, "%PNV")] = h
        h = color_hex(pct_ef, ok=0.5, warn=0.3)
        if h:
            colores[(i, "%Efec")] = h

        # CSV detallado: tramo original (real) vs forzado por métrica.
        ov_v = r.get("obj_visitas") or 0
        ef_real = (r.get("n_facturas") / ov_v) if ov_v else None   # N°facturas / obj visitas
        filas_csv.append({
            "Vendedor": r["nombre_canonico"], "Plan": plan,
            "Objetivo": fmt_clp(r.get("obj_venta")), "Fact-NC": fmt_clp(r.get("fact_nc")),
            "%PNV original": fmt_pct(r.get("logro_pnv")),
            "%PNV forzado": fmt_pct(_ov(r.get("pnv_logro_override"))),
            "Com PNV": fmt_clp(r.get("com_pnv")), "Bono 4%": fmt_clp(r.get("bono_4pct")),
            "Máq (entr/obj)": f"{fmt_num(r.get('maquinas_entregadas'))}/{fmt_num(r.get('obj_maquinas'))}",
            "%Máq original": fmt_pct(r.get("logro_maquinas")),
            "%Máq forzado": fmt_pct(_ov(r.get("maq_logro_override"))),
            "Com Máq": fmt_clp(r.get("com_maquinas")),
            "%Efec original": fmt_pct(ef_real),
            "%Efec forzado": fmt_pct(_ov(r.get("efectividad_override"))),
            "Cartera": fmt_num(r.get("cartera_clientes")), "Com Efec": fmt_clp(r.get("com_efectividad")),
            "Total Com.": fmt_clp(r.get("total_comision")), "Sem.Corr": fmt_clp(r.get("semana_corrida")),
            "Repos": fmt_clp(r.get("bono_reposicion")), "Total Pagar": fmt_clp(r.get("total_a_pagar")),
        })

    total = {
        "Vendedor": "TOTAL", "Plan": "", "Objetivo": fmt_clp(df["obj_venta"].sum()),
        "Fact-NC": fmt_clp(df["fact_nc"].sum()),
        "Com PNV": fmt_clp(df["com_pnv"].sum()), "Bono 4%": fmt_clp(df["bono_4pct"].sum()),
        "Com Máq": fmt_clp(df["com_maquinas"].sum()), "Com Efec": fmt_clp(df["com_efectividad"].sum()),
        "Total Com.": fmt_clp(df["total_comision"].sum()), "Sem.Corr": fmt_clp(df["semana_corrida"].sum()),
        "Repos": fmt_clp(df["bono_reposicion"].sum()), "Total Pagar": fmt_clp(df["total_a_pagar"].sum()),
    }
    filas.append({**{"%PNV": "", "Máq": "", "%Efec": "", "Cartera": ""}, **total})
    filas_csv.append(total)

    disp = pd.DataFrame(filas)
    disp_csv = pd.DataFrame(filas_csv).reindex(columns=list(filas_csv[0].keys()))
    return disp, colores, disp_csv


def _safe_int(val, default=0) -> int:
    try:
        v = float(val)
        return default if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return default


def _cartera_rangos(client, plan_id: int) -> list[int]:
    """Retorna los cartera_min disponibles para el plan (orden ascendente)."""
    try:
        df = get_tramos_efectividad(client)
        mins = (df[df["plan_id"] == plan_id]["cartera_min"]
                .dropna().astype(int).unique().tolist())
        return sorted(mins) if mins else [81, 91, 101, 111, 121, 131, 141]
    except Exception:
        return [81, 91, 101, 111, 121, 131, 141]


def _rango_label(mn: int, lista: list[int]) -> str:
    """Ej: cartera_min=81 → 'Rango 9  (81 – 90)'"""
    n   = (mn - 1) // 10 + 1          # 81→9, 91→10, …, 141→15
    idx = lista.index(mn)
    rng = f"{mn} – {lista[idx+1]-1}" if idx < len(lista) - 1 else f"{mn}+"
    return f"Rango {n}  ({rng})"


def _editor_entradas(client, df: pd.DataFrame, anio: int, mes: int):
    """Edita cartera de clientes, salas Ganga, override de efectividad y plan."""
    planes = get_planes_comision(client)
    plan_opts = ({int(p["id"]): p["nombre"] for _, p in planes.iterrows()}
                 if not planes.empty else {1: "Kreems normal", 2: "Nueva escala Macarena"})

    vendedores = df[["vendedor_id", "nombre_canonico", "cartera_clientes",
                     "salas_ganga", "efectividad_override",
                     "pnv_logro_override", "maq_logro_override", "plan_id"]].copy()
    vendedores = vendedores.sort_values("nombre_canonico")

    nombre_sel = st.selectbox(
        "Seleccionar vendedor",
        vendedores["nombre_canonico"].tolist(),
        key="sel_vend_comision",
    )
    fila        = vendedores[vendedores["nombre_canonico"] == nombre_sel].iloc[0]
    vendedor_id = int(fila["vendedor_id"])
    plan_actual = int(fila["plan_id"]) if pd.notna(fila.get("plan_id")) else 1

    # ── Plan + cartera fuera del form → re-renderizan al instante ───────────
    st.markdown(f"**Editando: {nombre_sel}** — {MESES[mes]} {anio}")
    _key = f"ent_{vendedor_id}_{anio}_{mes}"

    top1, top2 = st.columns(2)

    plan_id_sel = top1.selectbox(
        "Plan de comisión",
        options=list(plan_opts.keys()),
        index=list(plan_opts.keys()).index(plan_actual) if plan_actual in plan_opts else 0,
        format_func=lambda i: plan_opts[i],
        help="Solo cambiar si el vendedor usa una escala distinta (ej. Macarena).",
        key=f"{_key}_plan",
    )

    # Rangos según el plan ACTUALMENTE seleccionado (no el guardado en DB)
    rangos = _cartera_rangos(client, plan_id_sel)

    cartera_actual = _safe_int(fila.get("cartera_clientes"))
    _EXACTO = -1
    _BAJO   = 0
    rango_keys   = [_BAJO] + rangos + [_EXACTO]
    rango_labels = {
        _BAJO:   "< 81  (sin comisión de efectividad)",
        _EXACTO: "✏️  Número exacto…",
        **{mn: _rango_label(mn, rangos) for mn in rangos},
    }

    # Selección inicial: rango que contiene cartera_actual
    if cartera_actual <= 0:
        _default_rango = _BAJO
    else:
        matching = [mn for mn in rangos if mn <= cartera_actual]
        _default_rango = max(matching) if matching else _EXACTO

    rango_sel = top2.selectbox(
        "Rango de cartera de clientes",
        options=rango_keys,
        index=rango_keys.index(_default_rango) if _default_rango in rango_keys else len(rango_keys) - 1,
        format_func=lambda k: rango_labels[k],
        help="Define qué fila de la tabla de efectividad se usa (paga desde 81 clientes).",
        key=f"{_key}_rango",
    )

    if rango_sel == _EXACTO:
        cartera = st.number_input(
            "Número exacto de clientes",
            value=cartera_actual if cartera_actual > 0 else 0,
            step=1, min_value=0,
            key=f"{_key}_exacto",
        )
    else:
        cartera = rango_sel  # 0 → sin comisión; mn → límite inferior del rango

    # ── Formulario: solo salas + submit ─────────────────────────────────────
    with st.form("form_comision_entrada", clear_on_submit=False):
        salas = st.number_input(
            "Salas Ganga atendidas",
            value=_safe_int(fila.get("salas_ganga")), step=1, min_value=0,
            help="Bono reposición = $15.000 × salas.",
        )
        submitted = st.form_submit_button("💾 Guardar entrada", type="primary",
                                          use_container_width=True)

    # ── Criterio manual de tramo (fuera del form: los toggles re-renderizan al instante)
    st.markdown("---")
    st.caption(
        "**Criterio manual de tramo** — aplica cuando gerencia quiere usar un % "
        "distinto al calculado para elegir el tramo de la tabla. "
        "El logro real sigue mostrándose con ★ en la tabla principal."
    )
    oc1, oc2, oc3 = st.columns(3)

    _key = f"ov_{vendedor_id}_{anio}_{mes}"
    usar_pnv_ov  = oc1.checkbox("Forzar tramo PNV",
                                value=pd.notna(fila.get("pnv_logro_override")),
                                key=f"{_key}_pnv_chk",
                                help="Ej: logro real 104% → forzar tramo 100%.")
    pnv_ov_val   = oc1.number_input(
        "% tramo PNV",
        value=float(fila["pnv_logro_override"]) * 100 if pd.notna(fila.get("pnv_logro_override")) else 100.0,
        step=5.0, min_value=80.0, max_value=110.0,
        disabled=not usar_pnv_ov,
        key=f"{_key}_pnv_val", label_visibility="collapsed",
    )

    usar_maq_ov  = oc2.checkbox("Forzar tramo Máquinas",
                                value=pd.notna(fila.get("maq_logro_override")),
                                key=f"{_key}_maq_chk",
                                help="Ej: forzar tramo 80% aunque el logro real sea mayor.")
    maq_ov_val   = oc2.number_input(
        "% tramo Máq",
        value=float(fila["maq_logro_override"]) * 100 if pd.notna(fila.get("maq_logro_override")) else 100.0,
        step=5.0, min_value=40.0, max_value=140.0,
        disabled=not usar_maq_ov,
        key=f"{_key}_maq_val", label_visibility="collapsed",
    )

    usar_efect_ov = oc3.checkbox("Forzar % efectividad",
                                 value=pd.notna(fila.get("efectividad_override")),
                                 key=f"{_key}_efect_chk",
                                 help="Ej: aplicar 30% aunque el cálculo dé 22%.")
    efect_ov_val  = oc3.number_input(
        "% efectividad",
        value=float(fila["efectividad_override"]) * 100 if pd.notna(fila.get("efectividad_override")) else 30.0,
        step=10.0, min_value=0.0, max_value=100.0,
        disabled=not usar_efect_ov,
        key=f"{_key}_efect_val", label_visibility="collapsed",
    )

    if submitted:
        try:
            pnv_ov   = round(pnv_ov_val  / 100, 4) if usar_pnv_ov  else None
            maq_ov   = round(maq_ov_val  / 100, 4) if usar_maq_ov  else None
            efect_ov = round(efect_ov_val / 100, 4) if usar_efect_ov else None
            upsert_comision_entrada(client, vendedor_id, anio, mes,
                                    cartera, salas,
                                    efectividad_override=efect_ov,
                                    pnv_logro_override=pnv_ov,
                                    maq_logro_override=maq_ov)
            if plan_id_sel != plan_actual:
                update_vendedor_plan(client, vendedor_id, plan_id_sel)
            st.success(f"✅ Entrada de **{nombre_sel}** guardada.")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")


def _cierre_y_export(client, df: pd.DataFrame, anio: int, mes: int, snapshot: pd.DataFrame):
    from app.export_comisiones import comisiones_a_excel, comisiones_a_pdf, REPORTLAB_OK

    c1, c2, c3 = st.columns([1.2, 1, 1])

    with c1:
        st.markdown("**Cerrar mes** (congela el cálculo en el historial)")
        if st.button("🔒 Cerrar y congelar período", use_container_width=True):
            try:
                n = cerrar_mes_comisiones(client, anio, mes)
                st.success(f"✅ {n} vendedores congelados para {MESES[mes]} {anio}.")
                st.rerun()
            except Exception as e:
                st.error(f"Error al cerrar: {e}")

    detalle = get_ventas_detalle_doc(client, anio, mes)

    with c2:
        st.markdown("**Exportar Excel**")
        xlsx = comisiones_a_excel(df, anio, mes, detalle)
        st.download_button(
            "⬇️ Excel consolidado", data=xlsx,
            file_name=f"comisiones_{anio}_{mes:02d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with c3:
        st.markdown("**Exportar PDF (Anexo)**")
        nombres = df.sort_values("nombre_canonico")["nombre_canonico"].tolist()
        sel = st.selectbox("Vendedor (o todos)", ["— Todos —"] + nombres,
                           key="sel_pdf_comision", label_visibility="collapsed")
        if not REPORTLAB_OK:
            st.caption("Instala `reportlab` para habilitar el PDF.")
        else:
            sub = df if sel == "— Todos —" else df[df["nombre_canonico"] == sel]
            pdf = comisiones_a_pdf(sub, anio, mes, detalle)
            suf = "todos" if sel == "— Todos —" else sel.split()[0].lower()
            st.download_button(
                "⬇️ Anexo PDF", data=pdf,
                file_name=f"anexo_comisiones_{anio}_{mes:02d}_{suf}.pdf",
                mime="application/pdf", use_container_width=True,
            )


# ── Pestaña: edición de escalas y parámetros ────────────────────────────────

def _render_escalas(client):
    st.markdown(
        '<div class="estado-vacio" style="margin-bottom:.75rem">'
        'Editas las <strong>tablas maestras</strong> de comisión. Los cambios afectan '
        'el cálculo en vivo de <em>todos</em> los meses no cerrados. Los meses ya '
        'cerrados (snapshot) no se recalculan.</div>',
        unsafe_allow_html=True,
    )

    # ── Parámetros globales ──────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Parámetros</div>', unsafe_allow_html=True)
    par = get_parametros(client)
    if par.empty:
        st.info("Sin parámetros cargados.")
    else:
        par_disp = par[["clave", "valor", "descripcion"]].copy()
        par_disp["valor"] = pd.to_numeric(par_disp["valor"], errors="coerce")
        edited = st.data_editor(
            par_disp, key="ed_param", use_container_width=True, hide_index=True,
            disabled=["clave"],
            column_config={
                "clave": st.column_config.TextColumn("Clave"),
                "valor": st.column_config.NumberColumn("Valor", format="%.4f"),
                "descripcion": st.column_config.TextColumn("Descripción", width="large"),
            },
        )
        if st.button("💾 Guardar parámetros", key="save_param"):
            try:
                regs = [{"clave": r["clave"], "valor": float(r["valor"]),
                         "descripcion": r.get("descripcion")}
                        for _, r in edited.iterrows() if pd.notna(r["valor"])]
                upsert_parametros(client, regs)
                st.success("✅ Parámetros guardados.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
        st.caption("bono_pct = 4% (0.04) · bono_umbral = 110% (1.10) · "
                   "reposicion_monto = $ por sala Ganga.")

    # ── Escalas por plan ─────────────────────────────────────────────────────
    st.markdown('<div class="seccion-titulo">Escalas de comisión</div>',
                unsafe_allow_html=True)
    planes = get_planes_comision(client)
    if planes.empty:
        st.info("Sin planes de comisión.")
        return
    plan_opts = {int(p["id"]): p["nombre"] for _, p in planes.iterrows()}
    plan_id = st.selectbox("Plan", options=list(plan_opts.keys()),
                           format_func=lambda i: plan_opts[i], key="cfg_plan")

    _editor_tramo_1col(client, "comision_tramo_pnv", get_tramos_pnv, plan_id,
                       "PNV — % logro → monto", "pnv")
    _editor_tramo_1col(client, "comision_tramo_maquinas", get_tramos_maquinas, plan_id,
                       "Máquinas — % logro → monto", "maq")
    _editor_efectividad(client, plan_id)


def _editor_tramo_1col(client, tabla, getter, plan_id, titulo, key):
    """Editor para tablas (plan_id, logro_pct, monto): PNV y Máquinas."""
    st.markdown(f"**{titulo}** — {key.upper()}")
    df = getter(client)
    df = df[df["plan_id"] == plan_id] if not df.empty else df
    disp = pd.DataFrame({
        "Logro %": pd.to_numeric(df["logro_pct"], errors="coerce") * 100 if not df.empty else [],
        "Monto $": pd.to_numeric(df["monto"], errors="coerce") if not df.empty else [],
    })
    edited = st.data_editor(
        disp, key=f"ed_{key}_{plan_id}", num_rows="dynamic",
        use_container_width=True, hide_index=True,
        column_config={
            "Logro %": st.column_config.NumberColumn("Logro %", step=5, format="%d"),
            "Monto $": st.column_config.NumberColumn("Monto $", step=1000, format="%.2f"),
        },
    )
    if st.button(f"💾 Guardar {titulo.split(' —')[0]} ({plan_opts_label(plan_id)})",
                 key=f"save_{key}_{plan_id}"):
        try:
            regs = []
            for _, r in edited.iterrows():
                if pd.isna(r["Logro %"]) or pd.isna(r["Monto $"]):
                    continue
                regs.append({"plan_id": int(plan_id),
                             "logro_pct": round(float(r["Logro %"]) / 100, 4),
                             "monto": float(r["Monto $"])})
            replace_tramos_plan(client, tabla, plan_id, regs)
            st.success(f"✅ {titulo.split(' —')[0]} actualizado ({len(regs)} tramos).")
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")


def _editor_efectividad(client, plan_id):
    """Editor para la matriz 2D (plan_id, cartera_min, efectividad_pct, monto)."""
    st.markdown("**Efectividad — rango de cartera × % efectividad → monto**")
    df = get_tramos_efectividad(client)
    df = df[df["plan_id"] == plan_id] if not df.empty else df
    disp = pd.DataFrame({
        "Cartera desde": pd.to_numeric(df["cartera_min"], errors="coerce") if not df.empty else [],
        "Efectividad %": pd.to_numeric(df["efectividad_pct"], errors="coerce") * 100 if not df.empty else [],
        "Monto $": pd.to_numeric(df["monto"], errors="coerce") if not df.empty else [],
    })
    edited = st.data_editor(
        disp, key=f"ed_efec_{plan_id}", num_rows="dynamic",
        use_container_width=True, hide_index=True,
        column_config={
            "Cartera desde": st.column_config.NumberColumn("Cartera desde (n° clientes)", step=10, format="%d"),
            "Efectividad %": st.column_config.NumberColumn("Efectividad %", step=10, format="%d"),
            "Monto $": st.column_config.NumberColumn("Monto $", step=1000, format="%.2f"),
        },
    )
    if st.button(f"💾 Guardar Efectividad ({plan_opts_label(plan_id)})",
                 key=f"save_efec_{plan_id}"):
        try:
            regs = []
            for _, r in edited.iterrows():
                if pd.isna(r["Cartera desde"]) or pd.isna(r["Efectividad %"]) or pd.isna(r["Monto $"]):
                    continue
                regs.append({"plan_id": int(plan_id),
                             "cartera_min": int(r["Cartera desde"]),
                             "efectividad_pct": round(float(r["Efectividad %"]) / 100, 4),
                             "monto": float(r["Monto $"])})
            replace_tramos_plan(client, "comision_tramo_efectividad", plan_id, regs)
            st.success(f"✅ Efectividad actualizada ({len(regs)} celdas).")
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")


def plan_opts_label(plan_id):
    return {1: "Normal", 2: "Macarena"}.get(int(plan_id), f"Plan {plan_id}")
