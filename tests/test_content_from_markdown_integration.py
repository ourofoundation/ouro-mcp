from __future__ import annotations

import importlib
import sys
import types
import unittest


def _load_utils_module():
    # Allow these tests to run in lightweight environments where mcp is absent.
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

    if "mcp.server.fastmcp" not in sys.modules:
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")

        class _DummyContext:
            pass

        fastmcp_module.Context = _DummyContext
        sys.modules["mcp.server.fastmcp"] = fastmcp_module

    return importlib.import_module("ouro_mcp.utils")


class _FakeContent:
    def __init__(self) -> None:
        self.received_markdown: str | None = None

    def from_markdown(self, markdown: str) -> None:
        self.received_markdown = markdown


class _FakePosts:
    def __init__(self) -> None:
        self.instances: list[_FakeContent] = []

    def Content(self) -> _FakeContent:
        content = _FakeContent()
        self.instances.append(content)
        return content


class _FakeOuro:
    def __init__(self) -> None:
        self.posts = _FakePosts()


class TestContentFromMarkdownIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.utils = _load_utils_module()

    def test_content_from_markdown_normalizes_before_forwarding(self) -> None:
        ouro = _FakeOuro()
        raw = "\\`@feynman\\` first line\\n\\nsecond line"

        content = self.utils.content_from_markdown(ouro, raw)

        self.assertIs(content, ouro.posts.instances[0])
        self.assertEqual(content.received_markdown, "`{@feynman}` first line\n\nsecond line")

    def test_content_from_markdown_normalizes_plain_mentions(self) -> None:
        ouro = _FakeOuro()
        raw = "Thanks @feynman"

        content = self.utils.content_from_markdown(ouro, raw)

        self.assertIs(content, ouro.posts.instances[0])
        self.assertEqual(content.received_markdown, "Thanks `{@feynman}`")


if __name__ == "__main__":
    unittest.main()
