from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4


def _load_assets_module():
    # Allow this unit test to import the tool module in environments
    # where the MCP package is not installed.
    if "ouro" not in sys.modules:
        ouro_module = types.ModuleType("ouro")
        for name in (
            "AuthenticationError",
            "BadRequestError",
            "InternalServerError",
            "NotFoundError",
            "PermissionDeniedError",
            "RateLimitError",
        ):
            setattr(ouro_module, name, type(name, (Exception,), {}))
        sys.modules["ouro"] = ouro_module
    if "ouro.utils" not in sys.modules:
        sys.modules["ouro.utils"] = types.ModuleType("ouro.utils")
    if "ouro.utils.content" not in sys.modules:
        content_module = types.ModuleType("ouro.utils.content")

        def _description_to_markdown(value, max_length=500):
            if value is None:
                return None
            if isinstance(value, dict):
                value = value.get("text", "")
            return str(value)[:max_length]

        content_module.description_to_markdown = _description_to_markdown
        sys.modules["ouro.utils.content"] = content_module
    if "mcp" not in sys.modules:
        mcp_module = types.ModuleType("mcp")

        class _DummyClientCapabilities:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _DummyElicitationCapability:
            pass

        mcp_module.types = types.SimpleNamespace(
            ClientCapabilities=_DummyClientCapabilities,
            ElicitationCapability=_DummyElicitationCapability,
        )
        sys.modules["mcp"] = mcp_module
    if "mcp.server" not in sys.modules:
        sys.modules["mcp.server"] = types.ModuleType("mcp.server")
    if "mcp.server.fastmcp" not in sys.modules:
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")

        class _DummyContext:
            pass

        class _DummyFastMCP:
            pass

        fastmcp_module.Context = _DummyContext
        fastmcp_module.FastMCP = _DummyFastMCP
        sys.modules["mcp.server.fastmcp"] = fastmcp_module

    return importlib.import_module("ouro_mcp.tools.assets")


class TestGetAssetCommentDetail(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assets_module = _load_assets_module()

    def test_comment_includes_content_text(self) -> None:
        now = datetime.now(UTC)
        comment = SimpleNamespace(
            id=uuid4(),
            name="",
            asset_type="comment",
            visibility="inherit",
            created_at=now,
            last_updated=now,
            description=None,
            user=SimpleNamespace(username="mmoderwell"),
            content=SimpleNamespace(text="hello from a comment"),
        )

        detail = self.assets_module._format_asset_detail(comment, ouro=None)

        self.assertEqual(detail["asset_type"], "comment")
        self.assertEqual(detail["content_text"], "hello from a comment")


if __name__ == "__main__":
    unittest.main()
