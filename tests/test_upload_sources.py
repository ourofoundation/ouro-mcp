"""Post bodies and dataset rows that arrive as an upload_id."""

from __future__ import annotations

import os
import unittest
from unittest import mock
from unittest.mock import MagicMock

from ouro_mcp.tools.datasets import _resolve_dataset_data
from ouro_mcp.tools.posts import _resolve_post_markdown
from ouro_mcp.utils import discard_upload, source_names


def _ouro(content: bytes) -> MagicMock:
    ouro = MagicMock()
    ouro.files.read_upload.return_value = content
    return ouro


class TestPostFromUpload(unittest.TestCase):
    def test_reads_markdown_and_keeps_the_upload_until_the_post_exists(self) -> None:
        ouro = _ouro("# Title\n\nBody ✓\n".encode())
        markdown = _resolve_post_markdown(None, None, upload_id="files/u/0199.md", ouro=ouro)
        self.assertEqual(markdown, "# Title\n\nBody ✓\n")
        ouro.files.read_upload.assert_called_once_with("files/u/0199.md", discard=False)

    def test_rejects_a_non_markdown_upload_without_fetching_it(self) -> None:
        ouro = _ouro(b"")
        with self.assertRaises(ValueError) as cm:
            _resolve_post_markdown(None, None, upload_id="files/u/0199.png", ouro=ouro)
        self.assertIn(".md", str(cm.exception))
        ouro.files.read_upload.assert_not_called()

    def test_one_body_source_only(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_post_markdown("# inline", None, upload_id="files/u/0199.md", ouro=_ouro(b""))


class TestDatasetFromUpload(unittest.TestCase):
    def test_csv(self) -> None:
        df = _resolve_dataset_data(upload_id="files/u/0199.csv", ouro=_ouro(b"x,y\n1,2\n3,4\n"))
        self.assertEqual(df.to_dict(orient="records"), [{"x": 1, "y": 2}, {"x": 3, "y": 4}])

    def test_json_lines(self) -> None:
        df = _resolve_dataset_data(
            upload_id="files/u/0199.jsonl", ouro=_ouro(b'{"x": 1}\n{"x": 2}\n')
        )
        self.assertEqual(df["x"].tolist(), [1, 2])

    def test_json_array(self) -> None:
        df = _resolve_dataset_data(upload_id="files/u/0199.json", ouro=_ouro(b'[{"x": 1}]'))
        self.assertEqual(df.to_dict(orient="records"), [{"x": 1}])

    def test_rejects_an_unsupported_file_type(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_dataset_data(upload_id="files/u/0199.xlsx", ouro=_ouro(b""))

    def test_one_row_source_only(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_dataset_data(data='[{"x": 1}]', upload_id="files/u/0199.csv", ouro=_ouro(b""))


class TestUploadHelpers(unittest.TestCase):
    def test_discard_is_a_no_op_without_an_upload(self) -> None:
        ouro = MagicMock()
        discard_upload(ouro, None)
        ouro.files.discard_upload.assert_not_called()

    def test_a_failed_discard_does_not_fail_the_tool(self) -> None:
        ouro = MagicMock()
        ouro.files.discard_upload.side_effect = RuntimeError("storage down")
        discard_upload(ouro, "files/u/0199.md")

    def test_source_names_leave_out_paths_on_the_hosted_server(self) -> None:
        with mock.patch.dict(os.environ, {"OURO_MCP_LOCAL_FILES": "1"}):
            self.assertEqual(
                source_names("data_path", "data", "upload_id"), "data_path, data, or upload_id"
            )
        with mock.patch.dict(os.environ, {"OURO_MCP_LOCAL_FILES": "0"}):
            self.assertEqual(source_names("data_path", "data", "upload_id"), "data or upload_id")


if __name__ == "__main__":
    unittest.main()
