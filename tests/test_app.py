import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend import main


class AplicacionTests(unittest.TestCase):
    def setUp(self):
        self.directorio_temporal = tempfile.TemporaryDirectory()
        self.directorio_anterior = main.DIRECTORIO_PROYECTOS
        main.DIRECTORIO_PROYECTOS = self.directorio_temporal.name
        self.cliente = TestClient(main.app)

    def tearDown(self):
        main.DIRECTORIO_PROYECTOS = self.directorio_anterior
        self.directorio_temporal.cleanup()

    def crear_proyecto(self, proyecto_id: str = "pergamino-prueba") -> None:
        directorio = os.path.join(self.directorio_temporal.name, proyecto_id)
        os.makedirs(os.path.join(directorio, "imagenes"))
        resultado = {
            "guion": "Guion de prueba suficientemente largo.",
            "plan_visual": [
                {
                    "numero": numero,
                    "tipo": "RECREACION_IA",
                    "generar_ia": True,
                    "buscar": [],
                    "motivo": "Prueba",
                    "edicion": "Prueba",
                    "prompt": "Prueba",
                }
                for numero in range(1, 9)
            ],
            "musica": "Música documental",
            "minimax": "Vídeo documental",
            "publicacion": {
                "titulo": "Título",
                "descripcion": "Descripción",
                "hashtags": ["#historia"],
                "comentario_fijado": "Comentario",
            },
        }

        with open(
            os.path.join(directorio, "proyecto.json"),
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump(
                {
                    "proyecto_id": proyecto_id,
                    "tema": "Tema de prueba",
                    "resultado": resultado,
                },
                archivo,
            )

    def test_inicio_responde(self):
        respuesta = self.cliente.get("/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("EL PERGAMINO PERDIDO", respuesta.text)

    def test_pagina_produccion_responde(self):
        self.crear_proyecto()
        respuesta = self.cliente.get("/produccion/pergamino-prueba")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("PRODUCCIÓN FINAL", respuesta.text)
        self.assertIn("Tema de prueba", respuesta.text)


if __name__ == "__main__":
    unittest.main()
