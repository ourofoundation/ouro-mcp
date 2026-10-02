from __future__ import annotations

import json
import unittest

from ouro_mcp.hosted import LOCAL_PATH_PARAMS, apply_hosted_tool_surface
from ouro_mcp.server import mcp as registered
from ouro_mcp.tools import register_all_tools


def _server():
    """A fresh server with every tool registered; the shared one stays untouched."""
    server = type(registered)("test")
    register_all_tools(server)
    return server


class TestHostedToolSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.local = _server()._tool_manager._tools
        hosted = _server()
        apply_hosted_tool_surface(hosted)
        self.hosted = hosted._tool_manager._tools

    def test_local_server_keeps_path_params(self) -> None:
        for name, param in LOCAL_PATH_PARAMS.items():
            self.assertIn(param, self.local[name].parameters["properties"], name)

    def test_hosted_server_drops_path_params(self) -> None:
        for name, param in LOCAL_PATH_PARAMS.items():
            tool = self.hosted[name]
            self.assertNotIn(param, tool.parameters["properties"], name)
            self.assertNotIn(param, tool.parameters.get("required", []), name)

    def test_hosted_server_offers_signed_uploads(self) -> None:
        """The replacement for a local path: upload the bytes, then pass upload_id."""
        self.assertIn("create_upload_url", self.hosted)
        for name in LOCAL_PATH_PARAMS:
            if name == "download_asset":
                continue
            self.assertIn("upload_id", self.hosted[name].parameters["properties"], name)
            self.assertIn("upload_id", self.hosted[name].description, name)
        for name in ("create_service", "update_service"):
            self.assertIn("spec_upload_id", self.hosted[name].parameters["properties"], name)

    def test_same_tools_on_both_servers(self) -> None:
        """Hosted changes parameters, never which tools exist."""
        self.assertEqual(set(self.hosted), set(self.local))

    def test_hosted_download_only_returns_a_link(self) -> None:
        tool = self.hosted["download_asset"]
        self.assertNotIn("output_path", tool.parameters["properties"])
        self.assertIn("The result is a link", tool.description)
        self.assertNotIn("saved there", tool.description)

    def test_hosted_tools_never_mention_local_paths(self) -> None:
        """No description or schema left pointing at a parameter that is gone."""
        gone = set(LOCAL_PATH_PARAMS.values()) | {"WORKSPACE_ROOT", "WORKSPACE_MOUNT"}
        for name, tool in self.hosted.items():
            text = tool.description + json.dumps(tool.parameters)
            for word in gone:
                self.assertNotIn(word, text, f"{name} still mentions {word}")

    def test_every_local_path_param_is_listed(self) -> None:
        """A new tool that reads a local path must be added to LOCAL_PATH_PARAMS."""
        described = {
            (name, param)
            for name, tool in self.local.items()
            for param, schema in tool.parameters["properties"].items()
            # An upload_id stands in for a local file; it is not a path.
            if not param.endswith("upload_id")
            if "local" in (schema.get("description") or "").lower()
            or "on disk" in (schema.get("description") or "").lower()
            or "WORKSPACE_ROOT" in (schema.get("description") or "")
        }
        listed = set(LOCAL_PATH_PARAMS.items())
        self.assertLessEqual(described, listed, described - listed)


if __name__ == "__main__":
    unittest.main()
