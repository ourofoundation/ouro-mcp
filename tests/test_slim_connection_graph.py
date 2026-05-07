from __future__ import annotations

import json
import unittest

from ouro_mcp.utils import slim_connection_graph


class TestSlimConnectionGraph(unittest.TestCase):
    def test_strips_bloated_source_target(self) -> None:
        heavy = {"description": "x" * 5000, "preview": [1, 2, 3], "metadata": {"a": "b"}}
        conns = [
            {
                "id": "e1",
                "type": "derivative",
                "source_id": "s1",
                "target_id": "t1",
                "source": {
                    "id": "s1",
                    "name": "Src",
                    "asset_type": "file",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    **heavy,
                },
                "target": {"id": "t1", "name": "Tgt", "asset_type": "dataset", **heavy},
            }
        ]
        out = slim_connection_graph(conns, current_asset_id="t1")
        self.assertEqual(set(out), {"derivative"})
        self.assertEqual(len(out["derivative"]), 1)
        self.assertEqual(
            out["derivative"][0],
            {
                "id": "s1",
                "name": "Src",
                "asset_type": "file",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        self.assertNotIn("asset", out["derivative"][0])
        self.assertNotIn("target", out["derivative"][0])
        self.assertNotIn("description", json.dumps(out))

    def test_name_key_always_present_on_endpoints(self) -> None:
        conns = [{"id": "e1", "source": {"id": "x", "asset_type": "file"}}]
        out = slim_connection_graph(conns)
        self.assertIn("name", out["unknown"][0])
        self.assertIsNone(out["unknown"][0]["name"])

    def test_outgoing_connection_uses_other_endpoint(self) -> None:
        conns = [
            {
                "id": "e1",
                "type": "link",
                "source_id": "current",
                "target_id": "other",
                "source": {"id": "current", "name": "Current", "asset_type": "post"},
                "target": {"id": "other", "name": "Other", "asset_type": "route"},
            }
        ]
        out = slim_connection_graph(conns, current_asset_id="current")
        self.assertEqual(out["link"][0], {"id": "other", "name": "Other", "asset_type": "route"})

    def test_non_list_passthrough(self) -> None:
        self.assertIsNone(slim_connection_graph(None))
        self.assertEqual(slim_connection_graph({}), {})

    def test_malformed_edge_preserved(self) -> None:
        self.assertEqual(slim_connection_graph(["keep"]), {"unknown": [{"value": "keep"}]})


if __name__ == "__main__":
    unittest.main()
