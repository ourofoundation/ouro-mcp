"""Regression tests for the unified action input/output asset shape.

Covers `_compact_action_assets`, `_unified_action_assets`, and the
agent-facing shape produced by `_format_action_summary` (mirrored in
`_format_action_result`):

  - Agent responses only ever expose plural `input_assets` /
    `output_assets` — never the legacy singular keys.
  - Modern actions with `action_assets` join rows are slimmed to
    `{name, is_primary?, asset: {id, asset_type, name?, description?}}`.
  - Legacy actions with only the singular `input_asset` / `output_asset`
    FK columns are synthesized into a one-row plural list marked
    `is_primary: true` (no `name`, since legacy actions had no slot name).
  - Plural rows take precedence over the legacy singular when both are
    populated (the backend often double-writes during the transition).
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest


def _load_services_module():
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

        class _DummyFastMCP:
            pass

        fastmcp_module.Context = _DummyContext
        fastmcp_module.FastMCP = _DummyFastMCP
        sys.modules["mcp.server.fastmcp"] = fastmcp_module

    return importlib.import_module("ouro_mcp.tools.services")


class TestCompactActionAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.services = _load_services_module()

    def test_returns_none_for_empty_and_missing(self) -> None:
        compact = self.services._compact_action_assets
        self.assertIsNone(compact(None))
        self.assertIsNone(compact([]))
        self.assertIsNone(compact("not-a-list"))

    def test_slims_each_row_to_compact_shape(self) -> None:
        rows = [
            {
                "name": "report",
                "is_primary": True,
                "asset_id": "a1",
                "asset_type": "file",
                "asset": {
                    "id": "a1",
                    "name": "Benchmark report",
                    "asset_type": "file",
                    "description": {"text": "summary"},
                    "visibility": "public",
                    "preview": [{"row": 1}],
                },
            },
            {
                "name": "raw_results",
                "is_primary": False,
                "asset_id": "a2",
                "asset_type": "dataset",
                "asset": {"id": "a2", "asset_type": "dataset", "name": "Raw rows"},
            },
        ]
        result = self.services._compact_action_assets(rows)
        self.assertEqual(
            result,
            [
                {
                    "name": "report",
                    "is_primary": True,
                    "asset": {
                        "id": "a1",
                        "name": "Benchmark report",
                        "asset_type": "file",
                        "description": {"text": "summary"},
                    },
                },
                {
                    "name": "raw_results",
                    "asset": {"id": "a2", "name": "Raw rows", "asset_type": "dataset"},
                },
            ],
        )

    def test_falls_back_to_fk_columns_when_join_is_missing(self) -> None:
        rows = [{"name": "structure", "asset_id": "a1", "asset_type": "file"}]
        result = self.services._compact_action_assets(rows)
        self.assertEqual(
            result,
            [{"name": "structure", "asset": {"id": "a1", "asset_type": "file"}}],
        )


class TestUnifiedActionAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.services = _load_services_module()

    def test_legacy_singular_is_synthesized_into_plural(self) -> None:
        unified = self.services._unified_action_assets(
            None,
            {"id": "legacy", "asset_type": "file", "name": "Legacy"},
        )
        # Legacy actions had no slot name — synthesized rows carry only
        # `is_primary: true` and the compact asset block.
        self.assertEqual(
            unified,
            [
                {
                    "is_primary": True,
                    "asset": {
                        "id": "legacy",
                        "name": "Legacy",
                        "asset_type": "file",
                    },
                }
            ],
        )

    def test_empty_plural_falls_through_to_legacy(self) -> None:
        unified = self.services._unified_action_assets(
            [],
            {"id": "legacy", "asset_type": "file"},
        )
        self.assertIsNotNone(unified)
        self.assertEqual(unified[0]["asset"]["id"], "legacy")

    def test_plural_wins_when_both_populated(self) -> None:
        unified = self.services._unified_action_assets(
            [{"name": "report", "asset": {"id": "p1", "asset_type": "file"}}],
            {"id": "legacy", "asset_type": "file", "name": "Legacy"},
        )
        # The legacy singular is dropped entirely when join rows exist,
        # even though the backend may double-write during the transition.
        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["asset"]["id"], "p1")
        self.assertEqual(unified[0]["name"], "report")

    def test_returns_none_when_both_forms_absent(self) -> None:
        self.assertIsNone(self.services._unified_action_assets(None, None))
        self.assertIsNone(self.services._unified_action_assets([], None))


class TestFormatActionSummaryUnifiedShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.services = _load_services_module()

    def _action(self, **overrides):
        base = {
            "id": "00000000-0000-0000-0000-000000000001",
            "status": "success",
            "route_id": "00000000-0000-0000-0000-000000000002",
        }
        base.update(overrides)
        return base

    def test_modern_action_surfaces_plural_with_multiple_slots(self) -> None:
        action = self._action(
            output_assets=[
                {
                    "name": "report",
                    "is_primary": True,
                    "asset": {"id": "p1", "asset_type": "post", "name": "Report"},
                },
                {
                    "name": "raw_results",
                    "asset": {"id": "p2", "asset_type": "dataset"},
                },
            ],
        )
        row = self.services._format_action_summary(action)
        self.assertIn("output_assets", row)
        self.assertNotIn("output_asset", row)
        self.assertEqual([e["name"] for e in row["output_assets"]], ["report", "raw_results"])

    def test_legacy_singular_becomes_synthesized_plural(self) -> None:
        action = self._action(
            output_assets=[],
            output_asset={"id": "legacy", "asset_type": "file", "name": "Legacy"},
        )
        row = self.services._format_action_summary(action)
        # The agent only sees the plural shape, even for pre-action_assets data.
        self.assertIn("output_assets", row)
        self.assertNotIn("output_asset", row)
        self.assertEqual(
            row["output_assets"],
            [{"is_primary": True, "asset": {"id": "legacy", "name": "Legacy", "asset_type": "file"}}],
        )

    def test_no_output_keys_when_both_forms_absent(self) -> None:
        action = self._action()
        row = self.services._format_action_summary(action)
        self.assertNotIn("output_assets", row)
        self.assertNotIn("output_asset", row)

    def test_inputs_follow_the_same_unified_shape(self) -> None:
        action = self._action(
            input_assets=[
                {"name": "structure", "asset": {"id": "i1", "asset_type": "file"}},
            ],
            input_asset={"id": "legacy_in", "asset_type": "file"},
        )
        row = self.services._format_action_summary(action)
        self.assertIn("input_assets", row)
        self.assertNotIn("input_asset", row)
        self.assertEqual(row["input_assets"][0]["name"], "structure")


if __name__ == "__main__":
    unittest.main()
