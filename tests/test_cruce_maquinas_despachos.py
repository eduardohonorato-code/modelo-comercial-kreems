"""
Cruce máquina↔despacho por (documento, cliente_rut).

Reproduce el caso real de sep-2026: las NC de flete tienen serie de folios
propia (600–900 en 2026) y reutilizan números de facturas de feb–abr que SÍ
tienen despacho. Cruzando solo por número, la NC 618 (retiro de julio) salía
'entregada' con el despacho de marzo de la factura 618 de OTRO cliente, y ese
despacho de helados quedaba marcado es_maquina.

RUT ficticios: el repo es público.

    python -m unittest discover tests
"""
import unittest

import pandas as pd

from etl.maquinas import aplicar_estado_despachos, marcar_despachos_maquina
from etl.reconciliar_maquinas import diff_estados

RUT_NC = "11.111.111-1"       # cliente de la NC de flete (retiro anulado)
RUT_OTRO = "22.222.222-2"     # cliente de la factura de helados con el mismo folio
RUT_MAQ = "33.333.333-3"      # cliente de una máquina real con dos intentos


def _maquinas(estado_nc="gestionada"):
    return pd.DataFrame([
        # NC de flete de julio: folio 618 de la serie de notas de crédito.
        {"documento": "618", "fecha": "2026-07-08", "vendedor_id": 1,
         "cliente_rut": RUT_NC, "tipo_mov": "retiro", "estado": estado_nc,
         "sociedad_id": 2},
        # Instalación real, rechazada primero y entregada en el segundo intento.
        {"documento": "4853", "fecha": "2026-08-27", "vendedor_id": 1,
         "cliente_rut": RUT_MAQ, "tipo_mov": "nueva", "estado": "gestionada",
         "sociedad_id": 2},
    ])


def _despachos():
    return pd.DataFrame([
        # Factura 618 de marzo, de OTRO cliente: helados, no máquina.
        {"id": 1, "documento": "618", "cliente_rut": RUT_OTRO,
         "estado": "Entregada", "fecha_ruta": "2026-03-04", "es_maquina": True},
        # La máquina 4853: el rechazo viene PRIMERO en el archivo.
        {"id": 2, "documento": "4853", "cliente_rut": RUT_MAQ,
         "estado": "Rechazada", "fecha_ruta": "2026-08-29", "es_maquina": False},
        {"id": 3, "documento": "4853", "cliente_rut": RUT_MAQ,
         "estado": "Entregada", "fecha_ruta": "2026-08-28", "es_maquina": False},
    ])


class TestAplicarEstadoDespachos(unittest.TestCase):

    def test_nc_no_toma_el_despacho_de_otro_cliente(self):
        out = aplicar_estado_despachos(_maquinas(), _despachos())
        nc = out[out["documento"] == "618"].iloc[0]
        self.assertEqual(nc["estado"], "gestionada")

    def test_varios_intentos_manda_entregada(self):
        out = aplicar_estado_despachos(_maquinas(), _despachos())
        maq = out[out["documento"] == "4853"].iloc[0]
        self.assertEqual(maq["estado"], "entregada")

    def test_documento_numerico_con_punto_cero(self):
        d = _despachos().assign(documento=[618.0, 4853.0, 4853.0])
        out = aplicar_estado_despachos(_maquinas(), d)
        self.assertEqual(
            out.set_index("documento").loc["4853", "estado"], "entregada")

    def test_sin_cliente_rut_falla_en_vez_de_cruzar_por_numero(self):
        with self.assertRaises(ValueError):
            aplicar_estado_despachos(
                _maquinas(), _despachos().drop(columns=["cliente_rut"]))


class TestMarcarDespachosMaquina(unittest.TestCase):

    def test_despacho_de_helados_con_folio_de_nc_no_es_maquina(self):
        out = marcar_despachos_maquina(_despachos(), _maquinas())
        self.assertEqual(out.set_index("id")["es_maquina"].to_dict(),
                         {1: False, 2: True, 3: True})

    def test_sin_maquinas_ninguno_es_maquina(self):
        out = marcar_despachos_maquina(_despachos(), _maquinas().iloc[0:0])
        self.assertFalse(out["es_maquina"].any())


class TestReconciliar(unittest.TestCase):

    def test_repara_el_cruce_falso_heredado(self):
        # Así quedó la base: la NC 618 'entregada' por el despacho ajeno.
        dif = diff_estados(_maquinas(estado_nc="entregada"), _despachos())
        fila = dif.set_index("documento").loc["618"]
        self.assertEqual(fila["estado_nuevo"], "gestionada")
        self.assertEqual(fila["motivo"], "cruce con despacho de otro cliente")
        # La máquina real se actualiza por su propio despacho.
        self.assertEqual(
            dif.set_index("documento").loc["4853", "estado_nuevo"], "entregada")

    def test_no_toca_una_maquina_sin_despacho_alguno(self):
        m = _maquinas(estado_nc="entregada")
        d = _despachos()[_despachos()["documento"] != "618"]
        dif = diff_estados(m, d)
        self.assertNotIn("618", set(dif["documento"]))


class TestPrepararEntregas(unittest.TestCase):

    def test_marca_de_maquina_y_monto_por_cliente(self):
        from app.export_entregas import preparar_entregas
        ventas = pd.DataFrame([
            {"n_dcto": "618", "tipo_dcto": "FACTURA", "producto_codigo": "PAL-1",
             "cliente_rut": RUT_OTRO, "neto": 49200},
            {"n_dcto": "618", "tipo_dcto": "NOTA DE CREDITO",
             "producto_codigo": "FL-2", "cliente_rut": RUT_NC, "neto": -1},
            {"n_dcto": "4853", "tipo_dcto": "FACTURA", "producto_codigo": "FL-4",
             "cliente_rut": RUT_MAQ, "neto": 1},
        ])
        mov = _maquinas()
        d = preparar_entregas(ventas, _despachos(), mov).set_index("_doc")
        self.assertFalse(d.loc["618", "Es máquina"])
        self.assertEqual(d.loc["618", "Monto facturado"], 49200)
        self.assertTrue(d.loc["4853", "Es máquina"])
        self.assertEqual(d.loc["4853", "_est"], "Entregada")

    def test_mismo_folio_dos_clientes_son_dos_documentos(self):
        from app.export_entregas import preparar_entregas
        desp = pd.DataFrame([
            {"documento": "10", "cliente_rut": RUT_OTRO, "estado": "Entregada",
             "fecha_ruta": "2026-02-20"},
            {"documento": "10", "cliente_rut": RUT_MAQ, "estado": "Rechazada",
             "fecha_ruta": "2026-03-09"},
        ])
        ventas = pd.DataFrame(columns=["n_dcto", "tipo_dcto", "producto_codigo",
                                       "cliente_rut", "neto"])
        d = preparar_entregas(ventas, desp)
        self.assertEqual(len(d), 2)


if __name__ == "__main__":
    unittest.main()
