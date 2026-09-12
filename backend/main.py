from datetime import datetime
from dotenv import load_dotenv
from html import escape as escape_html
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request as UrlRequest,
    build_opener,
)

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from starlette.concurrency import run_in_threadpool

load_dotenv()

from backend.busqueda_imagenes import buscar_imagenes_reales
from backend.indice_temas import (
    construir_contexto_indice,
    crear_dossier_generacion,
    crear_referencia_tema,
    obtener_tema_por_id,
    obtener_tema_por_titulo,
    validar_seleccion,
)
from backend.imagenes import generar_imagen
from backend.produccion import (
    MAXIMO_BYTES_MUSICA,
    aprobar_borrador,
    aprobar_imagenes,
    aprobar_musica,
    aprobar_sincronizacion,
    cargar_estado as cargar_estado_produccion,
    crear_paquete,
    generar_borrador_seguro,
    guardar_estado as guardar_estado_produccion,
    guardar_musica,
    iniciar_generacion_borrador,
    obtener_imagenes as obtener_imagenes_produccion,
    obtener_resumen as obtener_resumen_produccion,
    preparar_sincronizacion,
    recuperar_montaje_interrumpido,
    validar_anclas_plan_visual,
    verificar_preparacion_montaje,
)
from backend.voz import (
    aprobar_voz,
    cargar_estado_voz,
    generar_voz,
    marcar_generacion_iniciada,
    obtener_configuracion,
    obtener_ruta_audio,
    voz_esta_aprobada,
)

from backend.instagram import (
    InstagramError,
    completar_oauth,
    estado_cuenta,
    iniciar_oauth,
    publicar_comentario,
    publicar_media,
)


TOTAL_IMAGENES = 8
DIRECTORIO_PROYECTOS = "backend/proyectos"
DIRECTORIO_PROYECTOS_APROBADOS = "backend/data/proyectos_aprobados"
MAXIMO_BYTES_FOTOGRAFIA = 20 * 1024 * 1024
TIEMPO_MAXIMO_DESCARGA = 30

os.makedirs(DIRECTORIO_PROYECTOS, exist_ok=True)

with open(
    "backend/manual/manual_maestro.txt",
    "r",
    encoding="utf-8"
) as f:
    manual_maestro = f.read()

with open(
    "backend/manual/plantilla_generacion.txt",
    "r",
    encoding="utf-8"
) as f:
    plantilla_generacion = f.read()

app = FastAPI()


def obtener_cliente_openai() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("Falta el secreto OPENAI_API_KEY.")

    return OpenAI(api_key=api_key)

app.mount(
    "/proyectos",
    StaticFiles(directory=DIRECTORIO_PROYECTOS),
    name="proyectos"
)

def contexto_indice_temas(request: Request) -> dict:
    contexto = construir_contexto_indice(
        DIRECTORIO_PROYECTOS
    )
    contexto["proyectos_aprobados"] = listar_proyectos_aprobados()
    return contexto


templates = Jinja2Templates(
    directory="backend/templates",
    context_processors=[contexto_indice_temas]
)


def normalizar_texto(texto: str) -> str:
    texto_normalizado = unicodedata.normalize(
        "NFKD",
        str(texto)
    )

    return texto_normalizado.encode(
        "ascii",
        "ignore"
    ).decode("ascii").lower()


def crear_slug(texto: str) -> str:
    texto_ascii = normalizar_texto(texto)

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        texto_ascii
    ).strip("-")

    return slug[:50].rstrip("-") or "sin-tema"


def listar_proyectos_aprobados() -> list[dict]:
    proyectos = []
    directorio = DIRECTORIO_PROYECTOS_APROBADOS

    if not os.path.isdir(directorio):
        return proyectos

    for nombre in sorted(os.listdir(directorio)):
        if not nombre.endswith(".json"):
            continue

        ruta = os.path.join(directorio, nombre)
        try:
            with open(ruta, "r", encoding="utf-8") as archivo:
                definicion = json.load(archivo)
        except (OSError, json.JSONDecodeError):
            continue

        proyecto_id = str(definicion.get("proyecto_id", "")).strip()
        tema = str(definicion.get("tema", "")).strip()
        if not proyecto_id or not tema:
            continue

        proyectos.append({
            "proyecto_id": proyecto_id,
            "tema": tema,
            "numero": str(definicion.get("numero", "")).strip(),
            "existente": os.path.isfile(
                os.path.join(
                    DIRECTORIO_PROYECTOS,
                    proyecto_id,
                    "proyecto.json",
                )
            ),
        })

    return proyectos


def validar_proyecto_id(proyecto_id: str) -> str:
    if not isinstance(proyecto_id, str):
        raise ValueError(
            "El identificador del proyecto no es válido."
        )

    if not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,99}",
        proyecto_id
    ):
        raise ValueError(
            "El identificador del proyecto no es válido."
        )

    return proyecto_id


def crear_directorio_proyecto(tema: str) -> str:
    fecha = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = crear_slug(tema)
    identificador_base = f"pergamino-{fecha}-{slug}"
    proyecto_id = identificador_base
    contador = 2

    while os.path.exists(
        os.path.join(DIRECTORIO_PROYECTOS, proyecto_id)
    ):
        proyecto_id = f"{identificador_base}-{contador}"
        contador += 1

    os.makedirs(
        os.path.join(
            DIRECTORIO_PROYECTOS,
            proyecto_id,
            "imagenes"
        )
    )

    return proyecto_id


def obtener_directorio_proyecto(proyecto_id: str) -> str:
    proyecto_id = validar_proyecto_id(proyecto_id)

    return os.path.join(
        DIRECTORIO_PROYECTOS,
        proyecto_id
    )


def obtener_directorio_imagenes(proyecto_id: str) -> str:
    return os.path.join(
        obtener_directorio_proyecto(proyecto_id),
        "imagenes"
    )


def obtener_ruta_imagen(
    proyecto_id: str,
    numero: int
) -> str:
    return os.path.join(
        obtener_directorio_imagenes(proyecto_id),
        f"imagen{numero}.png"
    )


def copiar_imagenes_aprobadas(proyecto_id: str) -> None:
    proyecto_id = validar_proyecto_id(proyecto_id)
    directorio_origen = os.path.join(
        DIRECTORIO_PROYECTOS_APROBADOS,
        proyecto_id,
        "imagenes",
    )
    if not os.path.isdir(directorio_origen):
        return

    directorio_destino = obtener_directorio_imagenes(proyecto_id)
    os.makedirs(directorio_destino, exist_ok=True)

    for numero in range(1, TOTAL_IMAGENES + 1):
        nombre = f"imagen{numero}.png"
        ruta_origen = os.path.join(directorio_origen, nombre)
        ruta_destino = os.path.join(directorio_destino, nombre)
        if os.path.isfile(ruta_origen) and not os.path.exists(ruta_destino):
            shutil.copy2(ruta_origen, ruta_destino)


def obtener_ruta_candidatas(
    proyecto_id: str,
    numero: int
) -> str:
    return os.path.join(
        obtener_directorio_proyecto(proyecto_id),
        f"candidatas-imagen-{numero}.json"
    )


def obtener_ruta_seleccion(
    proyecto_id: str,
    numero: int
) -> str:
    return os.path.join(
        obtener_directorio_proyecto(proyecto_id),
        f"seleccion-imagen-{numero}.json"
    )


def obtener_proyecto_id(resultado: dict) -> str:
    proyecto_id = resultado.get("_proyecto_id", "")

    if not proyecto_id:
        raise ValueError(
            "No se encuentra el identificador del proyecto."
        )

    validar_proyecto_id(proyecto_id)

    directorio = obtener_directorio_proyecto(proyecto_id)

    if not os.path.isdir(directorio):
        raise ValueError(
            "La carpeta del proyecto no existe."
        )

    return proyecto_id


def guardar_proyecto(
    proyecto_id: str,
    tema: str,
    resultado: dict,
    tema_indice: dict | None = None
) -> None:
    ruta = os.path.join(
        obtener_directorio_proyecto(proyecto_id),
        "proyecto.json"
    )

    if tema_indice is None and os.path.isfile(ruta):
        try:
            with open(
                ruta,
                "r",
                encoding="utf-8"
            ) as archivo:
                datos_anteriores = json.load(archivo)

            referencia_anterior = datos_anteriores.get(
                "tema_indice"
            )

            if isinstance(referencia_anterior, dict):
                tema_indice = referencia_anterior
        except (OSError, json.JSONDecodeError):
            pass

    datos = {
        "proyecto_id": proyecto_id,
        "tema": tema,
        "creado": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "resultado": resultado
    }

    if tema_indice:
        datos["tema_indice"] = tema_indice

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )


def cargar_proyecto(
    proyecto_id: str
) -> tuple[str, dict]:
    try:
        directorio = obtener_directorio_proyecto(
            proyecto_id
        )
    except ValueError as error:
        raise ValueError(
            "El identificador del proyecto no es válido."
        ) from error

    if not os.path.isdir(directorio):
        raise FileNotFoundError(
            "La carpeta del proyecto no existe."
        )

    ruta = os.path.join(
        directorio,
        "proyecto.json"
    )

    if not os.path.isfile(ruta):
        raise FileNotFoundError(
            "El archivo proyecto.json no existe."
        )

    try:
        with open(
            ruta,
            "r",
            encoding="utf-8"
        ) as archivo:
            datos = json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "No se pudo leer el proyecto guardado."
        ) from error

    if not isinstance(datos, dict):
        raise ValueError(
            "El proyecto guardado no tiene un formato válido."
        )

    tema = datos.get("tema", "")
    resultado = datos.get("resultado")

    if not isinstance(tema, str):
        raise ValueError(
            "El tema del proyecto guardado no es válido."
        )

    if not isinstance(resultado, dict):
        raise ValueError(
            "El resultado del proyecto guardado no es válido."
        )

    plan_visual = resultado.get("plan_visual")

    if (
        not isinstance(plan_visual, list)
        or len(plan_visual) != TOTAL_IMAGENES
    ):
        raise ValueError(
            "El proyecto guardado debe contener exactamente "
            "8 imágenes en plan_visual."
        )

    proyecto_guardado_id = datos.get(
        "proyecto_id",
        proyecto_id
    )

    if proyecto_guardado_id != proyecto_id:
        raise ValueError(
            "El identificador interno del proyecto no coincide "
            "con su carpeta."
        )

    resultado["_proyecto_id"] = proyecto_id

    return tema, resultado


def recuperar_proyecto_aprobado(proyecto_id: str) -> str:
    proyecto_id = validar_proyecto_id(proyecto_id)
    ruta_definicion = os.path.join(
        DIRECTORIO_PROYECTOS_APROBADOS,
        f"{proyecto_id}.json",
    )
    if not os.path.isfile(ruta_definicion):
        raise FileNotFoundError(
            "No existe un proyecto editorial aprobado con ese identificador."
        )

    directorio = obtener_directorio_proyecto(proyecto_id)
    ruta_proyecto = os.path.join(directorio, "proyecto.json")
    if os.path.isfile(ruta_proyecto):
        cargar_proyecto(proyecto_id)
        copiar_imagenes_aprobadas(proyecto_id)
        return proyecto_id
    if os.path.exists(directorio):
        raise ValueError(
            "La carpeta de destino ya existe pero no contiene un proyecto válido."
        )

    try:
        with open(ruta_definicion, "r", encoding="utf-8") as archivo:
            definicion = json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "La definición del proyecto aprobado no es válida."
        ) from error

    if definicion.get("proyecto_id") != proyecto_id:
        raise ValueError(
            "El identificador interno del proyecto aprobado no coincide."
        )

    tema = str(definicion.get("tema", "")).strip()
    resultado = definicion.get("resultado")
    if not tema or not isinstance(resultado, dict):
        raise ValueError("El proyecto aprobado está incompleto.")

    plan_visual = resultado.get("plan_visual")
    if not isinstance(plan_visual, list) or len(plan_visual) != TOTAL_IMAGENES:
        raise ValueError(
            "El proyecto aprobado debe contener exactamente ocho imágenes."
        )
    validar_anclas_plan_visual(
        str(resultado.get("guion", "")),
        plan_visual,
    )

    ficha_indice = obtener_tema_por_id(
        str(definicion.get("tema_indice_id", ""))
    )
    if ficha_indice["titulo"] != tema:
        raise ValueError(
            "El tema del proyecto no coincide con el índice maestro."
        )

    os.makedirs(os.path.join(directorio, "imagenes"))
    copiar_imagenes_aprobadas(proyecto_id)
    resultado = dict(resultado)
    resultado["_proyecto_id"] = proyecto_id
    guardar_proyecto(
        proyecto_id,
        tema,
        resultado,
        crear_referencia_tema(ficha_indice),
    )
    return proyecto_id


def guardar_candidatas(
    proyecto_id: str,
    numero: int,
    candidatas: list[dict]
) -> None:
    ruta = obtener_ruta_candidatas(
        proyecto_id,
        numero
    )

    datos = {
        "numero_imagen": numero,
        "actualizado": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "candidatas": candidatas
    }

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )


def cargar_candidatas_imagen(
    proyecto_id: str,
    numero: int
) -> list[dict]:
    ruta = obtener_ruta_candidatas(
        proyecto_id,
        numero
    )

    if not os.path.isfile(ruta):
        raise FileNotFoundError(
            f"No hay fotografías candidatas para la Imagen {numero}."
        )

    try:
        with open(
            ruta,
            "r",
            encoding="utf-8"
        ) as archivo:
            datos = json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "No se pudieron leer las fotografías candidatas guardadas."
        ) from error

    candidatas = datos.get("candidatas")

    if not isinstance(candidatas, list):
        raise ValueError(
            "Las fotografías candidatas guardadas no tienen un formato válido."
        )

    return candidatas


def guardar_seleccion(
    proyecto_id: str,
    numero: int,
    indice_candidata: int,
    candidata: dict,
    url_descargada: str,
    formato: str
) -> None:
    ruta = obtener_ruta_seleccion(
        proyecto_id,
        numero
    )

    datos = {
        "numero_imagen": numero,
        "indice_candidata": indice_candidata,
        "seleccionado": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "url_descargada": url_descargada,
        "formato_original": formato,
        "fotografia": candidata
    }

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )


def obtener_selecciones_guardadas(
    proyecto_id: str
) -> dict:
    selecciones = {}

    for numero in range(1, TOTAL_IMAGENES + 1):
        ruta = obtener_ruta_seleccion(
            proyecto_id,
            numero
        )

        if not os.path.isfile(ruta):
            continue

        try:
            with open(
                ruta,
                "r",
                encoding="utf-8"
            ) as archivo:
                datos = json.load(archivo)

            if isinstance(datos, dict):
                selecciones[numero] = datos
        except (OSError, json.JSONDecodeError):
            continue

    return selecciones


def validar_url_publica(url: str) -> str:
    try:
        datos = urlparse(str(url).strip())
        puerto = datos.port
    except ValueError as error:
        raise ValueError(
            "La dirección de la fotografía no es válida."
        ) from error

    if datos.scheme not in {"http", "https"} or not datos.hostname:
        raise ValueError(
            "La candidata no contiene una dirección web descargable."
        )

    if datos.username or datos.password:
        raise ValueError(
            "La dirección de la fotografía no es segura."
        )

    hostname = datos.hostname.rstrip(".").casefold()

    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError(
            "La dirección de la fotografía no es pública."
        )

    try:
        direcciones = socket.getaddrinfo(
            hostname,
            puerto or (443 if datos.scheme == "https" else 80),
            type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise ValueError(
            "No se pudo localizar el servidor de la fotografía."
        ) from error

    if not direcciones:
        raise ValueError(
            "No se pudo localizar el servidor de la fotografía."
        )

    for direccion in direcciones:
        ip_texto = direccion[4][0].split("%", 1)[0]

        try:
            ip = ipaddress.ip_address(ip_texto)
        except ValueError as error:
            raise ValueError(
                "La dirección del servidor de la fotografía no es válida."
            ) from error

        if not ip.is_global:
            raise ValueError(
                "La dirección de la fotografía no es pública."
            )

    return datos.geturl()


class RedireccionFotografiaSegura(HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl
    ):
        destino = urljoin(req.full_url, newurl)
        validar_url_publica(destino)

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            destino
        )


def detectar_formato_imagen(contenido: bytes) -> str:
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if contenido.startswith(b"\xff\xd8\xff"):
        return "jpeg"

    if contenido.startswith((b"GIF87a", b"GIF89a")):
        return "gif"

    if (
        len(contenido) >= 12
        and contenido.startswith(b"RIFF")
        and contenido[8:12] == b"WEBP"
    ):
        return "webp"

    raise ValueError(
        "El archivo descargado no es una fotografía PNG, JPEG, GIF o WebP."
    )


def descargar_fotografia(url: str) -> tuple[bytes, str, str]:
    url = validar_url_publica(url)
    solicitud = UrlRequest(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; ElPergaminoPerdido/1.0)"
            ),
            "Accept": "image/png,image/jpeg,image/gif,image/webp"
        }
    )
    cliente_http = build_opener(
        RedireccionFotografiaSegura()
    )

    try:
        with cliente_http.open(
            solicitud,
            timeout=TIEMPO_MAXIMO_DESCARGA
        ) as respuesta:
            url_final = validar_url_publica(
                respuesta.geturl()
            )
            longitud = respuesta.headers.get("Content-Length")

            if longitud:
                try:
                    if int(longitud) > MAXIMO_BYTES_FOTOGRAFIA:
                        raise ValueError(
                            "La fotografía supera el límite de 20 MB."
                        )
                except ValueError as error:
                    if "supera el límite" in str(error):
                        raise

            contenido = respuesta.read(
                MAXIMO_BYTES_FOTOGRAFIA + 1
            )
    except ValueError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            "El servidor de origen no permitió descargar la fotografía."
        ) from error

    if len(contenido) > MAXIMO_BYTES_FOTOGRAFIA:
        raise ValueError(
            "La fotografía supera el límite de 20 MB."
        )

    formato = detectar_formato_imagen(contenido)

    return contenido, url_final, formato


def guardar_fotografia_seleccionada(
    proyecto_id: str,
    numero: int,
    candidata: dict
) -> tuple[str, str]:
    urls = []

    for clave in ("imagen_url", "miniatura_url"):
        url = str(candidata.get(clave, "")).strip()

        if url and url not in urls:
            urls.append(url)

    urls_web = [
        url
        for url in urls
        if urlparse(url).scheme in {"http", "https"}
    ]

    if not urls_web:
        raise ValueError(
            "Esta candidata no es una fotografía real descargable."
        )

    ultimo_error = None

    for url in urls_web:
        try:
            contenido, url_final, formato = descargar_fotografia(url)
            break
        except (ValueError, RuntimeError) as error:
            ultimo_error = error
    else:
        raise RuntimeError(
            "No se pudo descargar ni la fotografía original ni su miniatura."
        ) from ultimo_error

    directorio = obtener_directorio_imagenes(proyecto_id)
    os.makedirs(directorio, exist_ok=True)
    descriptor, ruta_temporal = tempfile.mkstemp(
        prefix=f".imagen{numero}-",
        suffix=".tmp",
        dir=directorio
    )

    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido)

        os.replace(
            ruta_temporal,
            obtener_ruta_imagen(proyecto_id, numero)
        )
    except Exception:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)
        raise

    return url_final, formato


def obtener_candidatas_guardadas(
    proyecto_id: str
) -> dict:
    candidatas_guardadas = {}

    for numero in range(1, TOTAL_IMAGENES + 1):
        ruta = obtener_ruta_candidatas(
            proyecto_id,
            numero
        )

        if not os.path.exists(ruta):
            continue

        try:
            with open(
                ruta,
                "r",
                encoding="utf-8"
            ) as archivo:
                datos = json.load(archivo)

            candidatas_guardadas[numero] = datos.get(
                "candidatas",
                []
            )
        except (OSError, json.JSONDecodeError):
            candidatas_guardadas[numero] = []

    return candidatas_guardadas


def obtener_imagenes_generadas(
    proyecto_id: str
) -> dict:
    marca_tiempo = int(time.time())
    imagenes = {}

    for numero in range(1, TOTAL_IMAGENES + 1):
        if os.path.exists(
            obtener_ruta_imagen(proyecto_id, numero)
        ):
            imagenes[numero] = (
                f"/proyectos/{proyecto_id}/imagenes/"
                f"imagen{numero}.png?v={marca_tiempo}"
            )

    return imagenes


def obtener_estado_voz_interfaz(
    proyecto_id: str
) -> dict | None:
    directorio = obtener_directorio_proyecto(
        proyecto_id
    )
    estado = cargar_estado_voz(directorio)

    if not estado and os.path.isfile(obtener_ruta_audio(directorio)):
        estado = {
            "estado": "archivo_existente",
            "aprobada": False,
            "archivo": "voz.mp3",
            "origen": "OneDrive",
            "actualizado": datetime.now().isoformat(timespec="seconds"),
        }

    if not estado:
        return None

    estado = dict(estado)

    if os.path.isfile(obtener_ruta_audio(directorio)):
        marca_tiempo = int(time.time())
        estado["audio_url"] = (
            f"/proyectos/{proyecto_id}/voz.mp3"
            f"?v={marca_tiempo}"
        )

    return estado


def exigir_voz_aprobada(
    proyecto_id: str,
    resultado: dict
) -> None:
    guion = str(resultado.get("guion", "")).strip()

    directorio = obtener_directorio_proyecto(proyecto_id)

    # Permitir continuar cuando el audio final ya existe en OneDrive.
    # En ese caso no se vuelve a generar ni a consumir créditos.
    if os.path.isfile(obtener_ruta_audio(directorio)):
        return

    if not voz_esta_aprobada(
        directorio,
        guion
    ):
        raise ValueError(
            "Antes de preparar imágenes debes generar, escuchar "
            "y aprobar la voz."
        )


def ajustar_guion_a_duracion(
    guion: str,
    duracion_actual: float
) -> str:
    guion = str(guion).strip()

    if not guion:
        raise ValueError(
            "El guion está vacío."
        )

    if duracion_actual <= 0:
        raise ValueError(
            "La duración actual no es válida."
        )

    palabras_actuales = len(guion.split())
    proporcion = min(
        0.95,
        78.0 / duracion_actual
    )
    maximo_palabras = max(
        60,
        int(palabras_actuales * proporcion * 0.96)
    )
    respuesta = obtener_cliente_openai().responses.create(
        model="gpt-5.6-luna",
        input=f"""
Acorta el siguiente guion de El Pergamino Perdido para que su narración
quede entre 76 y 80 segundos en la misma voz. El audio actual dura
{duracion_actual:.2f} segundos.

REGLAS OBLIGATORIAS

- Devuelve únicamente la narración final, sin títulos ni explicaciones.
- Máximo aproximado: {maximo_palabras} palabras.
- Conserva el gancho inicial, los hechos esenciales y la conclusión.
- Mantén exactamente el mismo orden narrativo.
- No inventes datos, fechas, nombres ni citas.
- Elimina primero repeticiones, adjetivos y detalles secundarios.
- Mantén el tono documental, directo y misterioso.
- No añadas saludos ni instrucciones para locución.

GUION ORIGINAL

{guion}
"""
    )
    guion_ajustado = str(
        respuesta.output_text
    ).strip()

    if not guion_ajustado:
        raise ValueError(
            "No se pudo obtener el guion ajustado."
        )

    if len(guion_ajustado.split()) >= palabras_actuales:
        raise ValueError(
            "El ajuste no redujo la longitud del guion."
        )

    return guion_ajustado


def archivar_voz_anterior(
    directorio_proyecto: str
) -> None:
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")

    for nombre in ("voz.mp3", "voz.json"):
        ruta = os.path.join(
            directorio_proyecto,
            nombre
        )

        if not os.path.exists(ruta):
            continue

        base, extension = os.path.splitext(nombre)
        destino = os.path.join(
            directorio_proyecto,
            f"{base}-anterior-{marca}{extension}"
        )
        os.replace(ruta, destino)


def requiere_fotografia_real(escena: dict) -> bool:
    tipo = normalizar_texto(
        escena.get("tipo", "")
    )
    tipo = re.sub(r"[_-]+", " ", tipo)
    tipo = re.sub(r"\s+", " ", tipo).strip()

    return (
        "fotografia real" in tipo
        or "restauracion" in tipo
    )


def obtener_consultas_busqueda(
    escena: dict,
    tema: str
) -> list[str]:
    busquedas = escena.get("buscar", [])

    if isinstance(busquedas, str):
        consultas = [busquedas.strip()]
    elif isinstance(busquedas, list):
        consultas = [
            str(busqueda).strip()
            for busqueda in busquedas
            if str(busqueda).strip()
        ]
    else:
        consultas = []

    if not consultas:
        motivo = str(
            escena.get("motivo", "")
        ).strip()

        consulta_respaldo = (
            f"{tema} {motivo} fotografía real histórica"
        ).strip()

        consultas = [consulta_respaldo]

    return consultas


def crear_prompt_imagen(
    resultado: dict,
    numero: int,
    permitir_recreacion_ia: bool = False
) -> str:
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise ValueError(
            "El número de imagen debe estar entre 1 y 8."
        )

    plan_visual = resultado.get("plan_visual", [])

    if len(plan_visual) < numero:
        raise ValueError(
            f"No existe la imagen {numero} en el plan visual."
        )

    escena = plan_visual[numero - 1]

    es_recreacion_sustitutiva = (
        requiere_fotografia_real(escena)
        and permitir_recreacion_ia
    )

    if (
        requiere_fotografia_real(escena)
        and not es_recreacion_sustitutiva
    ):
        raise ValueError(
            "Esta escena requiere una fotografía real. "
            "Debe buscarse y seleccionarse antes de continuar."
        )

    if es_recreacion_sustitutiva:
        tipo = (
            "RECREACIÓN IA DOCUMENTAL AUTORIZADA TRAS DESCARTAR "
            "LAS FOTOGRAFÍAS CANDIDATAS"
        )
    else:
        tipo = escena.get("tipo", "")
    motivo = escena.get("motivo", "")
    edicion = escena.get("edicion", "")
    prompt_original = escena.get("prompt", "").strip()
    busquedas = escena.get("buscar", [])

    if isinstance(busquedas, list):
        referencias = "\n".join(
            str(busqueda) for busqueda in busquedas
        )
    else:
        referencias = str(busquedas)

    if prompt_original:
        descripcion = prompt_original
    else:
        descripcion = f"""
Referencias documentales:
{referencias}

Objetivo narrativo:
{motivo}

Dirección visual:
{edicion}
"""

    if numero == 1:
        requisitos = """
- Imagen hiperrealista y cinematográfica.
- Portada muy impactante.
- Conflicto, peligro, anomalía o consecuencia humana evidente.
- Un único punto focal.
- Comprensible en menos de un segundo.
- Estética de documental histórico premium.
- Rigor histórico.
- Iluminación dramática pero realista.
- Máxima nitidez.
"""
    else:
        requisitos = """
- Imagen hiperrealista y cinematográfica.
- Escena clara y relevante para el momento narrativo.
- Un punto focal principal.
- Estética de documental histórico premium.
- Rigor histórico.
- Iluminación realista.
- Máxima nitidez.
- Coherencia visual con el resto del Reel.
"""

    return f"""
Crea la imagen {numero} de 8 para un Reel documental histórico vertical 9:16.

TIPO DE RECURSO:
{tipo}

CONTENIDO:
{descripcion}

REQUISITOS:
{requisitos}
- Sin texto.
- Sin letras.
- Sin títulos.
- Sin logotipos.
- Sin marcas de agua.
- Sin aspecto de pintura.
- Sin aspecto de cartel.
- Sin apariencia de ilustración.
- No presentar una recreación como si fuera una fotografía histórica auténtica.
"""


def crear_imagen(
    resultado: dict,
    numero: int,
    proyecto_id: str,
    permitir_recreacion_ia: bool = False
) -> None:
    prompt_final = crear_prompt_imagen(
        resultado,
        numero,
        permitir_recreacion_ia
    )

    generar_imagen(
        prompt_final,
        numero,
        obtener_directorio_imagenes(proyecto_id)
    )


@app.get("/api/indice-temas")
async def consultar_indice_temas():
    return construir_contexto_indice(
        DIRECTORIO_PROYECTOS
    )


@app.get("/", response_class=HTMLResponse)
async def inicio(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": "",
            "resultado": None,
            "resultado_json": "",
            "imagenes": {},
            "candidatas": {},
            "selecciones": {},
            "imagen_generando": None,
            "voz": None,
            "voz_generando": False
        }
    )


@app.post("/recuperar-proyecto-aprobado/{proyecto_id}")
async def abrir_proyecto_aprobado(proyecto_id: str):
    try:
        proyecto_id = recuperar_proyecto_aprobado(proyecto_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return RedirectResponse(
        url=f"/proyecto/{proyecto_id}",
        status_code=303,
    )


@app.get(
    "/proyecto/{proyecto_id}",
    response_class=HTMLResponse
)
async def abrir_proyecto(
    proyecto_id: str,
    request: Request
):
    try:
        tema, resultado = cargar_proyecto(
            proyecto_id
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    resultado_json = json.dumps(
        resultado,
        ensure_ascii=False
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


@app.post("/generar", response_class=HTMLResponse)
async def generar(
    request: Request,
    tema: str = Form(""),
    tema_id: str = Form("")
):
    tema = tema.strip()
    tema_id = tema_id.strip()
    ficha_indice = None

    try:
        if tema_id:
            ficha_indice = obtener_tema_por_id(
                tema_id
            )
            validar_seleccion(
                ficha_indice,
                DIRECTORIO_PROYECTOS
            )
            tema = ficha_indice["titulo"]
        elif tema:
            ficha_indice = obtener_tema_por_titulo(
                tema
            )

            if ficha_indice:
                validar_seleccion(
                    ficha_indice,
                    DIRECTORIO_PROYECTOS
                )
                tema = ficha_indice["titulo"]
        else:
            raise ValueError(
                "Seleccione un tema del índice o escriba uno nuevo."
            )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    dossier_generacion = crear_dossier_generacion(
        ficha_indice
    )

    respuesta = obtener_cliente_openai().responses.create(
        model="gpt-5.6-luna",
        input=f"""
{manual_maestro}

{plantilla_generacion}
{dossier_generacion}

TEMA

{tema}
"""
    )

    resultado = json.loads(respuesta.output_text)
    plan_visual = resultado.get("plan_visual")

    if (
        not isinstance(plan_visual, list)
        or len(plan_visual) != TOTAL_IMAGENES
    ):
        raise ValueError(
            "La respuesta debe contener exactamente 8 imágenes "
            "en plan_visual."
        )

    validar_anclas_plan_visual(
        str(resultado.get("guion", "")),
        plan_visual,
    )

    proyecto_id = crear_directorio_proyecto(tema)
    resultado["_proyecto_id"] = proyecto_id

    guardar_proyecto(
        proyecto_id,
        tema,
        resultado,
        crear_referencia_tema(ficha_indice)
        if ficha_indice
        else None
    )

    resultado_json = json.dumps(
        resultado,
        ensure_ascii=False
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": {},
            "candidatas": {},
            "selecciones": {},
            "imagen_generando": None,
            "voz": None,
            "voz_generando": False
        }
    )


@app.post(
    "/generar-voz",
    response_class=HTMLResponse
)
async def iniciar_generacion_voz(
    request: Request,
    background_tasks: BackgroundTasks,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    try:
        resultado_formulario = json.loads(
            resultado_json
        )
        proyecto_id = obtener_proyecto_id(
            resultado_formulario
        )
        tema_guardado, resultado = cargar_proyecto(
            proyecto_id
        )
        obtener_configuracion()
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Los datos del Pergamino no son válidos."
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    guion = str(resultado.get("guion", "")).strip()

    if not guion:
        raise HTTPException(
            status_code=400,
            detail="El guion está vacío."
        )

    directorio = obtener_directorio_proyecto(
        proyecto_id
    )
    estado_actual = cargar_estado_voz(directorio)

    if (
        estado_actual
        and estado_actual.get("estado") == "generando"
    ):
        raise HTTPException(
            status_code=409,
            detail="La voz ya se está generando."
        )

    marcar_generacion_iniciada(
        directorio,
        guion
    )
    background_tasks.add_task(
        generar_voz,
        directorio,
        guion
    )
    resultado_json_guardado = json.dumps(
        resultado,
        ensure_ascii=False
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema_guardado or tema,
            "resultado": resultado,
            "resultado_json": resultado_json_guardado,
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": True
        }
    )


@app.post(
    "/comprobar-voz",
    response_class=HTMLResponse
)
async def comprobar_voz(
    request: Request,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    try:
        resultado_formulario = json.loads(
            resultado_json
        )
        proyecto_id = obtener_proyecto_id(
            resultado_formulario
        )
        tema_guardado, resultado = cargar_proyecto(
            proyecto_id
        )
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Los datos del Pergamino no son válidos."
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    voz = obtener_estado_voz_interfaz(
        proyecto_id
    )
    voz_generando = bool(
        voz and voz.get("estado") == "generando"
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema_guardado or tema,
            "resultado": resultado,
            "resultado_json": json.dumps(
                resultado,
                ensure_ascii=False
            ),
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": voz,
            "voz_generando": voz_generando
        }
    )


@app.post(
    "/aprobar-voz",
    response_class=HTMLResponse
)
async def confirmar_voz(
    request: Request,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    try:
        resultado_formulario = json.loads(
            resultado_json
        )
        proyecto_id = obtener_proyecto_id(
            resultado_formulario
        )
        tema_guardado, resultado = cargar_proyecto(
            proyecto_id
        )
        aprobar_voz(
            obtener_directorio_proyecto(proyecto_id),
            str(resultado.get("guion", ""))
        )
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Los datos del Pergamino no son válidos."
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema_guardado or tema,
            "resultado": resultado,
            "resultado_json": json.dumps(
                resultado,
                ensure_ascii=False
            ),
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


@app.post(
    "/ajustar-guion",
    response_class=HTMLResponse
)
async def ajustar_guion(
    request: Request,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    try:
        resultado_formulario = json.loads(
            resultado_json
        )
        proyecto_id = obtener_proyecto_id(
            resultado_formulario
        )
        tema_guardado, resultado = cargar_proyecto(
            proyecto_id
        )
        directorio = obtener_directorio_proyecto(
            proyecto_id
        )
        estado = cargar_estado_voz(directorio)

        if (
            not estado
            or estado.get("estado") != "excede_limite"
        ):
            raise ValueError(
                "El guion solo puede ajustarse cuando la voz "
                "supera 92 segundos."
            )

        duracion = estado.get("duracion_segundos")

        if not isinstance(duracion, (int, float)):
            raise ValueError(
                "La duración de la voz no es válida."
            )

        guion_ajustado = await run_in_threadpool(
            ajustar_guion_a_duracion,
            str(resultado.get("guion", "")),
            float(duracion)
        )
        resultado["guion"] = guion_ajustado
        archivar_voz_anterior(directorio)
        guardar_proyecto(
            proyecto_id,
            tema_guardado,
            resultado
        )
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Los datos del Pergamino no son válidos."
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo ajustar el guion automáticamente: "
                f"{error}"
            )
        ) from error

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema_guardado or tema,
            "resultado": resultado,
            "resultado_json": json.dumps(
                resultado,
                ensure_ascii=False
            ),
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": None,
            "voz_generando": False
        }
    )


@app.post(
    "/buscar-imagenes/{numero}",
    response_class=HTMLResponse
)
async def buscar_fotografias(
    numero: int,
    request: Request,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail="El número de imagen debe estar entre 1 y 8."
        )

    resultado = json.loads(resultado_json)
    plan_visual = resultado.get("plan_visual", [])

    if (
        not isinstance(plan_visual, list)
        or len(plan_visual) < numero
    ):
        raise HTTPException(
            status_code=400,
            detail=f"No existe la imagen {numero} en el plan visual."
        )

    escena = plan_visual[numero - 1]

    if not requiere_fotografia_real(escena):
        raise HTTPException(
            status_code=400,
            detail=(
                "Esta escena está indicada como recreación mediante IA, "
                "no como fotografía real."
            )
        )

    try:
        proyecto_id = obtener_proyecto_id(resultado)
        exigir_voz_aprobada(
            proyecto_id,
            resultado
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    consultas = obtener_consultas_busqueda(
        escena,
        tema
    )

    try:
        resultados_busqueda = await run_in_threadpool(
            buscar_imagenes_reales,
            consultas,
            6
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo completar la búsqueda de fotografías: "
                f"{error}"
            )
        ) from error

    guardar_candidatas(
        proyecto_id,
        numero,
        resultados_busqueda
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


@app.post(
    "/seleccionar-fotografia/{numero}",
    response_class=HTMLResponse
)
async def seleccionar_fotografia(
    numero: int,
    request: Request,
    indice_candidata: int = Form(...),
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail="El número de imagen debe estar entre 1 y 8."
        )

    try:
        resultado = json.loads(resultado_json)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Los datos del Pergamino no son válidos."
        ) from error

    plan_visual = resultado.get("plan_visual", [])

    if (
        not isinstance(plan_visual, list)
        or len(plan_visual) < numero
    ):
        raise HTTPException(
            status_code=400,
            detail=f"No existe la imagen {numero} en el plan visual."
        )

    if not requiere_fotografia_real(
        plan_visual[numero - 1]
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Esta escena no está configurada para usar "
                "una fotografía real."
            )
        )

    try:
        proyecto_id = obtener_proyecto_id(resultado)
        exigir_voz_aprobada(
            proyecto_id,
            resultado
        )
        candidatas = cargar_candidatas_imagen(
            proyecto_id,
            numero
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    if (
        indice_candidata < 0
        or indice_candidata >= len(candidatas)
    ):
        raise HTTPException(
            status_code=400,
            detail="La fotografía candidata seleccionada no existe."
        )

    candidata = candidatas[indice_candidata]

    if not isinstance(candidata, dict):
        raise HTTPException(
            status_code=400,
            detail="La fotografía candidata no tiene un formato válido."
        )

    try:
        url_descargada, formato = await run_in_threadpool(
            guardar_fotografia_seleccionada,
            proyecto_id,
            numero,
            candidata
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error)
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="No se pudo guardar la fotografía seleccionada."
        ) from error

    guardar_seleccion(
        proyecto_id,
        numero,
        indice_candidata,
        candidata,
        url_descargada,
        formato
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": None,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


@app.post(
    "/generar-imagen/{numero}",
    response_class=HTMLResponse
)
async def iniciar_generacion_imagen(
    numero: int,
    request: Request,
    background_tasks: BackgroundTasks,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail="El número de imagen debe estar entre 1 y 8."
        )

    resultado = json.loads(resultado_json)
    plan_visual = resultado.get("plan_visual", [])

    if (
        not isinstance(plan_visual, list)
        or len(plan_visual) < numero
    ):
        raise HTTPException(
            status_code=400,
            detail=f"No existe la imagen {numero} en el plan visual."
        )

    try:
        proyecto_id = obtener_proyecto_id(resultado)
        exigir_voz_aprobada(
            proyecto_id,
            resultado
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    escena = plan_visual[numero - 1]
    permitir_recreacion_ia = False

    if requiere_fotografia_real(escena):
        try:
            candidatas = cargar_candidatas_imagen(
                proyecto_id,
                numero
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Antes de generar una recreación IA deben buscarse "
                    "y revisarse fotografías reales."
                )
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error)
            ) from error

        if not candidatas:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No hay fotografías candidatas revisadas para "
                    "autorizar una recreación IA."
                )
            )

        selecciones = obtener_selecciones_guardadas(
            proyecto_id
        )

        if numero in selecciones:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Esta escena ya tiene una fotografía real "
                    "seleccionada."
                )
            )

        permitir_recreacion_ia = True

    ruta = obtener_ruta_imagen(
        proyecto_id,
        numero
    )

    if os.path.exists(ruta):
        os.remove(ruta)

    background_tasks.add_task(
        crear_imagen,
        resultado,
        numero,
        proyecto_id,
        permitir_recreacion_ia
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": obtener_imagenes_generadas(
                proyecto_id
            ),
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": numero,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


@app.post(
    "/comprobar-imagen/{numero}",
    response_class=HTMLResponse
)
async def comprobar_imagen(
    numero: int,
    request: Request,
    resultado_json: str = Form(...),
    tema: str = Form("")
):
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail="El número de imagen debe estar entre 1 y 8."
        )

    resultado = json.loads(resultado_json)

    try:
        proyecto_id = obtener_proyecto_id(resultado)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    imagenes = obtener_imagenes_generadas(proyecto_id)

    if numero in imagenes:
        imagen_generando = None
    else:
        imagen_generando = numero

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": imagenes,
            "candidatas": obtener_candidatas_guardadas(
                proyecto_id
            ),
            "selecciones": obtener_selecciones_guardadas(
                proyecto_id
            ),
            "imagen_generando": imagen_generando,
            "voz": obtener_estado_voz_interfaz(
                proyecto_id
            ),
            "voz_generando": False
        }
    )


def redirigir_produccion(proyecto_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/produccion/{proyecto_id}",
        status_code=303,
    )


def cargar_contexto_produccion(proyecto_id: str) -> dict:
    tema, resultado = cargar_proyecto(proyecto_id)
    directorio = obtener_directorio_proyecto(proyecto_id)
    recuperar_montaje_interrumpido(directorio)
    resumen = obtener_resumen_produccion(directorio)
    marca_tiempo = int(time.time())

    return {
        "proyecto_id": proyecto_id,
        "tema": tema,
        "resultado": resultado,
        "produccion": resumen,
        "voz": obtener_estado_voz_interfaz(proyecto_id),
        "musica_url": (
            f"/proyectos/{proyecto_id}/{resumen['musica_url']}?v={marca_tiempo}"
            if resumen.get("musica_url")
            else None
        ),
        "borrador_url": (
            f"/proyectos/{proyecto_id}/video_borrador.mp4?v={marca_tiempo}"
            if resumen.get("borrador_disponible")
            else None
        ),
        "final_url": (
            f"/proyectos/{proyecto_id}/video_final.mp4?v={marca_tiempo}"
            if resumen.get("final_disponible")
            else None
        ),
        "paquete_url": (
            f"/proyectos/{proyecto_id}/proyecto_completo.zip?v={marca_tiempo}"
            if resumen.get("paquete_disponible")
            else None
        ),
    }


@app.get(
    "/produccion/{proyecto_id}",
    response_class=HTMLResponse,
)
async def abrir_produccion(
    proyecto_id: str,
    request: Request,
):
    try:
        contexto = cargar_contexto_produccion(proyecto_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    contexto["request"] = request
    return templates.TemplateResponse(
        request=request,
        name="produccion.html",
        context=contexto,
    )


@app.post("/produccion/{proyecto_id}/sincronizacion")
async def preparar_sincronizacion_proyecto(
    proyecto_id: str,
):
    try:
        _, resultado = cargar_proyecto(proyecto_id)
        directorio = obtener_directorio_proyecto(proyecto_id)
        exigir_voz_aprobada(proyecto_id, resultado)
        obtener_imagenes_produccion(directorio)

        if not obtener_resumen_produccion(directorio)["imagenes_aprobadas"]:
            raise ValueError("Primero deben confirmarse las ocho imágenes.")

        preparar_sincronizacion(
            directorio,
            str(resultado.get("guion", "")),
            resultado.get("plan_visual", []),
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/aprobar-imagenes")
async def aprobar_imagenes_proyecto(proyecto_id: str):
    try:
        directorio = obtener_directorio_proyecto(proyecto_id)
        aprobar_imagenes(directorio)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/aprobar-sincronizacion")
async def aprobar_sincronizacion_proyecto(proyecto_id: str):
    try:
        directorio = obtener_directorio_proyecto(proyecto_id)
        aprobar_sincronizacion(directorio)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/musica")
async def cargar_musica_proyecto(
    proyecto_id: str,
    musica: UploadFile = File(...),
):
    try:
        directorio = obtener_directorio_proyecto(proyecto_id)

        if not os.path.isdir(directorio):
            raise FileNotFoundError("La carpeta del proyecto no existe.")

        contenido = await musica.read(MAXIMO_BYTES_MUSICA + 1)
        guardar_musica(
            directorio,
            musica.filename or "musica.mp3",
            contenido,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        await musica.close()

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/aprobar-musica")
async def aprobar_musica_proyecto(proyecto_id: str):
    try:
        directorio = obtener_directorio_proyecto(proyecto_id)
        aprobar_musica(directorio)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/verificar-preparacion")
async def verificar_preparacion_proyecto(proyecto_id: str):
    try:
        _, resultado = cargar_proyecto(proyecto_id)
        directorio = obtener_directorio_proyecto(proyecto_id)
        exigir_voz_aprobada(proyecto_id, resultado)
        verificacion = verificar_preparacion_montaje(
            directorio,
            voz_aprobada=True,
        )

        if not verificacion["preparado"]:
            guardar_estado_produccion(
                directorio,
                "preparacion_bloqueada",
                error="Control previo incompleto: "
                + " ".join(verificacion["bloqueos"]),
            )
        else:
            guardar_estado_produccion(
                directorio,
                "preparado_para_montaje",
                error="",
            )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/generar-borrador")
async def iniciar_borrador_proyecto(
    proyecto_id: str,
    background_tasks: BackgroundTasks,
):
    try:
        _, resultado = cargar_proyecto(proyecto_id)
        directorio = obtener_directorio_proyecto(proyecto_id)
        exigir_voz_aprobada(proyecto_id, resultado)
        obtener_imagenes_produccion(directorio)
        estado = cargar_estado_produccion(directorio)

        resumen = obtener_resumen_produccion(directorio)

        if not resumen["imagenes_aprobadas"]:
            raise ValueError("Las ocho imágenes deben estar aprobadas.")

        if len(resumen["sincronizacion"]) != 8:
            raise ValueError("Primero debe prepararse la sincronización.")

        if not estado.get("sincronizacion_aprobada"):
            raise ValueError("La sincronización debe revisarse y aprobarse.")

        if not estado.get("musica_aprobada"):
            raise ValueError("La música debe cargarse, escucharse y aprobarse.")

        verificacion = verificar_preparacion_montaje(
            directorio,
            voz_aprobada=True,
        )

        if not verificacion["preparado"]:
            raise ValueError(
                "El control previo ha bloqueado el montaje: "
                + " ".join(verificacion["bloqueos"])
            )

        iniciar_generacion_borrador(directorio)
        background_tasks.add_task(
            generar_borrador_seguro,
            directorio,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/aprobar-borrador")
async def aprobar_borrador_proyecto(proyecto_id: str):
    try:
        directorio = obtener_directorio_proyecto(proyecto_id)
        aprobar_borrador(directorio)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


@app.post("/produccion/{proyecto_id}/crear-paquete")
async def crear_paquete_proyecto(proyecto_id: str):
    try:
        _, resultado = cargar_proyecto(proyecto_id)
        directorio = obtener_directorio_proyecto(proyecto_id)
        crear_paquete(directorio, resultado)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return redirigir_produccion(proyecto_id)


def obtener_url_publica(request: Request) -> str:
    """Obtiene la base pública para que Instagram descargue el vídeo.

    Cuando el panel se abre a través de un túnel, las cabeceras reenviadas
    contienen la dirección pública. INSTAGRAM_PUBLIC_BASE_URL permite fijar
    esa dirección de forma explícita, de modo que publicar siga siendo
    correcto aunque el usuario abra el panel desde localhost.
    """
    base_configurada = os.getenv(
        "INSTAGRAM_PUBLIC_BASE_URL",
        "",
    ).strip().rstrip("/")
    if base_configurada:
        try:
            datos_configurados = urlparse(base_configurada)
            hostname_configurado = datos_configurados.hostname
            puerto_configurado = datos_configurados.port
        except ValueError as error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "INSTAGRAM_PUBLIC_BASE_URL no contiene una URL válida."
                ),
            ) from error

        if (
            datos_configurados.scheme != "https"
            or not datos_configurados.netloc
            or not hostname_configurado
            or puerto_configurado is not None
            or datos_configurados.path not in {"", "/"}
            or datos_configurados.params
            or datos_configurados.query
            or datos_configurados.fragment
            or datos_configurados.username
            or datos_configurados.password
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "INSTAGRAM_PUBLIC_BASE_URL debe ser el origen HTTPS "
                    "público, sin ruta ni credenciales."
                ),
            )

        return datos_configurados.geturl().rstrip("/")

    protocolo = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
    ).split(",", 1)[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    ).split(",", 1)[0].strip()

    if not host:
        raise HTTPException(
            status_code=400,
            detail="No se pudo determinar el host público de la aplicación.",
        )

    return f"{protocolo}://{host}".rstrip("/")

def obtener_datos_publicacion_instagram(
    resultado: dict,
) -> tuple[str, str]:
    """Convierte la publicación aprobada en caption y comentario."""
    publicacion = resultado.get("publicacion", {})
    if not isinstance(publicacion, dict):
        return "", ""

    partes = [
        str(publicacion.get("titulo", "")).strip(),
        str(publicacion.get("descripcion", "")).strip(),
    ]
    hashtags = publicacion.get("hashtags", [])
    if isinstance(hashtags, list):
        texto_hashtags = " ".join(
            str(hashtag).strip()
            for hashtag in hashtags
            if str(hashtag).strip()
        )
        if texto_hashtags:
            partes.append(texto_hashtags)

    caption = "\n\n".join(parte for parte in partes if parte)
    comentario = str(
        publicacion.get("comentario_fijado", "")
    ).strip()
    return caption, comentario


def obtener_proyectos_con_video_final() -> list[dict]:
    """Devuelve los proyectos locales que pueden demostrarse/publicarse."""
    proyectos = []
    if not os.path.isdir(DIRECTORIO_PROYECTOS):
        return proyectos

    for nombre in sorted(os.listdir(DIRECTORIO_PROYECTOS)):
        try:
            proyecto_id = validar_proyecto_id(nombre)
        except ValueError:
            continue

        directorio = obtener_directorio_proyecto(proyecto_id)
        video = os.path.join(directorio, "video_final.mp4")
        if not os.path.isfile(video):
            continue

        tema = proyecto_id
        caption = ""
        comentario_fijado = ""
        try:
            tema, resultado = cargar_proyecto(proyecto_id)
            caption, comentario_fijado = obtener_datos_publicacion_instagram(
                resultado
            )
        except (FileNotFoundError, ValueError):
            pass

        proyectos.append(
            {
                "proyecto_id": proyecto_id,
                "tema": tema,
                "caption": caption,
                "comentario_fijado": comentario_fijado,
            }
        )

    return proyectos


def exigir_clave_publicacion(
    request: Request,
    datos: dict,
) -> None:
    """Evita que un túnel público pueda publicar sin autorización local."""
    configurada = os.getenv("INSTAGRAM_PUBLISH_KEY", "").strip()
    if not configurada:
        raise HTTPException(
            status_code=503,
            detail=(
                "Falta configurar INSTAGRAM_PUBLISH_KEY en el entorno local."
            ),
        )

    proporcionada = str(
        datos.get("publish_key")
        or request.headers.get("x-pergamino-publish-key")
        or ""
    ).strip()

    if (
        not proporcionada
        or not secrets.compare_digest(proporcionada, configurada)
    ):
        raise HTTPException(
            status_code=403,
            detail="La clave local de publicación no es válida.",
        )


def renderizar_pagina_instagram(request: Request) -> str:
    estado = estado_cuenta()
    proyectos = obtener_proyectos_con_video_final()
    partes = [
        "<!doctype html>",
        '<html lang="es"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Instagram · El Pergamino Perdido</title>",
        (
            "<style>"
            "body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;"
            "padding:0 18px;background:#f5f1e8;color:#222}"
            "main{background:#fff;padding:28px;border-radius:14px;"
            "box-shadow:0 2px 14px #0002}"
            "label{display:block;margin:18px 0 6px;font-weight:700}"
            "select,textarea,input{box-sizing:border-box;width:100%;padding:11px;"
            "font:inherit;border:1px solid #bbb;border-radius:7px}"
            "textarea{min-height:120px;resize:vertical}"
            "button,a.boton{display:inline-block;margin-top:18px;padding:12px 18px;"
            "border:0;border-radius:7px;background:#1769aa;color:white;"
            "font-weight:700;text-decoration:none;cursor:pointer}"
            "button:disabled{opacity:.6;cursor:wait}"
            ".estado{padding:12px;border-radius:8px;background:#eef8ef;"
            "border:1px solid #79b984}"
            ".aviso{padding:12px;border-radius:8px;background:#fff7df;"
            "border:1px solid #d5aa35}"
            "pre{white-space:pre-wrap;background:#f2f2f2;padding:12px;"
            "border-radius:8px}"
            "</style>"
        ),
        "</head><body><main>",
        "<h1>📜 Publicar en Instagram</h1>",
        (
            "<p>El botón publica el vídeo final aprobado y deja automáticamente "
            "el comentario editorial del proyecto.</p>"
        ),
    ]

    if not estado.get("connected"):
        partes.extend(
            [
                '<p class="aviso">No hay ninguna cuenta conectada.</p>',
                '<a class="boton" href="/meta/instagram/login">'
                "Conectar cuenta de Instagram</a>",
            ]
        )
    else:
        username = escape_html(
            str(estado.get("username") or estado.get("name") or "cuenta")
        )
        partes.append(
            '<p class="estado">Conectada: @' + username + "</p>"
        )

        opciones = ['<option value="">Selecciona un Reel final</option>']
        for proyecto in proyectos:
            proyecto_id = escape_html(
                str(proyecto["proyecto_id"]),
                quote=True,
            )
            tema = escape_html(str(proyecto["tema"]))
            caption = escape_html(
                str(proyecto.get("caption", "")),
                quote=True,
            )
            comentario_fijado = escape_html(
                str(proyecto.get("comentario_fijado", "")),
                quote=True,
            )
            opciones.append(
                '<option value="' + proyecto_id + '" '
                'data-caption="' + caption + '" '
                'data-comentario-fijado="' + comentario_fijado + '">'
                + tema
                + "</option>"
            )

        partes.extend(
            [
                '<form id="form-instagram">',
                '<label for="proyecto_id">Vídeo final aprobado</label>',
                '<select id="proyecto_id" required>',
                "".join(opciones),
                "</select>",
                '<label for="caption">Texto de publicación</label>',
                '<textarea id="caption" maxlength="2200" required>'
                "</textarea>",
                '<label for="comentario_fijado">Comentario automático</label>',
                '<textarea id="comentario_fijado" maxlength="2200" '
                'readonly required></textarea>',
                '<p class="aviso">El comentario se publica automáticamente. '
                "La API oficial de Instagram no ofrece la operación de fijarlo; "
                "por eso la pantalla nunca lo marcará como fijado.</p>",
                '<label for="publish_key">Clave local de publicación</label>',
                '<input id="publish_key" type="password" autocomplete="off" '
                'required>',
                '<button id="publicar" type="submit">Publicar Reel</button>',
                "</form>",
                '<pre id="resultado" hidden></pre>',
                "<script>",
                "(function(){",
                'const form=document.getElementById("form-instagram");',
                'const boton=document.getElementById("publicar");',
                'const salida=document.getElementById("resultado");',
                'const proyectos=document.getElementById("proyecto_id");',
                'const caption=document.getElementById("caption");',
                'const comentario=document.getElementById("comentario_fijado");',
                'function cargarTextoAprobado(){',
                'const opcion=proyectos.options[proyectos.selectedIndex];',
                'caption.value=opcion?.dataset.caption||"";',
                'comentario.value=opcion?.dataset.comentarioFijado||"";',
                '}',
                'proyectos.addEventListener("change",cargarTextoAprobado);',
                'form.addEventListener("submit",async function(event){',
                "event.preventDefault();boton.disabled=true;",
                'salida.hidden=false;salida.textContent="Publicando...";',
                "try{",
                'const respuesta=await fetch("/meta/instagram/publish",{',
                'method:"POST",headers:{"Content-Type":"application/json"},',
                "body:JSON.stringify({",
                'proyecto_id:proyectos.value,',
                'caption:caption.value,',
                'comentario_fijado:comentario.value,',
                'media_type:"REELS",',
                'publish_key:document.getElementById("publish_key").value',
                "})});",
                "const datos=await respuesta.json();",
                "if(!respuesta.ok)throw new Error(datos.detail||"
                '"No se pudo publicar.");',
                'let mensaje="Reel publicado correctamente. ID: "+datos.media_id;',
                'if(datos.comentario?.success){',
                'mensaje+="\\nComentario publicado y verificado.";',
                'if(datos.comentario.comment_pinned===false)',
                'mensaje+="\\nEl comentario no está fijado: Instagram no permite fijarlo desde su API oficial.";',
                '}else if(datos.comentario?.error){',
                'mensaje+="\\nEl Reel sí está publicado, pero el comentario no pudo publicarse: "+datos.comentario.error;',
                '}else if(datos.comentario?.skipped){',
                'mensaje+="\\nNo había comentario editorial configurado.";',
                '}',
                'salida.textContent=mensaje;',
                "}catch(error){salida.textContent=error.message;}",
                "finally{boton.disabled=false;}",
                "});cargarTextoAprobado();})();",
                "</script>",
            ]
        )

        if not proyectos:
            partes.append(
                '<p class="aviso">No hay ningún vídeo final en el servidor local. '
                "Primero genera y aprueba un proyecto.</p>"
            )

    partes.extend(
        [
            '<p><a href="/">Volver al Centro de Producción</a></p>',
            "</main></body></html>",
        ]
    )
    return "".join(partes)


@app.get("/meta/instagram", response_class=HTMLResponse)
async def pagina_instagram(request: Request):
    return HTMLResponse(renderizar_pagina_instagram(request))


@app.get("/meta/instagram/login")
@app.get("/meta/instagram/connect")
async def iniciar_conexion_instagram():
    try:
        return RedirectResponse(
            url=iniciar_oauth(),
            status_code=307,
        )
    except InstagramError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/meta/instagram/callback", response_class=HTMLResponse)
async def meta_instagram_callback(request: Request):
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    if error:
        detalle = escape_html(
            error_description or error,
            quote=False,
        )
        return HTMLResponse(
            "<h1>Conexión de Instagram no completada</h1>"
            "<p>" + detalle + "</p>",
            status_code=400,
        )

    try:
        cuenta = completar_oauth(code, state)
    except InstagramError as exc:
        return HTMLResponse(
            "<h1>No se pudo conectar Instagram</h1>"
            "<p>" + escape_html(str(exc), quote=False) + "</p>",
            status_code=400,
        )

    nombre = escape_html(
        str(cuenta.get("username") or cuenta.get("name") or "cuenta"),
        quote=False,
    )
    return HTMLResponse(
        "<h1>Instagram conectado</h1>"
        "<p>Cuenta conectada: @" + nombre + "</p>"
        '<p><a href="/meta/instagram">Ir a publicar un Reel</a></p>'
    )


@app.api_route(
    "/meta/instagram/deauthorize",
    methods=["GET", "POST"],
)
async def desautorizar_instagram():
    """Punto de retorno que Meta usa al retirar la autorización."""
    return {"success": True}


@app.api_route(
    "/meta/instagram/data-deletion",
    methods=["GET", "POST"],
)
async def solicitar_eliminacion_datos_instagram(request: Request):
    """Devuelve la respuesta estándar para una solicitud de eliminación."""
    redirect_uri = os.getenv("INSTAGRAM_REDIRECT_URI", "").strip()
    datos_redirect = urlparse(redirect_uri)
    if datos_redirect.scheme and datos_redirect.netloc:
        base_publica = (
            f"{datos_redirect.scheme}://{datos_redirect.netloc}"
        )
    else:
        base_publica = str(request.base_url).rstrip("/")

    codigo = secrets.token_urlsafe(18)
    return {
        "url": (
            f"{base_publica}/meta/instagram/data-deletion/status"
            f"?code={quote(codigo)}"
        ),
        "confirmation_code": codigo,
    }


@app.get(
    "/meta/instagram/data-deletion/status",
    response_class=HTMLResponse,
)
async def estado_eliminacion_datos_instagram():
    return HTMLResponse(
        "<h1>Solicitud de eliminación recibida</h1>"
        "<p>La solicitud de eliminación de datos ha sido registrada.</p>"
    )


@app.get("/meta/instagram/status")
async def estado_instagram():
    return estado_cuenta()


@app.post("/meta/instagram/publish")
async def publicar_en_instagram(request: Request):
    try:
        datos = await request.json()
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="El cuerpo de la petición no es JSON válido.",
        ) from error

    if not isinstance(datos, dict):
        raise HTTPException(
            status_code=400,
            detail="El cuerpo de la petición debe ser un objeto JSON.",
        )

    exigir_clave_publicacion(request, datos)

    tipo = str(datos.get("media_type") or "REELS").upper().strip()
    media_url = str(datos.get("media_url") or "").strip()
    proyecto_id = str(datos.get("proyecto_id") or "").strip()
    caption = str(datos.get("caption") or "").strip()
    comentario_fijado = str(
        datos.get("comentario_fijado") or ""
    ).strip()

    if proyecto_id:
        if tipo != "REELS":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Los proyectos del Centro de Producción se publican "
                    "como REELS."
                ),
            )

        try:
            proyecto_id = validar_proyecto_id(proyecto_id)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

        ruta_video = os.path.join(
            obtener_directorio_proyecto(proyecto_id),
            "video_final.mp4",
        )
        if not os.path.isfile(ruta_video):
            raise HTTPException(
                status_code=400,
                detail=(
                    "El proyecto no tiene un vídeo final aprobado disponible."
                ),
            )

        media_url = (
            obtener_url_publica(request)
            + "/proyectos/"
            + quote(proyecto_id, safe="")
            + "/video_final.mp4"
        )

        if not caption or not comentario_fijado:
            try:
                _, resultado_proyecto = cargar_proyecto(proyecto_id)
                caption_defecto, comentario_defecto = (
                    obtener_datos_publicacion_instagram(resultado_proyecto)
                )
                if not caption:
                    caption = caption_defecto
                if not comentario_fijado:
                    comentario_fijado = comentario_defecto
            except (FileNotFoundError, ValueError):
                # El vídeo se puede publicar aunque el caption/comentario se
                # haya proporcionado explícitamente en la petición.
                pass

    if not media_url:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar proyecto_id o media_url.",
        )

    try:
        resultado = await run_in_threadpool(
            publicar_media,
            media_url,
            caption,
            tipo,
            bool(datos.get("share_to_feed", True)),
        )
    except InstagramError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if comentario_fijado:
        try:
            resultado["comentario"] = await run_in_threadpool(
                publicar_comentario,
                str(resultado["media_id"]),
                comentario_fijado,
            )
        except InstagramError as error:
            # El Reel ya existe: se devuelve su ID para poder reintentar solo
            # el comentario sin duplicar la publicación.
            resultado["comentario"] = {
                "success": False,
                "error": str(error),
                "media_id": resultado.get("media_id"),
            }
    else:
        resultado["comentario"] = {
            "success": False,
            "skipped": True,
            "error": "No hay comentario editorial configurado.",
        }

    return resultado


@app.post("/meta/instagram/comment")
async def publicar_comentario_en_instagram(request: Request):
    """Permite completar el comentario de un Reel ya publicado."""
    try:
        datos = await request.json()
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="El cuerpo de la petición no es JSON válido.",
        ) from error

    if not isinstance(datos, dict):
        raise HTTPException(
            status_code=400,
            detail="El cuerpo de la petición debe ser un objeto JSON.",
        )

    exigir_clave_publicacion(request, datos)
    media_id = str(datos.get("media_id") or "").strip()
    message = str(
        datos.get("message") or datos.get("comentario_fijado") or ""
    ).strip()
    if not media_id or not message:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar media_id y message.",
        )

    try:
        return await run_in_threadpool(
            publicar_comentario,
            media_id,
            message,
        )
    except InstagramError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
