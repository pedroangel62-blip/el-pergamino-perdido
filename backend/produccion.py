from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


TOTAL_IMAGENES = 8
PORTADA_SEGUNDOS = 3.0
CIERRE_SEGUNDOS = 3.0
ZOOM_MAXIMO_CIERRE = 1.02
ESCALA_SEGURA_CIERRE = 0.90
ANCHO_VIDEO = 1080
ALTO_VIDEO = 1920
FPS_VIDEO = 30
TRANSICION_SEGUNDOS = 0.15
PICO_OBJETIVO_VOZ_DB = -3.0
GANANCIA_MAXIMA_VOZ_DB = 18.0
GANANCIA_MAXIMA_MUSICA_DB = -20.0
MARGEN_MINIMO_MUSICA_DB = 14.0
CAIDA_MINIMA_FUNDIDO_DB = 12.0
LIMITE_AUDIO_LINEAL = 0.891
PICO_MAXIMO_MEZCLA_DB = -0.1
MAXIMO_BYTES_MUSICA = 50 * 1024 * 1024
EXTENSIONES_MUSICA = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
ARCHIVO_ESTADO = "produccion.json"
ARCHIVO_SINCRONIZACION = "sincronizacion.json"
ARCHIVO_VERIFICACION_TIMELINE = "verificacion_timeline.json"
ARCHIVO_VERIFICACION_VISUAL = "verificacion_visual.json"
ARCHIVO_VERIFICACION_AUDIO = "verificacion_audio.json"
ARCHIVO_VERIFICACION_PREVIA = "verificacion_previa.json"
ARCHIVO_BORRADOR = "video_borrador.mp4"
ARCHIVO_FINAL = "video_final.mp4"
ARCHIVO_PUBLICACION = "publicacion.txt"
ARCHIVO_PAQUETE = "proyecto_completo.zip"
ARCHIVO_ALINEACION_VOZ = "voz-alineacion.json"
ARCHIVO_SELLO_CIERRE = "sello-el-pergamino-perdido.jpeg"
RUTA_SELLO_CIERRE = (
    Path(__file__).resolve().parent
    / "assets"
    / ARCHIVO_SELLO_CIERRE
)


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def guardar_json_atomico(ruta: str, datos: dict) -> None:
    directorio = os.path.dirname(os.path.abspath(ruta))
    os.makedirs(directorio, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        prefix=".produccion-",
        suffix=".tmp",
        dir=directorio,
    )

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
        os.replace(temporal, ruta)
    except Exception:
        if os.path.exists(temporal):
            os.remove(temporal)
        raise


def cargar_json(ruta: str) -> dict | None:
    if not os.path.isfile(ruta):
        return None

    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, json.JSONDecodeError):
        return None

    return datos if isinstance(datos, dict) else None


def obtener_ruta_estado(directorio_proyecto: str) -> str:
    return os.path.join(directorio_proyecto, ARCHIVO_ESTADO)


def guardar_estado(
    directorio_proyecto: str,
    estado: str,
    **campos,
) -> dict:
    datos = cargar_json(obtener_ruta_estado(directorio_proyecto)) or {}
    datos.update(campos)
    datos["estado"] = estado
    datos["actualizado"] = ahora_iso()
    guardar_json_atomico(obtener_ruta_estado(directorio_proyecto), datos)
    return datos


def cargar_estado(directorio_proyecto: str) -> dict:
    return cargar_json(obtener_ruta_estado(directorio_proyecto)) or {
        "estado": "pendiente",
        "actualizado": ahora_iso(),
        "error": "",
    }


def invalidar_salidas(directorio_proyecto: str) -> None:
    for nombre in (
        ARCHIVO_BORRADOR,
        ARCHIVO_FINAL,
        ARCHIVO_PUBLICACION,
        ARCHIVO_PAQUETE,
        ARCHIVO_VERIFICACION_TIMELINE,
        ARCHIVO_VERIFICACION_VISUAL,
        ARCHIVO_VERIFICACION_AUDIO,
        ARCHIVO_VERIFICACION_PREVIA,
        "subtitulos.srt",
    ):
        ruta = os.path.join(directorio_proyecto, nombre)
        if os.path.isfile(ruta):
            os.remove(ruta)


def comprobar_ffmpeg() -> None:
    for ejecutable in ("ffmpeg", "ffprobe"):
        if shutil.which(ejecutable) is None:
            raise RuntimeError(
                f"No se encuentra {ejecutable}. Instala FFmpeg para montar el vídeo."
            )


def ejecutar(comando: list[str], tiempo_maximo: int = 600) -> None:
    try:
        resultado = subprocess.run(
            comando,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=tiempo_maximo,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("FFmpeg superó el tiempo máximo de ejecución.") from error

    if resultado.returncode != 0:
        detalle = resultado.stderr.strip()[-4000:]
        raise RuntimeError(f"FFmpeg no pudo completar la operación: {detalle}")


def obtener_duracion(ruta: str) -> float:
    comprobar_ffmpeg()

    if not os.path.isfile(ruta):
        raise FileNotFoundError(f"No se encuentra el archivo multimedia: {ruta}")

    comando = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        ruta,
    ]

    try:
        resultado = subprocess.run(
            comando,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("No se pudo medir la duración del archivo.") from error

    if resultado.returncode != 0:
        raise ValueError("El archivo multimedia no tiene un formato válido.")

    try:
        duracion = float(resultado.stdout.strip())
    except ValueError as error:
        raise ValueError("La duración del archivo multimedia no es válida.") from error

    if duracion <= 0:
        raise ValueError("El archivo multimedia está vacío.")

    return round(duracion, 3)


def obtener_ruta_sello_cierre(ruta_personalizada: str | None = None) -> str:
    ruta = (
        Path(ruta_personalizada).resolve()
        if ruta_personalizada
        else RUTA_SELLO_CIERRE
    )

    if not ruta.is_file():
        raise FileNotFoundError(
            "No se encuentra la Imagen 9 maestra. Guarda el sello fijo en "
            f"backend/assets/{ARCHIVO_SELLO_CIERRE}."
        )

    return str(ruta)


def crear_segmento_cierre(duracion_voz: float) -> dict:
    inicio = round(float(duracion_voz), 3)
    fin = round(inicio + CIERRE_SEGUNDOS, 3)
    return {
        "numero": 9,
        "tipo": "sello_fijo",
        "frase_entrada": "Fin de la narración · entra el sello fijo",
        "inicio": inicio,
        "fin": fin,
        "duracion": CIERRE_SEGUNDOS,
        "voz": False,
        "musica": True,
        "movimiento": "zoom_suave_seguro",
    }


def _indices_inicio(total_palabras: int, duracion: float) -> list[int]:
    inicios_tiempo = [0.0, PORTADA_SEGUNDOS]
    tramo = (duracion - PORTADA_SEGUNDOS) / (TOTAL_IMAGENES - 1)
    inicios_tiempo.extend(
        PORTADA_SEGUNDOS + tramo * indice
        for indice in range(1, TOTAL_IMAGENES - 1)
    )

    indices = [0]

    for posicion, instante in enumerate(inicios_tiempo[1:], start=1):
        estimado = round(total_palabras * instante / duracion)
        minimo = indices[-1] + 1
        maximo = total_palabras - (TOTAL_IMAGENES - posicion)
        indices.append(max(minimo, min(estimado, maximo)))

    return indices


def _frase_entrada(texto: str, maximo_palabras: int = 12) -> str:
    palabras = texto.split()
    frase = " ".join(palabras[:maximo_palabras]).strip()

    if len(palabras) > maximo_palabras:
        frase += "…"

    return frase


def _palabras_alineadas(guion: str, alineacion: object) -> list[dict]:
    if not isinstance(alineacion, dict):
        return []

    caracteres = alineacion.get("characters")
    inicios = alineacion.get("character_start_times_seconds")
    finales = alineacion.get("character_end_times_seconds")

    if not all(
        isinstance(elemento, list)
        for elemento in (caracteres, inicios, finales)
    ):
        return []

    if (
        len(caracteres) != len(guion)
        or len(inicios) != len(guion)
        or len(finales) != len(guion)
        or any(
            not isinstance(caracter, str) or len(caracter) != 1
            for caracter in caracteres
        )
        or "".join(caracteres) != guion
    ):
        return []

    if not all(
        isinstance(valor, (int, float)) and valor >= 0
        for valor in [*inicios, *finales]
    ):
        return []

    palabras = []

    for coincidencia in re.finditer(r"\S+", guion):
        inicio_indice = coincidencia.start()
        fin_indice = coincidencia.end() - 1
        inicio = float(inicios[inicio_indice])
        fin = float(finales[fin_indice])

        if fin < inicio:
            return []

        palabras.append(
            {
                "texto": coincidencia.group(),
                "caracter_inicio": inicio_indice,
                "caracter_fin": coincidencia.end(),
                "inicio": inicio,
                "fin": fin,
            }
        )

    return palabras


def validar_anclas_plan_visual(
    guion: str,
    plan_visual: object,
) -> list[dict]:
    guion_limpio = str(guion).strip()

    if not guion_limpio:
        raise ValueError("El guion está vacío.")

    if not isinstance(plan_visual, list) or len(plan_visual) != TOTAL_IMAGENES:
        raise ValueError(
            "El plan visual debe contener exactamente 8 imágenes."
        )

    anclas = []
    final_anterior = -1

    for posicion, escena in enumerate(plan_visual, start=1):
        if not isinstance(escena, dict) or escena.get("numero") != posicion:
            raise ValueError(
                f"La Imagen {posicion} no está numerada correctamente."
            )

        frase = str(escena.get("frase_entrada", "")).strip()
        contenido_visual = str(escena.get("motivo", "")).strip()

        if not frase:
            raise ValueError(
                f"La Imagen {posicion} no tiene una frase de entrada."
            )

        if not contenido_visual:
            raise ValueError(
                f"La Imagen {posicion} no describe su contenido visual."
            )

        coincidencias = list(
            re.finditer(re.escape(frase), guion_limpio)
        )

        if len(coincidencias) != 1:
            raise ValueError(
                f"La frase de entrada de la Imagen {posicion} debe aparecer "
                "una sola vez y de forma literal en el guion."
            )

        coincidencia = coincidencias[0]
        inicio = coincidencia.start()
        fin = coincidencia.end()

        if inicio > 0 and not guion_limpio[inicio - 1].isspace():
            raise ValueError(
                f"La frase de entrada de la Imagen {posicion} debe comenzar "
                "al principio de una palabra."
            )

        if posicion == 1 and inicio != 0:
            raise ValueError(
                "La frase de entrada de la Imagen 1 debe comenzar el guion."
            )

        if inicio <= final_anterior:
            raise ValueError(
                "Las frases de entrada de las imágenes deben ser únicas, "
                "estar ordenadas y no solaparse."
            )

        anclas.append(
            {
                "numero": posicion,
                "frase_entrada": frase,
                "caracter_inicio": inicio,
                "caracter_fin": fin,
                "contenido_visual": contenido_visual,
            }
        )
        final_anterior = fin - 1

    return anclas


def _tipo_limite_natural(guion: str, caracter_inicio: int) -> int:
    anterior = guion[:caracter_inicio].rstrip()

    if not anterior:
        return 2

    if anterior[-1] in ".!?":
        return 0

    if anterior[-1] in ",;:—–-":
        return 1

    return 2


def _indice_activo_en_instante(palabras: list[dict], instante: float) -> int:
    for indice, palabra in enumerate(palabras):
        if palabra["inicio"] <= instante < palabra["fin"]:
            return indice

        if palabra["inicio"] >= instante:
            return indice

    return len(palabras) - 1


def _indices_alineados(
    guion: str,
    palabras: list[dict],
    duracion: float,
) -> tuple[list[int], list[float]]:
    total = len(palabras)
    indice_portada = _indice_activo_en_instante(palabras, PORTADA_SEGUNDOS)
    indice_portada = max(1, min(indice_portada, total - (TOTAL_IMAGENES - 1)))
    indices = [0, indice_portada]
    inicios = [0.0, PORTADA_SEGUNDOS]
    tramo = (duracion - PORTADA_SEGUNDOS) / (TOTAL_IMAGENES - 1)

    for posicion in range(2, TOTAL_IMAGENES):
        objetivo = PORTADA_SEGUNDOS + tramo * (posicion - 1)
        minimo = indices[-1] + 1
        maximo = total - (TOTAL_IMAGENES - posicion)
        candidatos = []

        for indice in range(minimo, maximo + 1):
            inicio = float(palabras[indice]["inicio"])

            if inicio <= inicios[-1] or inicio >= duracion:
                continue

            tipo = _tipo_limite_natural(
                guion,
                int(palabras[indice]["caracter_inicio"]),
            )
            penalizacion = (0.0, 0.35, 0.9)[tipo]
            candidatos.append(
                (
                    abs(inicio - objetivo) + penalizacion,
                    abs(inicio - objetivo),
                    indice,
                )
            )

        if not candidatos:
            raise ValueError(
                "Las marcas temporales no permiten repartir el guion en 8 imágenes."
            )

        _, _, elegido = min(candidatos)
        indices.append(elegido)
        inicios.append(float(palabras[elegido]["inicio"]))

    return indices, inicios


def _indice_palabra_en_caracter(
    palabras: list[dict],
    caracter_inicio: int,
) -> int:
    for indice, palabra in enumerate(palabras):
        if int(palabra["caracter_inicio"]) == caracter_inicio:
            return indice

    raise ValueError(
        "Una frase de entrada no comienza en una palabra alineada por ElevenLabs."
    )


def _indices_semanticos(
    guion: str,
    palabras: list[dict],
    duracion: float,
    plan_visual: object,
) -> tuple[list[int], list[float], list[dict]]:
    anclas = validar_anclas_plan_visual(guion, plan_visual)
    indices_ancla = [
        _indice_palabra_en_caracter(
            palabras,
            int(ancla["caracter_inicio"]),
        )
        for ancla in anclas
    ]
    tiempos_ancla = [
        float(palabras[indice]["inicio"])
        for indice in indices_ancla
    ]

    if tiempos_ancla[1] > PORTADA_SEGUNDOS:
        raise ValueError(
            "La idea visual de la Imagen 2 comienza después del segundo 3. "
            "Debe estar activa cuando termine la portada."
        )

    if tiempos_ancla[2] <= PORTADA_SEGUNDOS:
        raise ValueError(
            "La frase de entrada de la Imagen 3 debe comenzar después de "
            "los 3 segundos de portada."
        )

    indice_segundo_tres = _indice_activo_en_instante(
        palabras,
        PORTADA_SEGUNDOS,
    )
    indices = [0, indice_segundo_tres, *indices_ancla[2:]]
    inicios = [0.0, PORTADA_SEGUNDOS, *tiempos_ancla[2:]]

    if any(
        actual >= siguiente
        for actual, siguiente in zip(indices, indices[1:])
    ):
        raise ValueError(
            "Las frases de entrada no producen ocho tramos narrativos "
            "ordenados y distintos."
        )

    if any(
        actual >= siguiente
        for actual, siguiente in zip(inicios, inicios[1:])
    ) or inicios[-1] >= duracion:
        raise ValueError(
            "Los tiempos de las frases de entrada no permiten una "
            "sincronización válida."
        )

    return indices, inicios, anclas


def crear_sincronizacion(
    guion: str,
    duracion: float,
    alineacion: dict | None = None,
    plan_visual: list[dict] | None = None,
) -> list[dict]:
    guion = str(guion).strip()

    if not guion:
        raise ValueError("El guion está vacío.")

    if duracion <= PORTADA_SEGUNDOS:
        raise ValueError("La voz debe durar más de 3 segundos.")

    coincidencias = list(re.finditer(r"\S+", guion))

    if len(coincidencias) < TOTAL_IMAGENES:
        raise ValueError("El guion es demasiado corto para repartirlo en 8 imágenes.")

    palabras = _palabras_alineadas(guion, alineacion)

    anclas = None

    if plan_visual is not None:
        if len(palabras) != len(coincidencias):
            raise ValueError(
                "La sincronización semántica requiere las marcas temporales "
                "reales de ElevenLabs."
            )

        indices, inicios, anclas = _indices_semanticos(
            guion,
            palabras,
            duracion,
            plan_visual,
        )
        metodo = "elevenlabs_semantic_alignment"
    elif len(palabras) == len(coincidencias):
        indices, inicios = _indices_alineados(guion, palabras, duracion)
        metodo = "elevenlabs_alignment"
    else:
        indices = _indices_inicio(len(coincidencias), duracion)
        tramo = (duracion - PORTADA_SEGUNDOS) / (TOTAL_IMAGENES - 1)
        inicios = [0.0, PORTADA_SEGUNDOS]
        inicios.extend(
            PORTADA_SEGUNDOS + tramo * indice
            for indice in range(1, TOTAL_IMAGENES - 1)
        )
        metodo = "estimado"

    finales = inicios[1:] + [duracion]
    sincronizacion = []

    for indice in range(TOTAL_IMAGENES):
        palabra_inicio = indices[indice]
        palabra_fin = (
            indices[indice + 1]
            if indice + 1 < TOTAL_IMAGENES
            else len(coincidencias)
        )
        caracter_inicio = coincidencias[palabra_inicio].start()
        caracter_fin = coincidencias[palabra_fin - 1].end()
        texto = re.sub(
            r"\s+",
            " ",
            guion[caracter_inicio:caracter_fin],
        ).strip()
        inicio = round(inicios[indice], 3)
        fin = round(finales[indice], 3)
        segmento = {
            "numero": indice + 1,
            "frase_entrada": (
                str(anclas[indice]["frase_entrada"])
                if anclas is not None and indice not in (0, 1)
                else _frase_entrada(texto)
            ),
            "texto": texto,
            "inicio": inicio,
            "fin": fin,
            "duracion": round(fin - inicio, 3),
            "metodo": metodo,
            "semantica_validada": anclas is not None,
        }

        if anclas is not None:
            segmento["frase_planificada"] = str(
                anclas[indice]["frase_entrada"]
            )
            segmento["contenido_visual"] = str(
                anclas[indice]["contenido_visual"]
            )

        if metodo in {
            "elevenlabs_alignment",
            "elevenlabs_semantic_alignment",
        }:
            segmento["palabras_alineadas"] = [
                {
                    "texto": palabra["texto"],
                    "inicio": round(float(palabra["inicio"]), 3),
                    "fin": round(float(palabra["fin"]), 3),
                }
                for palabra in palabras[palabra_inicio:palabra_fin]
            ]

        sincronizacion.append(segmento)

    return sincronizacion


def crear_plan_fotogramas(
    sincronizacion: list[dict],
    fps: int = FPS_VIDEO,
) -> list[dict]:
    if fps <= 0:
        raise ValueError("La frecuencia de fotogramas debe ser positiva.")

    if len(sincronizacion) != TOTAL_IMAGENES:
        raise ValueError(
            "La línea de tiempo requiere exactamente 8 imágenes."
        )

    marcas = [float(segmento["inicio"]) for segmento in sincronizacion]
    marcas.append(float(sincronizacion[-1]["fin"]))

    for actual, siguiente in zip(sincronizacion, sincronizacion[1:]):
        if abs(float(actual["fin"]) - float(siguiente["inicio"])) > 0.001:
            raise ValueError(
                "Los segmentos de voz no son contiguos y no pueden "
                "convertirse a fotogramas."
            )

    fotogramas = [round(marca * fps) for marca in marcas]
    fotogramas[0] = 0

    if any(
        actual >= siguiente
        for actual, siguiente in zip(fotogramas, fotogramas[1:])
    ):
        raise ValueError(
            "Dos cortes coinciden en el mismo fotograma. Debe revisarse "
            "la sincronización."
        )

    tolerancia_ms = 1000.0 / fps
    plan = []

    for indice, segmento in enumerate(sincronizacion):
        fotograma_inicio = fotogramas[indice]
        fotograma_fin = fotogramas[indice + 1]
        inicio_video = fotograma_inicio / fps
        fin_video = fotograma_fin / fps
        desviacion_ms = (
            inicio_video - float(segmento["inicio"])
        ) * 1000.0

        if abs(desviacion_ms) > tolerancia_ms + 0.001:
            raise ValueError(
                f"El corte de la Imagen {indice + 1} se desvía más de "
                "un fotograma respecto a la voz."
            )

        plan.append(
            {
                "numero": indice + 1,
                "fotograma_inicio": fotograma_inicio,
                "fotograma_fin": fotograma_fin,
                "fotogramas": fotograma_fin - fotograma_inicio,
                "inicio_voz": round(float(segmento["inicio"]), 3),
                "inicio_video": round(inicio_video, 6),
                "fin_video": round(fin_video, 6),
                "duracion_video": round(fin_video - inicio_video, 6),
                "desviacion_inicio_ms": round(desviacion_ms, 3),
            }
        )

    cierre_inicio = fotogramas[-1]
    cierre_fotogramas = round(CIERRE_SEGUNDOS * fps)
    cierre_fin = cierre_inicio + cierre_fotogramas
    inicio_cierre_video = cierre_inicio / fps
    desviacion_cierre_ms = (
        inicio_cierre_video - marcas[-1]
    ) * 1000.0

    if abs(desviacion_cierre_ms) > tolerancia_ms + 0.001:
        raise ValueError(
            "El cierre se desvía más de un fotograma respecto al final "
            "de la narración."
        )

    plan.append(
        {
            "numero": 9,
            "fotograma_inicio": cierre_inicio,
            "fotograma_fin": cierre_fin,
            "fotogramas": cierre_fotogramas,
            "inicio_voz": round(marcas[-1], 3),
            "inicio_video": round(inicio_cierre_video, 6),
            "fin_video": round(cierre_fin / fps, 6),
            "duracion_video": CIERRE_SEGUNDOS,
            "desviacion_inicio_ms": round(desviacion_cierre_ms, 3),
        }
    )
    return plan


def incorporar_plan_fotogramas(
    sincronizacion: list[dict],
    cierre: dict,
    fps: int = FPS_VIDEO,
) -> list[dict]:
    plan = crear_plan_fotogramas(sincronizacion, fps)

    for segmento, corte in zip(
        sincronizacion,
        plan[:TOTAL_IMAGENES],
        strict=True,
    ):
        segmento.update(corte)

    cierre.update(plan[-1])
    return plan


def preparar_sincronizacion(
    directorio_proyecto: str,
    guion: str,
    plan_visual: list[dict],
) -> list[dict]:
    voz = os.path.join(directorio_proyecto, "voz.mp3")
    duracion = obtener_duracion(voz)
    guion_limpio = str(guion).strip()
    datos_alineacion = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_ALINEACION_VOZ)
    )
    alineacion = None

    if (
        datos_alineacion
        and datos_alineacion.get("guion_sha256")
        == hashlib.sha256(guion_limpio.encode("utf-8")).hexdigest()
    ):
        alineacion = datos_alineacion.get("alignment")

    sincronizacion = crear_sincronizacion(
        guion_limpio,
        duracion,
        alineacion=alineacion,
        plan_visual=plan_visual,
    )
    cierre = crear_segmento_cierre(duracion)
    plan_fotogramas = incorporar_plan_fotogramas(
        sincronizacion,
        cierre,
        FPS_VIDEO,
    )
    metodo = str(sincronizacion[0].get("metodo", "estimado"))
    semantica_validada = all(
        segmento.get("semantica_validada") is True
        for segmento in sincronizacion
    )
    invalidar_salidas(directorio_proyecto)
    guardar_json_atomico(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION),
        {
            "duracion_voz": duracion,
            "voz_sha256": _hash_archivo(voz),
            "imagenes_sha256": obtener_hashes_imagenes(
                directorio_proyecto
            ),
            "portada_segundos": PORTADA_SEGUNDOS,
            "metodo": metodo,
            "semantica_validada": semantica_validada,
            "fps_timeline": FPS_VIDEO,
            "desviacion_maxima_ms": max(
                abs(float(corte["desviacion_inicio_ms"]))
                for corte in plan_fotogramas
            ),
            "segmentos": sincronizacion,
            "cierre": cierre,
            "duracion_total": cierre["fin"],
            "actualizado": ahora_iso(),
        },
    )

    guardar_estado(
        directorio_proyecto,
        "sincronizacion_preparada",
        sincronizacion_aprobada=False,
        metodo_sincronizacion=metodo,
        semantica_validada=semantica_validada,
        error="",
        duracion_voz=duracion,
        duracion_cierre=CIERRE_SEGUNDOS,
        duracion_total=cierre["fin"],
    )
    return sincronizacion


def cargar_sincronizacion(directorio_proyecto: str) -> list[dict]:
    datos = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION)
    )

    if not datos or not isinstance(datos.get("segmentos"), list):
        return []

    return datos["segmentos"]


def obtener_ruta_musica(directorio_proyecto: str) -> str | None:
    for extension in sorted(EXTENSIONES_MUSICA):
        ruta = os.path.join(directorio_proyecto, f"musica{extension}")
        if os.path.isfile(ruta):
            return ruta
    return None


def guardar_musica(
    directorio_proyecto: str,
    nombre_archivo: str,
    contenido: bytes,
) -> dict:
    extension = Path(nombre_archivo or "").suffix.lower()

    if extension not in EXTENSIONES_MUSICA:
        raise ValueError("La música debe ser MP3, WAV, M4A, AAC u OGG.")

    if not contenido:
        raise ValueError("El archivo de música está vacío.")

    if len(contenido) > MAXIMO_BYTES_MUSICA:
        raise ValueError("El archivo de música supera el límite de 50 MB.")

    os.makedirs(directorio_proyecto, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        prefix=".musica-",
        suffix=extension,
        dir=directorio_proyecto,
    )
    ruta = os.path.join(directorio_proyecto, f"musica{extension}")

    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido)
        obtener_duracion(temporal)
        os.replace(temporal, ruta)

        for extension_anterior in EXTENSIONES_MUSICA:
            ruta_anterior = os.path.join(
                directorio_proyecto,
                f"musica{extension_anterior}",
            )
            if ruta_anterior != ruta and os.path.isfile(ruta_anterior):
                os.remove(ruta_anterior)

        invalidar_salidas(directorio_proyecto)
    except Exception:
        if os.path.exists(temporal):
            os.remove(temporal)
        raise

    return guardar_estado(
        directorio_proyecto,
        "musica_pendiente_aprobacion",
        musica_archivo=os.path.basename(ruta),
        musica_aprobada=False,
        error="",
    )


def aprobar_musica(directorio_proyecto: str) -> dict:
    ruta = obtener_ruta_musica(directorio_proyecto)

    if not ruta:
        raise ValueError("Todavía no se ha cargado la música.")

    duracion = obtener_duracion(ruta)
    return guardar_estado(
        directorio_proyecto,
        "musica_aprobada",
        musica_archivo=os.path.basename(ruta),
        musica_aprobada=True,
        musica_duracion=duracion,
        musica_aprobada_en=ahora_iso(),
        error="",
    )


def obtener_imagenes(directorio_proyecto: str) -> list[str]:
    directorio = os.path.join(directorio_proyecto, "imagenes")
    rutas = [
        os.path.join(directorio, f"imagen{numero}.png")
        for numero in range(1, TOTAL_IMAGENES + 1)
    ]
    faltantes = [
        numero
        for numero, ruta in enumerate(rutas, start=1)
        if not os.path.isfile(ruta)
    ]

    if faltantes:
        lista = ", ".join(str(numero) for numero in faltantes)
        raise ValueError(f"Faltan las imágenes: {lista}.")

    return rutas


def _hash_archivo(ruta: str) -> str:
    calculo = hashlib.sha256()

    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            calculo.update(bloque)

    return calculo.hexdigest()


def obtener_hashes_imagenes(directorio_proyecto: str) -> dict[str, str]:
    return {
        str(numero): _hash_archivo(ruta)
        for numero, ruta in enumerate(
            obtener_imagenes(directorio_proyecto),
            start=1,
        )
    }


def imagenes_estan_aprobadas(directorio_proyecto: str) -> bool:
    estado = cargar_estado(directorio_proyecto)
    hashes_guardados = estado.get("imagenes_sha256")

    if not estado.get("imagenes_aprobadas") or not isinstance(hashes_guardados, dict):
        return False

    try:
        return hashes_guardados == obtener_hashes_imagenes(directorio_proyecto)
    except (FileNotFoundError, ValueError):
        return False


def aprobar_imagenes(directorio_proyecto: str) -> dict:
    hashes = obtener_hashes_imagenes(directorio_proyecto)
    invalidar_salidas(directorio_proyecto)
    return guardar_estado(
        directorio_proyecto,
        "imagenes_aprobadas",
        imagenes_aprobadas=True,
        imagenes_sha256=hashes,
        imagenes_aprobadas_en=ahora_iso(),
        sincronizacion_aprobada=False,
        error="",
    )


def aprobar_sincronizacion(directorio_proyecto: str) -> dict:
    sincronizacion = cargar_sincronizacion(directorio_proyecto)
    datos_sincronizacion = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION)
    ) or {}
    if len(sincronizacion) != TOTAL_IMAGENES:
        raise ValueError("La sincronización de las 8 imágenes no está preparada.")

    if not imagenes_estan_aprobadas(directorio_proyecto):
        raise ValueError("Las ocho imágenes deben estar aprobadas.")

    if datos_sincronizacion.get("semantica_validada") is not True:
        raise ValueError(
            "La sincronización no puede aprobarse porque las imágenes no "
            "están vinculadas a frases exactas del guion."
        )

    return guardar_estado(
        directorio_proyecto,
        "sincronizacion_aprobada",
        sincronizacion_aprobada=True,
        sincronizacion_aprobada_en=ahora_iso(),
        error="",
    )


def comprobar_preparacion_montaje(
    directorio_proyecto: str,
    voz_aprobada: bool,
) -> dict:
    estado = cargar_estado(directorio_proyecto)
    datos_sincronizacion = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION)
    ) or {}
    sincronizacion = datos_sincronizacion.get("segmentos") or []
    comprobaciones = []

    def registrar(clave: str, nombre: str, correcto: bool, detalle: str) -> None:
        comprobaciones.append(
            {
                "clave": clave,
                "nombre": nombre,
                "correcto": bool(correcto),
                "detalle": detalle,
            }
        )

    try:
        comprobar_ffmpeg()
        registrar("ffmpeg", "Motor de montaje", True, "FFmpeg y FFprobe disponibles.")
    except RuntimeError as error:
        registrar("ffmpeg", "Motor de montaje", False, str(error))

    voz = os.path.join(directorio_proyecto, "voz.mp3")
    try:
        duracion_voz = obtener_duracion(voz)
        pico_voz = medir_volumen_maximo(voz)
        voz_valida = math.isfinite(pico_voz) and pico_voz > -80.0
        registrar(
            "voz",
            "Voz",
            voz_aprobada and voz_valida,
            (
                f"Aprobada, audible y con {duracion_voz:.3f} s."
                if voz_aprobada and voz_valida
                else "La voz debe existir, ser audible y estar aprobada."
            ),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        registrar("voz", "Voz", False, str(error))

    try:
        obtener_imagenes(directorio_proyecto)
        aprobadas = imagenes_estan_aprobadas(directorio_proyecto)
        registrar(
            "imagenes",
            "Imágenes 1–8",
            aprobadas,
            (
                "Las 8 imágenes coinciden con las versiones aprobadas."
                if aprobadas
                else "Las 8 imágenes deben existir y coincidir con las aprobadas."
            ),
        )
    except ValueError as error:
        registrar("imagenes", "Imágenes 1–8", False, str(error))

    try:
        voz_sin_cambios = (
            datos_sincronizacion.get("voz_sha256")
            == _hash_archivo(voz)
        )
        imagenes_sin_cambios = (
            datos_sincronizacion.get("imagenes_sha256")
            == obtener_hashes_imagenes(directorio_proyecto)
        )
    except (FileNotFoundError, ValueError):
        voz_sin_cambios = False
        imagenes_sin_cambios = False

    semantica = (
        len(sincronizacion) == TOTAL_IMAGENES
        and datos_sincronizacion.get("semantica_validada") is True
        and datos_sincronizacion.get("metodo")
        == "elevenlabs_semantic_alignment"
        and voz_sin_cambios
        and imagenes_sin_cambios
    )
    sincronizacion_aprobada = estado.get("sincronizacion_aprobada") is True
    timeline_valida = False
    try:
        plan = crear_plan_fotogramas(sincronizacion, FPS_VIDEO)
        timeline_valida = (
            len(plan) == TOTAL_IMAGENES + 1
            and int(plan[-1]["fotogramas"])
            == round(CIERRE_SEGUNDOS * FPS_VIDEO)
        )
    except (KeyError, TypeError, ValueError):
        timeline_valida = False
    registrar(
        "sincronizacion",
        "Sincronización voz–imágenes",
        semantica and sincronizacion_aprobada and timeline_valida,
        (
            "8 entradas semánticas aprobadas, sin archivos cambiados y cuantizadas a 30 fps."
            if semantica and sincronizacion_aprobada and timeline_valida
            else "Debe recalcularse con la voz y las 8 imágenes definitivas y aprobarse."
        ),
    )

    sello_disponible = RUTA_SELLO_CIERRE.is_file()
    registrar(
        "imagen_9",
        "Imagen 9 maestra",
        sello_disponible,
        (
            "Sello fijo disponible para el cierre exacto de 3 segundos."
            if sello_disponible
            else "Falta la Imagen 9 maestra."
        ),
    )

    musica = obtener_ruta_musica(directorio_proyecto)
    try:
        if not musica:
            raise ValueError("Falta la pista musical.")
        ajuste = calcular_ajuste_audio(voz, musica)
        musica_correcta = estado.get("musica_aprobada") is True
        registrar(
            "musica",
            "Música",
            musica_correcta,
            (
                "Aprobada, audible y compatible con el margen de voz de "
                f"{ajuste['margen_voz_sobre_musica_db']:.2f} dB."
                if musica_correcta
                else "La música debe escucharse y aprobarse."
            ),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        registrar("musica", "Música", False, str(error))

    registrar(
        "subtitulos",
        "Sin subtítulos",
        True,
        "El montaje no genera ni incrusta subtítulos; cualquier SRT heredado se elimina.",
    )

    bloqueos = [
        comprobacion["detalle"]
        for comprobacion in comprobaciones
        if not comprobacion["correcto"]
    ]
    return {
        "preparado": not bloqueos,
        "comprobaciones": comprobaciones,
        "bloqueos": bloqueos,
        "sin_subtitulos": True,
        "imagen_9_segundos": CIERRE_SEGUNDOS,
        "actualizado": ahora_iso(),
    }


def verificar_preparacion_montaje(
    directorio_proyecto: str,
    voz_aprobada: bool,
) -> dict:
    resultado = comprobar_preparacion_montaje(
        directorio_proyecto,
        voz_aprobada,
    )
    guardar_json_atomico(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_PREVIA,
        ),
        resultado,
    )
    return resultado


def contar_fotogramas_video(ruta: str) -> int:
    resultado = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            ruta,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )

    if resultado.returncode != 0:
        raise RuntimeError(
            "FFprobe no pudo contar los fotogramas del vídeo."
        )

    try:
        fotogramas = int(resultado.stdout.strip())
    except ValueError as error:
        raise RuntimeError(
            "FFprobe no devolvió un recuento de fotogramas válido."
        ) from error

    if fotogramas <= 0:
        raise RuntimeError("El vídeo no contiene fotogramas.")

    return fotogramas


def obtener_firmas_fotogramas(ruta: str) -> list[str]:
    resultado = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            ruta,
            "-map",
            "0:v:0",
            "-f",
            "framemd5",
            "-",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )

    if resultado.returncode != 0:
        raise RuntimeError(
            "FFmpeg no pudo comprobar el movimiento de los fotogramas."
        )

    firmas = [
        linea.rsplit(",", 1)[-1].strip()
        for linea in resultado.stdout.splitlines()
        if linea.strip() and not linea.lstrip().startswith("#")
    ]

    if not firmas:
        raise RuntimeError("No se pudieron analizar los fotogramas del clip.")

    return firmas


def validar_efectos_visuales_clips(
    plan_fotogramas: list[dict],
    clips: list[str],
    fps: int,
) -> dict:
    if len(plan_fotogramas) != 9 or len(clips) != 9:
        raise ValueError(
            "El control visual requiere las ocho imágenes y la Imagen 9."
        )

    transiciones = []

    for corte, clip in zip(
        plan_fotogramas[:TOTAL_IMAGENES],
        clips[:TOTAL_IMAGENES],
        strict=True,
    ):
        firmas = obtener_firmas_fotogramas(clip)
        esperados = int(corte["fotogramas"])

        if len(firmas) != esperados or len(firmas) < 3:
            raise RuntimeError(
                f"No puede comprobarse el fundido de la Imagen {corte['numero']}."
            )

        firma_central = firmas[len(firmas) // 2]
        entrada_detectada = firmas[0] != firma_central
        salida_detectada = firmas[-1] != firma_central

        if not entrada_detectada or not salida_detectada:
            raise RuntimeError(
                f"El fundido de la Imagen {corte['numero']} no es visible."
            )

        transiciones.append(
            {
                "imagen": int(corte["numero"]),
                "fundido_entrada_detectado": entrada_detectada,
                "fundido_salida_detectado": salida_detectada,
            }
        )

    firmas_cierre = obtener_firmas_fotogramas(clips[-1])
    esperados_cierre = int(plan_fotogramas[-1]["fotogramas"])

    if len(firmas_cierre) != esperados_cierre:
        raise RuntimeError(
            "No puede comprobarse el movimiento de la Imagen 9."
        )

    duracion_transicion = max(TRANSICION_SEGUNDOS, 2.0 / fps)
    fotograma_referencia = min(
        len(firmas_cierre) - 2,
        max(1, math.ceil(duracion_transicion * fps) + 1),
    )
    fundido_cierre = firmas_cierre[0] != firmas_cierre[fotograma_referencia]
    zoom_detectado = firmas_cierre[fotograma_referencia] != firmas_cierre[-1]

    if not fundido_cierre:
        raise RuntimeError("El fundido de entrada de la Imagen 9 no es visible.")

    if not zoom_detectado:
        raise RuntimeError("El zoom suave de la Imagen 9 no es visible.")

    margen_seguro_por_lado = (
        1.0 - ESCALA_SEGURA_CIERRE * ZOOM_MAXIMO_CIERRE
    ) / 2.0

    if margen_seguro_por_lado < 0.04:
        raise RuntimeError(
            "El zoom de la Imagen 9 no conserva el margen mínimo de seguridad."
        )

    return {
        "verificados": True,
        "transiciones": {
            "tipo": "fundido_a_negro_discreto",
            "duracion_segundos": round(duracion_transicion, 3),
            "cortes_verificados": len(transiciones),
            "detalle": transiciones,
        },
        "cierre": {
            "fundido_entrada_detectado": fundido_cierre,
            "zoom_detectado": zoom_detectado,
            "zoom_inicial": 1.0,
            "zoom_final_maximo": ZOOM_MAXIMO_CIERRE,
            "escala_frontal": ESCALA_SEGURA_CIERRE,
            "margen_seguro_por_lado_porcentaje": round(
                margen_seguro_por_lado * 100,
                2,
            ),
            "fotogramas": esperados_cierre,
        },
    }


def validar_clips_fotograma_a_fotograma(
    plan_fotogramas: list[dict],
    clips: list[str],
) -> list[dict]:
    if len(plan_fotogramas) != 9 or len(clips) != 9:
        raise ValueError(
            "La verificación requiere las ocho imágenes y el cierre."
        )

    verificados = []
    fotograma_acumulado = 0

    for corte, clip in zip(plan_fotogramas, clips, strict=True):
        esperados = int(corte["fotogramas"])
        reales = contar_fotogramas_video(clip)

        if reales != esperados:
            raise RuntimeError(
                f"La Imagen {corte['numero']} contiene {reales} fotogramas; "
                f"se esperaban {esperados}."
            )

        if fotograma_acumulado != int(corte["fotograma_inicio"]):
            raise RuntimeError(
                f"La Imagen {corte['numero']} no comienza en el fotograma "
                "planificado."
            )

        verificados.append(
            {
                **corte,
                "fotogramas_reales": reales,
                "verificado": True,
            }
        )
        fotograma_acumulado += reales

    if fotograma_acumulado != int(plan_fotogramas[-1]["fotograma_fin"]):
        raise RuntimeError(
            "La suma de los clips no coincide con la línea de tiempo final."
        )

    return verificados


def _crear_clip(
    imagen: str,
    duracion: float,
    salida: str,
    ancho: int,
    alto: int,
    fps: int,
    fotogramas: int | None = None,
) -> None:
    fotogramas = (
        int(fotogramas)
        if fotogramas is not None
        else round(duracion * fps)
    )

    if fotogramas <= 0:
        raise ValueError("Un clip debe contener al menos un fotograma.")

    duracion = fotogramas / fps
    fundido = min(
        max(TRANSICION_SEGUNDOS, 2.0 / fps),
        duracion / 4,
    )
    salida_fundido = max(0.0, duracion - fundido)
    filtro = (
        "[0:v]split=2[fondo][frente];"
        f"[fondo]scale={ancho}:{alto}:force_original_aspect_ratio=increase,"
        f"crop={ancho}:{alto},boxblur=20:2[fondo2];"
        f"[frente]scale={ancho}:{alto}:force_original_aspect_ratio=decrease[frente2];"
        "[fondo2][frente2]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        f"fps={fps},format=yuv420p,"
        f"fade=t=in:st=0:d={fundido:.3f},"
        f"fade=t=out:st={salida_fundido:.3f}:d={fundido:.3f}[video]"
    )
    ejecutar(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-loop",
            "1",
            "-i",
            imagen,
            "-filter_complex",
            filtro,
            "-map",
            "[video]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            str(fotogramas),
            salida,
        ]
    )


def _crear_clip_cierre(
    imagen: str,
    salida: str,
    ancho: int,
    alto: int,
    fps: int,
) -> None:
    ancho_seguro = max(1, round(ancho * ESCALA_SEGURA_CIERRE))
    alto_seguro = max(1, round(alto * ESCALA_SEGURA_CIERRE))
    total_fotogramas = max(2, round(CIERRE_SEGUNDOS * fps))
    incremento_zoom = (ZOOM_MAXIMO_CIERRE - 1.0) / (total_fotogramas - 1)
    fundido = max(TRANSICION_SEGUNDOS, 2.0 / fps)
    filtro = (
        "[0:v]split=2[fondo][frente];"
        f"[fondo]scale={ancho}:{alto}:force_original_aspect_ratio=increase,"
        f"crop={ancho}:{alto},boxblur=20:2[fondo2];"
        f"[frente]scale={ancho_seguro}:{alto_seguro}:"
        "force_original_aspect_ratio=decrease[frente2];"
        "[fondo2][frente2]overlay=(W-w)/2:(H-h)/2,setsar=1[completo];"
        "[completo]zoompan="
        f"z='min(zoom+{incremento_zoom:.8f},{ZOOM_MAXIMO_CIERRE:.3f})':"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
        f"s={ancho}x{alto}:fps={fps},"
        f"format=yuv420p,fade=t=in:st=0:d={fundido:.3f}[video]"
    )
    ejecutar(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-loop",
            "1",
            "-i",
            imagen,
            "-filter_complex",
            filtro,
            "-map",
            "[video]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            str(total_fotogramas),
            salida,
        ]
    )


def medir_volumen_maximo(
    ruta: str,
    inicio: float | None = None,
    duracion: float | None = None,
) -> float:
    comando = ["ffmpeg", "-v", "info"]

    if inicio is not None:
        comando.extend(["-ss", f"{max(0.0, float(inicio)):.6f}"])

    if duracion is not None:
        comando.extend(["-t", f"{max(0.001, float(duracion)):.6f}"])

    comando.extend(
        [
            "-i",
            ruta,
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    resultado = subprocess.run(
        comando,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    coincidencia = re.search(
        r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB",
        resultado.stderr,
        flags=re.IGNORECASE,
    )

    if resultado.returncode != 0 or not coincidencia:
        raise RuntimeError("No se pudo medir el volumen del audio.")

    valor = coincidencia.group(1).lower()
    return float("-inf") if valor in {"inf", "-inf"} else float(valor)


def calcular_ajuste_audio(voz: str, musica: str) -> dict:
    pico_voz = medir_volumen_maximo(voz)
    pico_musica = medir_volumen_maximo(musica)

    if not math.isfinite(pico_voz) or pico_voz <= -80.0:
        raise RuntimeError("La pista de voz está en silencio.")

    if not math.isfinite(pico_musica) or pico_musica <= -80.0:
        raise RuntimeError("La pista musical está en silencio.")

    ganancia_voz = max(
        -GANANCIA_MAXIMA_VOZ_DB,
        min(
            GANANCIA_MAXIMA_VOZ_DB,
            PICO_OBJETIVO_VOZ_DB - pico_voz,
        ),
    )
    pico_voz_ajustado = pico_voz + ganancia_voz
    ganancia_musica_necesaria = (
        pico_voz_ajustado
        - MARGEN_MINIMO_MUSICA_DB
        - pico_musica
    )
    ganancia_musica = min(
        GANANCIA_MAXIMA_MUSICA_DB,
        ganancia_musica_necesaria,
    )

    if ganancia_musica < -60.0:
        raise RuntimeError(
            "La diferencia de volumen entre voz y música es excesiva."
        )

    pico_musica_ajustado = pico_musica + ganancia_musica
    margen = pico_voz_ajustado - pico_musica_ajustado

    if pico_voz_ajustado <= -24.0:
        raise RuntimeError(
            "La voz es demasiado baja incluso después del ajuste seguro."
        )

    if margen + 0.01 < MARGEN_MINIMO_MUSICA_DB:
        raise RuntimeError("La música no queda suficientemente debajo de la voz.")

    return {
        "pico_voz_original_db": round(pico_voz, 2),
        "ganancia_voz_db": round(ganancia_voz, 2),
        "pico_voz_ajustado_db": round(pico_voz_ajustado, 2),
        "pico_musica_original_db": round(pico_musica, 2),
        "ganancia_musica_db": round(ganancia_musica, 2),
        "pico_musica_ajustado_db": round(pico_musica_ajustado, 2),
        "margen_voz_sobre_musica_db": round(margen, 2),
        "margen_minimo_exigido_db": MARGEN_MINIMO_MUSICA_DB,
    }


def _mezclar_video_audio(
    video_base: str,
    voz: str,
    musica: str,
    inicio_cierre_video: float,
    duracion_video_total: float,
    salida: str,
) -> dict:
    ajuste = calcular_ajuste_audio(voz, musica)
    comando = ["ffmpeg", "-y", "-i", video_base, "-i", voz]
    inicio_salida = inicio_cierre_video
    duracion_fundido = duracion_video_total - inicio_cierre_video
    comando.extend(["-stream_loop", "-1", "-i", musica])
    comando.extend(
        [
            "-filter_complex",
            (
                f"[1:a]apad=pad_dur={CIERRE_SEGUNDOS + 1:.3f},"
                f"atrim=0:{duracion_video_total:.6f},asetpts=N/SR/TB,"
                f"volume={ajuste['ganancia_voz_db']:.2f}dB[voz];"
                f"[2:a]atrim=0:{duracion_video_total:.6f},asetpts=N/SR/TB,"
                f"volume={ajuste['ganancia_musica_db']:.2f}dB,"
                "afade=t=in:st=0:d=1,"
                f"afade=t=out:st={inicio_salida:.3f}:"
                f"d={duracion_fundido:.6f}[musica];"
                "[voz][musica]amix=inputs=2:duration=longest:"
                "dropout_transition=0:normalize=0,"
                f"alimiter=limit={LIMITE_AUDIO_LINEAL:.3f}:"
                "level=false:latency=true[audio]"
            ),
            "-map",
            "0:v:0",
            "-map",
            "[audio]",
        ]
    )

    comando.extend(
        [
            "-t",
            f"{duracion_video_total:.6f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            salida,
        ]
    )
    ejecutar(comando, tiempo_maximo=1200)
    return ajuste


def validar_control_audio_final(
    ruta: str,
    ajuste: dict,
    inicio_cierre_video: float,
    duracion_video_total: float,
    pico_final_db: float,
) -> dict:
    duracion_cierre = duracion_video_total - inicio_cierre_video
    volumen_inicio_cierre = medir_volumen_maximo(
        ruta,
        inicio=inicio_cierre_video,
        duracion=min(0.75, duracion_cierre / 2.0),
    )
    duracion_final = min(0.25, duracion_cierre / 4.0)
    volumen_final = medir_volumen_maximo(
        ruta,
        inicio=max(0.0, duracion_video_total - duracion_final),
        duracion=duracion_final,
    )

    if not math.isfinite(volumen_inicio_cierre) or volumen_inicio_cierre <= -70:
        raise RuntimeError("La música no se oye al comenzar la Imagen 9.")

    volumen_final_calculo = (
        volumen_final if math.isfinite(volumen_final) else -120.0
    )
    caida_fundido = volumen_inicio_cierre - volumen_final_calculo

    if caida_fundido + 0.1 < CAIDA_MINIMA_FUNDIDO_DB:
        raise RuntimeError(
            "La música no se desvanece suficientemente durante la Imagen 9."
        )

    if pico_final_db > PICO_MAXIMO_MEZCLA_DB:
        raise RuntimeError("La mezcla final presenta saturación digital.")

    return {
        "verificada": True,
        "voz_audible": True,
        "musica_subordinada": True,
        "sin_saturacion_digital": pico_final_db <= PICO_MAXIMO_MEZCLA_DB,
        "pico_maximo_permitido_db": PICO_MAXIMO_MEZCLA_DB,
        "pico_mezcla_final_db": round(pico_final_db, 2),
        "volumen_inicio_cierre_db": round(volumen_inicio_cierre, 2),
        "volumen_final_cierre_db": (
            round(volumen_final, 2)
            if math.isfinite(volumen_final)
            else "silencio"
        ),
        "caida_fundido_db": round(caida_fundido, 2),
        "caida_minima_exigida_db": CAIDA_MINIMA_FUNDIDO_DB,
        **ajuste,
    }


def validar_salida_multimedia(
    ruta: str,
    duracion_esperada: float,
    fotogramas_esperados: int | None = None,
    ancho_esperado: int | None = None,
    alto_esperado: int | None = None,
    fps_esperados: int | None = None,
) -> dict:
    sondeo = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            ruta,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    if sondeo.returncode != 0:
        raise RuntimeError("No se pudo verificar el vídeo borrador.")

    try:
        datos = json.loads(sondeo.stdout)
        streams = datos.get("streams", [])
        duracion = float(datos.get("format", {}).get("duration", 0))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("FFprobe devolvió datos no válidos del borrador.") from error

    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )

    if not video:
        raise RuntimeError("El borrador no contiene una pista de vídeo.")

    if not audio:
        raise RuntimeError("El borrador no contiene la pista de voz.")

    ancho = int(video.get("width", 0))
    alto = int(video.get("height", 0))

    try:
        fps = float(Fraction(str(video.get("r_frame_rate", "0/1"))))
    except (ValueError, ZeroDivisionError) as error:
        raise RuntimeError(
            "FFprobe no devolvió una frecuencia de fotogramas válida."
        ) from error

    if ancho_esperado is not None and ancho != int(ancho_esperado):
        raise RuntimeError("El borrador no tiene el ancho vertical planificado.")

    if alto_esperado is not None and alto != int(alto_esperado):
        raise RuntimeError("El borrador no tiene el alto vertical planificado.")

    if fps_esperados is not None and abs(fps - float(fps_esperados)) > 0.001:
        raise RuntimeError("El borrador no conserva los fotogramas por segundo.")

    if ancho <= 0 or alto <= 0 or ancho * 16 != alto * 9:
        raise RuntimeError("El borrador no conserva la proporción vertical 9:16.")

    if abs(duracion - duracion_esperada) > 0.5:
        raise RuntimeError(
            "La duración del borrador no coincide con la voz más el cierre de 3 segundos."
        )

    pico_audio = medir_volumen_maximo(ruta)

    if not math.isfinite(pico_audio) or pico_audio <= -80.0:
        raise RuntimeError("El borrador contiene audio, pero está en silencio.")

    fotogramas = contar_fotogramas_video(ruta)

    if (
        fotogramas_esperados is not None
        and fotogramas != fotogramas_esperados
    ):
        raise RuntimeError(
            f"El borrador contiene {fotogramas} fotogramas; se esperaban "
            f"{fotogramas_esperados}."
        )

    return {
        "duracion": round(duracion, 3),
        "video_codec": str(video.get("codec_name", "")),
        "audio_codec": str(audio.get("codec_name", "")),
        "audio_max_db": pico_audio,
        "fotogramas": fotogramas,
        "ancho": ancho,
        "alto": alto,
        "fps": fps,
        "pix_fmt": str(video.get("pix_fmt", "")),
    }


def generar_borrador(
    directorio_proyecto: str,
    ancho: int = ANCHO_VIDEO,
    alto: int = ALTO_VIDEO,
    fps: int = FPS_VIDEO,
    sello_cierre: str | None = None,
) -> dict:
    verificacion_previa = verificar_preparacion_montaje(
        directorio_proyecto,
        voz_aprobada=True,
    )
    if not verificacion_previa["preparado"]:
        raise ValueError(
            "El control previo ha bloqueado el montaje: "
            + " ".join(verificacion_previa["bloqueos"])
        )
    sincronizacion = cargar_sincronizacion(directorio_proyecto)
    datos_sincronizacion = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION)
    ) or {}

    if len(sincronizacion) != TOTAL_IMAGENES:
        raise ValueError("Primero debe prepararse la sincronización de las 8 imágenes.")

    if datos_sincronizacion.get("semantica_validada") is not True:
        raise ValueError(
            "El montaje está bloqueado hasta validar que cada imagen "
            "coincide con su frase exacta del guion."
        )

    estado = cargar_estado(directorio_proyecto)

    if not imagenes_estan_aprobadas(directorio_proyecto):
        raise ValueError("Las ocho imágenes deben estar aprobadas.")

    if not estado.get("sincronizacion_aprobada"):
        raise ValueError("La sincronización debe revisarse y aprobarse.")

    voz = os.path.join(directorio_proyecto, "voz.mp3")
    if (
        datos_sincronizacion.get("voz_sha256") != _hash_archivo(voz)
        or datos_sincronizacion.get("imagenes_sha256")
        != obtener_hashes_imagenes(directorio_proyecto)
    ):
        raise ValueError(
            "La voz o las imágenes han cambiado después de sincronizar. "
            "Debe recalcularse y aprobarse la sincronización."
        )

    imagenes = obtener_imagenes(directorio_proyecto)
    sello = obtener_ruta_sello_cierre(sello_cierre)
    duracion_voz = obtener_duracion(voz)
    subtitulos_heredados = os.path.join(
        directorio_proyecto,
        "subtitulos.srt",
    )

    if os.path.isfile(subtitulos_heredados):
        os.remove(subtitulos_heredados)

    plan_fotogramas = crear_plan_fotogramas(sincronizacion, fps)
    inicio_cierre_video = float(plan_fotogramas[-1]["inicio_video"])
    duracion_total = float(plan_fotogramas[-1]["fin_video"])
    fotogramas_totales = int(plan_fotogramas[-1]["fotograma_fin"])
    musica = obtener_ruta_musica(directorio_proyecto)

    if not musica:
        raise ValueError("Debe cargarse una pista musical antes del montaje.")

    if not estado.get("musica_aprobada"):
        raise ValueError("La música cargada todavía no está aprobada.")

    salida = os.path.join(directorio_proyecto, ARCHIVO_BORRADOR)

    with tempfile.TemporaryDirectory(
        prefix="montaje-",
        dir=directorio_proyecto,
    ) as temporal:
        clips = []

        for imagen, segmento, corte in zip(
            imagenes,
            sincronizacion,
            plan_fotogramas[:TOTAL_IMAGENES],
            strict=True,
        ):
            clip = os.path.join(temporal, f"clip-{segmento['numero']:02}.mp4")
            _crear_clip(
                imagen,
                float(corte["duracion_video"]),
                clip,
                ancho,
                alto,
                fps,
                fotogramas=int(corte["fotogramas"]),
            )
            clips.append(clip)

        clip_cierre = os.path.join(temporal, "clip-09-sello.mp4")
        _crear_clip_cierre(
            sello,
            clip_cierre,
            ancho,
            alto,
            fps,
        )
        clips.append(clip_cierre)
        clips_verificados = validar_clips_fotograma_a_fotograma(
            plan_fotogramas,
            clips,
        )
        efectos_visuales = validar_efectos_visuales_clips(
            plan_fotogramas,
            clips,
            fps,
        )

        lista = os.path.join(temporal, "clips.txt")
        with open(lista, "w", encoding="utf-8") as archivo:
            for clip in clips:
                ruta_segura = clip.replace("'", "'\\''")
                archivo.write(f"file '{ruta_segura}'\n")

        video_base = os.path.join(temporal, "video-base.mp4")
        ejecutar(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                lista,
                "-c",
                "copy",
                video_base,
            ]
        )
        fotogramas_video_base = contar_fotogramas_video(video_base)

        if fotogramas_video_base != fotogramas_totales:
            raise RuntimeError(
                "La concatenación ha alterado el número total de fotogramas."
            )

        temporal_salida = os.path.join(temporal, ARCHIVO_BORRADOR)
        ajuste_audio = _mezclar_video_audio(
            video_base,
            voz,
            musica,
            inicio_cierre_video,
            duracion_total,
            temporal_salida,
        )
        verificacion = validar_salida_multimedia(
            temporal_salida,
            duracion_total,
            fotogramas_esperados=fotogramas_totales,
            ancho_esperado=ancho,
            alto_esperado=alto,
            fps_esperados=fps,
        )
        control_audio = validar_control_audio_final(
            temporal_salida,
            ajuste_audio,
            inicio_cierre_video,
            duracion_total,
            verificacion["audio_max_db"],
        )
        os.replace(temporal_salida, salida)

    desviacion_maxima_ms = max(
        abs(float(corte["desviacion_inicio_ms"]))
        for corte in clips_verificados
    )
    guardar_json_atomico(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_TIMELINE,
        ),
        {
            "verificada": True,
            "fps": fps,
            "duracion_fotograma_ms": round(1000.0 / fps, 3),
            "desviacion_maxima_ms": round(desviacion_maxima_ms, 3),
            "fotogramas_totales": fotogramas_totales,
            "duracion_video": round(duracion_total, 6),
            "cortes": clips_verificados,
            "actualizado": ahora_iso(),
        },
    )
    guardar_json_atomico(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_AUDIO,
        ),
        {
            **control_audio,
            "musica_finaliza_en_imagen_9": True,
            "duracion_cierre_segundos": round(
                duracion_total - inicio_cierre_video,
                6,
            ),
            "actualizado": ahora_iso(),
        },
    )
    guardar_json_atomico(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_VISUAL,
        ),
        {
            "verificada_automaticamente": True,
            "requiere_revision_humana": True,
            "sin_subtitulos": True,
            "resolucion": {
                "ancho": verificacion["ancho"],
                "alto": verificacion["alto"],
                "proporcion": "9:16",
                "fps": verificacion["fps"],
                "formato_pixel": verificacion["pix_fmt"],
            },
            **efectos_visuales,
            "actualizado": ahora_iso(),
        },
    )

    return guardar_estado(
        directorio_proyecto,
        "borrador_pendiente_aprobacion",
        video_borrador=ARCHIVO_BORRADOR,
        video_final=None,
        borrador_aprobado=False,
        duracion_voz=duracion_voz,
        duracion_cierre=CIERRE_SEGUNDOS,
        duracion_total=duracion_total,
        duracion_video=verificacion["duracion"],
        audio_codec=verificacion["audio_codec"],
        audio_max_db=verificacion["audio_max_db"],
        timeline_verificada=True,
        control_visual_verificado=True,
        control_audio_verificado=True,
        sin_subtitulos=True,
        desviacion_maxima_ms=round(desviacion_maxima_ms, 3),
        fotogramas_totales=verificacion["fotogramas"],
        sello_cierre=ARCHIVO_SELLO_CIERRE,
        resolucion=f"{ancho}x{alto}",
        fps=fps,
        error="",
    )


def generar_borrador_seguro(directorio_proyecto: str) -> None:
    try:
        generar_borrador(directorio_proyecto)
    except Exception as error:
        guardar_estado(
            directorio_proyecto,
            "error",
            error=str(error),
            borrador_aprobado=False,
        )


def aprobar_borrador(directorio_proyecto: str) -> dict:
    estado = cargar_estado(directorio_proyecto)

    if estado.get("estado") != "borrador_pendiente_aprobacion":
        raise ValueError("El vídeo borrador no está preparado para aprobarse.")

    origen = os.path.join(directorio_proyecto, ARCHIVO_BORRADOR)
    destino = os.path.join(directorio_proyecto, ARCHIVO_FINAL)

    if not os.path.isfile(origen):
        raise FileNotFoundError("No se encuentra el vídeo borrador.")

    descriptor, temporal = tempfile.mkstemp(
        prefix=".video-final-",
        suffix=".mp4",
        dir=directorio_proyecto,
    )
    os.close(descriptor)

    try:
        shutil.copyfile(origen, temporal)
        os.replace(temporal, destino)
    except Exception:
        if os.path.exists(temporal):
            os.remove(temporal)
        raise

    return guardar_estado(
        directorio_proyecto,
        "video_final_aprobado",
        video_final=ARCHIVO_FINAL,
        borrador_aprobado=True,
        borrador_aprobado_en=ahora_iso(),
        error="",
    )


def crear_texto_publicacion(resultado: dict) -> str:
    publicacion = resultado.get("publicacion", {})
    hashtags = publicacion.get("hashtags", [])

    if not isinstance(hashtags, list):
        hashtags = []

    return "\n".join(
        [
            "TÍTULO",
            str(publicacion.get("titulo", "")).strip(),
            "",
            "PIE DE FOTO",
            str(publicacion.get("descripcion", "")).strip(),
            "",
            "HASHTAGS",
            " ".join(str(elemento).strip() for elemento in hashtags[:5]),
            "",
            "COMENTARIO FIJADO",
            str(publicacion.get("comentario_fijado", "")).strip(),
            "",
            "RECORDATORIO",
            "Publica el comentario y fíjalo manualmente en Instagram.",
            "",
        ]
    )


def crear_paquete(directorio_proyecto: str, resultado: dict) -> dict:
    estado = cargar_estado(directorio_proyecto)

    if estado.get("estado") not in {"video_final_aprobado", "paquete_preparado"}:
        raise ValueError("El vídeo final debe aprobarse antes de crear el paquete.")

    video_final = os.path.join(directorio_proyecto, ARCHIVO_FINAL)

    if not os.path.isfile(video_final):
        raise FileNotFoundError("No se encuentra el vídeo final.")

    publicacion = os.path.join(directorio_proyecto, ARCHIVO_PUBLICACION)
    with open(publicacion, "w", encoding="utf-8") as archivo:
        archivo.write(crear_texto_publicacion(resultado))

    paquete = os.path.join(directorio_proyecto, ARCHIVO_PAQUETE)
    descriptor, temporal = tempfile.mkstemp(
        prefix=".paquete-",
        suffix=".zip",
        dir=directorio_proyecto,
    )
    os.close(descriptor)

    try:
        with zipfile.ZipFile(
            temporal,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archivo_zip:
            for raiz, directorios, archivos in os.walk(directorio_proyecto):
                directorios[:] = [
                    nombre
                    for nombre in directorios
                    if not nombre.startswith("montaje-")
                ]

                for nombre in sorted(archivos):
                    ruta = os.path.join(raiz, nombre)

                    if (
                        ruta in {paquete, temporal}
                        or nombre.startswith(".")
                        or nombre == "subtitulos.srt"
                    ):
                        continue

                    relativo = os.path.relpath(ruta, directorio_proyecto)
                    archivo_zip.write(ruta, relativo)

        os.replace(temporal, paquete)
    except Exception:
        if os.path.exists(temporal):
            os.remove(temporal)
        raise

    return guardar_estado(
        directorio_proyecto,
        "paquete_preparado",
        paquete=ARCHIVO_PAQUETE,
        publicacion=ARCHIVO_PUBLICACION,
        paquete_creado_en=ahora_iso(),
        error="",
    )


def obtener_resumen(directorio_proyecto: str) -> dict:
    estado = cargar_estado(directorio_proyecto)
    sincronizacion = cargar_sincronizacion(directorio_proyecto)
    datos_sincronizacion = cargar_json(
        os.path.join(directorio_proyecto, ARCHIVO_SINCRONIZACION)
    ) or {}
    verificacion_timeline = cargar_json(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_TIMELINE,
        )
    ) or {}
    verificacion_visual = cargar_json(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_VISUAL,
        )
    ) or {}
    verificacion_audio = cargar_json(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_AUDIO,
        )
    ) or {}
    verificacion_previa = cargar_json(
        os.path.join(
            directorio_proyecto,
            ARCHIVO_VERIFICACION_PREVIA,
        )
    ) or {}
    musica = obtener_ruta_musica(directorio_proyecto)
    imagenes = os.path.join(directorio_proyecto, "imagenes")
    total_imagenes = sum(
        os.path.isfile(os.path.join(imagenes, f"imagen{numero}.png"))
        for numero in range(1, TOTAL_IMAGENES + 1)
    )
    estado.update(
        {
            "sincronizacion": sincronizacion,
            "metodo_sincronizacion": datos_sincronizacion.get(
                "metodo",
                estado.get("metodo_sincronizacion", "estimado"),
            ),
            "semantica_validada": datos_sincronizacion.get(
                "semantica_validada",
                estado.get("semantica_validada", False),
            ),
            "fps_timeline": datos_sincronizacion.get(
                "fps_timeline",
                FPS_VIDEO,
            ),
            "desviacion_planificada_ms": datos_sincronizacion.get(
                "desviacion_maxima_ms",
            ),
            "timeline_verificada": (
                verificacion_timeline.get("verificada") is True
            ),
            "verificacion_timeline": verificacion_timeline,
            "control_visual_verificado": (
                verificacion_visual.get("verificada_automaticamente") is True
            ),
            "verificacion_visual": verificacion_visual,
            "control_audio_verificado": (
                verificacion_audio.get("verificada") is True
            ),
            "verificacion_audio": verificacion_audio,
            "preparacion_verificada": (
                verificacion_previa.get("preparado") is True
            ),
            "verificacion_previa": verificacion_previa,
            "sin_subtitulos": True,
            "total_imagenes": total_imagenes,
            "cierre": datos_sincronizacion.get("cierre"),
            "duracion_total": datos_sincronizacion.get(
                "duracion_total",
                estado.get("duracion_total"),
            ),
            "sello_disponible": RUTA_SELLO_CIERRE.is_file(),
            "sello_archivo": ARCHIVO_SELLO_CIERRE,
            "imagenes_aprobadas": imagenes_estan_aprobadas(
                directorio_proyecto
            ),
            "musica_url": (
                os.path.basename(musica)
                if musica
                else None
            ),
            "borrador_disponible": os.path.isfile(
                os.path.join(directorio_proyecto, ARCHIVO_BORRADOR)
            ),
            "final_disponible": os.path.isfile(
                os.path.join(directorio_proyecto, ARCHIVO_FINAL)
            ),
            "paquete_disponible": os.path.isfile(
                os.path.join(directorio_proyecto, ARCHIVO_PAQUETE)
            ),
        }
    )
    return estado
