from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ouro_mcp.tools.posts import _resolve_post_markdown


class TestResolvePostMarkdown(unittest.TestCase):
    def test_passes_through_content_markdown(self) -> None:
        result = _resolve_post_markdown(content_markdown="# hello", content_path=None)
        self.assertEqual(result, "# hello")

    def test_rejects_both_inputs(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_post_markdown(content_markdown="# hello", content_path="/tmp/post.md")

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_post_markdown(
                content_markdown=None,
                content_path="/tmp/this-file-should-not-exist.md",
            )

    def test_rejects_non_markdown_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "post.txt"
            path.write_text("hello", encoding="utf-8")
            with self.assertRaises(ValueError):
                _resolve_post_markdown(content_markdown=None, content_path=str(path))

    def test_reads_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "post.markdown"
            path.write_text("# hello from file\n", encoding="utf-8")
            result = _resolve_post_markdown(content_markdown=None, content_path=str(path))
            self.assertEqual(result, "# hello from file\n")


if __name__ == "__main__":
    unittest.main()
