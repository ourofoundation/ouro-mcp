from __future__ import annotations

import base64
import unittest

from ouro_mcp.tools.files import _resolve_file_input


class TestResolveFileInput(unittest.TestCase):
    # --- happy paths ---

    def test_file_path_passthrough(self) -> None:
        result = _resolve_file_input(file_path="/tmp/data.cif")
        self.assertEqual(result, {"file_path": "/tmp/data.cif"})

    def test_base64_decodes_to_bytes(self) -> None:
        raw = b"binary-content-here"
        encoded = base64.b64encode(raw).decode("ascii")
        result = _resolve_file_input(
            file_content_base64=encoded, file_name="image.png"
        )
        self.assertEqual(result["file_content"], raw)
        self.assertEqual(result["file_name"], "image.png")

    def test_text_encodes_to_utf8(self) -> None:
        text = "data_cell_length_a 6.351\ndata_cell_length_b 6.351\n"
        result = _resolve_file_input(
            file_content_text=text, file_name="Mg2Si.cif"
        )
        self.assertEqual(result["file_content"], text.encode("utf-8"))
        self.assertEqual(result["file_name"], "Mg2Si.cif")

    def test_no_source_returns_empty(self) -> None:
        result = _resolve_file_input()
        self.assertEqual(result, {})

    # --- validation errors ---

    def test_rejects_path_and_base64(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _resolve_file_input(
                file_path="/tmp/f.cif",
                file_content_base64="AAAA",
                file_name="f.cif",
            )
        self.assertIn("file_path", str(cm.exception))
        self.assertIn("file_content_base64", str(cm.exception))

    def test_rejects_path_and_text(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_file_input(
                file_path="/tmp/f.cif",
                file_content_text="hello",
                file_name="f.cif",
            )

    def test_rejects_base64_and_text(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_file_input(
                file_content_base64="AAAA",
                file_content_text="hello",
                file_name="f.cif",
            )

    def test_rejects_all_three(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_file_input(
                file_path="/tmp/f.cif",
                file_content_base64="AAAA",
                file_content_text="hello",
                file_name="f.cif",
            )

    def test_base64_requires_file_name(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _resolve_file_input(file_content_base64="AAAA")
        self.assertIn("file_name", str(cm.exception))

    def test_text_requires_file_name(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _resolve_file_input(file_content_text="hello")
        self.assertIn("file_name", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
