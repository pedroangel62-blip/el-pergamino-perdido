import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from backend.musica_remota import (
    CONFIRMACION_COSTE_MUSICA,
    calcular_creditos_estimados,
    cargar_solicitud_musica,
    producir_musica,
)


class MusicaRemotaTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.aprobados = os.path.join(self.temporal.name, "aprobados")
        self.salida = os.path.join(self.temporal.name, "salida")
        os.makedirs(self.aprobados)
        self.proyecto_id = "pergamino-14-lineas-nazca"
        self.prompt = "Musica instrumental documental para Nazca."
        self.proyecto = {
            "proyecto_id": self.proyecto_id,
            "tema": "Las Lineas de Nazca",
            "resultado": {
                "guion": "Guion aprobado.",
                "plan_visual": [{"numero": n} for n in range(1, 9)],
                "musica": self.prompt,
                "sincronizacion_aprobada": {
                    "aprobada": True,
                    "duracion_total": 109.606,
                    "sin_subtitulos": True,
                },
                "_aprobaciones": {
                    "guion": True,
                    "plan_visual": True,
                    "sincronizacion": True,
                },
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

    def _crear_solicitud(
        self,
        accion="generar",
        confirmacion=CONFIRMACION_COSTE_MUSICA,
        duracion_ms=110_000,
        creditos_maximos=2000,
    ):
        ruta = os.path.join(self.temporal.name, "solicitud.json")
        self._guardar(ruta, {
            "accion": accion,
            "solicitud_id": "nazca-musica-001",
            "proyecto_id": self.proyecto_id,
            "duracion_ms": duracion_ms,
            "modelo": "music_v2",
            "creditos_maximos": creditos_maximos,
            "confirmacion_coste": confirmacion,
        })
        return ruta

    def test_calcula_1650_creditos_para_110_segundos(self):
        self.assertEqual(calcular_creditos_estimados(110_000), 1650)

    def test_comprobacion_no_consume_creditos(self):
        solicitud = cargar_solicitud_musica(
            self._crear_solicitud(accion="comprobar", confirmacion=""),
            self.aprobados,
        )
        self.assertEqual(solicitud["accion"], "comprobar")
        self.assertEqual(solicitud["creditos_estimados"], 1650)

    def test_generacion_exige_confirmacion_exacta(self):
        with self.assertRaisesRegex(ValueError, "autorizacion exacta"):
            cargar_solicitud_musica(
                self._crear_solicitud(confirmacion="adelante"),
                self.aprobados,
            )

    def test_bloquea_duracion_o_limite_distintos(self):
        with self.assertRaisesRegex(ValueError, "exactamente 110"):
            cargar_solicitud_musica(
                self._crear_solicitud(duracion_ms=120_000),
                self.aprobados,
            )
        with self.assertRaisesRegex(ValueError, "exactamente 2000"):
            cargar_solicitud_musica(
                self._crear_solicitud(creditos_maximos=3000),
                self.aprobados,
            )

    def test_bloquea_proyecto_con_subtitulos(self):
        self.proyecto["resultado"]["sincronizacion_aprobada"][
            "sin_subtitulos"
        ] = False
        self._guardar(
            os.path.join(self.aprobados, f"{self.proyecto_id}.json"),
            self.proyecto,
        )
        with self.assertRaisesRegex(ValueError, "sin subtitulos"):
            cargar_solicitud_musica(self._crear_solicitud(), self.aprobados)

    def test_genera_una_sola_pista_y_crea_manifiesto(self):
        solicitud = cargar_solicitud_musica(
            self._crear_solicitud(),
            self.aprobados,
        )

        def generador(prompt, duracion_ms, modelo):
            self.assertEqual(prompt, self.prompt)
            self.assertEqual(duracion_ms, 110_000)
            self.assertEqual(modelo, "music_v2")
            return {"audio": b"audio" * 300, "song_id": "song-123"}

        manifiesto = producir_musica(solicitud, self.salida, generador)
        destino = os.path.join(self.salida, self.proyecto_id)
        self.assertEqual(manifiesto["creditos_estimados"], 1650)
        self.assertEqual(manifiesto["creditos_maximos_autorizados"], 2000)
        self.assertTrue(os.path.isfile(os.path.join(destino, "musica.mp3")))
        self.assertTrue(
            os.path.isfile(os.path.join(destino, "manifiesto-musica.json"))
        )

        repeticion = Mock()
        with self.assertRaisesRegex(FileExistsError, "no se regenerara"):
            producir_musica(solicitud, self.salida, repeticion)
        repeticion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
