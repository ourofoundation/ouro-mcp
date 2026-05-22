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

    def test_dump_json_rewrites_timestamp_to_local_iso_when_timezone_configured(
        self,
    ) -> None:
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

        self.assertEqual(payload, {"created_at": "2026-04-06T21:02:19-05:00"})

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
        self.assertEqual(row, {"id": "1", "last_updated": "2026-04-07T09:01:14-05:00"})

    def test_dump_json_recurses_into_grouped_connection_endpoints(self) -> None:
        # Mirrors the shape `slim_connection_graph` returns inside
        # `get_asset_connections` — a dict of relationship-type → list of
        # endpoint dicts. Each endpoint's `created_at` must get compressed
        # too; otherwise large connection graphs leak the old verbose UTC
        # timestamps even after the rest of the response is shrunk.
        previous = os.environ.get("OURO_MCP_TIMEZONE")
        os.environ["OURO_MCP_TIMEZONE"] = "America/Chicago"
        try:
            payload = json.loads(
                self.utils.dump_json(
                    {
                        "asset_id": "r1",
                        "connections": {
                            "reference": [
                                {
                                    "id": "c1",
                                    "asset_type": "comment",
                                    "created_at": "2026-05-09T21:07:50.802+00:00",
                                },
                            ],
                            "link": [
                                {
                                    "id": "p1",
                                    "asset_type": "post",
                                    "name": "A post",
                                    "created_at": "2026-05-09T13:38:50.132+00:00",
                                },
                            ],
                        },
                    }
                )
            )
        finally:
            if previous is None:
                os.environ.pop("OURO_MCP_TIMEZONE", None)
            else:
                os.environ["OURO_MCP_TIMEZONE"] = previous

        reference_row = payload["connections"]["reference"][0]
        link_row = payload["connections"]["link"][0]
        self.assertEqual(reference_row["created_at"], "2026-05-09T16:07:50-05:00")
        self.assertEqual(link_row["created_at"], "2026-05-09T08:38:50-05:00")

    def test_dump_json_strips_legacy_local_siblings_when_timezone_configured(
        self,
    ) -> None:
        previous = os.environ.get("OURO_MCP_TIMEZONE")
        os.environ["OURO_MCP_TIMEZONE"] = "America/Chicago"
        try:
            payload = json.loads(
                self.utils.dump_json(
                    {
                        "created_at": "2026-04-07T02:02:19.962000+00:00",
                        "created_at_local": "should-be-dropped",
                        "created_at_local_label": "should-be-dropped",
                    }
                )
            )
        finally:
            if previous is None:
                os.environ.pop("OURO_MCP_TIMEZONE", None)
            else:
                os.environ["OURO_MCP_TIMEZONE"] = previous

        self.assertEqual(payload, {"created_at": "2026-04-06T21:02:19-05:00"})

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
