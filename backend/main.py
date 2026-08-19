from datetime import datetime
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
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
    obtener_imagenes as obtener_imagenes_produccion,
    obtener_resumen as obtener_resumen_produccion,
    preparar_sincronizacion,
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


TOTAL_IMAGENES = 8
DIRECTORIO_PROYECTOS = "backend/proyectos"
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
    return construir_contexto_indice(
        DIRECTORIO_PROYECTOS
    )


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

    if not voz_esta_aprobada(
        obtener_directorio_proyecto(proyecto_id),
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

        if estado.get("estado") == "generando_borrador":
            raise ValueError("El vídeo borrador ya se está generando.")

        guardar_estado_produccion(
            directorio,
            "generando_borrador",
            error="",
            borrador_aprobado=False,
        )
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
