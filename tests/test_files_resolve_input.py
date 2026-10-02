from __future__ import annotations

import base64
import unittest
from pathlib import Path

from ouro_mcp.tools.assets import download_command
from ouro_mcp.tools.files import _resolve_file_input, upload_command


class TestResolveFileInput(unittest.TestCase):
    # --- happy paths ---

    def test_file_path_passthrough(self) -> None:
        result = _resolve_file_input(file_path="/tmp/data.cif")
        self.assertEqual(result, {"file_path": str(Path("/tmp/data.cif").resolve())})

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

    def test_upload_id_passthrough(self) -> None:
        self.assertEqual(
            _resolve_file_input(upload_id="files/u/a.png"), {"upload_id": "files/u/a.png"}
        )
        self.assertEqual(
            _resolve_file_input(upload_id="files/u/a.png", file_name="plot.png"),
            {"upload_id": "files/u/a.png", "file_name": "plot.png"},
        )

    def test_rejects_upload_id_with_inline_content(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _resolve_file_input(
                upload_id="files/u/a.txt", file_content_text="x", file_name="a.txt"
            )
        self.assertIn("upload_id", str(cm.exception))

    def test_upload_command_quotes_for_a_shell(self) -> None:
        command = upload_command(
            {
                "method": "PUT",
                "headers": {"content-type": "image/png"},
                "upload_url": "https://storage.example/object/upload/sign/files/u/a.png?token=t&x=1",
            },
            "my plot.png",
        )
        self.assertEqual(
            command,
            "curl -sS -f -o /dev/null -X PUT -H 'content-type: image/png' --data-binary @'my plot.png' "
            "'https://storage.example/object/upload/sign/files/u/a.png?token=t&x=1'",
        )

    def test_download_command_quotes_for_a_shell(self) -> None:
        command = download_command(
            {
                "file_name": "my post.md",
                "download_url": "https://api.example/assets/abc/download?token=p.s&x=1",
            }
        )
        self.assertEqual(
            command,
            "curl -sS -f -L -o 'my post.md' 'https://api.example/assets/abc/download?token=p.s&x=1'",
        )

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
