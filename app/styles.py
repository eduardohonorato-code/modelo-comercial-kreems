"""CSS global de la app — mobile-first."""
import base64
import functools
from pathlib import Path

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "kreems_logo.png"


@functools.lru_cache(maxsize=1)
def logo_data_uri() -> str:
    """Devuelve el logo Kreems como data-URI base64 (para embeber en HTML).

    Cacheado: el PNG se lee una sola vez por proceso. Si el archivo no
    existe, devuelve cadena vacía y quien lo use debe degradar a texto.
    """
    try:
        data = LOGO_PATH.read_bytes()
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


def logo_img(css_class: str = "brand-logo", alt: str = "Kreems") -> str:
    """Etiqueta <img> con el logo embebido. Cadena vacía si no hay logo."""
    uri = logo_data_uri()
    if not uri:
        return ""
    return f'<img class="{css_class}" src="{uri}" alt="{alt}">'


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

/* ── Variables ── */
:root {
  /* Marca Kreems */
  --rosa:       #E62984;   /* magenta de marca (acento, valores KPI, activo) */
  --rosa-deep:  #C01E6E;   /* magenta profundo (fondos con texto blanco, títulos) */
  --rosa-dark:  #9E175A;   /* aún más profundo (gradientes) */
  --rosa-50:    #FDEAF3;   /* tinte muy claro (hover, filas, insight) */
  --rosa-100:   #FBDCEC;   /* tinte claro (hover profundo, total row) */
  --rosa-grad:  linear-gradient(135deg, #E62984 0%, #B81E6B 100%);

  /* Alias retro-compatibles: el código existente usa var(--azul) como acento */
  --azul:       var(--rosa-deep);
  --azul-light: var(--rosa);

  --verde:     #1A7F4B;
  --rojo:      #C0392B;
  --amarillo:  #D4881E;
  --gris:      #6B7280;
  --gris-light:#E5E7EB;
  --bg-card:   #FFFFFF;
  --bg-page:   #FBF7FA;
  --bg-zebra:  #FEFAFC;
  --sombra:    0 2px 8px rgba(34,12,24,.08);
  --sombra-hover: 0 8px 24px rgba(192,30,110,.16);
}

/* ── Tipografía base ── */
html, body, [class*="css"] {
  font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
}

/* ── Ocultar navegación automática de Streamlit multi-page ── */
[data-testid="stSidebarNav"] { display: none !important; }

/* ── Sidebar más angosto ── */
[data-testid="stSidebar"] > div:first-child { width: 200px !important; }
section[data-testid="stSidebarContent"] { width: 200px !important; }

/* ── Layout general ── */
.block-container {
  padding-top: 3.5rem !important;
  padding-bottom: 2rem !important;
  padding-left: 1.5rem !important;
  padding-right: 1.5rem !important;
  max-width: 100% !important;
}

/* ── Título de página (h2 generado con st.markdown) ── */
h2 { font-size: 1.25rem !important; font-weight: 700 !important;
     color: var(--azul) !important; margin-bottom: .5rem !important; }

/* ── Sidebar brand ── */
.sidebar-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: .35rem 0 .85rem;
}
.sidebar-brand img.brand-logo {
  width: 132px;
  max-width: 80%;
  height: auto;
  display: block;
}
/* Logo embebido en cabeceras de fondo rosado → versión sobre oscuro */
.brand-logo-on-dark { filter: brightness(0) invert(1); }

/* ── Sidebar nav: etiqueta de sección ── */
.nav-section-label {
  font-size: .62rem;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--gris);
  padding: .6rem .1rem .15rem;
  margin: 0;
}

/* ── Sidebar nav: botones (inactivo) ── */
[data-testid="stSidebar"] [data-testid="baseButton-secondary"] {
  background: transparent !important;
  border: none !important;
  color: #374151 !important;
  font-weight: 500 !important;
  font-size: .875rem !important;
  text-align: left !important;
  justify-content: flex-start !important;
  padding: .5rem .75rem !important;
  border-radius: 8px !important;
  box-shadow: none !important;
  margin-bottom: 2px !important;
}
[data-testid="stSidebar"] [data-testid="baseButton-secondary"]:hover {
  background: var(--rosa-50) !important;
  color: var(--rosa-deep) !important;
}

/* ── Sidebar nav: botón activo (primary) ── */
[data-testid="stSidebar"] [data-testid="baseButton-primary"] {
  background: var(--rosa-50) !important;
  border: none !important;
  border-left: 3px solid var(--rosa) !important;
  color: var(--rosa-deep) !important;
  font-weight: 700 !important;
  font-size: .875rem !important;
  text-align: left !important;
  justify-content: flex-start !important;
  padding: .5rem .75rem !important;
  border-radius: 0 8px 8px 0 !important;
  box-shadow: none !important;
  margin-bottom: 2px !important;
}
[data-testid="stSidebar"] [data-testid="baseButton-primary"]:hover {
  background: var(--rosa-100) !important;
}

/* ── Sidebar: info de usuario con avatar ── */
.user-info {
  display: flex;
  align-items: center;
  gap: .65rem;
  padding: .4rem 0 .6rem;
}
.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--rosa-grad);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: .78rem;
  flex-shrink: 0;
}
.user-name {
  font-weight: 600;
  font-size: .8rem;
  color: #1A1A2E;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 130px;
  line-height: 1.2;
}

/* ── Inicio: acceso rápido ── */
.acceso-card {
  background: white;
  border-radius: 14px;
  padding: 1.25rem 1.1rem 1.05rem;
  box-shadow: var(--sombra);
  border-top: 4px solid var(--rosa);
  height: 100%;
  margin-bottom: .5rem;
  transition: transform .16s ease, box-shadow .16s ease;
}
.acceso-card:hover { transform: translateY(-3px); box-shadow: var(--sombra-hover); }
.acceso-card-icon {
  font-size: 1.55rem; margin-bottom: .5rem; line-height: 1;
  width: 44px; height: 44px; border-radius: 11px;
  display: flex; align-items: center; justify-content: center;
  background: var(--rosa-50);
}
.acceso-card-title {
  font-size: .95rem;
  font-weight: 700;
  color: var(--rosa-deep);
  margin-bottom: .3rem;
}
.acceso-card-desc { font-size: .78rem; color: var(--gris); line-height: 1.4; }

/* ── Badge de rol ── */
.badge {
  display: inline-block;
  padding: .18rem .65rem;
  border-radius: 999px;
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .05em;
  text-transform: uppercase;
}
.badge-gerencia { background: var(--azul);  color: white; }
.badge-vendedor { background: var(--verde); color: white; }

/* ── Tarjetas KPI ── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: .75rem;
  margin-bottom: 1.25rem;
}
/* Grilla fija de 3 columnas (para 6 KPIs → 2×3 simétrico) */
.kpi-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: .75rem;
  margin-bottom: 1.25rem;
}
@media (max-width: 600px) {
  .kpi-grid-3 { grid-template-columns: repeat(2, 1fr); }
}
.kpi-card {
  background: var(--bg-card);
  border-radius: 12px;
  box-shadow: var(--sombra);
  padding: .9rem 1rem .85rem;
  text-align: center;
  border-top: 3px solid var(--gris-light);
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: var(--sombra-hover); }
/* Variante destacada: % Cumplimiento */
.kpi-card.destacado {
  border-top: 3px solid var(--rosa);
  background: linear-gradient(180deg, var(--rosa-50) 0%, #FFFFFF 60%);
  grid-column: span 2;
}
@media (max-width: 480px) {
  /* En móvil muy angosto destacado sigue ocupando 2 cols si caben */
  .kpi-grid { grid-template-columns: repeat(2, 1fr); gap: .5rem; }
  .kpi-card.destacado { grid-column: span 2; }
}

.kpi-label {
  font-size: .7rem;
  color: var(--gris);
  font-weight: 600;
  letter-spacing: .045em;
  text-transform: uppercase;
  margin-bottom: .22rem;
}
.kpi-value {
  font-size: 1.55rem;
  font-weight: 700;
  color: var(--azul);
  line-height: 1.1;
  letter-spacing: -.02em;
}
.kpi-card.destacado .kpi-value { font-size: 1.85rem; }
.kpi-value.verde    { color: var(--verde); }
.kpi-value.rojo     { color: var(--rojo); }
.kpi-value.amarillo { color: var(--amarillo); }
.kpi-sub {
  font-size: .68rem;
  color: var(--gris);
  margin-top: .18rem;
  line-height: 1.35;
}

/* ── Sección header ── */
.seccion-titulo {
  font-size: .92rem;
  font-weight: 700;
  color: var(--azul);
  border-left: 4px solid var(--azul);
  padding-left: .55rem;
  margin: 1.3rem 0 .65rem;
  line-height: 1.2;
}

/* ── Strip de contexto (días, última factura) ── */
.kpi-strip {
  display: flex;
  gap: .4rem;
  margin-bottom: .85rem;
  flex-wrap: wrap;
  align-items: center;
}
.kpi-strip-card {
  background: var(--azul);
  color: white;
  border-radius: 6px;
  padding: .28rem .75rem;
  display: flex;
  align-items: baseline;
  gap: .45rem;
}
.kpi-strip-value {
  font-size: .95rem;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -.01em;
}
.kpi-strip-label {
  font-size: .6rem;
  font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
  opacity: .8;
}

/* ── Tabla resumen ── */
.tabla-container {
  overflow-x: auto;
  border-radius: 10px;
  box-shadow: var(--sombra);
  -webkit-overflow-scrolling: touch;
}
table.kreems {
  width: 100%;
  border-collapse: collapse;
  font-size: .76rem;
  background: white;
}
table.kreems th {
  background: var(--azul);
  color: white;
  padding: .45rem .55rem;
  text-align: right;
  white-space: nowrap;
  font-weight: 600;
  font-size: .7rem;
  letter-spacing: .02em;
  cursor: default;
}
table.kreems th:first-child {
  text-align: left;
  position: sticky;
  left: 0;
  z-index: 2;
  background: var(--azul);
}
table.kreems td {
  padding: .38rem .55rem;
  border-bottom: 1px solid #F0F0F5;
  text-align: right;
  white-space: nowrap;
}
table.kreems td:first-child {
  text-align: left;
  font-weight: 600;
  position: sticky;
  left: 0;
  z-index: 1;
  background: white;
}
/* Zebra */
table.kreems tbody tr:nth-child(even) td        { background: var(--bg-zebra); }
table.kreems tbody tr:nth-child(even) td:first-child { background: var(--bg-zebra); }
/* Hover (sobreescribe zebra) */
table.kreems tbody tr:hover td { background: var(--rosa-50); }
table.kreems tbody tr:hover td:first-child { background: var(--rosa-50); }
/* Fila TOTAL */
table.kreems tr.total-row td {
  background: var(--rosa-100) !important;
  font-weight: 700;
  border-top: 2px solid var(--rosa);
}
table.kreems tr.total-row td:first-child { background: var(--rosa-100) !important; }

.verde-bg   { color: var(--verde);    font-weight: 700; }
.rojo-bg    { color: var(--rojo);     font-weight: 700; }
.amarillo-bg { color: var(--amarillo); font-weight: 700; }

/* ── Estado vacío ── */
.estado-vacio {
  text-align: center;
  padding: 1.5rem 1rem;
  color: var(--gris);
  font-size: .85rem;
  border: 1.5px dashed var(--gris-light);
  border-radius: 10px;
  background: var(--bg-page);
}

/* ── Nota explicativa (embudo Pedidos → Fact-NC) ── */
.nota-embudo {
  background: var(--rosa-50);
  border-left: 4px solid var(--rosa);
  border-radius: 8px;
  padding: .75rem 1rem .75rem 1.1rem;
  margin: .4rem 0 1rem;
  font-size: .78rem;
  color: #2A3340;
  line-height: 1.5;
}
.nota-embudo strong { color: var(--azul); }
.nota-embudo ul { margin: .45rem 0 0; padding-left: 1.1rem; }
.nota-embudo li { margin-bottom: .3rem; }
.nota-embudo li:last-child { margin-bottom: 0; }

/* ── Login card ── */
.login-wrap { max-width: 380px; margin: 3rem auto; }
.login-card {
  background: white;
  border-radius: 14px;
  padding: 2rem 2rem 1.5rem;
  box-shadow: 0 4px 24px rgba(0,0,0,.1);
}
.login-logo { text-align: center; margin-bottom: 1.5rem; }
.login-logo img { width: 168px; max-width: 70%; height: auto; margin: 0 auto .25rem; display: block; }
.login-logo h1 { color: var(--rosa-deep); font-size: 1.6rem; margin: 0; font-weight: 700; }
.login-logo p  { color: var(--gris); font-size: .85rem; margin: .35rem 0 0; }
.login-card { border-top: 4px solid var(--rosa); }

/* ── Dashboard header (S1) ── */
.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--rosa-grad);
  color: white;
  border-radius: 14px;
  padding: .95rem 1.35rem;
  margin-bottom: 1.1rem;
  flex-wrap: wrap;
  gap: .5rem;
  box-shadow: 0 6px 20px rgba(192,30,110,.22);
}
.dash-header-left  { display: flex; align-items: center; gap: .85rem; }
.dash-header-logo  {
  flex-shrink: 0;
  background: #fff;
  border-radius: 10px;
  padding: .4rem .65rem;
  display: inline-flex;
  align-items: center;
  box-shadow: 0 2px 8px rgba(0,0,0,.14);
}
.dash-header-logo img {
  height: 26px; width: auto; display: block;
}
.dash-header-title { font-size: 1.05rem; font-weight: 700; line-height: 1.2; letter-spacing: -.01em; }
.dash-header-sub   { font-size: .75rem; opacity: .78; margin-top: .1rem; }
.dash-header-right { display: flex; align-items: center; }
.dash-header-update { font-size: .72rem; opacity: .8; white-space: nowrap; }

/* ── KPI Cards con ícono (S2) ── */
.kpi-6-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: .7rem;
  margin-bottom: 1.1rem;
}
@media (max-width: 1100px) { .kpi-6-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 400px) { .kpi-6-grid { grid-template-columns: 1fr; } }

.kpi-icon-card {
  background: var(--bg-card);
  border-radius: 10px;
  box-shadow: var(--sombra);
  padding: .8rem 1rem .8rem .85rem;
  display: flex;
  align-items: flex-start;
  gap: .7rem;
  border-left: 4px solid var(--azul);
  transition: box-shadow .15s;
}
.kpi-icon-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.11); }
.kic-icon  { font-size: 1.5rem; line-height: 1; flex-shrink: 0; margin-top: .1rem; }
.kic-body  { flex: 1; min-width: 0; }
.kic-label {
  font-size: .64rem; font-weight: 600; letter-spacing: .055em;
  text-transform: uppercase; color: var(--gris); margin-bottom: .18rem;
}
.kic-value {
  font-size: 1.28rem; font-weight: 700; color: var(--azul);
  letter-spacing: -.02em; line-height: 1.15;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.kic-sub   { font-size: .64rem; color: var(--gris); margin-top: .14rem; line-height: 1.3; }
.kic-delta { font-size: .66rem; font-weight: 600; margin-top: .14rem; }
.kic-delta.verde   { color: var(--verde); }
.kic-delta.rojo    { color: var(--rojo); }
.kic-delta.gris    { color: var(--gris); }
.kic-value.verde   { color: var(--verde); }
.kic-value.rojo    { color: var(--rojo); }
.kic-value.amarillo{ color: var(--amarillo); }

/* ── Semáforo dot (S3 tabla) ── */
.semaforo-dot {
  display: inline-block; width: 8px; height: 8px;
  border-radius: 50%; margin-right: 5px; vertical-align: middle; flex-shrink: 0;
}
.semaforo-dot.verde   { background: var(--verde); }
.semaforo-dot.amarillo{ background: var(--amarillo); }
.semaforo-dot.rojo    { background: var(--rojo); }

/* ── Top/Risk list (S5) ── */
.top-list { list-style: none; padding: 0; margin: 0; }
.top-list li {
  display: flex; align-items: center; gap: .5rem;
  padding: .38rem .1rem;
  border-bottom: 1px solid var(--gris-light);
  font-size: .78rem;
}
.top-list li:last-child { border-bottom: none; }
.top-rank { font-weight: 700; color: var(--azul); min-width: 1.4rem; }
.top-name { flex: 1; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top-pct  { font-weight: 700; font-size: .75rem; }
.top-pct.verde   { color: var(--verde); }
.top-pct.amarillo{ color: var(--amarillo); }
.top-pct.rojo    { color: var(--rojo); }

/* ── Insight bullets (S5) ── */
.insight-card {
  background: var(--rosa-50);
  border-radius: 12px;
  padding: .9rem 1.05rem;
  height: 100%;
  border: 1px solid var(--rosa-100);
}
.insight-title { font-size: .72rem; font-weight: 700; color: var(--azul);
                 text-transform: uppercase; letter-spacing: .05em; margin-bottom: .55rem; }
.insight-item  { display: flex; gap: .5rem; margin-bottom: .42rem;
                 font-size: .76rem; color: #374151; line-height: 1.4; align-items: flex-start; }
.insight-bullet{ flex-shrink: 0; font-size: .78rem; margin-top: .05rem; }

/* ── Proyección table (S5) ── */
.proy-table { width: 100%; border-collapse: collapse; font-size: .77rem; }
.proy-table td { padding: .42rem .5rem; border-bottom: 1px solid var(--gris-light); }
.proy-table td:last-child { text-align: right; font-weight: 600; }
.proy-table tr:last-child td { border-bottom: none; font-weight: 700; }
.proy-row-header td { background: var(--rosa-50); font-weight: 700;
                      color: var(--azul); font-size: .7rem;
                      text-transform: uppercase; letter-spacing: .04em; }

/* ── Mobile general ── */
@media (max-width: 600px) {
  .kpi-value { font-size: 1.25rem; }
  .kpi-card.destacado .kpi-value { font-size: 1.45rem; }
  table.kreems { font-size: .73rem; }
  table.kreems th, table.kreems td { padding: .4rem .5rem; }
  .seccion-titulo { font-size: .85rem; }
  .kic-value { font-size: 1.1rem; }
  .dash-header-title { font-size: .92rem; }
}
</style>
"""


def color_pct(valor, umbral_ok=1.0, umbral_warn=0.7):
    """Devuelve clase CSS según el porcentaje."""
    if valor is None:
        return ""
    if valor >= umbral_ok:
        return "verde-bg"
    if valor >= umbral_warn:
        return "amarillo-bg"
    return "rojo-bg"


def fmt_clp(n, prefix="$"):
    """Formatea número como peso chileno: $1.234.567"""
    if n is None:
        return "—"
    try:
        return f"{prefix}{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "—"


def fmt_pct(n):
    """Formatea fracción como porcentaje: 0.85 → 85%"""
    if n is None:
        return "—"
    try:
        v = float(n)
        import math
        if math.isnan(v) or math.isinf(v):
            return "—"
        return f"{v*100:.1f}%"
    except Exception:
        return "—"


def fmt_num(n):
    """Formatea entero con separador de miles."""
    if n is None:
        return "—"
    try:
        return f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "—"
