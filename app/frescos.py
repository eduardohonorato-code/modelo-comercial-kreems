"""
Recarga los módulos que Streamlit Cloud dejó viejos en memoria tras un deploy.

Streamlit vuelve a ejecutar las páginas en cada visita, pero los módulos que
ellas importan (app.data, app.kpis_maquinas…) quedan cacheados en el proceso.
Después de un push, la página nueva corre contra el módulo VIEJO y revienta con
ImportError al buscar una función recién agregada. Pasó al estrenar Control de
Máquinas (sep-2026) y otra vez con Control Entregas (oct-2026: faltaba
`get_pedidos_rango`). Hasta ahora la salida era «Manage app → Reboot app».

`refrescar` mira si el módulo ya tiene los nombres que la página necesita y, si
no, lo recarga del disco. Es barato: si el módulo está al día no hace nada.
"""
import importlib
import sys


def refrescar(requeridos: dict[str, tuple[str, ...]]) -> None:
    """`{"app.data": ("get_pedidos_rango", …)}` → recarga los que no los tengan.

    Se respeta el orden del dict: poner primero los módulos de los que dependen
    los siguientes (p. ej. app.kpis_maquinas antes de un módulo que lo importa).
    """
    for nombre, attrs in requeridos.items():
        mod = sys.modules.get(nombre)
        if mod is None:
            continue    # nunca se importó: el import normal lo trae nuevo
        if any(not hasattr(mod, a) for a in attrs):
            importlib.reload(mod)
