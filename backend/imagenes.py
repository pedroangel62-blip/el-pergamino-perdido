import base64
import os

from openai import OpenAI


def obtener_cliente() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("Falta el secreto OPENAI_API_KEY.")

    return OpenAI(api_key=api_key)


def generar_imagen(
    prompt: str,
    numero: int,
    directorio_salida: str = "backend/imagenes"
) -> str:
    if not prompt.strip():
        raise ValueError("El prompt está vacío.")

    if numero < 1 or numero > 8:
        raise ValueError(
            "El número de imagen debe estar entre 1 y 8."
        )

    if not isinstance(directorio_salida, str) or not directorio_salida:
        raise ValueError(
            "La carpeta de salida de la imagen no es válida."
        )

    resultado = obtener_cliente().images.generate(
        model="gpt-image-2",
        prompt=prompt,
        size="1024x1536",
        quality="high",
    )

    imagen_bytes = base64.b64decode(
        resultado.data[0].b64_json
    )

    os.makedirs(
        directorio_salida,
        exist_ok=True
    )

    ruta = os.path.join(
        directorio_salida,
        f"imagen{numero}.png"
    )

    with open(ruta, "wb") as archivo:
        archivo.write(imagen_bytes)

    return ruta
