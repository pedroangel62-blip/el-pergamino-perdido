"""Integración mínima y segura con Instagram Business Login.

Este módulo implementa el flujo oficial de Instagram Login y la publicación
de una imagen o un Reel mediante el Graph API de Instagram. Los tokens se
guardan únicamente en el estado local indicado por variables de entorno.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


OAUTH_AUTHORIZE_URL = "https://api.instagram.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE_URL = "https://graph.instagram.com"


class InstagramError(RuntimeError):
    """Error controlado de la integración con Instagram."""


class InstagramConfigurationError(InstagramError):
    """Falta o es inválida una variable de configuración."""


class InstagramAPIError(InstagramError):
    """Instagram rechazó una petición o devolvió un error."""


def _env(nombre: str, obligatorio: bool = False) -> str:
    valor = os.getenv(nombre, "").strip()
    if obligatorio and not valor:
        raise InstagramConfigurationError(
            f"Falta la variable de entorno {nombre}."
        )
    return valor


def _graph_version() -> str:
    version = _env("INSTAGRAM_GRAPH_API_VERSION") or "v25.0"
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise InstagramConfigurationError(
            "INSTAGRAM_GRAPH_API_VERSION no tiene un formato válido."
        )
    return version


def _app_id() -> str:
    return _env("INSTAGRAM_APP_ID", obligatorio=True)


def _app_secret() -> str:
    return _env("INSTAGRAM_APP_SECRET", obligatorio=True)


def _redirect_uri() -> str:
    uri = _env("INSTAGRAM_REDIRECT_URI", obligatorio=True)
    datos = urlparse(uri)
    if datos.scheme != "https" or not datos.netloc:
        raise InstagramConfigurationError(
            "INSTAGRAM_REDIRECT_URI debe ser una URL HTTPS completa."
        )
    return uri


def _state_path() -> Path:
    return Path(
        _env("INSTAGRAM_OAUTH_STATE_PATH")
        or "backend/data/instagram/oauth_state.json"
    )


def _account_path() -> Path:
    return Path(
        _env("INSTAGRAM_ACCOUNT_PATH")
        or "backend/data/instagram/account.json"
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(f"{path.name}.tmp")
    temporal.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporal, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows no aplica necesariamente los mismos permisos POSIX.
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InstagramError(
            "No hay una cuenta de Instagram conectada."
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise InstagramError(
            "No se pudo leer la conexión local de Instagram."
        ) from error

    if not isinstance(data, dict):
        raise InstagramError(
            "La conexión local de Instagram no tiene un formato válido."
        )
    return data


def _parse_response(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstagramAPIError(
            "Instagram devolvió una respuesta que no se pudo interpretar."
        ) from error

    if not isinstance(value, dict):
        raise InstagramAPIError(
            "Instagram devolvió una respuesta con formato inesperado."
        )
    return value


def _safe_error_message(data: dict[str, Any], token: str = "") -> str:
    error_data = data.get("error")
    if isinstance(error_data, dict):
        message = str(error_data.get("message", "")).strip()
        error_type = str(error_data.get("type", "")).strip()
        code = error_data.get("code")
        partes = [parte for parte in (error_type, message) if parte]
        if code is not None:
            partes.append(f"código {code}")
        texto = ": ".join(partes) if partes else "Instagram rechazó la petición."
    else:
        texto = str(data.get("message", "")).strip()
        if not texto:
            texto = "Instagram rechazó la petición."

    if token:
        texto = texto.replace(token, "[token oculto]")
    return texto[:800]


def _request_json(
    method: str,
    url: str,
    *,
    token: str = "",
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    form: bool = False,
) -> dict[str, Any]:
    if params:
        separador = "&" if "?" in url else "?"
        url = f"{url}{separador}{urlencode(params)}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "ElPergaminoPerdido/1.0",
    }
    body = None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if payload is not None:
        if form:
            body = urlencode(payload).encode("utf-8")
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded"
            )
        else:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(request, timeout=45) as response:
            return _parse_response(response.read())
    except HTTPError as error:
        try:
            data = _parse_response(error.read())
            detalle = _safe_error_message(data, token)
        except InstagramAPIError:
            detalle = f"HTTP {error.code}"
        raise InstagramAPIError(detalle) from error
    except URLError as error:
        raise InstagramAPIError(
            "No se pudo conectar con Instagram. Comprueba la conexión a Internet."
        ) from error
    except TimeoutError as error:
        raise InstagramAPIError(
            "Instagram tardó demasiado en responder."
        ) from error


def _graph_request(
    method: str,
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = (
        f"{GRAPH_BASE_URL}/{_graph_version()}/"
        f"{path.lstrip('/')}"
    )
    return _request_json(
        method,
        url,
        token=token,
        params=params,
        payload=payload,
    )


def _validate_public_media_url(media_url: str) -> str:
    url = str(media_url or "").strip()
    datos = urlparse(url)
    hostname = (datos.hostname or "").rstrip(".").casefold()

    if datos.scheme != "https" or not hostname:
        raise InstagramError(
            "Instagram necesita una URL HTTPS pública para el archivo multimedia."
        )

    nombres_locales = {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
    }
    if hostname in nombres_locales or hostname.endswith(".local"):
        raise InstagramError(
            "La URL multimedia no puede apuntar al equipo local."
        )

    if datos.username or datos.password:
        raise InstagramError(
            "La URL multimedia no puede contener credenciales."
        )

    return url


def iniciar_oauth() -> str:
    """Crea un estado CSRF y devuelve la URL de autorización."""
    redirect_uri = _redirect_uri()
    state = secrets.token_urlsafe(32)
    _write_json(
        _state_path(),
        {
            "state": state,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    parametros = {
        "client_id": _app_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": (
            "instagram_business_basic,"
            "instagram_business_content_publish"
        ),
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(parametros)}"


def _consumir_estado(state: str) -> None:
    path = _state_path()
    stored = _read_json(path)
    esperado = str(stored.get("state", ""))

    try:
        creado = datetime.fromisoformat(
            str(stored.get("created_at", ""))
        )
    except ValueError as error:
        raise InstagramError(
            "El estado de seguridad de Instagram no es válido."
        ) from error

    if (
        not esperado
        or not state
        or not secrets.compare_digest(esperado, state)
    ):
        raise InstagramError(
            "El estado de seguridad de Instagram no coincide."
        )

    if datetime.now(timezone.utc) - creado > timedelta(minutes=15):
        raise InstagramError(
            "El estado de seguridad de Instagram ha caducado."
        )

    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _exchange_code(code: str) -> dict[str, Any]:
    return _request_json(
        "POST",
        OAUTH_TOKEN_URL,
        payload={
            "client_id": _app_id(),
            "client_secret": _app_secret(),
            "grant_type": "authorization_code",
            "redirect_uri": _redirect_uri(),
            "code": code,
        },
        form=True,
    )


def _exchange_long_lived(short_token: str) -> dict[str, Any]:
    return _request_json(
        "GET",
        f"{GRAPH_BASE_URL}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": _app_secret(),
            "access_token": short_token,
        },
    )


def completar_oauth(code: str, state: str) -> dict[str, Any]:
    """Valida el estado, canjea el código y guarda la cuenta local."""
    if not code or not state:
        raise InstagramError(
            "Instagram no devolvió un código y un estado completos."
        )

    _consumir_estado(state)
    token_data = _exchange_code(code)
    short_token = str(token_data.get("access_token", "")).strip()
    if not short_token:
        raise InstagramAPIError(
            "Instagram no devolvió un token de acceso."
        )

    token = short_token
    expires_in = int(token_data.get("expires_in", 0) or 0)
    token_type = "short_lived"

    try:
        long_data = _exchange_long_lived(short_token)
        long_token = str(long_data.get("access_token", "")).strip()
        if long_token:
            token = long_token
            expires_in = int(long_data.get("expires_in", 0) or 0)
            token_type = "long_lived"
    except InstagramAPIError:
        # El token corto sigue permitiendo comprobar la integración, pero
        # queda identificado para que el usuario lo renueve después.
        pass

    profile = _graph_request(
        "GET",
        "me",
        token,
        params={"fields": "id,username,name"},
    )
    instagram_user_id = str(profile.get("id", "")).strip()
    if not instagram_user_id:
        raise InstagramAPIError(
            "Instagram no devolvió el identificador de la cuenta."
        )

    expires_at = None
    if expires_in > 0:
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=expires_in)
        ).isoformat()

    account = {
        "instagram_user_id": instagram_user_id,
        "username": str(profile.get("username", "")).strip(),
        "name": str(profile.get("name", "")).strip(),
        "access_token": token,
        "token_type": token_type,
        "expires_at": expires_at,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(_account_path(), account)
    return account


def cargar_cuenta() -> dict[str, Any]:
    account = _read_json(_account_path())
    token = str(account.get("access_token", "")).strip()
    user_id = str(account.get("instagram_user_id", "")).strip()
    if not token or not user_id:
        raise InstagramError(
            "La conexión local de Instagram está incompleta."
        )
    return account


def estado_cuenta() -> dict[str, Any]:
    try:
        account = cargar_cuenta()
    except InstagramError:
        return {"connected": False}

    return {
        "connected": True,
        "instagram_user_id": account.get("instagram_user_id"),
        "username": account.get("username"),
        "name": account.get("name"),
        "token_type": account.get("token_type"),
        "expires_at": account.get("expires_at"),
        "connected_at": account.get("connected_at"),
    }


def _renovar_si_necesario(account: dict[str, Any]) -> dict[str, Any]:
    expires_at = account.get("expires_at")
    if not expires_at:
        return account

    try:
        vencimiento = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return account

    if vencimiento - datetime.now(timezone.utc) > timedelta(days=7):
        return account

    token = str(account.get("access_token", "")).strip()
    data = _request_json(
        "GET",
        f"{GRAPH_BASE_URL}/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": token,
        },
    )
    nuevo_token = str(data.get("access_token", "")).strip()
    if not nuevo_token:
        raise InstagramAPIError(
            "Instagram no devolvió un token renovado."
        )

    expires_in = int(data.get("expires_in", 0) or 0)
    account = dict(account)
    account["access_token"] = nuevo_token
    account["token_type"] = "long_lived"
    if expires_in:
        account["expires_at"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=expires_in)
        ).isoformat()
    _write_json(_account_path(), account)
    return account


def _esperar_contenedor(
    account: dict[str, Any],
    container_id: str,
) -> dict[str, Any]:
    timeout = int(_env("INSTAGRAM_CONTAINER_TIMEOUT_SECONDS") or "180")
    intervalo = int(_env("INSTAGRAM_CONTAINER_POLL_SECONDS") or "5")
    inicio = time.monotonic()

    while time.monotonic() - inicio <= timeout:
        estado = _graph_request(
            "GET",
            container_id,
            str(account["access_token"]),
            params={"fields": "status_code,status"},
        )
        status_code = str(estado.get("status_code", "")).upper()

        if status_code == "FINISHED":
            return estado

        if status_code in {"ERROR", "EXPIRED"}:
            detalle = str(estado.get("status", "")).strip()
            raise InstagramAPIError(
                "Instagram no pudo procesar el contenido."
                + (f" Detalle: {detalle}" if detalle else "")
            )

        time.sleep(max(1, intervalo))

    raise InstagramAPIError(
        "Instagram no terminó de procesar el contenido dentro del tiempo esperado."
    )


def publicar_media(
    media_url: str,
    caption: str = "",
    media_type: str = "REELS",
    share_to_feed: bool = True,
) -> dict[str, Any]:
    """Crea un contenedor y publica una imagen o un Reel."""
    url = _validate_public_media_url(media_url)
    tipo = str(media_type or "REELS").upper().strip()
    if tipo not in {"IMAGE", "REELS"}:
        raise InstagramError(
            "El tipo de contenido debe ser IMAGE o REELS."
        )

    texto = str(caption or "").strip()
    if len(texto) > 2200:
        raise InstagramError(
            "El texto de publicación supera los 2200 caracteres."
        )

    account = _renovar_si_necesario(cargar_cuenta())
    token = str(account["access_token"])
    user_id = str(account["instagram_user_id"])

    payload: dict[str, Any] = {
        "caption": texto,
    }
    if tipo == "IMAGE":
        payload["image_url"] = url
    else:
        payload.update(
            {
                "media_type": "REELS",
                "video_url": url,
                "share_to_feed": bool(share_to_feed),
            }
        )

    container = _graph_request(
        "POST",
        f"{user_id}/media",
        token,
        payload=payload,
    )
    container_id = str(container.get("id", "")).strip()
    if not container_id:
        raise InstagramAPIError(
            "Instagram no devolvió el identificador del contenedor."
        )

    if tipo == "REELS":
        _esperar_contenedor(account, container_id)

    publicado = _graph_request(
        "POST",
        f"{user_id}/media_publish",
        token,
        payload={"creation_id": container_id},
    )
    media_id = str(publicado.get("id", "")).strip()
    if not media_id:
        raise InstagramAPIError(
            "Instagram no devolvió el identificador de la publicación."
        )

    return {
        "success": True,
        "instagram_user_id": user_id,
        "username": account.get("username", ""),
        "container_id": container_id,
        "media_id": media_id,
        "media_type": tipo,
    }
