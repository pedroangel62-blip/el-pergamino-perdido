import hashlib
import json
import os
import re
from datetime import datetime
from typing import Callable

from backend.voz import generar_voz


DIRECTORIO_PROYECTOS_APROBADOS = os.path.join(
    "backend",
    "data",
    "proyectos_aprobados",
)
ACCIONES_PERMITIDAS = {"comprobar", "generar"}
CONFIRMACION_COSTE = "AUTORIZO_CONSUMIR_CREDITOS_ELEVENLABS"
ARCHIVOS_VOZ = (
    "voz.mp3",
    "voz.json",
    "voz-alineacion.json",
)


def _leer_json(ruta: str, descripcion: str) -> dict:
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"No se pudo leer {descripcion}.") from error

    if not isinstance(datos, dict):
        raise ValueError(f"{descripcion.capitalize()} no tiene un formato valido.")

    return datos


def _validar_identificador(valor: object, nombre: str) -> str:
    identificador = str(valor or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", identificador):
        raise ValueError(f"{nombre} no es valido.")
    return identificador


def cargar_solicitud(
    ruta_solicitud: str,
    directorio_aprobados: str = DIRECTORIO_PROYECTOS_APROBADOS,
) -> dict:
    solicitud = _leer_json(ruta_solicitud, "la solicitud de voz")
    accion = str(solicitud.get("accion", "")).strip().lower()
    if accion not in ACCIONES_PERMITIDAS:
        raise ValueError("La accion debe ser comprobar o generar.")

    solicitud_id = _validar_identificador(
        solicitud.get("solicitud_id"),
        "El identificador de la solicitud",
    )
    proyecto_id = _validar_identificador(
        solicitud.get("proyecto_id"),
        "El identificador del proyecto",
    )
    ruta_proyecto = os.path.join(
        directorio_aprobados,
        f"{proyecto_id}.json",
    )
    if not os.path.isfile(ruta_proyecto):
        raise ValueError("El proyecto editorial aprobado no existe.")

    proyecto = _leer_json(ruta_proyecto, "el proyecto editorial aprobado")
    if proyecto.get("proyecto_id") != proyecto_id:
        raise ValueError("El identificador interno del proyecto no coincide.")

    resultado = proyecto.get("resultado")
    if not isinstance(resultado, dict):
        raise ValueError("El proyecto editorial aprobado esta incompleto.")

    guion = str(resultado.get("guion", "")).strip()
    plan_visual = resultado.get("plan_visual")
    aprobaciones = resultado.get("_aprobaciones")
    if not guion:
        raise ValueError("El proyecto no contiene un guion aprobado.")
    if not isinstance(plan_visual, list) or len(plan_visual) != 8:
        raise ValueError("El proyecto debe contener exactamente ocho imagenes.")
    if not isinstance(aprobaciones, dict) or not aprobaciones.get("guion"):
        raise ValueError("El guion no tiene aprobacion editorial.")

    if accion == "generar" and solicitud.get("confirmacion_coste") != CONFIRMACION_COSTE:
        raise ValueError(
            "Falta la autorizacion exacta para consumir creditos de ElevenLabs."
        )

    return {
        "accion": accion,
        "solicitud_id": solicitud_id,
        "proyecto_id": proyecto_id,
        "proyecto": proyecto,
        "guion": guion,
    }


def _guardar_json(ruta: str, datos: dict) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)


def _sha256(ruta: str) -> str:
    resumen = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def producir_voz(
    solicitud: dict,
    directorio_salida: str,
    generador: Callable[[str, str], dict] = generar_voz,
) -> dict:
    if solicitud.get("accion") != "generar":
        raise ValueError("La solicitud no autoriza generar la voz.")

    proyecto_id = solicitud["proyecto_id"]
    destino = os.path.abspath(
        os.path.join(directorio_salida, proyecto_id)
    )
    if os.path.exists(destino):
        raise FileExistsError(
            "La salida de esta solicitud ya existe; no se regenerara la voz."
        )

    os.makedirs(destino)
    proyecto = json.loads(json.dumps(solicitud["proyecto"], ensure_ascii=False))
    proyecto["resultado"]["_proyecto_id"] = proyecto_id
    _guardar_json(os.path.join(destino, "proyecto.json"), proyecto)

    estado = generador(destino, solicitud["guion"])
    if not isinstance(estado, dict) or estado.get("estado") == "error":
        detalle = estado.get("error", "") if isinstance(estado, dict) else ""
        raise RuntimeError(detalle or "ElevenLabs no pudo generar la voz.")

    faltantes = [
        nombre
        for nombre in ARCHIVOS_VOZ
        if not os.path.isfile(os.path.join(destino, nombre))
    ]
    if faltantes:
        raise RuntimeError(
            "La generacion no produjo todos los archivos requeridos: "
            + ", ".join(faltantes)
        )

    archivos = {}
    for nombre in ("proyecto.json", *ARCHIVOS_VOZ):
        ruta = os.path.join(destino, nombre)
        archivos[nombre] = {
            "bytes": os.path.getsize(ruta),
            "sha256": _sha256(ruta),
        }

    manifiesto = {
        "solicitud_id": solicitud["solicitud_id"],
        "proyecto_id": proyecto_id,
        "tema": str(proyecto.get("tema", "")),
        "caracteres_guion": len(solicitud["guion"]),
        "estado": estado,
        "archivos": archivos,
        "creado": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _guardar_json(os.path.join(destino, "manifiesto-voz.json"), manifiesto)
    return manifiesto
