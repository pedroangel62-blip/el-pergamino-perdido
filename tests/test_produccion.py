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
    CIERRE_SEGUNDOS,
    aprobar_borrador,
    aprobar_imagenes,
    aprobar_musica,
    aprobar_sincronizacion,
    crear_paquete,
    crear_sincronizacion,
    crear_subtitulos,
    crear_texto_publicacion,
    generar_borrador,
    guardar_musica,
    obtener_duracion,
    preparar_sincronizacion,
)


PNG_UN_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def crear_audio(ruta: str, duracion: float, frecuencia: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frecuencia}:duration={duracion}",
            "-q:a",
            "7",
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


class SincronizacionTests(unittest.TestCase):
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

        subtitulos = crear_subtitulos(segmentos)
        self.assertGreater(subtitulos.count(" --> "), 8)

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
        segmentos = crear_sincronizacion(
            guion,
            duracion,
            alineacion=alineacion,
        )
        tiempos_reales = set(alineacion["character_start_times_seconds"])

        self.assertEqual(segmentos[0]["fin"], 3.0)
        self.assertTrue(
            all(
                segmento["metodo"] == "elevenlabs_alignment"
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

        subtitulos = crear_subtitulos(segmentos)
        primera_palabra = segmentos[0]["palabras_alineadas"][0]
        self.assertIn("00:00:00,000", subtitulos)
        self.assertEqual(primera_palabra["texto"], "Primera")

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "FFmpeg no está instalado",
    )
    def test_preparacion_identifica_alineacion_real(self):
        with tempfile.TemporaryDirectory() as directorio:
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

            segmentos = preparar_sincronizacion(directorio, guion)

            with open(
                os.path.join(directorio, "sincronizacion.json"),
                "r",
                encoding="utf-8",
            ) as archivo:
                guardada = json.load(archivo)

            self.assertEqual(guardada["metodo"], "elevenlabs_alignment")
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
    def test_genera_aprueba_y_empaqueta_un_reel(self):
        with tempfile.TemporaryDirectory() as directorio:
            imagenes = os.path.join(directorio, "imagenes")
            os.makedirs(imagenes)

            for numero in range(1, 9):
                with open(
                    os.path.join(imagenes, f"imagen{numero}.png"),
                    "wb",
                ) as archivo:
                    archivo.write(PNG_UN_PIXEL)

            voz = os.path.join(directorio, "voz.mp3")
            musica_origen = os.path.join(directorio, "musica-origen.mp3")
            sello = os.path.join(directorio, "sello.png")
            with open(sello, "wb") as archivo:
                archivo.write(PNG_UN_PIXEL)
            crear_audio(voz, 8.0, 440)
            duracion_voz = obtener_duracion(voz)
            crear_audio(musica_origen, 8.0, 220)
            guion = " ".join(f"palabra{indice}" for indice in range(1, 81))
            aprobar_imagenes(directorio)
            segmentos = preparar_sincronizacion(directorio, guion)
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
                sello_cierre=sello,
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


if __name__ == "__main__":
    unittest.main()
