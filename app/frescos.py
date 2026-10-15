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


def al_cambiar_version(version: str) -> bool:
    """
    Si el sello de versión cambió desde la última ejecución de este proceso
    (hubo un deploy sin Reboot), recarga TODOS los módulos `app.*`, páginas
    incluidas. Devuelve True si recargó.

    `refrescar` no alcanzaba: arregla las dependencias de una página, pero no
    la página misma. El 15-10-2026 Control de Máquinas siguió mostrando la
    versión anterior —sin los pedidos sin DTE— porque `app.pages.control_maquinas`
    ya estaba en memoria antes del deploy. Por eso VERSION se sube en cada
    commit con cambios visibles: es también la señal de recarga.

    Se recorre la lista dos veces porque un módulo puede recargarse antes que
    otro del que importa nombres; en la segunda vuelta ya los toma nuevos.
    El estado se guarda en `sys` y no en este módulo, para que sobreviva a que
    este mismo archivo se recargue.
    """
    previa = getattr(sys, "_kreems_version_cargada", None)
    if previa == version:
        return False
    sys._kreems_version_cargada = version
    # Con `previa` None (primera ejecución que ve este archivo) también se
    # recarga: puede ser un archivo nuevo dentro de un proceso viejo, que es
    # justo el caso del 15-10. Ocurre una sola vez por proceso.
    nombres = [n for n, m in list(sys.modules.items())
               if n.startswith("app.") and m is not None
               and n not in ("app.frescos", "app.version")
               and getattr(m, "__file__", None)]
    for _ in range(2):
        for n in nombres:
            mod = sys.modules.get(n)
            if mod is not None:
                try:
                    importlib.reload(mod)
                except Exception:
                    # Un módulo que no recarga no debe tumbar la app: queda el
                    # que estaba y, en el peor caso, se resuelve con Reboot.
                    pass
    return True


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
