from datetime import datetime
import hashlib
import json
import os
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


LIMITE_DURACION_VOZ = 92.0
FORMATO_AUDIO = "mp3_44100_128"
MODELO_PREDETERMINADO = "eleven_multilingual_v2"
MAXIMO_BYTES_AUDIO = 50 * 1024 * 1024
TIEMPO_MAXIMO_GENERACION = 180


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def obtener_ruta_audio(directorio_proyecto: str) -> str:
    return os.path.join(
        directorio_proyecto,
        "voz.mp3"
    )


def obtener_ruta_estado(directorio_proyecto: str) -> str:
    return os.path.join(
        directorio_proyecto,
        "voz.json"
    )


def guardar_json_atomico(ruta: str, datos: dict) -> None:
    directorio = os.path.dirname(ruta)
    os.makedirs(directorio, exist_ok=True)
    descriptor, ruta_temporal = tempfile.mkstemp(
        prefix=".voz-estado-",
        suffix=".tmp",
        dir=directorio
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8"
        ) as archivo:
            json.dump(
                datos,
                archivo,
                ensure_ascii=False,
                indent=2
            )

        os.replace(ruta_temporal, ruta)
    except Exception:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
        raise


def guardar_estado_voz(
    directorio_proyecto: str,
    estado: dict
) -> None:
    guardar_json_atomico(
        obtener_ruta_estado(directorio_proyecto),
        estado
    )


def cargar_estado_voz(
    directorio_proyecto: str
) -> dict | None:
    ruta = obtener_ruta_estado(directorio_proyecto)

    if not os.path.isfile(ruta):
        return None

    try:
        with open(
            ruta,
            "r",
            encoding="utf-8"
        ) as archivo:
            estado = json.load(archivo)
    except (OSError, json.JSONDecodeError):
        return {
            "estado": "error",
            "error": (
                "El estado de la voz no se pudo leer. "
                "Vuelve a generar la narración."
            ),
            "actualizado": ahora_iso(),
            "limite_segundos": LIMITE_DURACION_VOZ
        }

    if not isinstance(estado, dict):
        return {
            "estado": "error",
            "error": (
                "El estado de la voz no tiene un formato válido. "
                "Vuelve a generar la narración."
            ),
            "actualizado": ahora_iso(),
            "limite_segundos": LIMITE_DURACION_VOZ
        }

    duracion = estado.get("duracion_segundos")
    nombre_estado = estado.get("estado")

    if (
        nombre_estado in {
            "pendiente_aprobacion",
            "excede_limite"
        }
        and isinstance(duracion, (int, float))
    ):
        if duracion > LIMITE_DURACION_VOZ:
            estado["estado"] = "excede_limite"
        else:
            estado["estado"] = "pendiente_aprobacion"

        estado["limite_segundos"] = LIMITE_DURACION_VOZ

    return estado


def crear_hash_guion(guion: str) -> str:
    return hashlib.sha256(
        guion.encode("utf-8")
    ).hexdigest()


def marcar_generacion_iniciada(
    directorio_proyecto: str,
    guion: str
) -> dict:
    estado = {
        "estado": "generando",
        "guion_aprobado": True,
        "guion_sha256": crear_hash_guion(guion),
        "duracion_segundos": None,
        "limite_segundos": LIMITE_DURACION_VOZ,
        "aprobada": False,
        "actualizado": ahora_iso(),
        "error": ""
    }
    guardar_estado_voz(
        directorio_proyecto,
        estado
    )
    return estado


def leer_entero_synchsafe(datos: bytes) -> int:
    if len(datos) != 4:
        raise ValueError(
            "La cabecera ID3 del audio no es válida."
        )

    if any(valor & 0x80 for valor in datos):
        raise ValueError(
            "La cabecera ID3 del audio no es válida."
        )

    return (
        (datos[0] << 21)
        | (datos[1] << 14)
        | (datos[2] << 7)
        | datos[3]
    )


def calcular_duracion_mp3(contenido: bytes) -> float:
    if len(contenido) < 4:
        raise ValueError(
            "ElevenLabs devolvió un audio vacío o incompleto."
        )

    posicion = 0

    if contenido.startswith(b"ID3"):
        if len(contenido) < 10:
            raise ValueError(
                "La cabecera ID3 del audio está incompleta."
            )

        tamano_id3 = leer_entero_synchsafe(
            contenido[6:10]
        )
        posicion = 10 + tamano_id3

    tasas_mpeg1 = (
        0, 32, 40, 48, 56, 64, 80, 96,
        112, 128, 160, 192, 224, 256, 320, 0
    )
    tasas_mpeg2 = (
        0, 8, 16, 24, 32, 40, 48, 56,
        64, 80, 96, 112, 128, 144, 160, 0
    )
    frecuencias_base = (44100, 48000, 32000)
    duracion = 0.0
    fotogramas = 0

    while posicion + 4 <= len(contenido):
        cabecera = int.from_bytes(
            contenido[posicion:posicion + 4],
            "big"
        )

        if (cabecera & 0xFFE00000) != 0xFFE00000:
            posicion += 1
            continue

        version = (cabecera >> 19) & 0b11
        capa = (cabecera >> 17) & 0b11
        indice_tasa = (cabecera >> 12) & 0b1111
        indice_frecuencia = (cabecera >> 10) & 0b11
        relleno = (cabecera >> 9) & 0b1

        if (
            version == 0b01
            or capa != 0b01
            or indice_tasa in {0, 15}
            or indice_frecuencia == 3
        ):
            posicion += 1
            continue

        if version == 0b11:
            tasa_kbps = tasas_mpeg1[indice_tasa]
            frecuencia = frecuencias_base[indice_frecuencia]
            muestras = 1152
            factor = 144
        elif version == 0b10:
            tasa_kbps = tasas_mpeg2[indice_tasa]
            frecuencia = frecuencias_base[indice_frecuencia] // 2
            muestras = 576
            factor = 72
        else:
            tasa_kbps = tasas_mpeg2[indice_tasa]
            frecuencia = frecuencias_base[indice_frecuencia] // 4
            muestras = 576
            factor = 72

        tamano_fotograma = (
            factor * tasa_kbps * 1000 // frecuencia
        ) + relleno

        if (
            tamano_fotograma <= 4
            or posicion + tamano_fotograma > len(contenido)
        ):
            break

        duracion += muestras / frecuencia
        fotogramas += 1
        posicion += tamano_fotograma

    if fotogramas == 0 or duracion <= 0:
        raise ValueError(
            "No se pudo calcular la duración del audio MP3."
        )

    return round(duracion, 2)


def obtener_configuracion() -> tuple[str, str, str]:
    api_key = os.getenv(
        "ELEVENLABS_API_KEY",
        ""
    ).strip()
    voice_id = os.getenv(
        "ELEVENLABS_VOICE_ID",
        ""
    ).strip()
    model_id = os.getenv(
        "ELEVENLABS_MODEL_ID",
        MODELO_PREDETERMINADO
    ).strip() or MODELO_PREDETERMINADO

    if not api_key:
        raise ValueError(
            "Falta el secreto ELEVENLABS_API_KEY."
        )

    if not voice_id:
        raise ValueError(
            "Falta el secreto ELEVENLABS_VOICE_ID."
        )

    return api_key, voice_id, model_id


def solicitar_audio_elevenlabs(
    guion: str,
    api_key: str,
    voice_id: str,
    model_id: str
) -> bytes:
    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{quote(voice_id, safe='')}"
        f"?output_format={FORMATO_AUDIO}"
    )
    cuerpo = json.dumps(
        {
            "text": guion,
            "model_id": model_id
        },
        ensure_ascii=False
    ).encode("utf-8")
    solicitud = Request(
        url,
        data=cuerpo,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        },
        method="POST"
    )

    try:
        with urlopen(
            solicitud,
            timeout=TIEMPO_MAXIMO_GENERACION
        ) as respuesta:
            tipo = respuesta.headers.get(
                "Content-Type",
                ""
            ).lower()
            contenido = respuesta.read(
                MAXIMO_BYTES_AUDIO + 1
            )
    except HTTPError as error:
        detalle = error.read(4096).decode(
            "utf-8",
            errors="replace"
        )
        raise RuntimeError(
            "ElevenLabs rechazó la generación "
            f"(HTTP {error.code}): {detalle}"
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            "No se pudo conectar con ElevenLabs."
        ) from error

    if len(contenido) > MAXIMO_BYTES_AUDIO:
        raise RuntimeError(
            "El audio generado supera el límite de 50 MB."
        )

    if not contenido:
        raise RuntimeError(
            "ElevenLabs no devolvió ningún audio."
        )

    if tipo and "audio" not in tipo:
        detalle = contenido[:4096].decode(
            "utf-8",
            errors="replace"
        )
        raise RuntimeError(
            "ElevenLabs devolvió una respuesta no válida: "
            f"{detalle}"
        )

    return contenido


def generar_voz(
    directorio_proyecto: str,
    guion: str
) -> dict:
    guion = str(guion).strip()

    if not guion:
        raise ValueError(
            "El guion está vacío."
        )

    api_key, voice_id, model_id = obtener_configuracion()

    try:
        contenido = solicitar_audio_elevenlabs(
            guion,
            api_key,
            voice_id,
            model_id
        )
        duracion = calcular_duracion_mp3(contenido)
        directorio = os.path.abspath(directorio_proyecto)
        os.makedirs(directorio, exist_ok=True)
        descriptor, ruta_temporal = tempfile.mkstemp(
            prefix=".voz-",
            suffix=".mp3",
            dir=directorio
        )

        try:
            with os.fdopen(descriptor, "wb") as archivo:
                archivo.write(contenido)

            os.replace(
                ruta_temporal,
                obtener_ruta_audio(directorio)
            )
        except Exception:
            if os.path.exists(ruta_temporal):
                os.remove(ruta_temporal)
            raise

        if duracion > LIMITE_DURACION_VOZ:
            nombre_estado = "excede_limite"
        else:
            nombre_estado = "pendiente_aprobacion"

        estado = {
            "estado": nombre_estado,
            "guion_aprobado": True,
            "guion_sha256": crear_hash_guion(guion),
            "duracion_segundos": duracion,
            "limite_segundos": LIMITE_DURACION_VOZ,
            "aprobada": False,
            "modelo": model_id,
            "formato": FORMATO_AUDIO,
            "actualizado": ahora_iso(),
            "error": ""
        }
        guardar_estado_voz(
            directorio_proyecto,
            estado
        )
        return estado
    except Exception as error:
        estado_error = {
            "estado": "error",
            "guion_aprobado": True,
            "guion_sha256": crear_hash_guion(guion),
            "duracion_segundos": None,
            "limite_segundos": LIMITE_DURACION_VOZ,
            "aprobada": False,
            "actualizado": ahora_iso(),
            "error": str(error)
        }
        guardar_estado_voz(
            directorio_proyecto,
            estado_error
        )
        return estado_error


def aprobar_voz(
    directorio_proyecto: str,
    guion: str
) -> dict:
    estado = cargar_estado_voz(
        directorio_proyecto
    )

    if not estado:
        raise ValueError(
            "Todavía no se ha generado la voz."
        )

    if estado.get("estado") not in {
        "pendiente_aprobacion",
        "excede_limite"
    }:
        raise ValueError(
            "La voz no está preparada para aprobarse."
        )

    duracion = estado.get("duracion_segundos")

    if not isinstance(duracion, (int, float)):
        raise ValueError(
            "La duración de la voz no es válida."
        )

    if estado.get("guion_sha256") != crear_hash_guion(
        str(guion).strip()
    ):
        raise ValueError(
            "El guion ha cambiado desde que se generó la voz."
        )

    if not os.path.isfile(
        obtener_ruta_audio(directorio_proyecto)
    ):
        raise ValueError(
            "No se encuentra el archivo voz.mp3."
        )

    estado["estado"] = "aprobada"
    estado["aprobada"] = True
    estado["aprobada_sobre_limite"] = (
        duracion > LIMITE_DURACION_VOZ
    )
    estado["aprobada_en"] = ahora_iso()
    estado["actualizado"] = ahora_iso()
    guardar_estado_voz(
        directorio_proyecto,
        estado
    )
    return estado


def voz_esta_aprobada(
    directorio_proyecto: str,
    guion: str
) -> bool:
    estado = cargar_estado_voz(
        directorio_proyecto
    )

    if not estado:
        return False

    return (
        estado.get("estado") == "aprobada"
        and estado.get("aprobada") is True
        and estado.get("guion_sha256") == crear_hash_guion(
            str(guion).strip()
        )
        and os.path.isfile(
            obtener_ruta_audio(directorio_proyecto)
        )
    )
