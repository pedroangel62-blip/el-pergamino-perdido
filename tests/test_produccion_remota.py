import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from backend.produccion_remota import (
    CONFIRMACION_COSTE,
    cargar_solicitud,
    producir_voz,
)


class ProduccionRemotaTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.aprobados = os.path.join(self.temporal.name, "aprobados")
        self.salida = os.path.join(self.temporal.name, "salida")
        os.makedirs(self.aprobados)
        self.proyecto_id = "pergamino-14-lineas-nazca"
        self.guion = "Guion aprobado de prueba para la voz de Nazca."
        self.proyecto = {
            "proyecto_id": self.proyecto_id,
            "numero": "XIV",
            "tema": "Las Líneas de Nazca",
            "resultado": {
                "guion": self.guion,
                "plan_visual": [
                    {"numero": numero} for numero in range(1, 9)
                ],
                "_aprobaciones": {"guion": True, "plan_visual": True},
            },
        }
        self._guardar(
            os.path.join(self.aprobados, f"{self.proyecto_id}.json"),
            self.proyecto,
        )

    def tearDown(self):
        self.temporal.cleanup()

    @staticmethod
    def _guardar(ruta, datos):
        with open(ruta, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo)

    def _crear_solicitud(self, accion="generar", confirmacion=CONFIRMACION_COSTE):
        ruta = os.path.join(self.temporal.name, "solicitud.json")
        self._guardar(ruta, {
            "accion": accion,
            "solicitud_id": "nazca-voz-001",
            "proyecto_id": self.proyecto_id,
            "confirmacion_coste": confirmacion,
        })
        return ruta

    def test_comprobacion_no_necesita_autorizar_consumo(self):
        ruta = self._crear_solicitud(accion="comprobar", confirmacion="")
        solicitud = cargar_solicitud(ruta, self.aprobados)

        self.assertEqual(solicitud["accion"], "comprobar")
        self.assertEqual(solicitud["proyecto_id"], self.proyecto_id)

    def test_generacion_exige_confirmacion_exacta(self):
        ruta = self._crear_solicitud(confirmacion="adelante")

        with self.assertRaisesRegex(ValueError, "autorizacion exacta"):
            cargar_solicitud(ruta, self.aprobados)

    def test_rechaza_proyecto_sin_guion_aprobado(self):
        self.proyecto["resultado"]["_aprobaciones"]["guion"] = False
        self._guardar(
            os.path.join(self.aprobados, f"{self.proyecto_id}.json"),
            self.proyecto,
        )

        with self.assertRaisesRegex(ValueError, "aprobacion editorial"):
            cargar_solicitud(self._crear_solicitud(), self.aprobados)

    def test_genera_una_sola_vez_y_crea_manifiesto(self):
        solicitud = cargar_solicitud(
            self._crear_solicitud(),
            self.aprobados,
        )

        def generador(directorio, guion):
            self.assertEqual(guion, self.guion)
            with open(os.path.join(directorio, "voz.mp3"), "wb") as archivo:
                archivo.write(b"audio")
            self._guardar(
                os.path.join(directorio, "voz.json"),
                {"estado": "pendiente_aprobacion", "duracion_segundos": 80.0},
            )
            self._guardar(
                os.path.join(directorio, "voz-alineacion.json"),
                {"alignment": {"characters": ["G"]}},
            )
            return {"estado": "pendiente_aprobacion", "duracion_segundos": 80.0}

        manifiesto = producir_voz(solicitud, self.salida, generador)
        destino = os.path.join(self.salida, self.proyecto_id)

        self.assertEqual(manifiesto["solicitud_id"], "nazca-voz-001")
        self.assertEqual(manifiesto["caracteres_guion"], len(self.guion))
        self.assertIn("voz.mp3", manifiesto["archivos"])
        self.assertTrue(os.path.isfile(os.path.join(destino, "manifiesto-voz.json")))

        repeticion = Mock()
        with self.assertRaisesRegex(FileExistsError, "no se regenerara"):
            producir_voz(solicitud, self.salida, repeticion)
        repeticion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
