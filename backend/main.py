from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

from backend.imagenes import generar_imagen

import json
import os
import time


TOTAL_IMAGENES = 8
DIRECTORIO_IMAGENES = "backend/imagenes"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

with open("backend/manual/manual_maestro.txt", "r", encoding="utf-8") as f:
    manual_maestro = f.read()

with open("backend/manual/plantilla_generacion.txt", "r", encoding="utf-8") as f:
    plantilla_generacion = f.read()

app = FastAPI()

app.mount(
    "/imagenes",
    StaticFiles(directory=DIRECTORIO_IMAGENES),
    name="imagenes"
)

templates = Jinja2Templates(directory="backend/templates")


def obtener_ruta_imagen(numero: int) -> str:
    return os.path.join(
        DIRECTORIO_IMAGENES,
        f"imagen{numero}.png"
    )


def obtener_imagenes_generadas() -> dict:
    marca_tiempo = int(time.time())
    imagenes = {}

    for numero in range(1, TOTAL_IMAGENES + 1):
        if os.path.exists(obtener_ruta_imagen(numero)):
            imagenes[numero] = (
                f"/imagenes/imagen{numero}.png?v={marca_tiempo}"
            )

    return imagenes


def eliminar_imagenes_anteriores() -> None:
    for numero in range(1, TOTAL_IMAGENES + 1):
        ruta = obtener_ruta_imagen(numero)

        if os.path.exists(ruta):
            os.remove(ruta)


def crear_prompt_imagen(resultado: dict, numero: int) -> str:
    if numero < 1 or numero > TOTAL_IMAGENES:
        raise ValueError("El número de imagen debe estar entre 1 y 8.")

    plan_visual = resultado.get("plan_visual", [])

    if len(plan_visual) < numero:
        raise ValueError(
            f"No existe la imagen {numero} en el plan visual."
        )

    escena = plan_visual[numero - 1]

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


def crear_imagen(resultado: dict, numero: int) -> None:
    prompt_final = crear_prompt_imagen(resultado, numero)
    generar_imagen(prompt_final, numero)


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
            "imagen_generando": None
        }
    )


@app.post("/generar", response_class=HTMLResponse)
async def generar(request: Request, tema: str = Form(...)):
    respuesta = client.responses.create(
        model="gpt-5.6-luna",
        input=f"""
{manual_maestro}

{plantilla_generacion}

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

    eliminar_imagenes_anteriores()

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
            "imagen_generando": None
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

    if len(plan_visual) < numero:
        raise HTTPException(
            status_code=400,
            detail=f"No existe la imagen {numero} en el plan visual."
        )

    ruta = obtener_ruta_imagen(numero)

    if os.path.exists(ruta):
        os.remove(ruta)

    background_tasks.add_task(
        crear_imagen,
        resultado,
        numero
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "tema": tema,
            "resultado": resultado,
            "resultado_json": resultado_json,
            "imagenes": obtener_imagenes_generadas(),
            "imagen_generando": numero
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
    imagenes = obtener_imagenes_generadas()

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
            "imagen_generando": imagen_generando
        }
    )