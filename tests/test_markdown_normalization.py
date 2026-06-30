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


class TestNormalizeMarkdownInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.utils = _load_utils_module()

    def test_decodes_literal_newlines(self) -> None:
        text = "line one\\n\\nline two"
        self.assertEqual(self.utils.normalize_markdown_input(text), "line one\n\nline two")

    def test_decodes_windows_newlines(self) -> None:
        text = "line one\\r\\nline two"
        self.assertEqual(self.utils.normalize_markdown_input(text), "line one\nline two")

    def test_unescapes_backticks_for_mentions(self) -> None:
        text = "\\`@feynman\\` hello"
        self.assertEqual(self.utils.normalize_markdown_input(text), "`{@feynman}` hello")

    def test_normalizes_plain_mentions_to_canonical_form(self) -> None:
        text = "hello @feynman"
        self.assertEqual(self.utils.normalize_markdown_input(text), "hello `{@feynman}`")

    def test_normalizes_at_brace_form(self) -> None:
        text = "@{mmoderwell} hi"
        self.assertEqual(
            self.utils.normalize_markdown_input(text), "`{@mmoderwell}` hi"
        )

    def test_normalizes_unbackticked_brace_at_form(self) -> None:
        text = "{@reviewer} ready"
        self.assertEqual(
            self.utils.normalize_markdown_input(text), "`{@reviewer}` ready"
        )

    def test_preserves_canonical_form(self) -> None:
        text = "ping `{@feynman}` please"
        self.assertEqual(
            self.utils.normalize_markdown_input(text), "ping `{@feynman}` please"
        )

    def test_does_not_touch_email_addresses(self) -> None:
        text = "email foo@bar.com here"
        self.assertEqual(
            self.utils.normalize_markdown_input(text), "email foo@bar.com here"
        )


if __name__ == "__main__":
    unittest.main()
