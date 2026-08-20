import json
import os
import tempfile
import unittest
from unittest.mock import patch

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

    def test_inicio_ofrece_recuperar_nazca_sin_generar_con_ia(self):
        respuesta = self.cliente.get("/")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("PROYECTOS EDITORIALES APROBADOS", respuesta.text)
        self.assertIn("Las Líneas de Nazca", respuesta.text)
        self.assertIn("Pergamino XIV", respuesta.text)

    def test_recupera_nazca_una_sola_vez_sin_llamar_openai(self):
        ruta = (
            "/recuperar-proyecto-aprobado/"
            "pergamino-14-lineas-nazca"
        )
        with patch.object(
            main,
            "obtener_cliente_openai",
            side_effect=AssertionError("No debe llamarse a OpenAI"),
        ):
            respuesta = self.cliente.post(ruta, follow_redirects=False)

        self.assertEqual(respuesta.status_code, 303)
        self.assertEqual(
            respuesta.headers["location"],
            "/proyecto/pergamino-14-lineas-nazca",
        )
        archivo_proyecto = os.path.join(
            self.directorio_temporal.name,
            "pergamino-14-lineas-nazca",
            "proyecto.json",
        )
        with open(archivo_proyecto, "r", encoding="utf-8") as archivo:
            proyecto = json.load(archivo)

        self.assertEqual(proyecto["tema_indice"]["id"], "banco-006")
        self.assertEqual(len(proyecto["resultado"]["plan_visual"]), 8)
        self.assertTrue(
            proyecto["resultado"]["_aprobaciones"]["guion"]
        )
        self.assertTrue(
            proyecto["resultado"]["_aprobaciones"]["plan_visual"]
        )
        sincronizacion = proyecto["resultado"]["sincronizacion_aprobada"]
        self.assertTrue(sincronizacion["aprobada"])
        self.assertTrue(sincronizacion["sin_subtitulos"])
        self.assertEqual(len(sincronizacion["segmentos"]), 9)
        self.assertEqual(sincronizacion["segmentos"][0]["fin"], 3.0)
        self.assertEqual(
            sincronizacion["segmentos"][-1]["inicio"],
            106.606,
        )
        self.assertEqual(
            sincronizacion["segmentos"][-1]["fin"],
            109.606,
        )
        self.assertIn("110 segundos", proyecto["resultado"]["musica"])
        musica = proyecto["resultado"]["musica_aprobada"]
        self.assertTrue(musica["aprobada"])
        self.assertTrue(musica["instrumental"])
        self.assertFalse(musica["regeneracion_automatica"])
        self.assertEqual(musica["duracion_real_segundos"], 110.04)
        self.assertEqual(musica["creditos_estimados"], 1650)
        self.assertEqual(musica["creditos_maximos_autorizados"], 2000)
        self.assertEqual(
            musica["sha256"],
            "48cb06a455c57f7670f65f03190d81a1559f42dc5bbd63c157df9374782e2c62",
        )
        self.assertTrue(
            proyecto["resultado"]["_aprobaciones"]["musica"]
        )
        imagen_5 = os.path.join(
            self.directorio_temporal.name,
            "pergamino-14-lineas-nazca",
            "imagenes",
            "imagen5.png",
        )
        self.assertTrue(os.path.isfile(imagen_5))
        proyecto["marca_no_sobrescribir"] = True
        with open(archivo_proyecto, "w", encoding="utf-8") as archivo:
            json.dump(proyecto, archivo)

        segunda_respuesta = self.cliente.post(
            ruta,
            follow_redirects=False,
        )
        self.assertEqual(segunda_respuesta.status_code, 303)
        with open(archivo_proyecto, "r", encoding="utf-8") as archivo:
            proyecto_reabierto = json.load(archivo)
        self.assertTrue(proyecto_reabierto["marca_no_sobrescribir"])

    def test_pagina_produccion_responde(self):
        self.crear_proyecto()
        respuesta = self.cliente.get("/produccion/pergamino-prueba")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("PRODUCCIÓN FINAL", respuesta.text)
        self.assertIn("Tema de prueba", respuesta.text)

    def test_pagina_recupera_un_montaje_interrumpido(self):
        self.crear_proyecto()
        directorio = os.path.join(
            self.directorio_temporal.name,
            "pergamino-prueba",
        )
        temporal_montaje = os.path.join(directorio, "montaje-abandonado")
        os.makedirs(temporal_montaje)
        with open(
            os.path.join(directorio, "produccion.json"),
            "w",
            encoding="utf-8",
        ) as archivo:
            json.dump({"estado": "generando_borrador"}, archivo)

        respuesta = self.cliente.get("/produccion/pergamino-prueba")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("El montaje anterior se interrumpió", respuesta.text)
        self.assertIn("puede volver a generar el borrador", respuesta.text)
        self.assertFalse(os.path.exists(temporal_montaje))

    def test_control_previo_bloquea_el_render_desde_la_interfaz(self):
        self.crear_proyecto()
        resumen = {
            "imagenes_aprobadas": True,
            "sincronizacion": [{} for _ in range(8)],
        }
        estado = {
            "sincronizacion_aprobada": True,
            "musica_aprobada": True,
        }
        verificacion = {
            "preparado": False,
            "bloqueos": ["Falta validar la Imagen 9."],
        }

        with (
            patch.object(main, "exigir_voz_aprobada"),
            patch.object(main, "obtener_imagenes_produccion"),
            patch.object(
                main,
                "obtener_resumen_produccion",
                return_value=resumen,
            ),
            patch.object(
                main,
                "cargar_estado_produccion",
                return_value=estado,
            ),
            patch.object(
                main,
                "verificar_preparacion_montaje",
                return_value=verificacion,
            ),
            patch.object(main, "iniciar_generacion_borrador") as iniciar,
            patch.object(main, "generar_borrador_seguro") as generar,
        ):
            respuesta = self.cliente.post(
                "/produccion/pergamino-prueba/generar-borrador",
                follow_redirects=False,
            )

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("control previo", respuesta.json()["detail"].lower())
        iniciar.assert_not_called()
        generar.assert_not_called()

    def test_render_solo_se_encola_despues_de_todas_las_aprobaciones(self):
        self.crear_proyecto()
        resumen = {
            "imagenes_aprobadas": True,
            "sincronizacion": [{} for _ in range(8)],
        }
        estado = {
            "sincronizacion_aprobada": True,
            "musica_aprobada": True,
        }

        with (
            patch.object(main, "exigir_voz_aprobada"),
            patch.object(main, "obtener_imagenes_produccion"),
            patch.object(
                main,
                "obtener_resumen_produccion",
                return_value=resumen,
            ),
            patch.object(
                main,
                "cargar_estado_produccion",
                return_value=estado,
            ),
            patch.object(
                main,
                "verificar_preparacion_montaje",
                return_value={"preparado": True, "bloqueos": []},
            ),
            patch.object(main, "iniciar_generacion_borrador") as iniciar,
            patch.object(main, "generar_borrador_seguro") as generar,
        ):
            respuesta = self.cliente.post(
                "/produccion/pergamino-prueba/generar-borrador",
                follow_redirects=False,
            )

        self.assertEqual(respuesta.status_code, 303)
        iniciar.assert_called_once()
        generar.assert_called_once()
        self.assertEqual(iniciar.call_args.args, generar.call_args.args)

    def test_aprobacion_final_y_paquete_requieren_acciones_separadas(self):
        self.crear_proyecto()
        with (
            patch.object(main, "aprobar_borrador") as aprobar,
            patch.object(main, "crear_paquete") as empaquetar,
        ):
            respuesta_aprobacion = self.cliente.post(
                "/produccion/pergamino-prueba/aprobar-borrador",
                follow_redirects=False,
            )
            aprobar.assert_called_once()
            empaquetar.assert_not_called()

            respuesta_paquete = self.cliente.post(
                "/produccion/pergamino-prueba/crear-paquete",
                follow_redirects=False,
            )

        self.assertEqual(respuesta_aprobacion.status_code, 303)
        self.assertEqual(respuesta_paquete.status_code, 303)
        empaquetar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
