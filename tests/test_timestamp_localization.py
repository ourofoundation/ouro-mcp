from __future__ import annotations

import importlib
import json
import os
import sys
import types
import unittest


def _load_utils_module():
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

        fastmcp_module.Context = _DummyContext
        sys.modules["mcp.server.fastmcp"] = fastmcp_module

    return importlib.import_module("ouro_mcp.utils")


class TestTimestampLocalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.utils = _load_utils_module()

    def test_dump_json_adds_local_timestamp_fields_when_timezone_configured(self) -> None:
        previous = os.environ.get("OURO_MCP_TIMEZONE")
        os.environ["OURO_MCP_TIMEZONE"] = "America/Chicago"
        try:
            payload = json.loads(
                self.utils.dump_json(
                    {"created_at": "2026-04-07T02:02:19.962000+00:00"}
                )
            )
        finally:
            if previous is None:
                os.environ.pop("OURO_MCP_TIMEZONE", None)
            else:
                os.environ["OURO_MCP_TIMEZONE"] = previous

        self.assertEqual(payload["created_at"], "2026-04-07T02:02:19.962000+00:00")
        self.assertEqual(payload["created_at_local"], "2026-04-06T21:02:19.962000-05:00")
        self.assertEqual(payload["created_at_local_label"], "2026-04-06 09:02 PM CDT")

    def test_dump_json_recurses_into_nested_results(self) -> None:
        previous = os.environ.get("OURO_MCP_TIMEZONE")
        os.environ["OURO_MCP_TIMEZONE"] = "America/Chicago"
        try:
            payload = json.loads(
                self.utils.dump_json(
                    {
                        "results": [
                            {"id": "1", "last_updated": "2026-04-07T14:01:14.385000+00:00"}
                        ]
                    }
                )
            )
        finally:
            if previous is None:
                os.environ.pop("OURO_MCP_TIMEZONE", None)
            else:
                os.environ["OURO_MCP_TIMEZONE"] = previous

        row = payload["results"][0]
        self.assertEqual(row["last_updated_local"], "2026-04-07T09:01:14.385000-05:00")
        self.assertEqual(row["last_updated_local_label"], "2026-04-07 09:01 AM CDT")

    def test_dump_json_leaves_payload_unchanged_without_timezone(self) -> None:
        previous = os.environ.get("OURO_MCP_TIMEZONE")
        os.environ.pop("OURO_MCP_TIMEZONE", None)
        try:
            payload = json.loads(
                self.utils.dump_json(
                    {"created_at": "2026-04-07T02:02:19.962000+00:00"}
                )
            )
        finally:
            if previous is not None:
                os.environ["OURO_MCP_TIMEZONE"] = previous

        self.assertEqual(payload, {"created_at": "2026-04-07T02:02:19.962000+00:00"})


if __name__ == "__main__":
    unittest.main()
