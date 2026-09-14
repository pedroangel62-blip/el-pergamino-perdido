import unittest
from unittest.mock import patch

from backend.busqueda_imagenes import (
    extraer_candidatas_de_pagina,
    extraer_resultados_imagen,
    buscar_imagenes_reales,
    eliminar_duplicados,
)
from backend.main import fusionar_candidatas


class BusquedaImagenesTests(unittest.TestCase):
    def test_extrae_formato_oficial_de_resultados_de_imagen(self):
        respuesta = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "query": "Alaska fotografía histórica",
                    },
                    "results": [
                        {
                            "type": "image_result",
                            "image_url": (
                                "https://cdn.example/alaska-1.jpg"
                            ),
                            "thumbnail_url": (
                                "https://cdn.example/alaska-1-thumb.jpg"
                            ),
                            "source_website_url": (
                                "https://archivo.example/ficha"
                            ),
                            "caption": "Alaska en una fotografía histórica",
                        }
                    ],
                }
            ]
        }

        resultados = extraer_resultados_imagen(respuesta)

        self.assertEqual(len(resultados), 1)
        self.assertEqual(
            resultados[0]["imagen_url"],
            "https://cdn.example/alaska-1.jpg",
        )
        self.assertEqual(
            resultados[0]["fuente_url"],
            "https://archivo.example/ficha",
        )

    def test_conserva_imagenes_distintas_de_la_misma_pagina(self):
        candidatas = [
            {
                "imagen_url": "https://archivo.example/a.jpg",
                "miniatura_url": "https://archivo.example/a-thumb.jpg",
                "fuente_url": "https://archivo.example/ficha",
                "descripcion": "Misma ficha",
            },
            {
                "imagen_url": "https://archivo.example/b.jpg",
                "miniatura_url": "https://archivo.example/b-thumb.jpg",
                "fuente_url": "https://archivo.example/ficha",
                "descripcion": "Misma ficha",
            },
        ]

        resultados = eliminar_duplicados(candidatas)

        self.assertEqual(len(resultados), 2)

    def test_extrae_varias_imagenes_de_la_pagina_fuente(self):
        html = """
        <html>
          <head>
            <title>Galería histórica</title>
            <meta name="description"
                  content="Archivo fotográfico histórico">
          </head>
          <body>
            <img src="/media/uno.jpg" alt="Primera fotografía">
            <img data-src="/media/dos.jpg" alt="Segunda fotografía">
            <a href="/media/tres.jpg">Tercera</a>
          </body>
        </html>
        """
        padre = {
            "imagen_url": "https://archivo.example/media/original.jpg",
            "fuente_url": "https://archivo.example/ficha",
            "fuente_nombre": "archivo.example",
            "titulo": "Alaska",
            "descripcion": "Alaska en una fotografía histórica",
        }

        resultados = extraer_candidatas_de_pagina(
            "https://archivo.example/ficha",
            html,
            padre,
        )

        self.assertEqual(len(resultados), 3)
        self.assertTrue(
            all(
                resultado["fuente_url"]
                == "https://archivo.example/ficha"
                for resultado in resultados
            )
        )
        self.assertTrue(
            all(
                resultado["contexto_fuente"]
                for resultado in resultados
            )
        )

    def test_busqueda_incluye_galeria_sin_perder_resultado_original(self):
        padre = {
            "imagen_url": "https://archivo.example/original.jpg",
            "miniatura_url": "https://archivo.example/original-thumb.jpg",
            "fuente_url": "https://archivo.example/ficha",
            "fuente_nombre": "archivo.example",
            "titulo": "Alaska",
            "autor": "",
            "descripcion": "Alaska en una fotografía histórica",
        }
        html = """
        <html>
          <head><title>Alaska — galería</title></head>
          <body>
            <img src="/media/uno.jpg" alt="Alaska, primera foto">
            <img src="/media/dos.jpg" alt="Alaska, segunda foto">
          </body>
        </html>
        """

        with patch(
            "backend.busqueda_imagenes.obtener_modo_busqueda",
            return_value="real",
        ), patch(
            "backend.busqueda_imagenes.buscar_en_una_respuesta",
            return_value=[padre],
        ), patch(
            "backend.busqueda_imagenes.obtener_html_publico",
            return_value=("https://archivo.example/ficha", html),
        ):
            resultados = buscar_imagenes_reales(
                ["Alaska fotografía histórica"],
                max_resultados=10,
            )

        self.assertEqual(len(resultados), 3)
        self.assertEqual(
            resultados[0]["imagen_url"],
            "https://archivo.example/original.jpg",
        )
        self.assertTrue(
            any(resultado.get("origen_galeria") for resultado in resultados)
        )

    def test_fusiona_resultados_nuevos_y_anteriores(self):
        anteriores = [
            {
                "imagen_url": "https://archivo.example/antigua.jpg",
                "miniatura_url": "https://archivo.example/antigua-thumb.jpg",
            }
        ]
        nuevas = [
            {
                "imagen_url": "https://archivo.example/nueva.jpg",
                "miniatura_url": "https://archivo.example/nueva-thumb.jpg",
            }
        ]

        resultados = fusionar_candidatas(anteriores, nuevas)

        self.assertEqual(len(resultados), 2)
        self.assertEqual(
            resultados[0]["imagen_url"],
            "https://archivo.example/nueva.jpg",
        )
        self.assertEqual(
            resultados[1]["imagen_url"],
            "https://archivo.example/antigua.jpg",
        )


if __name__ == "__main__":
    unittest.main()
