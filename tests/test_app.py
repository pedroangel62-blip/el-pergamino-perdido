import json
import os
import tempfile
import unittest
from types import SimpleNamespace
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

    def test_url_publica_fija_prevalece_sobre_host_local(self):
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(scheme="http"),
        )
        with patch.dict(
            os.environ,
            {
                "INSTAGRAM_PUBLIC_BASE_URL": (
                    "https://pergamino-equipo.tailnet.ts.net/"
                ),
            },
        ):
            resultado = main.obtener_url_publica(request)

        self.assertEqual(
            resultado,
            "https://pergamino-equipo.tailnet.ts.net",
        )

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

    def test_detecta_formato_avif(self):
        contenido = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00"

        self.assertEqual(
            main.detectar_formato_imagen(contenido),
            "avif",
        )

    def test_previsualiza_fotografia_remota_sin_openai(self):
        contenido = b"\x89PNG\r\n\x1a\narchivo"

        with (
            patch.object(
                main,
                "descargar_fotografia",
                return_value=(
                    contenido,
                    "https://cdn.example/foto.png",
                    "png",
                ),
            ),
            patch.object(
                main,
                "obtener_cliente_openai",
                side_effect=AssertionError("No debe llamarse a OpenAI"),
            ),
        ):
            respuesta = self.cliente.get(
                "/previsualizar-fotografia",
                params={"url": "https://cdn.example/foto.png"},
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["content-type"], "image/png")
        self.assertEqual(respuesta.content, contenido)

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

    def test_proyecto_local_guardado_aparece_y_se_abre(self):
        self.crear_proyecto("pergamino-kubrick")

        respuesta = self.cliente.get("/")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("PROYECTOS EDITORIALES APROBADOS Y GUARDADOS", respuesta.text)
        self.assertIn("Tema de prueba", respuesta.text)
        self.assertIn(
            "/recuperar-proyecto-aprobado/pergamino-kubrick",
            respuesta.text,
        )

        respuesta_apertura = self.cliente.post(
            "/recuperar-proyecto-aprobado/pergamino-kubrick",
            follow_redirects=False,
        )

        self.assertEqual(respuesta_apertura.status_code, 303)
        self.assertEqual(
            respuesta_apertura.headers["location"],
            "/proyecto/pergamino-kubrick",
        )

    def test_subir_fotografia_local_no_llama_openai(self):
        proyecto_id = "pergamino-subida-local"
        self.crear_proyecto(proyecto_id)

        ruta_proyecto = os.path.join(
            self.directorio_temporal.name,
            proyecto_id,
            "proyecto.json",
        )
        with open(ruta_proyecto, "r", encoding="utf-8") as archivo:
            proyecto = json.load(archivo)

        proyecto["resultado"]["_proyecto_id"] = proyecto_id
        proyecto["resultado"]["plan_visual"][0]["tipo"] = (
            "FOTOGRAFÍA REAL"
        )

        with open(ruta_proyecto, "w", encoding="utf-8") as archivo:
            json.dump(proyecto, archivo)

        with (
            patch.object(main, "exigir_voz_aprobada"),
            patch.object(
                main,
                "obtener_estado_voz_interfaz",
                return_value={"estado": "aprobada"},
            ),
            patch.object(
                main,
                "obtener_cliente_openai",
                side_effect=AssertionError("No debe llamarse a OpenAI"),
            ),
        ):
            respuesta = self.cliente.post(
                "/subir-fotografia/1",
                data={
                    "tema": "Tema de prueba",
                    "resultado_json": json.dumps(
                        proyecto["resultado"],
                        ensure_ascii=False,
                    ),
                },
                files={
                    "archivo": (
                        "foto.png",
                        b"\x89PNG\r\n\x1a\narchivo",
                        "image/png",
                    )
                },
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            "Fotografía real seleccionada y guardada",
            respuesta.text,
        )
        self.assertIn("imagen1.png", respuesta.text)
        ruta_imagen = os.path.join(
            self.directorio_temporal.name,
            proyecto_id,
            "imagenes",
            "imagen1.png",
        )
        self.assertTrue(os.path.isfile(ruta_imagen))
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.directorio_temporal.name,
                    proyecto_id,
                    "seleccion-imagen-1.json",
                )
            )
        )

    def test_recreacion_ia_explicita_sin_fotografias_candidatas(self):
        proyecto_id = "pergamino-recreacion-explicita"
        self.crear_proyecto(proyecto_id)

        ruta_proyecto = os.path.join(
            self.directorio_temporal.name,
            proyecto_id,
            "proyecto.json",
        )
        with open(ruta_proyecto, "r", encoding="utf-8") as archivo:
            proyecto = json.load(archivo)

        proyecto["resultado"]["_proyecto_id"] = proyecto_id
        proyecto["resultado"]["plan_visual"][0]["tipo"] = (
            "FOTOGRAFÍA REAL"
        )

        with open(ruta_proyecto, "w", encoding="utf-8") as archivo:
            json.dump(proyecto, archivo)

        with patch.object(
            main,
            "obtener_estado_voz_interfaz",
            return_value={"estado": "aprobada"},
        ):
            pagina = self.cliente.get(
                f"/proyecto/{proyecto_id}"
            )
        self.assertEqual(pagina.status_code, 200)
        self.assertIn(
            "No hay una fotografía adecuada: generar",
            pagina.text,
        )
        self.assertIn(
            'name="confirmar_recreacion_ia"',
            pagina.text,
        )

        datos = {
            "tema": "Tema de prueba",
            "resultado_json": json.dumps(
                proyecto["resultado"],
                ensure_ascii=False,
            ),
        }

        with (
            patch.object(main, "exigir_voz_aprobada"),
            patch.object(
                main,
                "obtener_estado_voz_interfaz",
                return_value={"estado": "aprobada"},
            ),
            patch.object(main, "crear_imagen") as crear,
        ):
            bloqueada = self.cliente.post(
                "/generar-imagen/1",
                data=datos,
            )
            autorizada = self.cliente.post(
                "/generar-imagen/1",
                data={
                    **datos,
                    "confirmar_recreacion_ia": "si",
                },
            )

        self.assertEqual(bloqueada.status_code, 400)
        self.assertIn(
            "Antes de generar una recreación IA",
            bloqueada.json()["detail"],
        )
        self.assertEqual(autorizada.status_code, 200)
        self.assertIn("Tiempo transcurrido:", autorizada.text)
        self.assertIn(
            "Próxima comprobación automática en",
            autorizada.text,
        )
        crear.assert_called_once()
        self.assertTrue(crear.call_args.args[-1])


    def test_subir_imagen_externa_en_recreacion_ia_no_llama_openai(self):
        proyecto_id = "pergamino-imagen-externa-ia"
        self.crear_proyecto(proyecto_id)

        ruta_proyecto = os.path.join(
            self.directorio_temporal.name,
            proyecto_id,
            "proyecto.json",
        )
        with open(ruta_proyecto, "r", encoding="utf-8") as archivo:
            proyecto = json.load(archivo)

        proyecto["resultado"]["_proyecto_id"] = proyecto_id
        with open(ruta_proyecto, "w", encoding="utf-8") as archivo:
            json.dump(proyecto, archivo)

        with (
            patch.object(main, "exigir_voz_aprobada"),
            patch.object(
                main,
                "obtener_estado_voz_interfaz",
                return_value={"estado": "aprobada"},
            ),
            patch.object(
                main,
                "obtener_cliente_openai",
                side_effect=AssertionError("No debe llamarse a OpenAI"),
            ),
        ):
            respuesta = self.cliente.post(
                "/subir-fotografia/1",
                data={
                    "tema": "Tema de prueba",
                    "resultado_json": json.dumps(
                        proyecto["resultado"],
                        ensure_ascii=False,
                    ),
                },
                files={
                    "archivo": (
                        "collage-externo.png",
                        b"\x89PNG\r\n\x1a\narchivo",
                        "image/png",
                    )
                },
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            "Imagen externa subida y guardada",
            respuesta.text,
        )
        self.assertIn(
            "Subir una imagen creada fuera de la aplicación",
            respuesta.text,
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.directorio_temporal.name,
                    proyecto_id,
                    "imagenes",
                    "imagen1.png",
                )
            )
        )

        with open(
            os.path.join(
                self.directorio_temporal.name,
                proyecto_id,
                "seleccion-imagen-1.json",
            ),
            "r",
            encoding="utf-8",
        ) as archivo:
            seleccion = json.load(archivo)

        self.assertEqual(
            seleccion["fotografia"]["origen"],
            "imagen_externa",
        )

    def test_pagina_produccion_responde(self):
        self.crear_proyecto()
        respuesta = self.cliente.get("/produccion/pergamino-prueba")

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("PRODUCCIÓN FINAL", respuesta.text)
        self.assertIn("Tema de prueba", respuesta.text)


    def test_video_borrador_admite_descarga_por_rangos(self):
        self.crear_proyecto()
        ruta_video = os.path.join(
            self.directorio_temporal.name,
            "pergamino-prueba",
            "video_borrador.mp4",
        )
        contenido = b"0123456789"
        with open(ruta_video, "wb") as archivo:
            archivo.write(contenido)

        ruta = "/media/proyectos/pergamino-prueba/video_borrador.mp4"
        completo = self.cliente.get(ruta)
        self.assertEqual(completo.status_code, 200)
        self.assertEqual(completo.content, contenido)
        self.assertEqual(completo.headers["content-type"], "video/mp4")
        self.assertEqual(completo.headers["accept-ranges"], "bytes")

        parcial = self.cliente.get(
            ruta,
            headers={"Range": "bytes=2-6"},
        )
        self.assertEqual(parcial.status_code, 206)
        self.assertEqual(parcial.content, b"23456")
        self.assertEqual(
            parcial.headers["content-range"],
            "bytes 2-6/10",
        )

        descarga = self.cliente.get(
            "/descargas/proyectos/pergamino-prueba/video_borrador"
        )
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(descarga.content, contenido)
        self.assertEqual(
            descarga.headers["content-type"],
            "application/octet-stream",
        )
        self.assertIn(
            'attachment; filename="video_borrador.mp4"',
            descarga.headers["content-disposition"],
        )


    def test_biblioteca_musical_se_muestra_y_se_puede_seleccionar(self):
        self.crear_proyecto()
        directorio_anterior = main.DIRECTORIO_MUSICA_BASE

        with tempfile.TemporaryDirectory() as biblioteca:
            try:
                main.DIRECTORIO_MUSICA_BASE = biblioteca
                carpeta = os.path.join(biblioteca, "MP3_MONTAJE")
                os.makedirs(carpeta)
                ruta_pista = os.path.join(carpeta, "Misterio_Prueba.mp3")
                with open(ruta_pista, "wb") as archivo:
                    archivo.write(b"pista de prueba")

                carpeta_maestros = os.path.join(biblioteca, "WAV_MAESTROS")
                os.makedirs(carpeta_maestros)
                with open(
                    os.path.join(carpeta_maestros, "Maestro_Prueba.wav"),
                    "wb",
                ) as archivo:
                    archivo.write(b"maestro que no se muestra")

                pagina = self.cliente.get("/produccion/pergamino-prueba")
                self.assertEqual(pagina.status_code, 200)
                self.assertIn("Misterio Prueba", pagina.text)
                self.assertNotIn("Maestro Prueba", pagina.text)
                self.assertIn("/api/musicas-base/MP3_MONTAJE/Misterio_Prueba.mp3", pagina.text)
                self.assertIn('id="abrir-biblioteca-musica"', pagina.text)
                self.assertIn('id="biblioteca-musica-dialog"', pagina.text)
                self.assertIn("Abrir biblioteca musical", pagina.text)

                catalogo = self.cliente.get("/api/musicas-base")
                self.assertEqual(catalogo.status_code, 200)
                self.assertEqual(
                    catalogo.json()["grupos"][0]["pistas"][0]["formato"],
                    "MP3",
                )
                self.assertNotIn(
                    "Maestro_Prueba",
                    json.dumps(catalogo.json(), ensure_ascii=False),
                )

                preescucha = self.cliente.get(
                    "/api/musicas-base/MP3_MONTAJE/Misterio_Prueba.mp3"
                )
                self.assertEqual(preescucha.status_code, 200)
                self.assertEqual(preescucha.content, b"pista de prueba")
                self.assertEqual(
                    preescucha.headers["content-type"],
                    "audio/mpeg",
                )

                with patch.object(
                    main,
                    "guardar_musica",
                    return_value={"estado": "musica_pendiente_aprobacion"},
                ) as guardar:
                    respuesta = self.cliente.post(
                        "/produccion/pergamino-prueba/musica-biblioteca",
                        data={
                            "ruta_biblioteca": (
                                "MP3_MONTAJE/Misterio_Prueba.mp3"
                            ),
                        },
                        follow_redirects=False,
                    )

                self.assertEqual(respuesta.status_code, 303)
                guardar.assert_called_once_with(
                    os.path.join(
                        self.directorio_temporal.name,
                        "pergamino-prueba",
                    ),
                    "Misterio_Prueba.mp3",
                    b"pista de prueba",
                )
            finally:
                main.DIRECTORIO_MUSICA_BASE = directorio_anterior

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
            patch.object(main, "iniciar_montaje_en_hilo") as iniciar_montaje,
        ):
            respuesta = self.cliente.post(
                "/produccion/pergamino-prueba/generar-borrador",
                follow_redirects=False,
            )

        self.assertEqual(respuesta.status_code, 303)
        iniciar_montaje.assert_called_once_with(
            "pergamino-prueba",
            os.path.join(self.directorio_temporal.name, "pergamino-prueba"),
        )

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
