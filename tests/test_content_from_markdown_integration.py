from __future__ import annotations

import unittest

from ouro_mcp import markdown_input


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
        cls.utils = markdown_input

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
