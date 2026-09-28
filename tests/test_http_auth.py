from __future__ import annotations

import base64
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from mcp_types import INVALID_REQUEST

from ouro import AuthenticationError
from ouro_mcp import http_auth
from ouro_mcp.http_auth import (
    ApiKeyHeaderShim,
    ApiKeyMiddleware,
    OuroTokenVerifier,
    client_for_credential,
    extract_api_key,
    jwt_claims,
    set_http_mode,
)
from ouro_mcp.utils import resolve_local_path


def _jwt(claims: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256'})}.{part(claims)}.signature"


class TestJwtClaims(unittest.TestCase):
    def test_reads_payload(self) -> None:
        self.assertEqual(jwt_claims(_jwt({"sub": "u1", "exp": 5}))["sub"], "u1")

    def test_pat_is_not_a_jwt(self) -> None:
        self.assertIsNone(jwt_claims("ouro_pat_abc123"))
        self.assertIsNone(jwt_claims("a.b.c"))


class TestApiKeyHeaderShim(unittest.IsolatedAsyncioTestCase):
    async def _headers_seen(self, headers: list[tuple[bytes, bytes]]) -> dict[bytes, bytes]:
        seen: dict[bytes, bytes] = {}

        async def app(scope, _receive, _send):
            seen.update(dict(scope["headers"]))

        await ApiKeyHeaderShim(app)({"type": "http", "headers": headers}, None, None)
        return seen

    async def test_promotes_x_api_key(self) -> None:
        seen = await self._headers_seen([(b"x-api-key", b"pat_1")])
        self.assertEqual(seen[b"authorization"], b"Bearer pat_1")

    async def test_keeps_existing_authorization(self) -> None:
        seen = await self._headers_seen(
            [(b"authorization", b"Bearer jwt"), (b"x-api-key", b"pat_1")]
        )
        self.assertEqual(seen[b"authorization"], b"Bearer jwt")


class TestOuroTokenVerifier(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_oauth_access_token(self) -> None:
        token = _jwt({"sub": "u1", "exp": int(time.time()) + 600, "client_id": "claude"})
        client = SimpleNamespace(user=SimpleNamespace(id="u1"))
        with patch.object(http_auth, "client_for_credential", return_value=client):
            result = await OuroTokenVerifier().verify_token(token)
        self.assertEqual(result.client_id, "claude")
        self.assertEqual(result.subject, "u1")

    async def test_pat_carries_the_advertised_scope(self) -> None:
        client = SimpleNamespace(user=SimpleNamespace(id="u1"))
        with patch.object(http_auth, "client_for_credential", return_value=client):
            result = await OuroTokenVerifier().verify_token("pat_1")
        self.assertEqual(result.scopes, [http_auth.OURO_MCP_OAUTH_SCOPE])
        self.assertEqual(result.client_id, "ouro-api-key")

    async def test_oauth_token_keeps_its_granted_scopes(self) -> None:
        token = _jwt({"sub": "u1", "exp": int(time.time()) + 600, "client_id": "c", "scope": "profile"})
        client = SimpleNamespace(user=SimpleNamespace(id="u1"))
        with patch.object(http_auth, "client_for_credential", return_value=client):
            result = await OuroTokenVerifier().verify_token(token)
        self.assertEqual(result.scopes, ["profile"])

    async def test_rejects_expired_token_without_network(self) -> None:
        token = _jwt({"sub": "u1", "exp": int(time.time()) - 1})
        with patch.object(http_auth, "client_for_credential") as build:
            self.assertIsNone(await OuroTokenVerifier().verify_token(token))
        build.assert_not_called()

    async def test_rejected_credential_is_none(self) -> None:
        response = httpx.Response(401, request=httpx.Request("GET", "https://x/user"))
        error = AuthenticationError("bad", response=response, body=None)
        with patch.object(http_auth, "client_for_credential", side_effect=error):
            self.assertIsNone(await OuroTokenVerifier().verify_token("pat"))

    async def test_backend_outage_raises(self) -> None:
        response = httpx.Response(502, request=httpx.Request("POST", "https://x/users/get-token"))
        error = httpx.HTTPStatusError("down", request=response.request, response=response)
        with patch.object(http_auth, "client_for_credential", side_effect=error):
            with self.assertRaises(httpx.HTTPStatusError):
                await OuroTokenVerifier().verify_token("pat")


class TestClientForCredential(unittest.TestCase):
    def setUp(self) -> None:
        http_auth._clients.clear()
        self.addCleanup(http_auth._clients.clear)

    def test_jwt_uses_access_token_and_pat_uses_api_key(self) -> None:
        token = _jwt({"sub": "u1", "exp": int(time.time()) + 600})
        with patch.object(http_auth, "Ouro", return_value=MagicMock()) as ouro:
            client_for_credential(token)
            client_for_credential("pat_1")
        self.assertEqual(ouro.call_args_list[0].kwargs["access_token"], token)
        self.assertEqual(ouro.call_args_list[1].kwargs["api_key"], "pat_1")

    def test_caches_until_ttl(self) -> None:
        with patch.object(http_auth, "Ouro", return_value=MagicMock()) as ouro:
            client_for_credential("pat_1")
            client_for_credential("pat_1")
            self.assertEqual(ouro.call_count, 1)
            http_auth._clients["pat_1"].valid_until = time.time() - 1
            client_for_credential("pat_1")
        self.assertEqual(ouro.call_count, 2)


class TestExtractApiKey(unittest.TestCase):
    def test_bearer_token(self) -> None:
        self.assertEqual(
            extract_api_key({"Authorization": "Bearer pat_123"}),
            "pat_123",
        )

    def test_x_api_key(self) -> None:
        self.assertEqual(extract_api_key({"X-Api-Key": "pat_456"}), "pat_456")

    def test_missing(self) -> None:
        self.assertIsNone(extract_api_key({"Accept": "application/json"}))
        self.assertIsNone(extract_api_key(None))


class TestApiKeyMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_http_mode_rejects_a_request_without_a_key(self) -> None:
        set_http_mode(True)
        self.addCleanup(lambda: set_http_mode(False))
        middleware = ApiKeyMiddleware()
        ctx = SimpleNamespace(request=SimpleNamespace(headers={"accept": "application/json"}))

        with self.assertRaises(Exception) as raised:
            await middleware(ctx, self._should_not_run)

        self.assertEqual(raised.exception.code, INVALID_REQUEST)

    async def test_stdio_does_not_require_a_key(self) -> None:
        set_http_mode(False)
        middleware = ApiKeyMiddleware()
        seen: list[str] = []

        async def call_next(_ctx):
            seen.append("ok")
            return "ok"

        result = await middleware(SimpleNamespace(request=None), call_next)
        self.assertEqual(result, "ok")
        self.assertEqual(seen, ["ok"])

    async def _should_not_run(self, _ctx):
        raise AssertionError("handler ran without an API key")


class TestLocalFilesDisabled(unittest.TestCase):
    def test_rejects_paths_when_disabled(self) -> None:
        previous = os.environ.get("OURO_MCP_LOCAL_FILES")
        os.environ["OURO_MCP_LOCAL_FILES"] = "0"
        self.addCleanup(self._restore, previous)
        with self.assertRaises(PermissionError):
            resolve_local_path("/etc/passwd")

    @staticmethod
    def _restore(previous: str | None) -> None:
        if previous is None:
            os.environ.pop("OURO_MCP_LOCAL_FILES", None)
        else:
            os.environ["OURO_MCP_LOCAL_FILES"] = previous
