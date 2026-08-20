import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable


DIRECTORIO_PROYECTOS_APROBADOS = os.path.join(
    "backend",
    "data",
    "proyectos_aprobados",
)
ACCIONES_PERMITIDAS = {"comprobar", "generar"}
CONFIRMACION_COSTE_MUSICA = (
    "AUTORIZO_CONSUMIR_HASTA_2000_CREDITOS_ELEVENLABS_MUSICA"
)
CREDITOS_MAXIMOS_AUTORIZADOS = 2000
CREDITOS_MUSICA_POR_MINUTO = 900
DURACION_MUSICA_NAZCA_MS = 110_000
MODELO_MUSICA = "music_v2"
URL_MUSICA = (
    "https://api.elevenlabs.io/v1/music"
    "?output_format=mp3_48000_192"
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


def calcular_creditos_estimados(duracion_ms: int) -> int:
    return math.ceil(
        duracion_ms / 60_000 * CREDITOS_MUSICA_POR_MINUTO
    )


def cargar_solicitud_musica(
    ruta_solicitud: str,
    directorio_aprobados: str = DIRECTORIO_PROYECTOS_APROBADOS,
) -> dict:
    solicitud = _leer_json(ruta_solicitud, "la solicitud de musica")
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
    ruta_proyecto = os.path.join(directorio_aprobados, f"{proyecto_id}.json")
    if not os.path.isfile(ruta_proyecto):
        raise ValueError("El proyecto editorial aprobado no existe.")

    proyecto = _leer_json(ruta_proyecto, "el proyecto editorial aprobado")
    if proyecto.get("proyecto_id") != proyecto_id:
        raise ValueError("El identificador interno del proyecto no coincide.")

    resultado = proyecto.get("resultado")
    if not isinstance(resultado, dict):
        raise ValueError("El proyecto editorial aprobado esta incompleto.")
    prompt = str(resultado.get("musica", "")).strip()
    if not prompt:
        raise ValueError("El proyecto no contiene un prompt musical aprobado.")

    aprobaciones = resultado.get("_aprobaciones")
    sincronizacion = resultado.get("sincronizacion_aprobada")
    if not isinstance(aprobaciones, dict) or not all(
        aprobaciones.get(clave)
        for clave in ("guion", "plan_visual", "sincronizacion")
    ):
        raise ValueError("Faltan aprobaciones editoriales previas a la musica.")
    if not isinstance(sincronizacion, dict) or not sincronizacion.get("aprobada"):
        raise ValueError("La sincronizacion no esta aprobada.")
    if sincronizacion.get("sin_subtitulos") is not True:
        raise ValueError("El proyecto debe permanecer sin subtitulos.")

    duracion_ms = int(solicitud.get("duracion_ms", 0))
    if duracion_ms != DURACION_MUSICA_NAZCA_MS:
        raise ValueError("La musica de Nazca debe durar exactamente 110 segundos.")
    duracion_total_ms = round(float(sincronizacion.get("duracion_total", 0)) * 1000)
    if duracion_ms < duracion_total_ms:
        raise ValueError("La musica no cubre la duracion total del video.")

    creditos_maximos = int(solicitud.get("creditos_maximos", 0))
    creditos_estimados = calcular_creditos_estimados(duracion_ms)
    if creditos_maximos != CREDITOS_MAXIMOS_AUTORIZADOS:
        raise ValueError("El limite autorizado debe ser exactamente 2000 creditos.")
    if creditos_estimados > creditos_maximos:
        raise ValueError("La estimacion supera el limite de creditos autorizado.")

    modelo = str(solicitud.get("modelo", "")).strip()
    if modelo != MODELO_MUSICA:
        raise ValueError("El modelo musical debe ser music_v2.")
    if accion == "generar" and (
        solicitud.get("confirmacion_coste") != CONFIRMACION_COSTE_MUSICA
    ):
        raise ValueError(
            "Falta la autorizacion exacta para consumir creditos de musica."
        )

    return {
        "accion": accion,
        "solicitud_id": solicitud_id,
        "proyecto_id": proyecto_id,
        "proyecto": proyecto,
        "prompt": prompt,
        "duracion_ms": duracion_ms,
        "modelo": modelo,
        "creditos_estimados": creditos_estimados,
        "creditos_maximos": creditos_maximos,
    }


def solicitar_musica_elevenlabs(
    prompt: str,
    duracion_ms: int,
    modelo: str,
) -> dict:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta el secreto ELEVENLABS_API_KEY.")

    cuerpo = json.dumps({
        "prompt": prompt,
        "music_length_ms": duracion_ms,
        "model_id": modelo,
        "force_instrumental": True,
        "sign_with_c2pa": False,
    }).encode("utf-8")
    peticion = urllib.request.Request(
        URL_MUSICA,
        data=cuerpo,
        headers={
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=600) as respuesta:
            audio = respuesta.read()
            song_id = respuesta.headers.get("song-id", "")
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"ElevenLabs rechazo la generacion musical (HTTP {error.code})."
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError("No se pudo conectar con ElevenLabs.") from error

    if len(audio) < 1024:
        raise RuntimeError("ElevenLabs no devolvio una pista musical valida.")
    return {"audio": audio, "song_id": song_id}


def _guardar_json(ruta: str, datos: dict) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)


def _sha256_bytes(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def producir_musica(
    solicitud: dict,
    directorio_salida: str,
    generador: Callable[[str, int, str], dict] = solicitar_musica_elevenlabs,
) -> dict:
    if solicitud.get("accion") != "generar":
        raise ValueError("La solicitud no autoriza generar la musica.")

    proyecto_id = solicitud["proyecto_id"]
    destino = os.path.abspath(os.path.join(directorio_salida, proyecto_id))
    if os.path.exists(destino):
        raise FileExistsError(
            "La salida de esta solicitud ya existe; no se regenerara la musica."
        )
    os.makedirs(destino)

    resultado = generador(
        solicitud["prompt"],
        solicitud["duracion_ms"],
        solicitud["modelo"],
    )
    audio = resultado.get("audio") if isinstance(resultado, dict) else None
    if not isinstance(audio, bytes) or len(audio) < 1024:
        raise RuntimeError("La generacion no produjo una pista musical valida.")

    ruta_musica = os.path.join(destino, "musica.mp3")
    with open(ruta_musica, "wb") as archivo:
        archivo.write(audio)

    proyecto = json.loads(json.dumps(solicitud["proyecto"], ensure_ascii=False))
    _guardar_json(os.path.join(destino, "proyecto.json"), proyecto)
    manifiesto = {
        "solicitud_id": solicitud["solicitud_id"],
        "proyecto_id": proyecto_id,
        "tema": str(proyecto.get("tema", "")),
        "modelo": solicitud["modelo"],
        "instrumental": True,
        "duracion_solicitada_segundos": solicitud["duracion_ms"] / 1000,
        "creditos_estimados": solicitud["creditos_estimados"],
        "creditos_maximos_autorizados": solicitud["creditos_maximos"],
        "song_id": str(resultado.get("song_id", "")),
        "prompt_sha256": _sha256_bytes(solicitud["prompt"].encode("utf-8")),
        "archivo": {
            "nombre": "musica.mp3",
            "bytes": len(audio),
            "sha256": _sha256_bytes(audio),
        },
        "creado": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _guardar_json(os.path.join(destino, "manifiesto-musica.json"), manifiesto)
    return manifiesto
