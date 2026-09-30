"""
Exportación de los datos sin API (etl/exportar_sin_api.py) sin tocar la base.

RUT ficticios: el repo es público.

    python -m unittest discover tests
"""
import unittest

import pandas as pd

from etl import exportar_sin_api as ex

VENDEDORES = pd.DataFrame({"id": [6, 13], "nombre_canonico": ["Carlos Sanhueza", "Jorge Jara"]})


class TestPegarVendedor(unittest.TestCase):

    def test_agrega_el_nombre_al_lado_del_id_sin_perder_filas(self):
        cartera = pd.DataFrame({
            "cliente_rut": ["11.111.111-1", "22.222.222-2", "33.333.333-3"],
            "vendedor_id": [13, 99, None],   # 99 ya no está en dim_vendedor
            "ruta": ["R1", "R2", "R3"],
        })
        salida = ex.pegar_vendedor(cartera, VENDEDORES)
        self.assertEqual(list(salida.columns), ["cliente_rut", "vendedor_id", "vendedor_nombre", "ruta"])
        self.assertEqual(len(salida), 3)
        self.assertEqual(salida["vendedor_nombre"].tolist()[0], "Jorge Jara")
        self.assertTrue(salida["vendedor_nombre"].iloc[1:].isna().all())

    def test_tabla_sin_vendedor_queda_igual(self):
        metas = pd.DataFrame({"anio": [2026], "mes": [8], "meta_gestiones_semana": [22]})
        self.assertIs(ex.pegar_vendedor(metas, VENDEDORES), metas)


class TestPegarSociedad(unittest.TestCase):

    def test_traduce_el_id_de_la_app_al_nombre_del_saas(self):
        desp = pd.DataFrame({"documento": ["1", "2"], "sociedad_id": [2, 1]})
        salida = ex.pegar_sociedad(desp)
        self.assertEqual(salida["sociedad"].tolist(), ["grannatural", "acuna"])
        self.assertEqual(list(salida.columns), ["documento", "sociedad_id", "sociedad"])


class TestControl(unittest.TestCase):

    def test_cuenta_filas_vendedores_y_los_ids_sin_nombre(self):
        cartera = ex.pegar_vendedor(pd.DataFrame({
            "cliente_rut": ["11.111.111-1", "22.222.222-2"], "vendedor_id": [13, 99]}), VENDEDORES)
        c = ex.armar_control({"cartera": cartera}).iloc[0]
        self.assertEqual((c["filas"], c["vendedores"], c["clientes"], c["sin_nombre_vendedor"]),
                         (2, 2, 2, 1))


if __name__ == "__main__":
    unittest.main()
