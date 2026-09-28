from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

from mcp_types import INVALID_REQUEST

from ouro_mcp.http_auth import ApiKeyMiddleware, extract_api_key, set_http_mode
from ouro_mcp.utils import resolve_local_path


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
