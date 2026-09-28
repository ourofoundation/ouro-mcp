"""Per-request Ouro clients for the hosted HTTP transport.

stdio keeps one process-wide client built from ``OURO_API_KEY``. The public
HTTP server must not do that: every connected user sends their own personal
access token, and tool calls run as that user.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Mapping
from contextvars import ContextVar

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST

from ouro import Ouro
from ouro_mcp import __version__
from ouro_mcp.constants import ENV_OURO_API_KEY, ENV_OURO_BASE_URL

_MAX_CLIENTS = 128
_api_key: ContextVar[str | None] = ContextVar("ouro_mcp_api_key", default=None)
_clients: OrderedDict[str, Ouro] = OrderedDict()
_clients_lock = threading.Lock()
_http_mode = False
_stdio_client: Ouro | None = None


def set_http_mode(enabled: bool) -> None:
    global _http_mode
    _http_mode = enabled


def http_mode() -> bool:
    return _http_mode


def bind_stdio_client(client: Ouro | None) -> None:
    """Hold the single stdio client for resources that cannot take a Context."""
    global _stdio_client
    _stdio_client = client


def current_ouro() -> Ouro:
    """Client for this call: the request token over HTTP, the process client on stdio."""
    if _http_mode:
        return _client_for_request()
    if _stdio_client is None:
        raise RuntimeError(
            f"Ouro client is not initialized. Set {ENV_OURO_API_KEY} and restart the server."
        )
    return _stdio_client


def extract_api_key(headers: Mapping[str, str] | None) -> str | None:
    """Return a bearer token or ``X-Api-Key`` value, if the request has one."""
    if not headers:
        return None
    lowered = {str(key).lower(): value for key, value in headers.items()}
    authorization = (lowered.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    api_key = (lowered.get("x-api-key") or "").strip()
    return api_key or None


class ApiKeyMiddleware:
    """Require a per-request API key and make it visible to tool threads."""

    async def __call__(self, ctx, call_next):
        if not _http_mode:
            return await call_next(ctx)

        request = getattr(ctx, "request", None)
        headers = getattr(request, "headers", None) if request is not None else None
        key = extract_api_key(headers)
        if not key:
            raise MCPError(
                code=INVALID_REQUEST,
                message=(
                    "Missing API key. Send Authorization: Bearer <personal access token> "
                    "from https://ouro.foundation/settings/api-keys."
                ),
            )

        token = _api_key.set(key)
        try:
            return await call_next(ctx)
        finally:
            _api_key.reset(token)


class RequestScopedOuro:
    """Stand-in for the shared Ouro client. Tools already call ``ouro.<resource>``."""

    def __getattr__(self, name: str):
        return getattr(current_ouro(), name)


def _client_for_request() -> Ouro:
    key = _api_key.get()
    if not key:
        raise RuntimeError(
            "No API key for this request. Send Authorization: Bearer <personal access token>."
        )

    with _clients_lock:
        cached = _clients.get(key)
        if cached is not None:
            _clients.move_to_end(key)
            return cached

    kwargs: dict[str, str] = {"api_key": key, "client": f"ouro-mcp/{__version__}"}
    base_url = os.environ.get(ENV_OURO_BASE_URL, "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    client = Ouro(**kwargs)

    with _clients_lock:
        existing = _clients.get(key)
        if existing is not None:
            _clients.move_to_end(key)
            return existing
        _clients[key] = client
        _clients.move_to_end(key)
        while len(_clients) > _MAX_CLIENTS:
            _clients.popitem(last=False)
    return client
