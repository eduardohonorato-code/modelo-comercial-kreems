"""
Sello de versión visible en la app (login y sidebar).

Para qué: Streamlit Cloud a veces se queda pegado en un commit anterior, y sin
una marca a la vista no hay forma de distinguir "el cambio no funciona" de "el
cambio todavía no llegó". Con esto basta mirar el pie del login.

Al hacer un cambio que el usuario deba ver, subir VERSION en el mismo commit.
"""
VERSION = "2026-08-31i · concretado sin fantasmas"
