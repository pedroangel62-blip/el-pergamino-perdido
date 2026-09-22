"""Worker persistente para renderizar el vídeo fuera del servidor web."""

import os
import sys

from backend.produccion import (
    generar_borrador_seguro,
    guardar_estado,
    registrar_proceso_montaje,
)


def main() -> int:
    if len(sys.argv) != 2:
        return 2

    directorio_proyecto = sys.argv[1]
    try:
        registrar_proceso_montaje(
            directorio_proyecto,
            os.getpid(),
        )
        generar_borrador_seguro(directorio_proyecto)
    except Exception as error:
        try:
            guardar_estado(
                directorio_proyecto,
                "error",
                error=f"El proceso de montaje terminó de forma inesperada: {error}",
                borrador_aprobado=False,
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
