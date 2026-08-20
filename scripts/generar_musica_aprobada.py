#!/usr/bin/env python3
import argparse
import json
import os
import sys


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from backend.musica_remota import cargar_solicitud_musica, producir_musica


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida o genera la musica de un proyecto aprobado."
    )
    parser.add_argument("--solicitud", required=True)
    parser.add_argument("--salida", default="salida-musica")
    parser.add_argument("--solo-validar", action="store_true")
    parser.add_argument("--github-output")
    return parser


def main() -> int:
    argumentos = crear_parser().parse_args()
    solicitud = cargar_solicitud_musica(argumentos.solicitud)
    resumen = {
        "accion": solicitud["accion"],
        "solicitud_id": solicitud["solicitud_id"],
        "proyecto_id": solicitud["proyecto_id"],
        "duracion_segundos": solicitud["duracion_ms"] / 1000,
        "creditos_estimados": solicitud["creditos_estimados"],
        "creditos_maximos": solicitud["creditos_maximos"],
    }

    if argumentos.github_output:
        with open(argumentos.github_output, "a", encoding="utf-8") as archivo:
            for clave, valor in resumen.items():
                archivo.write(f"{clave}={valor}\n")

    if argumentos.solo_validar:
        print(json.dumps(resumen, ensure_ascii=False))
        return 0

    manifiesto = producir_musica(solicitud, argumentos.salida)
    print(json.dumps({
        "proyecto_id": manifiesto["proyecto_id"],
        "solicitud_id": manifiesto["solicitud_id"],
        "duracion_solicitada_segundos": manifiesto[
            "duracion_solicitada_segundos"
        ],
        "creditos_estimados": manifiesto["creditos_estimados"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
