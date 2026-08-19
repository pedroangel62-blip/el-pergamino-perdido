import json
import os
import tempfile
import unittest

from backend.indice_temas import (
    construir_contexto_indice,
    obtener_tema_por_id,
    obtener_tema_por_titulo,
    validar_seleccion,
)


class IndiceTemasTests(unittest.TestCase):
    def test_indice_contiene_banco_y_archivo_el_caso(self):
        with tempfile.TemporaryDirectory() as directorio:
            contexto = construir_contexto_indice(directorio)

        self.assertEqual(len(contexto["indice_categorias"]), 8)
        self.assertEqual(contexto["indice_totales"]["temas"], 109)
        self.assertEqual(contexto["indice_totales"]["disponibles"], 108)
        self.assertEqual(contexto["indice_totales"]["bloqueados"], 1)
        self.assertEqual(
            contexto["indice_recomendado"]["id"],
            "el-caso-001"
        )

    def test_proyecto_filadelfia_esta_unificado(self):
        ficha = obtener_tema_por_titulo(
            "El experimento de Filadelfia"
        )

        self.assertIsNotNone(ficha)
        self.assertEqual(ficha["id"], "banco-077")
        self.assertEqual(ficha["titulo"], "Proyecto Filadelfia")
        self.assertEqual(ficha["numeros_origen"], [77, 79])

    def test_grado_c_permanece_bloqueado(self):
        ficha = obtener_tema_por_id("el-caso-030")

        self.assertEqual(ficha["estado"], "bloqueado")

        with tempfile.TemporaryDirectory() as directorio:
            with self.assertRaisesRegex(
                ValueError,
                "bloqueado"
            ):
                validar_seleccion(ficha, directorio)

    def test_un_tema_usado_no_se_recomienda_ni_se_repite(self):
        with tempfile.TemporaryDirectory() as directorio:
            carpeta = os.path.join(directorio, "pergamino-prueba")
            os.makedirs(carpeta)

            with open(
                os.path.join(carpeta, "proyecto.json"),
                "w",
                encoding="utf-8"
            ) as archivo:
                json.dump(
                    {
                        "tema": "El niño desaparecido de Somosierra",
                        "tema_indice": {"id": "el-caso-001"}
                    },
                    archivo
                )

            contexto = construir_contexto_indice(directorio)

            self.assertEqual(
                contexto["indice_recomendado"]["id"],
                "el-caso-002"
            )
            self.assertEqual(contexto["indice_totales"]["usados"], 1)

            with self.assertRaisesRegex(
                ValueError,
                "ya se utilizó"
            ):
                validar_seleccion(
                    obtener_tema_por_id("el-caso-001"),
                    directorio
                )


if __name__ == "__main__":
    unittest.main()
