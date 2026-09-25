"""Regresiones de lectura denegada, transferencia y consulta del worker."""
import asyncio
import ctypes
import errno
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import main, produccion


class EntregaVideoTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.raiz = Path(self.temporal.name)
        self.proyecto = self.raiz / "pergamino-prueba"
        self.proyecto.mkdir()
        self.ruta = self.proyecto / "video_borrador.mp4"
        # Más de un bloque para comprobar que no se trunca la transferencia.
        self.contenido = bytes(range(256)) * 5000
        self.ruta.write_bytes(self.contenido)
        self.parche = patch.object(main, "DIRECTORIO_PROYECTOS", str(self.raiz))
        self.parche.start()
        self.addCleanup(self.parche.stop)
        self.cliente = TestClient(main.app)
        self.media = "/media/proyectos/pergamino-prueba/video_borrador.mp4"
        self.api = "/api/proyectos/pergamino-prueba/video_borrador"
        self.descarga = "/descargas/proyectos/pergamino-prueba/video_borrador"

    def test_transferencia_rangos_head_y_copia_preservan_los_bytes(self):
        for url in (self.media, self.descarga):
            with self.subTest(url=url):
                respuesta = self.cliente.get(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(hashlib.sha256(respuesta.content).digest(),
                                 hashlib.sha256(self.contenido).digest())
                head = self.cliente.head(url, headers={"Range": "bytes=0-3"})
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(int(head.headers["content-length"]), len(self.contenido))
                for rango, esperado in (
                    ("bytes=2-6", self.contenido[2:7]),
                    ("bytes=-7", self.contenido[-7:]),
                    (f"bytes={len(self.contenido)-5}-", self.contenido[-5:]),
                ):
                    parcial = self.cliente.get(url, headers={"Range": rango})
                    self.assertEqual(parcial.status_code, 206)
                    self.assertEqual(parcial.content, esperado)
                for rango in ("bytes=99999999-", "bytes=-0", "bytes=5-2", "bytes=a-b"):
                    invalido = self.cliente.get(url, headers={"Range": rango})
                    self.assertEqual(invalido.status_code, 416)
        with patch.dict(os.environ, {"USERPROFILE": str(self.raiz / "usuario")}):
            copia = self.cliente.post(self.api + "/copiar-descargas")
        self.assertEqual(copia.status_code, 200)
        self.assertEqual(Path(copia.json()["ruta"]).read_bytes(), self.contenido)

    def comprobar_denegacion(self):
        for metodo, url, cabeceras in (
            ("GET", self.media, {}),
            ("GET", self.media, {"Range": "bytes=0-7"}),
            ("HEAD", self.media, {}),
            ("GET", self.descarga, {}),
            ("GET", self.api + "/info", {}),
            ("GET", self.api + "/chunk", {}),
            ("POST", self.api + "/copiar-descargas", {}),
        ):
            with self.subTest(metodo=metodo, url=url):
                respuesta = self.cliente.request(metodo, url, headers=cabeceras)
                self.assertEqual(respuesta.status_code, 423)
                self.assertNotIn("content-disposition", respuesta.headers)
                self.assertNotIn("content-range", respuesta.headers)
                if metodo != "HEAD":
                    self.assertIn("MP4 de origen", respuesta.json()["detail"])

    def test_permiso_denegado_responde_antes_de_enviar_cabeceras_de_video(self):
        original = open
        def apertura(ruta, *args, **kwargs):
            if os.path.abspath(ruta) == str(self.ruta):
                raise PermissionError(errno.EACCES, "Permission denied", str(ruta))
            return original(ruta, *args, **kwargs)
        destino = self.raiz / "Downloads" / "video_borrador.mp4"
        destino.parent.mkdir()
        destino.write_bytes(b"copia anterior")
        with patch("builtins.open", side_effect=apertura), patch.dict(
            os.environ, {"USERPROFILE": str(self.raiz)}
        ):
            self.comprobar_denegacion()
        self.assertEqual(destino.read_bytes(), b"copia anterior")

    def test_copia_incompleta_no_reemplaza_la_anterior(self):
        destino = self.raiz / "Downloads" / "video_borrador.mp4"
        destino.parent.mkdir()
        destino.write_bytes(b"anterior")
        with patch.dict(os.environ, {"USERPROFILE": str(self.raiz)}), patch.object(
            main.shutil, "copyfileobj", side_effect=OSError("disco lleno")
        ):
            respuesta = self.cliente.post(self.api + "/copiar-descargas")
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(destino.read_bytes(), b"anterior")
        self.assertEqual(list(destino.parent.iterdir()), [destino])

    def test_video_vacio_no_se_anuncia_como_disponible(self):
        self.ruta.write_bytes(b"")
        self.assertEqual(self.cliente.get(self.api + "/info").status_code, 409)
        self.assertEqual(self.cliente.get(self.media).status_code, 409)

    def test_descriptor_se_cierra_si_falla_el_envio(self):
        async def ejecutar():
            archivo = io.BytesIO(b"video")
            respuesta = main.RespuestaVideoAbierto(archivo, iter([b"video"]))
            async def send(evento):
                raise OSError("cliente desconectado")
            async def receive():
                return {"type": "http.disconnect"}
            with self.assertRaises(Exception):
                await respuesta({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
            self.assertTrue(archivo.closed)
        asyncio.run(ejecutar())

    @unittest.skipUnless(os.name == "nt", "Requiere bloqueo nativo de Windows")
    def test_archivo_bloqueado_real_se_recupera_al_liberar_el_handle(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # GENERIC_READ, sin FILE_SHARE_READ: reproduce la denegación sin cambiar ACL.
        handle = kernel.CreateFileW(str(self.ruta), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.comprobar_denegacion()
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(self.cliente.get(self.media).content, self.contenido)

    def test_consultar_progreso_no_termina_un_proceso_real(self):
        opciones = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        with subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; print('listo'); time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **opciones,
        ) as proceso:
            try:
                self.assertEqual(proceso.stdout.readline().strip(), b"listo")
                produccion.guardar_estado(str(self.proyecto), "generando_borrador", montaje_porcentaje=42)
                produccion.guardar_json_atomico(
                    str(self.proyecto / produccion.ARCHIVO_MONTAJE_EN_CURSO),
                    {"pid": proceso.pid, "inicio_proceso": produccion._inicio_proceso(proceso.pid)},
                )
                for _ in range(8):
                    estado = self.cliente.get("/api/proyectos/pergamino-prueba/montaje")
                    self.assertEqual(estado.status_code, 200)
                    self.assertEqual(estado.json()["estado"], "generando_borrador")
                    self.assertEqual(estado.json()["porcentaje"], 42)
                    self.assertIsNone(proceso.poll(), "Consultar progreso terminó el proceso")
            finally:
                proceso.terminate()
                proceso.wait(timeout=10)
        self.assertTrue(produccion.recuperar_montaje_interrumpido(str(self.proyecto)))


if __name__ == "__main__":
    unittest.main()
