"""
Borrado de las filas de fact_pedidos que la API de Autoventa ya no trae
(etl/run_autoventa_api.ids_sobrantes), sin tocar la base.

    python -m unittest discover tests
"""
import unittest

import pandas as pd

from etl.run_autoventa_api import ids_sobrantes


class TestIdsSobrantes(unittest.TestCase):

    def test_borra_las_filas_del_mes_que_la_api_ya_no_trae(self):
        existentes = [
            {"id": 1, "n_pedido": "4627", "producto_codigo": "MUS-1", "linea": 1},
            {"id": 2, "n_pedido": "4627", "producto_codigo": "01", "linea": 1},       # código viejo
            {"id": 3, "n_pedido": "4689", "producto_codigo": "RE-7x20", "linea": 1},  # Sin DTE que no se facturó
            {"id": 4, "n_pedido": "4700", "producto_codigo": "CR-1", "linea": 2},     # sobra la 2ª línea
            {"id": 5, "n_pedido": "4700", "producto_codigo": "CR-1", "linea": 1},
        ]
        fact_pedidos = pd.DataFrame({
            "n_pedido": ["4627", "4700"],
            "producto_codigo": ["MUS-1", "CR-1"],
            "linea": pd.array([1, 1], dtype="Int64"),
        })
        self.assertEqual(ids_sobrantes(existentes, fact_pedidos), [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
