import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from backend import instagram


class RespuestaFalsa:
    def __init__(self, data: dict):
        self.data = json.dumps(data).encode("utf-8")

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class InstagramTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.directorio = Path(self.temporal.name)
        self.estado = self.directorio / "oauth_state.json"
        self.cuenta = self.directorio / "account.json"
        self.entorno = patch.dict(
            os.environ,
            {
                "INSTAGRAM_APP_ID": "1050579897592243",
                "INSTAGRAM_APP_SECRET": "secreto-de-prueba",
                "INSTAGRAM_GRAPH_API_VERSION": "v25.0",
                "INSTAGRAM_REDIRECT_URI": (
                    "https://example.trycloudflare.com/meta/instagram/callback"
                ),
                "INSTAGRAM_OAUTH_STATE_PATH": str(self.estado),
                "INSTAGRAM_ACCOUNT_PATH": str(self.cuenta),
                "INSTAGRAM_CONTAINER_TIMEOUT_SECONDS": "5",
                "INSTAGRAM_CONTAINER_POLL_SECONDS": "1",
            },
            clear=False,
        )
        self.entorno.start()

    def tearDown(self):
        self.entorno.stop()
        self.temporal.cleanup()

    def test_iniciar_oauth_guarda_estado_y_scope_correcto(self):
        url = instagram.iniciar_oauth()
        query = parse_qs(urlparse(url).query)

        self.assertEqual(urlparse(url).netloc, "api.instagram.com")
        self.assertEqual(query["client_id"], ["1050579897592243"])
        self.assertNotIn("force_reauth", query)
        self.assertEqual(
            query["redirect_uri"],
            ["https://example.trycloudflare.com/meta/instagram/callback"],
        )
        self.assertEqual(
            query["scope"],
            ["instagram_business_basic,instagram_business_content_publish"],
        )
        self.assertTrue(query["state"][0])
        self.assertTrue(self.estado.is_file())

    def test_rechaza_url_local(self):
        with self.assertRaises(instagram.InstagramError):
            instagram.publicar_media(
                "https://127.0.0.1/proyectos/prueba/video_final.mp4"
            )

    def test_publica_reel_y_no_devuelve_el_token(self):
        token = "token-privado-de-prueba"
        self.cuenta.write_text(
            json.dumps(
                {
                    "instagram_user_id": "17840000000000000",
                    "username": "elpergaminoperdidos",
                    "access_token": token,
                    "token_type": "long_lived",
                    "expires_at": None,
                }
            ),
            encoding="utf-8",
        )

        respuestas = [
            RespuestaFalsa({"id": "container-1"}),
            RespuestaFalsa(
                {
                    "status_code": "FINISHED",
                    "status": "ok",
                }
            ),
            RespuestaFalsa({"id": "media-1"}),
        ]
        with patch.object(instagram, "urlopen", side_effect=respuestas) as abrir:
            resultado = instagram.publicar_media(
                "https://example.trycloudflare.com/video_final.mp4",
                "Prueba de publicación del Pergamino",
                "REELS",
            )

        self.assertEqual(resultado["media_id"], "media-1")
        self.assertNotIn("access_token", resultado)
        self.assertNotIn(token, json.dumps(resultado))

        self.assertEqual(abrir.call_count, 3)
        primera_peticion = abrir.call_args_list[0].args[0]
        self.assertEqual(primera_peticion.method, "POST")
        self.assertEqual(
            primera_peticion.headers["Authorization"],
            f"Bearer {token}",
        )
        cuerpo = json.loads(primera_peticion.data.decode("utf-8"))
        self.assertEqual(cuerpo["media_type"], "REELS")
        self.assertEqual(
            cuerpo["video_url"],
            "https://example.trycloudflare.com/video_final.mp4",
        )


if __name__ == "__main__":
    unittest.main()
