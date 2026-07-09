"""Carga la cartera oficial de clientes por vendedor a `cartera_cliente`.

Fuente: reporte de clientes de Autoventa (la API no expone el vendedor
exclusivo, verificado 2026-07-09):
  - clientes.xlsx            → casas matrices (1 fila por cliente, con
                               "Vend. exclusivo" = cod_vendedor_autoventa)
  - clientes_direcciones.xlsx → sucursales (opcional; agrega n_sucursales)

Uso:
    python -m etl.cargar_cartera "<ruta clientes.xlsx>" ["<ruta direcciones.xlsx>"] [--dry-run]

Idempotente: upsert por cliente_rut. Los códigos de vendedor sin mapear en
dim_vendedor NO se descartan: quedan con vendedor_id NULL y se reportan.
"""
import sys

import pandas as pd

from etl.cleaners import normalizar_rut
from etl.db import get_client


def _leer_clientes(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    req = ["Código cliente", "RUT", "Nombre", "Vend. exclusivo"]
    faltan = [c for c in req if c not in df.columns]
    if faltan:
        raise SystemExit(f"[ERROR] {path}: faltan columnas {faltan}")
    out = pd.DataFrame({
        "codigo_cliente": df["Código cliente"].astype(str).str.strip(),
        "cliente_rut": normalizar_rut(df["RUT"]),
        "nombre": df["Nombre"].astype(str).str.strip(),
        "ruta": df.get("Ruta"),
        "cod_vendedor": df["Vend. exclusivo"],
    })
    out = out.dropna(subset=["cliente_rut"])
    # "Vend. exclusivo" llega como float (32304.0) → texto entero
    out["cod_vendedor"] = out["cod_vendedor"].map(
        lambda v: str(int(v)) if pd.notna(v) else None)
    # Un RUT puede repetirse (razones sociales duplicadas): gana la primera
    # fila con vendedor asignado.
    out = out.sort_values("cod_vendedor", na_position="last")
    return out.drop_duplicates("cliente_rut", keep="first")


def _contar_sucursales(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    if "Código cliente" not in df.columns:
        raise SystemExit(f"[ERROR] {path}: falta 'Código cliente'")
    return (df.groupby(df["Código cliente"].astype(str).str.strip())
              .size().rename("n_sucursales").reset_index()
              .rename(columns={"Código cliente": "codigo_cliente"}))


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    path_clientes = args[0]
    path_dirs = args[1] if len(args) > 1 else None

    cart = _leer_clientes(path_clientes)
    print(f"[cartera] {len(cart)} clientes leídos de {path_clientes}")

    if path_dirs:
        suc = _contar_sucursales(path_dirs)
        cart = cart.merge(suc, on="codigo_cliente", how="left")
        print(f"[cartera] sucursales agregadas desde {path_dirs} "
              f"({int(cart['n_sucursales'].notna().sum())} clientes con sucursal)")
    if "n_sucursales" not in cart.columns:
        cart["n_sucursales"] = 0
    cart["n_sucursales"] = cart["n_sucursales"].fillna(0).astype(int)

    client = get_client()

    # Mapa cod_vendedor_autoventa → vendedor_id
    dv = pd.DataFrame(client.table("dim_vendedor")
                      .select("id,nombre_canonico,cod_vendedor_autoventa")
                      .execute().data)
    dv = dv.dropna(subset=["cod_vendedor_autoventa"])
    mapa = {str(c): int(i) for c, i in
            zip(dv["cod_vendedor_autoventa"].astype(str), dv["id"])}
    cart["vendedor_id"] = cart["cod_vendedor"].map(mapa)

    # Reporte de mapeo (regla de calidad: nada se descarta en silencio)
    resumen = (cart.assign(mapeado=cart["vendedor_id"].notna())
                   .groupby(["cod_vendedor", "mapeado"], dropna=False)
                   .size().reset_index(name="clientes"))
    print("\n[cartera] clientes por código de vendedor:")
    nombres = {str(c): n for c, n in
               zip(dv["cod_vendedor_autoventa"].astype(str), dv["nombre_canonico"])}
    for _, r in resumen.sort_values("clientes", ascending=False).iterrows():
        cod = r["cod_vendedor"] or "(sin código)"
        tag = nombres.get(str(r["cod_vendedor"]), "⚠️ SIN MAPEAR en dim_vendedor")
        print(f"   {cod:<12} {tag:<40} {r['clientes']:>4} clientes")

    sin_map = cart[cart["vendedor_id"].isna() & cart["cod_vendedor"].notna()]
    if not sin_map.empty:
        print(f"\n[cartera] ⚠️ {len(sin_map)} clientes con código sin mapear "
              f"(quedan vendedor_id NULL): "
              f"{sorted(sin_map['cod_vendedor'].unique())}")

    if dry:
        print("\n[cartera] --dry-run: no se escribe nada.")
        return

    def _s(v):
        """str o None — pandas convierte None→NaN en columnas object."""
        return str(v) if pd.notna(v) else None

    regs = []
    for _, r in cart.iterrows():
        regs.append({
            "cliente_rut": r["cliente_rut"],
            "vendedor_id": int(r["vendedor_id"]) if pd.notna(r["vendedor_id"]) else None,
            "cod_vendedor": _s(r["cod_vendedor"]),
            "codigo_cliente": _s(r["codigo_cliente"]),
            "nombre": _s(r["nombre"]),
            "ruta": _s(r.get("ruta")),
            "n_sucursales": int(r["n_sucursales"]),
        })
    _LOTE = 500
    for i in range(0, len(regs), _LOTE):
        client.table("cartera_cliente").upsert(
            regs[i:i + _LOTE], on_conflict="cliente_rut").execute()
    print(f"\n[cartera] ✅ upsert de {len(regs)} clientes en cartera_cliente.")


if __name__ == "__main__":
    main()
