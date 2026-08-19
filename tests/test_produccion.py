import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from backend.produccion import (
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
            crear_audio(voz, 8.0, 440)
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
            )

            self.assertEqual(len(segmentos), 8)
            self.assertEqual(estado["estado"], "borrador_pendiente_aprobacion")
            self.assertTrue(os.path.isfile(os.path.join(directorio, "video_borrador.mp4")))

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
