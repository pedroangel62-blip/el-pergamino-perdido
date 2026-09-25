"""Prueba de Play, descarga y error visible con Chrome y un MP4 sintético.

Se ejecuta aparte de unittest. Solo usa datos temporales y no llama a APIs de IA.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

import uvicorn

from backend import main, produccion
from tests.test_app import AplicacionTests


def verificar():
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Chrome no está instalado; la prueba no puede darse por superada.")
    browser = os.environ["PERGAMINO_BROWSER_BIN"]
    caso = AplicacionTests()
    caso.setUp()
    caso.crear_proyecto()
    proyecto = Path(caso.directorio_temporal.name) / "pergamino-prueba"
    video = proyecto / "video_borrador.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=180x320:rate=30", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-t", "5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(video),
    ], check=True)
    produccion.guardar_estado(str(proyecto), "borrador_pendiente_aprobacion")
    with socket.socket() as escucha:
        escucha.bind(("127.0.0.1", 0))
        puerto = escucha.getsockname()[1]
    origen = f"http://127.0.0.1:{puerto}"
    servidor = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=puerto, log_level="error"))
    hilo = threading.Thread(target=servidor.run, daemon=True)

    def comando(*args):
        resultado = subprocess.run([
            browser, "--session", "pergamino-ci", "--executable-path", chrome,
            "--args=--autoplay-policy=no-user-gesture-required", *args,
        ], capture_output=True, text=True, timeout=40)
        if resultado.returncode:
            raise RuntimeError(resultado.stdout + resultado.stderr)
        return resultado.stdout

    try:
        hilo.start()
        limite = time.monotonic() + 10
        while not servidor.started:
            if time.monotonic() > limite:
                raise RuntimeError("El servidor de prueba no arrancó.")
            time.sleep(0.05)
        comando("open", origen + "/produccion/pergamino-prueba")
        comando("wait", "--load", "networkidle")
        assert "Descargar" in comando("snapshot", "-i")
        resultado = comando("eval", """(async () => {
            const v = document.getElementById('video-borrador');
            if (!v || !Number.isFinite(v.duration) || v.duration <= 0) throw Error('Sin vídeo válido');
            await v.play();
            await new Promise(r => setTimeout(r, 500));
            if (v.paused || v.currentTime <= 0) throw Error('Play no avanza');
            v.currentTime = 3;
            await new Promise(r => setTimeout(r, 400));
            if (v.currentTime < 3) throw Error('No se puede avanzar en el vídeo');
            v.pause();
            return {play: true, seek: true, duracion: v.duration};
        })()""")
        print("Reproducción real:", resultado)
        with tempfile.TemporaryDirectory() as descargas:
            destino = Path(descargas) / "video.mp4"
            comando("download", "#descargar-video-borrador", str(destino))
            assert hashlib.sha256(destino.read_bytes()).digest() == hashlib.sha256(video.read_bytes()).digest()
            print("Descarga del navegador: hash idéntico al MP4 original")
            with patch.dict(os.environ, {"USERPROFILE": descargas}):
                comando("click", "#copiar-video-descargas button")
                comando("wait", "--fn", "document.getElementById('copiar-video-estado').textContent.includes('Vídeo copiado')")
                assert (Path(descargas) / "Downloads" / "video_borrador.mp4").read_bytes() == video.read_bytes()
            print("Copia desde la interfaz: bytes completos")
        errores = json.loads(comando("errors", "--json"))
        assert not errores["data"]["errors"], "Chrome registró errores de JavaScript"
        print("Navegador sin errores de JavaScript")

        apertura_original = open
        def denegar(ruta, *args, **kwargs):
            if os.path.abspath(ruta) == str(video):
                raise PermissionError(errno.EACCES, "Permission denied", str(ruta))
            return apertura_original(ruta, *args, **kwargs)
        # La denegación nativa se prueba en Windows; aquí se prueba su mensaje en Chrome.
        with patch.object(main, "open", side_effect=denegar, create=True):
            comando("reload")
            comando("wait", "--fn", "document.getElementById('video-borrador-estado').textContent.includes('MP4 de origen')")
            mensaje = comando("get", "text", "#video-borrador-estado")
            assert "Windows no permite leer" in mensaje
            assert "Vídeo servido directamente" not in comando("get", "text", "body")
            print("Denegación de lectura: mensaje visible sin anunciar vídeo listo")
    finally:
        try:
            comando("close")
        finally:
            servidor.should_exit = True
            hilo.join(timeout=10)
            caso.tearDown()


if __name__ == "__main__":
    verificar()
