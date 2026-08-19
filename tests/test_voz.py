import base64
import json
import unittest
from unittest.mock import patch

from backend.voz import (
    decodificar_respuesta_elevenlabs,
    solicitar_audio_elevenlabs,
)


class RespuestaSimulada:
    def __init__(self, contenido: bytes):
        self.contenido = contenido
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, traza):
        return False

    def read(self, limite: int) -> bytes:
        return self.contenido[:limite]


def crear_respuesta() -> bytes:
    return json.dumps(
        {
            "audio_base64": base64.b64encode(b"audio-de-prueba").decode("ascii"),
            "alignment": {
                "characters": ["H", "o", "l", "a"],
                "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3],
                "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4],
            },
        }
    ).encode("utf-8")


class ElevenLabsTimestampsTests(unittest.TestCase):
    def test_decodifica_audio_y_marcas_temporales(self):
        audio, alineacion = decodificar_respuesta_elevenlabs(crear_respuesta())

        self.assertEqual(audio, b"audio-de-prueba")
        self.assertEqual("".join(alineacion["characters"]), "Hola")
        self.assertEqual(alineacion["character_end_times_seconds"][-1], 0.4)

    @patch("backend.voz.urlopen")
    def test_usa_el_endpoint_con_marcas_sin_segunda_generacion(self, urlopen):
        urlopen.return_value = RespuestaSimulada(crear_respuesta())

        audio, alineacion = solicitar_audio_elevenlabs(
            "Hola",
            "clave-prueba",
            "voz/prueba",
            "eleven_multilingual_v2",
        )

        solicitud = urlopen.call_args.args[0]
        self.assertIn("/with-timestamps?", solicitud.full_url)
        self.assertIn("voz%2Fprueba", solicitud.full_url)
        self.assertEqual(solicitud.headers["Accept"], "application/json")
        self.assertEqual(audio, b"audio-de-prueba")
        self.assertEqual(len(alineacion["characters"]), 4)
        urlopen.assert_called_once()

    def test_rechaza_respuesta_sin_alineacion(self):
        contenido = json.dumps(
            {
                "audio_base64": base64.b64encode(b"audio").decode("ascii"),
            }
        ).encode("utf-8")

        with self.assertRaisesRegex(RuntimeError, "marcas temporales"):
            decodificar_respuesta_elevenlabs(contenido)


if __name__ == "__main__":
    unittest.main()
