"""
Exportación del histórico de Acuña (etl/exportar_acuna.py) sin tocar la base.

Cubre lo que haría que la importación en el SaaS no cuadre: la paginación que
se salta o repite filas, el folio que existe como factura y como NC, y el
signo de las notas de crédito.

RUT ficticios: el repo es público.

    python -m unittest discover tests
"""
import unittest

import pandas as pd

from etl import exportar_acuna as ex

RUT_A = "11.111.111-1"
RUT_B = "22.222.222-2"


class _Consulta:
    """Imita la cadena de supabase-py que usa _leer_por_id."""

    def __init__(self, filas):
        self._filas, self._gt, self._eq, self._lim = filas, None, {}, None

    def select(self, *_a, **_k):
        return self

    def gt(self, col, val):
        self._gt = (col, val)
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._lim = n
        return self

    def execute(self):
        col, val = self._gt
        f = [r for r in self._filas if r[col] > val
             and all(r.get(k) == v for k, v in self._eq.items())]
        f = sorted(f, key=lambda r: r["id"])[: self._lim]
        return type("R", (), {"data": f})()


class _Cliente:
    def __init__(self, filas):
        self._filas = filas

    def table(self, _nombre):
        return _Consulta(self._filas)


def _ventas():
    return pd.DataFrame([
        {"id": 1, "fecha": "2025-03-04", "tipo_dcto": "FACTURA ELECTRONICA",
         "n_dcto": "500", "linea": 1, "vendedor_id": 7, "cliente_rut": RUT_A,
         "producto_codigo": "P1", "sucursal": "Casa Matriz", "cantidad": 2,
         "neto": 1000, "total": 1190, "costo": 600, "margen": 400, "direccion_id": 10},
        {"id": 2, "fecha": "2025-03-04", "tipo_dcto": "FACTURA ELECTRONICA",
         "n_dcto": "500", "linea": 2, "vendedor_id": 7, "cliente_rut": RUT_A,
         "producto_codigo": "FL-4", "sucursal": "Casa Matriz", "cantidad": 1,
         "neto": 1, "total": 1, "costo": 0, "margen": 1, "direccion_id": 10},
        # Mismo folio 500, pero como nota de crédito y de otro cliente.
        {"id": 3, "fecha": "2025-04-10", "tipo_dcto": "NOTA DE CREDITO ELECTRONICA",
         "n_dcto": "500", "linea": 1, "vendedor_id": 8, "cliente_rut": RUT_B,
         "producto_codigo": "P1", "sucursal": "Casa Matriz", "cantidad": -1,
         "neto": -500, "total": -595, "costo": -300, "margen": -200, "direccion_id": None},
    ])


def _dims():
    clientes = pd.DataFrame([{"rut": RUT_A, "razon_social": "Cliente A"},
                             {"rut": RUT_B, "razon_social": "Cliente B"}])
    productos = pd.DataFrame([
        {"codigo": "P1", "nombre": "Paleta", "categoria": "Paletas",
         "subcategoria": "", "fabricante": "Kreems", "unidad_medida": "CAJA"},
        {"codigo": "FL-4", "nombre": "Flete instalación", "categoria": "Maquinas",
         "subcategoria": "", "fabricante": "", "unidad_medida": "UN"}])
    vendedores = pd.DataFrame([
        {"id": 7, "nombre_canonico": "Vendedor Uno", "cod_vendedor_autoventa": "V1"},
        {"id": 8, "nombre_canonico": "Vendedor Dos", "cod_vendedor_autoventa": None}])
    direcciones = pd.DataFrame([{"id": 10, "nombre": "Local 1", "direccion": "Calle 1",
                                 "comuna": "Temuco"}])
    return clientes, productos, vendedores, direcciones


class TestLectura(unittest.TestCase):
    def test_pagina_por_id_sin_saltar_ni_repetir(self):
        filas = [{"id": i, "sociedad_id": 1 if i % 3 else 2} for i in range(1, 2501)]
        original = ex._PAGINA
        ex._PAGINA = 100
        try:
            df = ex._leer_por_id(_Cliente(filas), "fact_ventas", "id", sociedad_id=1)
        finally:
            ex._PAGINA = original
        esperados = [r["id"] for r in filas if r["sociedad_id"] == 1]
        self.assertEqual(df["id"].tolist(), esperados)


class TestArmado(unittest.TestCase):
    def setUp(self):
        self.items = ex.armar_items(_ventas(), *_dims())

    def test_una_fila_por_linea_con_nombres(self):
        self.assertEqual(len(self.items), 3)
        self.assertEqual(list(self.items.columns), ex._COLS_ITEMS)
        fila = self.items.iloc[0]
        self.assertEqual(fila["cliente_razon_social"], "Cliente A")
        self.assertEqual(fila["vendedor_nombre"], "Vendedor Uno")
        self.assertEqual(fila["direccion_nombre"], "Local 1")

    def test_folio_repetido_son_dos_documentos(self):
        docs = ex.armar_documentos(self.items)
        self.assertEqual(len(docs), 2)
        fac = docs[docs["tipo_dcto"].str.startswith("FACTURA")].iloc[0]
        self.assertEqual((fac["n_lineas"], fac["neto"]), (2, 1001))

    def test_nc_conserva_su_signo_y_el_control_cuadra(self):
        control = ex.armar_control(self.items)
        total = control[control["mes"] == "TOTAL"].iloc[0]
        self.assertEqual(total["lineas"], 3)
        self.assertEqual(total["documentos"], 2)
        self.assertEqual(total["neto"], 501)       # 1000 + 1 − 500
        nc = control[control["tipo_dcto"].str.startswith("NOTA")].iloc[0]
        self.assertEqual(nc["neto"], -500)


if __name__ == "__main__":
    unittest.main()
