"""Per-request Ouro clients for the hosted HTTP transport.

stdio keeps one process-wide client built from ``OURO_API_KEY``. The public
HTTP server must not do that: every connected user sends their own credential,
and tool calls run as that user.

A credential is either an Ouro access token (a Supabase JWT, which is what the
OAuth flow issues) or a personal access token. Both arrive as
``Authorization: Bearer``; ``X-Api-Key`` is still accepted for PATs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from base64 import urlsafe_b64decode
from collections import OrderedDict
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass

import anyio
import httpx
from mcp.server.auth.provider import AccessToken
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST
from starlette.types import ASGIApp, Receive, Scope, Send

from ouro import AuthenticationError, Ouro, PermissionDeniedError
from ouro_mcp import __version__
from ouro_mcp.constants import ENV_OURO_API_KEY, ENV_OURO_BASE_URL, OURO_MCP_OAUTH_SCOPE

log = logging.getLogger("ouro_mcp.http_auth")

_MAX_CLIENTS = 128
# Re-verify cached credentials this often, so a revoked PAT or OAuth grant
# stops working within minutes rather than at process restart.
_VERIFY_TTL_SECONDS = 300


@dataclass
class _CachedClient:
    client: Ouro
    valid_until: float


_api_key: ContextVar[str | None] = ContextVar("ouro_mcp_api_key", default=None)
# Organization (and optional team) this request is pinned to, from the
# X-Ouro-Org / X-Ouro-Team headers. Empty means unpinned.
_pin: ContextVar[tuple[str, str]] = ContextVar("ouro_mcp_pin", default=("", ""))
_clients: OrderedDict[str, _CachedClient] = OrderedDict()
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


def jwt_claims(token: str) -> dict | None:
    """Decode a JWT payload without verifying it. ``None`` if it is not a JWT.

    The signature is checked by the Ouro backend when the client is built;
    this only reads ``exp`` and ``sub`` for caching and the auth context.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(urlsafe_b64decode(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


class ApiKeyHeaderShim:
    """Promote ``X-Api-Key`` to ``Authorization: Bearer`` for the SDK's auth backend."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = scope.get("headers") or []
            names = {name.lower() for name, _ in headers}
            if b"authorization" not in names:
                api_key = next(
                    (value.strip() for name, value in headers if name.lower() == b"x-api-key"),
                    b"",
                )
                if api_key:
                    scope = dict(scope)
                    scope["headers"] = [*headers, (b"authorization", b"Bearer " + api_key)]
        await self.app(scope, receive, send)


class OuroTokenVerifier:
    """Accept a credential only if the Ouro backend resolves it to a user.

    Returning ``None`` makes the SDK answer 401 with a ``WWW-Authenticate``
    header that points at the protected resource metadata, which is what
    starts the OAuth flow in clients like Claude. Backend outages raise
    instead, so clients do not throw away a good token.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = jwt_claims(token)
        exp = _int_claim(claims, "exp")
        if exp is not None and exp <= time.time():
            return None
        try:
            client = await anyio.to_thread.run_sync(client_for_credential, token)
        except (AuthenticationError, PermissionDeniedError):
            return None
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                return None
            raise

        user_id = getattr(client.user, "id", None)
        oauth_client_id = (claims or {}).get("client_id")
        scope = (claims or {}).get("scope")
        if oauth_client_id:
            scopes = scope.split() if isinstance(scope, str) else []
        else:
            # PATs and first-party sessions already carry full account access.
            scopes = [OURO_MCP_OAUTH_SCOPE]
        return AccessToken(
            token=token,
            client_id=str(oauth_client_id or "ouro-api-key"),
            scopes=scopes,
            expires_at=exp,
            subject=str(user_id) if user_id else None,
        )


def extract_pin(headers: Mapping[str, str] | None) -> tuple[str, str]:
    """Organization and team a connection asked to be pinned to, if any."""
    if not headers:
        return ("", "")
    lowered = {str(key).lower(): value for key, value in headers.items()}
    organization = str(lowered.get("x-ouro-org") or "").strip()
    team = str(lowered.get("x-ouro-team") or "").strip() if organization else ""
    return (organization, team)


class ApiKeyMiddleware:
    """Require a per-request credential and make it visible to tool threads."""

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
                    "Missing credentials. Connect with OAuth, or send Authorization: Bearer "
                    "<personal access token> from https://ouro.foundation/settings/api-keys."
                ),
            )

        token = _api_key.set(key)
        pin_token = _pin.set(extract_pin(headers))
        try:
            return await call_next(ctx)
        finally:
            _pin.reset(pin_token)
            _api_key.reset(token)


class RequestScopedOuro:
    """Stand-in for the shared Ouro client. Tools already call ``ouro.<resource>``."""

    def __getattr__(self, name: str):
        return getattr(current_ouro(), name)


def _client_for_request() -> Ouro:
    key = _api_key.get()
    if not key:
        raise RuntimeError(
            "No credentials for this request. Send Authorization: Bearer <token>."
        )
    organization, team = _pin.get()
    return client_for_credential(key, organization=organization, team=team)


def client_for_credential(credential: str, organization: str = "", team: str = "") -> Ouro:
    """Cached client for a PAT or access token. Building one verifies it.

    ``organization`` pins the client (see ``Ouro(organization=...)``). The
    server's own OURO_ORG_ID never applies here: it would pin every caller.
    """
    now = time.time()
    cache_key = f"{credential}\n{organization}\n{team}" if organization else credential
    with _clients_lock:
        cached = _clients.get(cache_key)
        if cached is not None and cached.valid_until > now:
            _clients.move_to_end(cache_key)
            return cached.client

    claims = jwt_claims(credential)
    kwargs: dict[str, str] = {
        "client": f"ouro-mcp/{__version__}",
        "organization": organization,
        "team": team,
    }
    if claims is not None:
        kwargs["access_token"] = credential
    else:
        kwargs["api_key"] = credential
    base_url = os.environ.get(ENV_OURO_BASE_URL, "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    client = Ouro(**kwargs)

    valid_until = now + _VERIFY_TTL_SECONDS
    exp = _int_claim(claims, "exp")
    if exp is not None:
        valid_until = min(valid_until, exp)

    with _clients_lock:
        _clients[cache_key] = _CachedClient(client=client, valid_until=valid_until)
        _clients.move_to_end(cache_key)
        while len(_clients) > _MAX_CLIENTS:
            _clients.popitem(last=False)
    return client


def _int_claim(claims: dict | None, name: str) -> int | None:
    value = (claims or {}).get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
