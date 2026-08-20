from datetime import datetime
import hashlib
import json
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
MAXIMO_BYTES_MUSICA = 50 * 1024 * 1024
EXTENSIONES_MUSICA = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
ARCHIVO_ESTADO = "produccion.json"
ARCHIVO_SINCRONIZACION = "sincronizacion.json"
ARCHIVO_SUBTITULOS = "subtitulos.srt"
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


def _marca_srt(segundos: float) -> str:
    milisegundos = max(0, round(segundos * 1000))
    horas, resto = divmod(milisegundos, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    segundos_enteros, milesimas = divmod(resto, 1000)
    return f"{horas:02}:{minutos:02}:{segundos_enteros:02},{milesimas:03}"


def crear_subtitulos(sincronizacion: list[dict]) -> str:
    bloques = []
    contador = 1

    for segmento in sincronizacion:
        palabras_alineadas = segmento.get("palabras_alineadas")

        if isinstance(palabras_alineadas, list) and palabras_alineadas:
            fragmentos_alineados = [
                palabras_alineadas[indice:indice + 9]
                for indice in range(0, len(palabras_alineadas), 9)
            ]

            for fragmento in fragmentos_alineados:
                inicio = float(fragmento[0]["inicio"])
                fin = float(fragmento[-1]["fin"])
                bloques.append(
                    "\n".join(
                        [
                            str(contador),
                            f"{_marca_srt(inicio)} --> {_marca_srt(fin)}",
                            " ".join(
                                str(palabra["texto"])
                                for palabra in fragmento
                            ),
                        ]
                    )
                )
                contador += 1

            continue

        palabras = str(segmento["texto"]).split()
        fragmentos = [
            palabras[indice:indice + 9]
            for indice in range(0, len(palabras), 9)
        ] or [[]]
        duracion_fragmento = float(segmento["duracion"]) / len(fragmentos)

        for indice, fragmento in enumerate(fragmentos):
            inicio = float(segmento["inicio"]) + duracion_fragmento * indice
            fin = (
                float(segmento["fin"])
                if indice + 1 == len(fragmentos)
                else inicio + duracion_fragmento
            )
            bloques.append(
                "\n".join(
                    [
                        str(contador),
                        f"{_marca_srt(inicio)} --> {_marca_srt(fin)}",
                        " ".join(fragmento),
                    ]
                )
            )
            contador += 1

    return "\n\n".join(bloques) + "\n"


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
            "portada_segundos": PORTADA_SEGUNDOS,
            "metodo": metodo,
            "semantica_validada": semantica_validada,
            "segmentos": sincronizacion,
            "cierre": cierre,
            "duracion_total": cierre["fin"],
            "actualizado": ahora_iso(),
        },
    )

    with open(
        os.path.join(directorio_proyecto, ARCHIVO_SUBTITULOS),
        "w",
        encoding="utf-8",
    ) as archivo:
        archivo.write(crear_subtitulos(sincronizacion))

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


def _crear_clip(
    imagen: str,
    duracion: float,
    salida: str,
    ancho: int,
    alto: int,
    fps: int,
) -> None:
    fundido = min(0.25, duracion / 4)
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
            "-t",
            f"{duracion:.3f}",
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
        "format=yuv420p,fade=t=in:st=0:d=0.250[video]"
    )
    ejecutar(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-loop",
            "1",
            "-t",
            f"{CIERRE_SEGUNDOS:.3f}",
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
            salida,
        ]
    )


def _escapar_subtitulos(ruta: str) -> str:
    return (
        os.path.abspath(ruta)
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def _mezclar_video_audio(
    video_base: str,
    voz: str,
    musica: str | None,
    subtitulos: str,
    duracion_voz: float,
    duracion_total: float,
    salida: str,
) -> None:
    estilo = (
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=110"
    )
    filtro_subtitulos = (
        f"subtitles='{_escapar_subtitulos(subtitulos)}':"
        f"force_style='{estilo}'"
    )
    comando = ["ffmpeg", "-y", "-i", video_base, "-i", voz]

    if musica:
        inicio_salida = max(duracion_voz, duracion_total - CIERRE_SEGUNDOS)
        comando.extend(["-stream_loop", "-1", "-i", musica])
        comando.extend(
            [
                "-filter_complex",
                (
                    f"[1:a]apad=pad_dur={CIERRE_SEGUNDOS:.3f},"
                    f"atrim=0:{duracion_total:.3f},asetpts=N/SR/TB,"
                    "volume=1.0[voz];"
                    f"[2:a]atrim=0:{duracion_total:.3f},asetpts=N/SR/TB,"
                    "volume=0.10,afade=t=in:st=0:d=1,"
                    f"afade=t=out:st={inicio_salida:.3f}:"
                    f"d={CIERRE_SEGUNDOS:.3f}[musica];"
                    "[voz][musica]amix=inputs=2:duration=longest:"
                    "dropout_transition=0[audio]"
                ),
                "-map",
                "0:v:0",
                "-map",
                "[audio]",
            ]
        )
    else:
        comando.extend(
            [
                "-filter_complex",
                (
                    f"[1:a]apad=pad_dur={CIERRE_SEGUNDOS:.3f},"
                    f"atrim=0:{duracion_total:.3f},asetpts=N/SR/TB[audio]"
                ),
                "-map",
                "0:v:0",
                "-map",
                "[audio]",
            ]
        )

    comando.extend(
        [
            "-vf",
            filtro_subtitulos,
            "-t",
            f"{duracion_total:.3f}",
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


def validar_salida_multimedia(ruta: str, duracion_esperada: float) -> dict:
    sondeo = subprocess.run(
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

    if abs(duracion - duracion_esperada) > 0.5:
        raise RuntimeError(
            "La duración del borrador no coincide con la voz más el cierre de 3 segundos."
        )

    volumen = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
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
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    coincidencia = re.search(
        r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB",
        volumen.stderr,
        flags=re.IGNORECASE,
    )

    if volumen.returncode != 0 or not coincidencia:
        raise RuntimeError("No se pudo comprobar el volumen del borrador.")

    valor = coincidencia.group(1).lower()

    if valor in {"inf", "-inf"} or float(valor) <= -80.0:
        raise RuntimeError("El borrador contiene audio, pero está en silencio.")

    return {
        "duracion": round(duracion, 3),
        "video_codec": str(video.get("codec_name", "")),
        "audio_codec": str(audio.get("codec_name", "")),
        "audio_max_db": float(valor),
    }


def generar_borrador(
    directorio_proyecto: str,
    ancho: int = ANCHO_VIDEO,
    alto: int = ALTO_VIDEO,
    fps: int = FPS_VIDEO,
    sello_cierre: str | None = None,
) -> dict:
    comprobar_ffmpeg()
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

    imagenes = obtener_imagenes(directorio_proyecto)
    sello = obtener_ruta_sello_cierre(sello_cierre)
    voz = os.path.join(directorio_proyecto, "voz.mp3")
    duracion_voz = obtener_duracion(voz)
    cierre = crear_segmento_cierre(duracion_voz)
    duracion_total = float(cierre["fin"])
    musica = obtener_ruta_musica(directorio_proyecto)
    if musica and not estado.get("musica_aprobada"):
        raise ValueError("La música cargada todavía no está aprobada.")

    subtitulos = os.path.join(directorio_proyecto, ARCHIVO_SUBTITULOS)

    if not os.path.isfile(subtitulos):
        raise FileNotFoundError("No se encuentra el archivo de subtítulos.")

    salida = os.path.join(directorio_proyecto, ARCHIVO_BORRADOR)

    with tempfile.TemporaryDirectory(
        prefix="montaje-",
        dir=directorio_proyecto,
    ) as temporal:
        clips = []

        for imagen, segmento in zip(imagenes, sincronizacion, strict=True):
            clip = os.path.join(temporal, f"clip-{segmento['numero']:02}.mp4")
            _crear_clip(
                imagen,
                float(segmento["duracion"]),
                clip,
                ancho,
                alto,
                fps,
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
        temporal_salida = os.path.join(temporal, ARCHIVO_BORRADOR)
        _mezclar_video_audio(
            video_base,
            voz,
            musica,
            subtitulos,
            duracion_voz,
            duracion_total,
            temporal_salida,
        )
        verificacion = validar_salida_multimedia(
            temporal_salida,
            duracion_total,
        )
        os.replace(temporal_salida, salida)

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

                    if ruta in {paquete, temporal} or nombre.startswith("."):
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
