"""
ETL principal — Kreems Sistema Comercial.

Uso:
    python -m etl.run_etl
    python etl/run_etl.py

Prerrequisitos:
    1. .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.
    2. Fase 1 (001_modelo_datos.sql) ya ejecutada en Supabase.
    3. Archivos de muestra en /data/muestras/ (ver etl/config.py para los patrones).
    4. Al menos un vendedor en dim_vendedor (el seed del 001 crea los de prueba;
       los reales deben registrarse antes de la primera carga o quedarán sin mapear).

Idempotencia:
    Re-ejecutar el script con los mismos archivos produce el mismo resultado en
    Supabase sin duplicados. Cargar un mes, varios o el histórico completo da
    siempre el mismo estado final.
"""
import argparse
import logging
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Permite ejecutar como script directo además de como módulo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from etl.db import get_client
from etl.config import DATA_DIR, FILE_MATCH, MENSUAL_DIR, FUENTES
from etl.cleaners import construir_mapeo_vendedor
from etl.upsert import upsert_tabla
from etl.maquinas import (derivar_maquinas_obuma, aplicar_estado_despachos,
                          aplicar_override_vendedor, marcar_despachos_maquina)
from etl.loaders.obuma import cargar_obuma_multi
from etl.loaders.autoventa import cargar_autoventa

# -- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
        logging.FileHandler(
            Path(__file__).parent.parent / "etl_run.log", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


# -- Helpers ------------------------------------------------------------------─

def _normalizar_nombre_archivo(nombre: str) -> str:
    """Sin acentos y en MAYÚSCULAS, para comparar 'Acuña' == 'ACUNA' == 'acuna'."""
    nfkd = unicodedata.normalize("NFKD", nombre)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _candidatos(clave: str) -> list[Path]:
    """Archivos cuyo nombre normalizado cumple las reglas incluye/excluye/ext."""
    regla = FILE_MATCH[clave]
    out = []
    for p in DATA_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != regla["ext"]:
            continue
        nombre = _normalizar_nombre_archivo(p.name)
        if all(k in nombre for k in regla["incluye"]) and not any(
            k in nombre for k in regla["excluye"]
        ):
            out.append(p)
    return out


def _buscar_mensual(clave: str, periodo: tuple) -> Path | None:
    """
    Esquema vigente: data/mensual/<fuente>/<...AAAA-MM...>.<ext>.
    Una carpeta por fuente; el archivo del mes se reconoce por el token AAAA-MM
    en el nombre (tolera separador '-' o '_'). Devuelve None si la fuente no
    tiene archivo para ese período (se omite esa fuente, sin error).
    """
    anio, mes = periodo
    carpeta = MENSUAL_DIR / FUENTES[clave]["carpeta"]
    ext = FUENTES[clave]["ext"]
    if not carpeta.is_dir():
        logger.info("  [%s] sin carpeta %s — fuente omitida.", clave, carpeta.name)
        return None
    tokens = (f"{anio:04d}-{mes:02d}", f"{anio:04d}_{mes:02d}")
    candidatos = [
        p for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() == ext
        and any(t in p.stem for t in tokens)
    ]
    if not candidatos:
        logger.info("  [%s] sin archivo de %d-%02d en %s/ — fuente omitida.",
                    clave, anio, mes, carpeta.name)
        return None
    if len(candidatos) > 1:
        logger.error("[%s] varios archivos para %d-%02d en %s/: %s. "
                     "Deja solo uno. Abortando.",
                     clave, anio, mes, carpeta.name, [c.name for c in candidatos])
        sys.exit(1)
    logger.info("  [%s] usando (mensual): %s/%s",
                clave, carpeta.name, candidatos[0].name)
    return candidatos[0]


def _encontrar_archivo(clave: str, periodo: tuple | None = None) -> Path | None:
    """
    Localiza el archivo de una fuente.
      · Con --periodo → esquema organizado data/mensual/<fuente>/ (sin ambigüedad);
        solo carga lo que esté presente para ese mes.
      · Sin --periodo → fallback legacy a la carpeta plana data/muestras (match por
        palabra clave; el más reciente si hay varios).
    """
    if periodo is not None:
        return _buscar_mensual(clave, periodo)

    candidatos = _candidatos(clave)
    if not candidatos:
        logger.warning("No se encontró ningún archivo para '%s' (regla: %s)",
                       clave, FILE_MATCH[clave])
        return None
    if len(candidatos) == 1:
        logger.info("  [%s] usando: %s", clave, candidatos[0].name)
        return candidatos[0]

    logger.warning("Varios archivos coinciden con '%s': %s",
                   clave, [c.name for c in candidatos])

    ruta = max(candidatos, key=lambda p: p.stat().st_mtime)
    logger.warning("  [%s] sin --periodo: usando el más reciente por fecha de "
                   "archivo: %s (usa --periodo AAAA-MM para elegir sin ambigüedad)",
                   clave, ruta.name)
    return ruta


def _reportar_match(fact_ventas, docs_autoventa: set):
    """
    Reporta el % de match Obuma ↔ Autoventa **por sociedad**.

    Por qué por sociedad: Autoventa solo cubre Gran Natural. Si el export de
    Acuña es de un período distinto (ej. 2025) y el de Autoventa es de 2026,
    el match global es artificialmente bajo. Separar por sociedad da el número
    real: se espera ~80%+ para la sociedad con períodos coincidentes.
    """
    for soc_id, soc_nombre in [(1, "Acuña"), (2, "Gran Natural")]:
        sub = fact_ventas[fact_ventas["sociedad_id"] == soc_id]
        if sub.empty:
            continue
        n_dcto = set(sub["n_dcto"].dropna().astype(str))
        match  = n_dcto & docs_autoventa
        pct    = 100 * len(match) / len(n_dcto) if n_dcto else 0
        nivel  = logging.WARNING if (pct < 70 and soc_id == 2) else logging.INFO
        logger.log(
            nivel,
            "[Cruce %s] N°DCTO Obuma=%d | Autoventa facturados=%d | Match=%d (%.1f%%)%s",
            soc_nombre, len(n_dcto), len(docs_autoventa), len(match), pct,
            " ← periodos distintos, normal" if pct < 5 else
            (" ⚠ bajo — verificar archivos" if pct < 70 else " ✓"),
        )


def _asegurar_vendedor_sin_asignar(client) -> int:
    """
    Garantiza que exista el vendedor 'Sin asignar' y devuelve su id.
    Se usa como bucket para ventas/pedidos sin vendedor en el ERP. No se le liga
    ningún usuario (user_id NULL), así que por RLS solo gerencia/admin lo ven.
    """
    resp = (client.table("dim_vendedor")
            .select("id").eq("nombre_canonico", "Sin asignar").execute())
    if resp.data:
        return resp.data[0]["id"]
    ins = (client.table("dim_vendedor")
           .insert({"nombre_canonico": "Sin asignar", "activo": True}).execute())
    return ins.data[0]["id"]


def _leer_overrides_maquina(client):
    """
    Lee maquina_vendedor_override desde Supabase. Si la tabla aún no existe
    (no se ha corrido el 013), retorna vacío y el ETL sigue sin overrides.
    """
    import pandas as pd
    try:
        r = (client.table("maquina_vendedor_override")
             .select("sociedad_id,documento,vendedor_id").range(0, 9999).execute())
        df = pd.DataFrame(r.data) if r.data else pd.DataFrame(
            columns=["sociedad_id", "documento", "vendedor_id"])
        if not df.empty:
            logger.info("  Overrides de vendedor de máquina cargados: %d", len(df))
        return df
    except Exception as exc:
        logger.warning("  No se pudo leer maquina_vendedor_override (%s). "
                       "Sigo sin overrides.", exc)
        return pd.DataFrame(columns=["sociedad_id", "documento", "vendedor_id"])


def _guardar_log_no_mapeados(log: list):
    """Escribe los vendedores/RUTs no mapeados en un CSV junto al log."""
    if not log:
        logger.info("Sin vendedores no mapeados en esta carga. ✓")
        return
    import csv
    ruta = Path(__file__).parent.parent / "etl_no_mapeados.csv"
    with ruta.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["fuente", "nombre_original"])
        writer.writeheader()
        writer.writerows(log)
    # Mostrar resumen sin duplicados
    unicos = {r["nombre_original"] for r in log}
    logger.warning(
        "Vendedores no mapeados (%d únicos): %s\n  → Detalle en etl_no_mapeados.csv",
        len(unicos), sorted(unicos),
    )


# -- Pipeline principal --------------------------------------------------------

def _obuma_vacio() -> dict:
    """Resultado Obuma vacío (cuando no hay archivos Obuma en esta corrida)."""
    import pandas as pd
    return {
        "dim_cliente":  pd.DataFrame(columns=["cliente_rut", "razon_social",
                                              "comuna", "region", "tipo",
                                              "sociedad_id", "es_maquina"]),
        "dim_producto": pd.DataFrame(columns=["codigo"]),
        "fact_ventas":  pd.DataFrame(columns=["sociedad_id", "n_dcto",
                                              "producto_codigo", "cliente_rut"]),
        "stats":        {"obuma_filas_raw": 0},
    }


def _autoventa_vacio() -> dict:
    """Resultado Autoventa vacío (cuando no hay archivos Autoventa)."""
    import pandas as pd
    return {
        "fact_pedidos":   pd.DataFrame(),
        "fact_despachos": pd.DataFrame(),
        "fact_maquinas":  pd.DataFrame(),
        "dim_cliente":    pd.DataFrame(columns=["cliente_rut"]),
        "stats":          {"pedidos_total": 0},
        "_docs_facturados": set(),
    }


def run(periodo: tuple | None = None):
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("ETL Kreems — inicio %s", inicio.isoformat())
    if periodo:
        logger.info("Período objetivo: %d-%02d (se filtran las ventas Obuma a este mes)",
                    *periodo)
    else:
        logger.info("Sin --periodo: se carga lo que traigan los archivos (cualquier mes).")
    logger.info("=" * 60)

    # 1. Conexión
    client = get_client()
    logger.info("Conexión Supabase OK")

    # 2. Verificar archivos
    path_acuna       = _encontrar_archivo("obuma_acuna", periodo)
    path_grannatural = _encontrar_archivo("obuma_grannatural", periodo)
    path_pedidos     = _encontrar_archivo("autoventa_pedidos")
    path_despachos   = _encontrar_archivo("autoventa_despachos")
    path_objetivos   = _encontrar_archivo("objetivos")  # puede ser None

    # Ya NO se exigen los 4 archivos: GN (ventas) y pedidos vienen por API
    # (run_obuma_api / run_autoventa_api). Aquí se carga SOLO lo que esté presente
    # — típicamente Acuña (Obuma) y/o el detalle de despachos (Autoventa). Se
    # aborta únicamente si no hay NINGÚN archivo que cargar.
    hay_obuma     = bool(path_acuna or path_grannatural)
    hay_autoventa = bool(path_pedidos or path_despachos)
    if not (hay_obuma or hay_autoventa):
        logger.error("No se encontró ningún archivo Obuma ni Autoventa en %s. "
                     "Nada que cargar. Abortando.", DATA_DIR)
        sys.exit(1)
    logger.info("Fuentes a cargar → Obuma: %s | Autoventa: %s",
                "sí" if hay_obuma else "no (omitido)",
                "sí" if hay_autoventa else "no (omitido)")

    # 3. Cargar dim_vendedor para construir el mapeo nombre→id
    logger.info("\n-- Cargando dim_vendedor desde Supabase --")
    resp = client.table("dim_vendedor").select("id, nombre_canonico").execute()
    mapeo_vendedor = construir_mapeo_vendedor(resp.data or [])
    logger.info("  Vendedores en dim_vendedor: %d", len(mapeo_vendedor))

    # Vendedor 'Sin asignar': bucket para ventas que vienen SIN vendedor en el ERP
    # (así suman al total de gerencia y cuadra con Power BI; ningún vendedor real
    #  lo ve porque no tiene user_id ligado).
    fallback_id = _asegurar_vendedor_sin_asignar(client)
    logger.info("  Vendedor 'Sin asignar' id=%s (ventas sin vendedor en el ERP)", fallback_id)

    log_no_mapeados: list = []

    # 4. ETL Obuma (solo los archivos presentes; Acuña y/o GN)
    if hay_obuma:
        logger.info("\n-- Obuma: cargando ventas --")
        obuma_files = []
        if path_acuna:       obuma_files.append((path_acuna, "acuna"))
        if path_grannatural: obuma_files.append((path_grannatural, "grannatural"))
        obuma = cargar_obuma_multi(obuma_files, mapeo_vendedor, log_no_mapeados,
                                   periodo=periodo, fallback_vendedor_id=fallback_id)
    else:
        logger.info("\n-- Obuma: sin archivos, omitido --")
        obuma = _obuma_vacio()

    # 5. ETL Autoventa (pedidos y/o despachos; los ausentes van como None)
    if hay_autoventa:
        logger.info("\n-- Autoventa: cargando pedidos y despachos --")
        autov = cargar_autoventa(path_pedidos, path_despachos, mapeo_vendedor,
                                 log_no_mapeados, fallback_vendedor_id=fallback_id)
    else:
        logger.info("\n-- Autoventa: sin archivos, omitido --")
        autov = _autoventa_vacio()

    # 6. Match Obuma ↔ Autoventa (por sociedad)
    logger.info("\n-- Cruce Obuma ↔ Autoventa --")
    _reportar_match(obuma["fact_ventas"], autov["_docs_facturados"])

    # 7. Upserts — Dimensiones primero (las FK las requieren)
    logger.info("\n-- Upserts a Supabase --")

    # dim_cliente: combinar Obuma + Autoventa, priorizar datos de Obuma
    # renombrar cliente_rut → rut para que coincida con la PK de la tabla
    dim_cliente = (
        obuma["dim_cliente"]
        .set_index("cliente_rut")
        .combine_first(autov["dim_cliente"].set_index("cliente_rut"))
        .reset_index()
        .rename(columns={"cliente_rut": "rut"})
    )
    upsert_tabla(client, "dim_cliente", dim_cliente, on_conflict="rut")

    # dim_producto (solo Obuma tiene datos completos de producto)
    upsert_tabla(client, "dim_producto", obuma["dim_producto"], on_conflict="codigo")

    # Hechos
    upsert_tabla(
        client, "fact_ventas", obuma["fact_ventas"],
        on_conflict="sociedad_id,tipo_dcto,n_dcto,producto_codigo,linea",
    )
    upsert_tabla(
        client, "fact_pedidos", autov["fact_pedidos"],
        on_conflict="sociedad_id,n_pedido,producto_codigo,linea",
    )

    # Máquinas: fuente única = Obuma (categoría 'Maquinas', códigos FL-x), igual
    # que la carga histórica. Se derivan de fact_ventas y se les aplica el estado
    # de entrega cruzando con los despachos de Autoventa. (Antes se usaba
    # autov["fact_maquinas"] de MAQUINAS_POP; quedó obsoleto para unificar ambos
    # flujos.)
    logger.info("\n-- Máquinas (derivadas de Obuma) --")
    fact_maquinas = derivar_maquinas_obuma(obuma["fact_ventas"])
    fact_maquinas = aplicar_estado_despachos(fact_maquinas, autov["fact_despachos"])
    # Override manual de vendedor (tabla maquina_vendedor_override; vacía = sin efecto)
    fact_maquinas = aplicar_override_vendedor(
        fact_maquinas, _leer_overrides_maquina(client))

    # Marcar es_maquina en los despachos según las máquinas (Obuma) antes de subir.
    fact_despachos = marcar_despachos_maquina(autov["fact_despachos"], fact_maquinas)
    upsert_tabla(
        client, "fact_despachos", fact_despachos,
        on_conflict="sociedad_id,documento,cliente_rut",
    )
    if not fact_maquinas.empty:
        upsert_tabla(
            client, "fact_maquinas", fact_maquinas,
            on_conflict="sociedad_id,documento,cliente_rut,tipo_mov",
        )

    # 8. Objetivos (opcional)
    if path_objetivos:
        logger.info("\n-- Objetivos mensuales --")
        _cargar_objetivos(client, path_objetivos, mapeo_vendedor, log_no_mapeados)

    # 9. Log de no mapeados
    logger.info("\n-- Vendedores no mapeados --")
    _guardar_log_no_mapeados(log_no_mapeados)

    # 10. Resumen final
    fin = datetime.now()
    logger.info("\n%s", "=" * 60)
    logger.info("ETL completado en %.1f segundos", (fin - inicio).total_seconds())
    logger.info("Estadísticas Obuma:    %s", obuma["stats"])
    logger.info("Estadísticas Autoventa: %s", autov["stats"])
    logger.info("%s", "=" * 60)


def _cargar_objetivos(client, path: Path, mapeo_vendedor: dict, log_no_mapeados: list):
    """Carga el Excel de objetivos mensuales."""
    import pandas as pd
    from etl.cleaners import normalizar_columnas, limpiar_monto

    try:
        df = pd.read_excel(path, engine="openpyxl")
        df = normalizar_columnas(df)
        logger.info("  Objetivos leídos: %d filas", len(df))

        # Esperamos columnas: Vendedor, Año, Mes, Obj. Venta, Obj. Maquinas, Obj. Visitas
        col_map = {}
        for c in df.columns:
            cu = c.upper().replace(".", "").strip()
            if "VENDEDOR" in cu:            col_map[c] = "vendedor_nombre"
            elif "AÑO" in cu or "ANO" in cu: col_map[c] = "anio"
            elif "MES" in cu:               col_map[c] = "mes"
            elif "VENTA" in cu:             col_map[c] = "obj_venta"
            elif "MAQUINA" in cu:           col_map[c] = "obj_maquinas"
            elif "VISITA" in cu:            col_map[c] = "obj_visitas"
        df = df.rename(columns=col_map)

        df["vendedor_id"] = mapear_vendedor_id(
            df["vendedor_nombre"], mapeo_vendedor, log_no_mapeados, fuente="objetivos"
        )
        df = df.dropna(subset=["vendedor_id", "anio", "mes"])
        for col in ["obj_venta", "obj_maquinas", "obj_visitas"]:
            if col in df.columns:
                df[col] = limpiar_monto(df[col]).fillna(0)

        cols = ["vendedor_id", "anio", "mes", "obj_venta", "obj_maquinas", "obj_visitas"]
        cols_present = [c for c in cols if c in df.columns]
        upsert_tabla(client, "objetivos_mensuales", df[cols_present],
                     on_conflict="vendedor_id,anio,mes")
    except Exception as exc:
        logger.error("  Error cargando objetivos: %s", exc)


def _parse_periodo(valor: str | None) -> tuple | None:
    """'2026-05' → (2026, 5). None si no se pasa."""
    if not valor:
        return None
    try:
        anio, mes = valor.split("-")
        return (int(anio), int(mes))
    except (ValueError, AttributeError):
        raise SystemExit(f"--periodo inválido: '{valor}'. Formato esperado AAAA-MM (ej. 2026-05).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ETL Kreems — carga ventas/pedidos/máquinas a Supabase.")
    ap.add_argument("--periodo", metavar="AAAA-MM",
                    help="Mes a cargar (ej. 2026-05). Filtra las ventas Obuma a ese mes y "
                         "elige el archivo correcto si hay varios. Si se omite, carga todo lo "
                         "que traigan los archivos.")
    args = ap.parse_args()
    run(_parse_periodo(args.periodo))
