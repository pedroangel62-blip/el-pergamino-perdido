import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from backend.produccion import (
    ARCHIVO_VERIFICACION_AUDIO,
    ARCHIVO_VERIFICACION_PREVIA,
    ARCHIVO_VERIFICACION_TIMELINE,
    ARCHIVO_VERIFICACION_VISUAL,
    CIERRE_SEGUNDOS,
    _crear_clip_cierre,
    aprobar_borrador,
    aprobar_imagenes,
    aprobar_musica,
    aprobar_sincronizacion,
    crear_paquete,
    crear_plan_fotogramas,
    crear_sincronizacion,
    crear_texto_publicacion,
    comprobar_preparacion_montaje,
    generar_borrador,
    guardar_musica,
    obtener_duracion,
    preparar_sincronizacion,
    validar_anclas_plan_visual,
    verificar_preparacion_montaje,
)


PNG_UN_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def crear_audio(
    ruta: str,
    duracion: float,
    frecuencia: int,
    ganancia_db: float = 0.0,
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frecuencia}:duration={duracion}",
            "-af",
            f"volume={ganancia_db:.2f}dB",
            "-q:a",
            "7",
            ruta,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def crear_imagen_sintetica(ruta: str, tono: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=1",
            "-vf",
            f"hue=h={tono}",
            "-frames:v",
            "1",
            ruta,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def sondear_archivo(ruta: str) -> dict:
    resultado = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            ruta,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(resultado.stdout)


def medir_max_db(ruta: str, inicio: float, duracion: float) -> float:
    resultado = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{inicio:.3f}",
            "-t",
            f"{duracion:.3f}",
            "-i",
            ruta,
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    coincidencia = re.search(
        r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?)) dB",
        resultado.stderr,
        re.IGNORECASE,
    )
    if not coincidencia:
        raise AssertionError("FFmpeg no devolvió max_volume")
    valor = coincidencia.group(1).lower()
    return float("-inf") if valor == "-inf" else float(valor)


def crear_plan_visual(anclas: list[str]) -> list[dict]:
    return [
        {
            "numero": numero,
            "frase_entrada": frase,
            "motivo": f"Contenido de la Imagen {numero}",
        }
        for numero, frase in enumerate(anclas, start=1)
    ]


def guardar_alineacion_uniforme(
    directorio: str,
    guion: str,
    duracion: float,
) -> dict:
    paso = max(0.001, (duracion - 0.05) / len(guion))
    alineacion = {
        "characters": list(guion),
        "character_start_times_seconds": [
            indice * paso for indice in range(len(guion))
        ],
        "character_end_times_seconds": [
            (indice + 1) * paso for indice in range(len(guion))
        ],
    }
    with open(
        os.path.join(directorio, "voz-alineacion.json"),
        "w",
        encoding="utf-8",
    ) as archivo:
        json.dump(
            {
                "guion_sha256": hashlib.sha256(
                    guion.encode("utf-8")
                ).hexdigest(),
                "alignment": alineacion,
            },
            archivo,
        )
    return alineacion


class SincronizacionTests(unittest.TestCase):
    def test_cuantiza_los_cortes_sin_deriva_acumulada(self):
        marcas = [
            0.0,
            3.0,
            11.017,
            21.049,
            31.083,
            41.118,
            51.151,
            61.184,
            71.219,
        ]
        sincronizacion = [
            {
                "numero": indice + 1,
                "inicio": marcas[indice],
                "fin": marcas[indice + 1],
            }
            for indice in range(8)
        ]

        plan = crear_plan_fotogramas(sincronizacion, fps=30)

        self.assertEqual(len(plan), 9)
        self.assertEqual(plan[1]["fotograma_inicio"], 90)
        self.assertEqual(plan[-1]["fotogramas"], 90)
        self.assertTrue(
            all(
                actual["fotograma_fin"]
                == siguiente["fotograma_inicio"]
                for actual, siguiente in zip(plan, plan[1:])
            )
        )
        self.assertLessEqual(
            max(
                abs(float(corte["desviacion_inicio_ms"]))
                for corte in plan
            ),
            1000 / 30,
        )

    def test_ocho_segmentos_contiguos_con_portada_de_tres_segundos(self):
        guion = " ".join(f"palabra{indice}" for indice in range(1, 161))
        segmentos = crear_sincronizacion(guion, 80.0)

        self.assertEqual(len(segmentos), 8)
        self.assertEqual(segmentos[0]["inicio"], 0.0)
        self.assertEqual(segmentos[0]["fin"], 3.0)
        self.assertEqual(segmentos[-1]["fin"], 80.0)

        for actual, siguiente in zip(segmentos, segmentos[1:]):
            self.assertEqual(actual["fin"], siguiente["inicio"])
            self.assertTrue(actual["frase_entrada"])

    def test_rechaza_un_guion_demasiado_corto(self):
        with self.assertRaisesRegex(ValueError, "demasiado corto"):
            crear_sincronizacion("solo siete palabras para un guion corto", 20)

    def test_usa_tiempos_reales_y_limites_naturales_de_elevenlabs(self):
        guion = (
            "Primera frase del misterio. Segunda pista bajo la lluvia, "
            "muy cerca del puerto. Tercera señal en la pared antigua. "
            "Cuarta huella detrás del reloj. Quinta clave dentro del sobre. "
            "Sexta sombra junto a la ventana. Séptima respuesta al amanecer. "
            "Octava verdad que cierra definitivamente el caso."
        )
        paso = 0.08
        alineacion = {
            "characters": list(guion),
            "character_start_times_seconds": [
                round(indice * paso, 3) for indice in range(len(guion))
            ],
            "character_end_times_seconds": [
                round((indice + 1) * paso, 3) for indice in range(len(guion))
            ],
        }
        duracion = len(guion) * paso
        plan_visual = crear_plan_visual(
            [
                "Primera frase del misterio.",
                "Segunda pista bajo la lluvia,",
                "Tercera señal en la pared antigua.",
                "Cuarta huella detrás del reloj.",
                "Quinta clave dentro del sobre.",
                "Sexta sombra junto a la ventana.",
                "Séptima respuesta al amanecer.",
                "Octava verdad que cierra definitivamente el caso.",
            ]
        )
        segmentos = crear_sincronizacion(
            guion,
            duracion,
            alineacion=alineacion,
            plan_visual=plan_visual,
        )
        tiempos_reales = set(alineacion["character_start_times_seconds"])

        self.assertEqual(segmentos[0]["fin"], 3.0)
        self.assertTrue(
            all(
                segmento["metodo"] == "elevenlabs_semantic_alignment"
                for segmento in segmentos
            )
        )
        self.assertTrue(
            all(
                segmento["inicio"] in tiempos_reales
                for segmento in segmentos[2:]
            )
        )
        self.assertTrue(
            all(segmento.get("palabras_alineadas") for segmento in segmentos)
        )

        primera_palabra = segmentos[0]["palabras_alineadas"][0]
        self.assertEqual(primera_palabra["texto"], "Primera")
        self.assertEqual(
            segmentos[2]["frase_entrada"],
            "Tercera señal en la pared antigua.",
        )
        self.assertTrue(
            all(
                segmento["semantica_validada"]
                for segmento in segmentos
            )
        )

    def test_rechaza_una_frase_visual_que_no_existe_en_el_guion(self):
        guion = "Uno abre el relato. Dos sigue la pista. Tres cierra el caso."
        plan_visual = crear_plan_visual(
            [
                "Uno abre el relato.",
                "Dos sigue la pista.",
                "frase inexistente",
                "Tres cierra el caso.",
                "otra frase",
                "otra frase distinta",
                "penúltima frase",
                "última frase",
            ]
        )

        with self.assertRaisesRegex(ValueError, "Imagen 3"):
            validar_anclas_plan_visual(guion, plan_visual)

    def test_no_permite_aprobar_una_sincronizacion_no_semantica(self):
        with tempfile.TemporaryDirectory() as directorio:
            imagenes = os.path.join(directorio, "imagenes")
            os.makedirs(imagenes)
            for numero in range(1, 9):
                with open(
                    os.path.join(imagenes, f"imagen{numero}.png"),
                    "wb",
                ) as archivo:
                    archivo.write(PNG_UN_PIXEL)

            aprobar_imagenes(directorio)
            with open(
                os.path.join(directorio, "sincronizacion.json"),
                "w",
                encoding="utf-8",
            ) as archivo:
                json.dump(
                    {
                        "semantica_validada": False,
                        "segmentos": [
                            {"numero": numero}
                            for numero in range(1, 9)
                        ],
                    },
                    archivo,
                )

            with self.assertRaisesRegex(ValueError, "frases exactas"):
                aprobar_sincronizacion(directorio)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "FFmpeg no está instalado",
    )
    def test_preparacion_identifica_alineacion_real(self):
        with tempfile.TemporaryDirectory() as directorio:
            imagenes = os.path.join(directorio, "imagenes")
            os.makedirs(imagenes)
            for numero in range(1, 9):
                crear_imagen_sintetica(
                    os.path.join(imagenes, f"imagen{numero}.png"),
                    numero * 30,
                )
            voz = os.path.join(directorio, "voz.mp3")
            crear_audio(voz, 8.0, 440)
            duracion_voz = obtener_duracion(voz)
            guion = " ".join(f"palabra{indice}" for indice in range(1, 81))
            paso = 7.9 / len(guion)
            alineacion = {
                "characters": list(guion),
                "character_start_times_seconds": [
                    indice * paso for indice in range(len(guion))
                ],
                "character_end_times_seconds": [
                    (indice + 1) * paso for indice in range(len(guion))
                ],
            }
            with open(
                os.path.join(directorio, "voz-alineacion.json"),
                "w",
                encoding="utf-8",
            ) as archivo:
                json.dump(
                    {
                        "guion_sha256": hashlib.sha256(
                            guion.encode("utf-8")
                        ).hexdigest(),
                        "alignment": alineacion,
                    },
                    archivo,
                )

            plan_visual = crear_plan_visual(
                [
                    "palabra1 palabra2",
                    "palabra10 palabra11",
                    "palabra35 palabra36",
                    "palabra43 palabra44",
                    "palabra51 palabra52",
                    "palabra59 palabra60",
                    "palabra67 palabra68",
                    "palabra75 palabra76",
                ]
            )
            segmentos = preparar_sincronizacion(
                directorio,
                guion,
                plan_visual,
            )

            with open(
                os.path.join(directorio, "sincronizacion.json"),
                "r",
                encoding="utf-8",
            ) as archivo:
                guardada = json.load(archivo)

            self.assertEqual(
                guardada["metodo"],
                "elevenlabs_semantic_alignment",
            )
            self.assertTrue(guardada["semantica_validada"])
            self.assertEqual(len(segmentos), 8)
            self.assertEqual(guardada["cierre"]["numero"], 9)
            self.assertEqual(guardada["cierre"]["inicio"], duracion_voz)
            self.assertEqual(
                guardada["cierre"]["fin"],
                round(duracion_voz + CIERRE_SEGUNDOS, 3),
            )
            self.assertEqual(guardada["cierre"]["duracion"], CIERRE_SEGUNDOS)
            self.assertFalse(guardada["cierre"]["voz"])
            self.assertTrue(guardada["cierre"]["musica"])


class PublicacionTests(unittest.TestCase):
    def test_limita_los_hashtags_y_anade_recordatorio(self):
        texto = crear_texto_publicacion(
            {
                "publicacion": {
                    "titulo": "📜 EL PERGAMINO 14 — Prueba",
                    "descripcion": "Descripción",
                    "hashtags": ["#1", "#2", "#3", "#4", "#5", "#6"],
                    "comentario_fijado": "Comentario",
                }
            }
        )

        self.assertIn("#1 #2 #3 #4 #5", texto)
        self.assertNotIn("#6", texto)
        self.assertIn("fíjalo manualmente", texto)


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "FFmpeg no está instalado",
)
class MontajeTests(unittest.TestCase):
    def test_clip_cierre_dura_exactamente_tres_segundos(self):
        with tempfile.TemporaryDirectory() as directorio:
            sello = os.path.join(directorio, "sello.png")
            cierre = os.path.join(directorio, "cierre.mp4")
            with open(sello, "wb") as archivo:
                archivo.write(PNG_UN_PIXEL)

            _crear_clip_cierre(
                sello,
                cierre,
                ancho=180,
                alto=320,
                fps=30,
            )

            self.assertAlmostEqual(
                obtener_duracion(cierre),
                CIERRE_SEGUNDOS,
                delta=0.05,
            )

    def test_genera_aprueba_y_empaqueta_un_reel(self):
        with tempfile.TemporaryDirectory() as directorio:
            imagenes = os.path.join(directorio, "imagenes")
            os.makedirs(imagenes)

            for numero in range(1, 9):
                crear_imagen_sintetica(
                    os.path.join(imagenes, f"imagen{numero}.png"),
                    numero * 35,
                )

            voz = os.path.join(directorio, "voz.mp3")
            musica_origen = os.path.join(directorio, "musica-origen.mp3")
            crear_audio(voz, 20.0, 440)
            duracion_voz = obtener_duracion(voz)
            crear_audio(musica_origen, 20.0, 220)
            guion = " ".join(f"palabra{indice}" for indice in range(1, 81))
            guardar_alineacion_uniforme(directorio, guion, duracion_voz)
            plan_visual = crear_plan_visual(
                [
                    "palabra1 palabra2",
                    "palabra10 palabra11",
                    "palabra35 palabra36",
                    "palabra43 palabra44",
                    "palabra51 palabra52",
                    "palabra59 palabra60",
                    "palabra67 palabra68",
                    "palabra75 palabra76",
                ]
            )
            aprobar_imagenes(directorio)
            with open(
                os.path.join(directorio, "subtitulos.srt"),
                "w",
                encoding="utf-8",
            ) as archivo:
                archivo.write("archivo heredado que debe eliminarse")
            segmentos = preparar_sincronizacion(
                directorio,
                guion,
                plan_visual,
            )
            self.assertFalse(
                os.path.exists(os.path.join(directorio, "subtitulos.srt"))
            )
            aprobar_sincronizacion(directorio)

            with open(musica_origen, "rb") as archivo:
                guardar_musica(directorio, "fondo.mp3", archivo.read())

            os.remove(musica_origen)
            aprobar_musica(directorio)
            estado = generar_borrador(
                directorio,
                ancho=180,
                alto=320,
                fps=10,
            )

            self.assertEqual(len(segmentos), 8)
            self.assertEqual(estado["estado"], "borrador_pendiente_aprobacion")
            borrador = os.path.join(directorio, "video_borrador.mp4")
            self.assertTrue(os.path.isfile(borrador))
            sondeo = sondear_archivo(borrador)
            tipos = {stream["codec_type"] for stream in sondeo["streams"]}
            self.assertEqual(tipos, {"audio", "video"})
            self.assertEqual(estado["audio_codec"], "aac")
            self.assertGreater(estado["audio_max_db"], -80.0)
            self.assertTrue(estado["timeline_verificada"])
            self.assertGreater(estado["fotogramas_totales"], 0)
            ruta_timeline = os.path.join(
                directorio,
                ARCHIVO_VERIFICACION_TIMELINE,
            )
            self.assertTrue(os.path.isfile(ruta_timeline))
            with open(ruta_timeline, "r", encoding="utf-8") as archivo:
                timeline = json.load(archivo)
            self.assertTrue(timeline["verificada"])
            self.assertEqual(len(timeline["cortes"]), 9)
            self.assertTrue(
                all(corte["verificado"] for corte in timeline["cortes"])
            )
            self.assertEqual(
                timeline["fotogramas_totales"],
                estado["fotogramas_totales"],
            )
            ruta_visual = os.path.join(
                directorio,
                ARCHIVO_VERIFICACION_VISUAL,
            )
            self.assertTrue(os.path.isfile(ruta_visual))
            with open(ruta_visual, "r", encoding="utf-8") as archivo:
                control_visual = json.load(archivo)
            self.assertTrue(control_visual["verificada_automaticamente"])
            self.assertTrue(control_visual["sin_subtitulos"])
            self.assertTrue(control_visual["cierre"]["zoom_detectado"])
            self.assertGreaterEqual(
                control_visual["cierre"][
                    "margen_seguro_por_lado_porcentaje"
                ],
                4.0,
            )
            ruta_audio = os.path.join(
                directorio,
                ARCHIVO_VERIFICACION_AUDIO,
            )
            self.assertTrue(os.path.isfile(ruta_audio))
            with open(ruta_audio, "r", encoding="utf-8") as archivo:
                control_audio = json.load(archivo)
            self.assertTrue(control_audio["verificada"])
            self.assertTrue(control_audio["voz_audible"])
            self.assertTrue(control_audio["musica_subordinada"])
            self.assertTrue(control_audio["sin_saturacion_digital"])
            self.assertLessEqual(
                control_audio["pico_mezcla_final_db"],
                control_audio["pico_maximo_permitido_db"],
            )
            self.assertLessEqual(control_audio["ganancia_musica_db"], -20.0)
            self.assertGreaterEqual(
                control_audio["margen_voz_sobre_musica_db"],
                control_audio["margen_minimo_exigido_db"],
            )
            self.assertGreaterEqual(
                control_audio["caida_fundido_db"],
                control_audio["caida_minima_exigida_db"],
            )
            self.assertAlmostEqual(
                float(sondeo["format"]["duration"]),
                duracion_voz + CIERRE_SEGUNDOS,
                delta=0.5,
            )
            volumen_inicio_cierre = medir_max_db(
                borrador,
                duracion_voz,
                1.0,
            )
            volumen_final_cierre = medir_max_db(
                borrador,
                duracion_voz + CIERRE_SEGUNDOS - 0.25,
                0.25,
            )
            self.assertGreater(volumen_inicio_cierre, -80.0)
            self.assertLess(volumen_final_cierre, volumen_inicio_cierre)

            aprobar_borrador(directorio)
            resultado = {
                "publicacion": {
                    "titulo": "Título",
                    "descripcion": "Descripción",
                    "hashtags": ["#historia"],
                    "comentario_fijado": "Comentario",
                }
            }
            estado_final = crear_paquete(directorio, resultado)

            self.assertEqual(estado_final["estado"], "paquete_preparado")
            paquete = os.path.join(directorio, "proyecto_completo.zip")

            with zipfile.ZipFile(paquete) as archivo_zip:
                nombres = set(archivo_zip.namelist())

            self.assertIn("video_final.mp4", nombres)
            self.assertIn("publicacion.txt", nombres)
            self.assertIn("sincronizacion.json", nombres)
            self.assertIn(ARCHIVO_VERIFICACION_TIMELINE, nombres)
            self.assertIn(ARCHIVO_VERIFICACION_VISUAL, nombres)
            self.assertIn(ARCHIVO_VERIFICACION_AUDIO, nombres)
            self.assertIn(ARCHIVO_VERIFICACION_PREVIA, nombres)
            self.assertNotIn("subtitulos.srt", nombres)

    def test_reel_sintetico_completo_a_30_fps(self):
        with tempfile.TemporaryDirectory() as directorio:
            imagenes = os.path.join(directorio, "imagenes")
            os.makedirs(imagenes)

            for numero in range(1, 9):
                crear_imagen_sintetica(
                    os.path.join(imagenes, f"imagen{numero}.png"),
                    numero * 35,
                )

            voz = os.path.join(directorio, "voz.mp3")
            musica_origen = os.path.join(directorio, "musica-origen.mp3")
            crear_audio(voz, 75.0, 440, ganancia_db=-12.0)
            crear_audio(musica_origen, 75.0, 220, ganancia_db=8.0)
            duracion_voz = obtener_duracion(voz)
            guion = " ".join(
                f"palabra{indice}" for indice in range(1, 161)
            )
            guardar_alineacion_uniforme(directorio, guion, duracion_voz)
            plan_visual = crear_plan_visual(
                [
                    "palabra1 palabra2",
                    "palabra5 palabra6",
                    "palabra35 palabra36",
                    "palabra59 palabra60",
                    "palabra83 palabra84",
                    "palabra107 palabra108",
                    "palabra131 palabra132",
                    "palabra151 palabra152",
                ]
            )
            aprobar_imagenes(directorio)
            preparar_sincronizacion(directorio, guion, plan_visual)
            aprobar_sincronizacion(directorio)

            control_bloqueado = comprobar_preparacion_montaje(
                directorio,
                voz_aprobada=True,
            )
            self.assertFalse(control_bloqueado["preparado"])
            self.assertIn(
                "musica",
                {
                    comprobacion["clave"]
                    for comprobacion in control_bloqueado["comprobaciones"]
                    if not comprobacion["correcto"]
                },
            )

            with self.assertRaisesRegex(ValueError, "pista musical"):
                generar_borrador(
                    directorio,
                    ancho=180,
                    alto=320,
                    fps=30,
                )

            with open(musica_origen, "rb") as archivo:
                guardar_musica(directorio, "fondo.mp3", archivo.read())
            aprobar_musica(directorio)

            control_previo = verificar_preparacion_montaje(
                directorio,
                voz_aprobada=True,
            )
            self.assertTrue(control_previo["preparado"])
            self.assertTrue(control_previo["sin_subtitulos"])
            self.assertEqual(len(control_previo["comprobaciones"]), 7)
            self.assertTrue(
                os.path.isfile(
                    os.path.join(
                        directorio,
                        ARCHIVO_VERIFICACION_PREVIA,
                    )
                )
            )
            imagen_1 = os.path.join(imagenes, "imagen1.png")
            with open(imagen_1, "rb") as archivo:
                imagen_1_aprobada = archivo.read()
            crear_imagen_sintetica(imagen_1, 177)
            control_archivo_cambiado = comprobar_preparacion_montaje(
                directorio,
                voz_aprobada=True,
            )
            self.assertFalse(control_archivo_cambiado["preparado"])
            self.assertFalse(
                next(
                    comprobacion["correcto"]
                    for comprobacion in control_archivo_cambiado[
                        "comprobaciones"
                    ]
                    if comprobacion["clave"] == "sincronizacion"
                )
            )
            with open(imagen_1, "wb") as archivo:
                archivo.write(imagen_1_aprobada)

            estado = generar_borrador(
                directorio,
                ancho=180,
                alto=320,
                fps=30,
            )

            self.assertEqual(estado["fps"], 30)
            self.assertTrue(estado["control_visual_verificado"])
            self.assertTrue(estado["control_audio_verificado"])
            self.assertTrue(estado["sin_subtitulos"])
            with open(
                os.path.join(directorio, ARCHIVO_VERIFICACION_VISUAL),
                "r",
                encoding="utf-8",
            ) as archivo:
                control_visual = json.load(archivo)
            self.assertEqual(control_visual["cierre"]["fotogramas"], 90)
            self.assertEqual(
                control_visual["transiciones"]["cortes_verificados"],
                8,
            )
            self.assertEqual(control_visual["resolucion"]["fps"], 30.0)
            with open(
                os.path.join(directorio, ARCHIVO_VERIFICACION_AUDIO),
                "r",
                encoding="utf-8",
            ) as archivo:
                control_audio = json.load(archivo)
            self.assertTrue(control_audio["musica_finaliza_en_imagen_9"])
            self.assertTrue(control_audio["sin_saturacion_digital"])
            self.assertGreaterEqual(
                control_audio["margen_voz_sobre_musica_db"],
                14.0,
            )
            self.assertGreaterEqual(control_audio["caida_fundido_db"], 12.0)


if __name__ == "__main__":
    unittest.main()
