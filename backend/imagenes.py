import base64
import os
from openai import OpenAI


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generar_imagen(prompt: str, numero: int) -> str:
    if not prompt.strip():
        raise ValueError("El prompt está vacío.")

    if numero < 1 or numero > 8:
        raise ValueError(
            "El número de imagen debe estar entre 1 y 8."
        )

    resultado = client.images.generate(
        model="gpt-image-2",
        prompt=prompt,
        size="1024x1536",
        quality="high",
    )

    imagen_bytes = base64.b64decode(
        resultado.data[0].b64_json
    )

    os.makedirs(
        "backend/imagenes",
        exist_ok=True
    )

    nombre = f"imagen{numero}.png"

    ruta = os.path.join(
        "backend",
        "imagenes",
        nombre
    )

    with open(ruta, "wb") as archivo:
        archivo.write(imagen_bytes)

    return ruta