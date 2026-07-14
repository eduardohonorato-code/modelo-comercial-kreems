"""
Funciones de limpieza y normalización reutilizables.
Nada de I/O aquí: solo transformaciones puras sobre Series/DataFrames.
"""
import re
import unicodedata
import pandas as pd


# ── RUT chileno ──────────────────────────────────────────────────────────────

def _strip_rut(raw: str) -> str:
    """Elimina puntos y guiones; devuelve solo dígitos+DV en mayúscula."""
    return re.sub(r"[.\-\s]", "", str(raw)).upper().strip()


def normalizar_rut(serie: pd.Series) -> pd.Series:
    """
    Normaliza a formato XX.XXX.XXX-X.
    - Elimina puntos y guiones del input.
    - Agrega puntos de miles y guión antes del DV.
    - Devuelve NaN si el RUT es inválido o vacío.
    """
    def _fmt(raw):
        if pd.isna(raw):
            return pd.NA
        s = _strip_rut(raw)
        if len(s) < 2:
            return pd.NA
        cuerpo, dv = s[:-1], s[-1]
        if not cuerpo.isdigit():
            return pd.NA
        # Formato con puntos de miles
        try:
            cuerpo_fmt = f"{int(cuerpo):,}".replace(",", ".")
        except ValueError:
            return pd.NA
        return f"{cuerpo_fmt}-{dv}"

    return serie.map(_fmt)


# ── Nombres de vendedor ──────────────────────────────────────────────────────

def _normalizar_nombre(nombre: str) -> str:
    """
    Quita acentos, convierte a mayúsculas y colapsa espacios.
    Usado para comparar nombres entre sistemas (Obuma vs Autoventa).
    """
    nfkd = unicodedata.normalize("NFKD", str(nombre))
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos).upper().strip()


def construir_mapeo_vendedor(dim_vendedor_rows: list[dict]) -> dict[str, int]:
    """
    Devuelve {nombre_normalizado: vendedor_id} desde la tabla dim_vendedor.
    Permite hacer lookup tolerante a variaciones de acento/mayúsculas.
    """
    return {
        _normalizar_nombre(r["nombre_canonico"]): r["id"]
        for r in dim_vendedor_rows
        if r.get("nombre_canonico") and r.get("id")
    }


def agregar_alias(mapeo: dict[str, int], alias_rows: list[dict]) -> dict[str, int]:
    """
    Agrega alias (nombre tal como llega del ERP → vendedor_id) al mapeo. Los alias
    SOBREESCRIBEN el match por nombre_canonico: sirve para reemplazos de vendedor
    (ej. Carlos factura bajo el nombre de Diego mientras no está en el ERP).
    `alias_rows` = [{'alias': 'Diego...', 'vendedor_id': N}, ...].
    """
    for a in alias_rows or []:
        alias, vid = a.get("alias"), a.get("vendedor_id")
        if alias and vid:
            mapeo[_normalizar_nombre(alias)] = vid
    return mapeo


def aplicar_reasignacion(df: pd.DataFrame, reglas: list[dict]) -> pd.DataFrame:
    """
    Reasigna vendedor_id POR FECHA sobre un fact DataFrame: las filas de `origen_id`
    con `fecha >= desde` pasan a `destino_id`. Para reemplazos de vendedor que
    facturan bajo el nombre de otro (ej. Carlos factura como Diego desde jul-2026):
    lo de Diego desde julio → Carlos, y lo anterior queda con Diego. Es date-aware
    (a diferencia del alias por nombre), así el histórico no se toca.

    `reglas` = [{'origen_id': 9, 'destino_id': 14, 'desde': '2026-07-01'}, ...].
    El df debe tener columnas 'vendedor_id' y 'fecha'.
    """
    if df is None or getattr(df, "empty", True) or not reglas:
        return df
    if "vendedor_id" not in df.columns or "fecha" not in df.columns:
        return df
    f = pd.to_datetime(df["fecha"], errors="coerce")
    for r in reglas:
        try:
            mask = (df["vendedor_id"] == r["origen_id"]) & (f >= pd.Timestamp(r["desde"]))
            if int(mask.sum()):
                df.loc[mask, "vendedor_id"] = r["destino_id"]
        except Exception:
            continue
    return df


def mapear_vendedor_id(
    serie: pd.Series,
    mapeo: dict[str, int],
    log_no_mapeados: list,
    fuente: str = "",
    fallback_id: int | None = None,
) -> pd.Series:
    """
    Mapea una Serie de nombres de vendedor a sus IDs. Distingue dos casos para
    no perder ventas ni esconder errores de mapeo:

    - Nombre VACÍO/NaN (el documento no trae vendedor en el ERP): se asigna
      `fallback_id` (vendedor 'Sin asignar') SIN registrar en el log. No es un
      error: es una venta legítima sin vendedor, que igual debe sumar al total.
    - Nombre PRESENTE que no mapea: se registra en log_no_mapeados (para corregir
      la tabla de mapeo) Y se asigna `fallback_id` para no perder el monto.

    Si no se entrega `fallback_id`, los no resueltos quedan NULL (comportamiento
    anterior).
    """
    resultados = []
    for nombre in serie:
        # Documento sin vendedor en el ERP → bucket 'Sin asignar'
        if pd.isna(nombre) or str(nombre).strip() == "":
            resultados.append(fallback_id if fallback_id is not None else pd.NA)
            continue
        clave = _normalizar_nombre(nombre)
        vid = mapeo.get(clave)
        if vid is None:
            log_no_mapeados.append({"fuente": fuente, "nombre_original": nombre})
            resultados.append(fallback_id if fallback_id is not None else pd.NA)
        else:
            resultados.append(vid)
    return pd.array(resultados, dtype="Int64")


# ── Fechas y montos ──────────────────────────────────────────────────────────

def parsear_fecha(serie: pd.Series, formatos=None) -> pd.Series:
    """Convierte a datetime tolerando varios formatos. Devuelve NaT si falla."""
    if formatos is None:
        formatos = ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S"]
    for fmt in formatos:
        try:
            parsed = pd.to_datetime(serie, format=fmt, errors="coerce")
            if parsed.notna().sum() > 0:
                return parsed
        except Exception:
            continue
    return pd.to_datetime(serie, errors="coerce")


def limpiar_monto(serie: pd.Series) -> pd.Series:
    """
    Limpia columnas de montos:
    - Elimina separadores de miles (puntos y comas según contexto).
    - Convierte a float. Devuelve NaN si no es numérico.
    """
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")
    s = serie.astype(str).str.strip()
    s = s.str.replace(r"[^\d,.\-]", "", regex=True)
    # Si hay coma Y punto, asumimos que el punto es separador de miles
    tiene_ambos = s.str.contains(r"\.", regex=False) & s.str.contains(",", regex=False)
    s = s.where(~tiene_ambos, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(s, errors="coerce")


# ── Columnas ─────────────────────────────────────────────────────────────────

def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia nombres de columnas: strip, colapsa espacios, quita caracteres raros.
    Preserva acentos y ñ (necesarios para identificar columnas como 'Categoría').
    """
    df.columns = [
        re.sub(r"\s+", " ", col).strip()
        for col in df.columns
    ]
    return df


# ── Dirección de cliente (sucursal) ─────────────────────────────────────────

def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto))
                   if not unicodedata.combining(c))


def normalizar_direccion(direccion: pd.Series, comuna: pd.Series | None = None,
                         region: pd.Series | None = None) -> pd.Series:
    """
    Identidad de una sucursal: la dirección física, no el código del ERP.

    Obuma reutiliza la misma dirección física con códigos distintos, y a veces le
    concatena la comuna y la región ("CALLE 123" vs "CALLE 123 - COMUNA - REGION",
    ambos el mismo local). Para que todas esas formas
    colapsen en una sola sucursal: mayúsculas sin tildes, se eliminan los segmentos
    finales que repiten la comuna o la región, y se colapsa la puntuación.
    """
    d = direccion.fillna("").map(_sin_tildes).str.upper()
    com = (comuna.fillna("").map(_sin_tildes).str.upper()
           if comuna is not None else pd.Series([""] * len(d), index=d.index))
    reg = (region.fillna("").map(_sin_tildes).str.upper()
           if region is not None else pd.Series([""] * len(d), index=d.index))

    def _limpiar(texto, c, r):
        partes = [p.strip(" .,") for p in re.split(r"\s+-\s+", texto) if p.strip(" .,")]
        fuera = {x.strip() for x in (c, r) if x.strip()}
        utiles = [p for p in partes if p not in fuera] or partes
        out = " ".join(utiles)
        out = re.sub(r"[.,;]+", " ", out)
        return re.sub(r"\s+", " ", out).strip()

    return pd.Series([_limpiar(t, c, r) for t, c, r in zip(d, com, reg)],
                     index=d.index).replace("", pd.NA)
