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

    def test_null_name_is_omitted_but_asset_type_is_preserved(self) -> None:
        # `name` drops out when null (display-only). `asset_type` is the
        # discriminator agents use to pick the next tool, so it must stay
        # in the response — even as `null` — to avoid silently changing
        # the shape based on backend completeness.
        conns = [{"id": "e1", "source": {"id": "x", "asset_type": "file"}}]
        out = slim_connection_graph(conns)
        self.assertEqual(out["unknown"][0], {"id": "x", "asset_type": "file"})

    def test_null_asset_type_is_still_emitted(self) -> None:
        conns = [{"id": "e1", "source": {"id": "x"}}]
        out = slim_connection_graph(conns)
        endpoint = out["unknown"][0]
        self.assertIn("asset_type", endpoint)
        self.assertIsNone(endpoint["asset_type"])
        self.assertNotIn("name", endpoint)

    def test_empty_string_name_is_dropped(self) -> None:
        # Real data: comments come back with `name: ""` from the backend
        # (nameless asset type). Treat the empty string the same as null
        # so the slimmed shape doesn't carry purely decorative `, "name": ""`
        # on every comment edge.
        conns = [
            {
                "id": "e1",
                "type": "reference",
                "source": {"id": "c1", "name": "", "asset_type": "comment"},
            }
        ]
        out = slim_connection_graph(conns)
        self.assertEqual(out["reference"][0], {"id": "c1", "asset_type": "comment"})

    def test_present_name_and_asset_type_are_preserved(self) -> None:
        conns = [{"id": "e1", "source": {"id": "x", "name": "X", "asset_type": "file"}}]
        out = slim_connection_graph(conns)
        self.assertEqual(out["unknown"][0], {"id": "x", "asset_type": "file", "name": "X"})

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

    def test_action_edges_preserve_action_id(self) -> None:
        conns = [
            {
                "id": "e1",
                "type": "action",
                "action_id": "act-1",
                "source_id": "cif",
                "target_id": "relaxed",
                "source": {"id": "cif", "name": "CIF", "asset_type": "file"},
                "target": {"id": "relaxed", "name": "Relaxed", "asset_type": "file"},
            }
        ]
        out = slim_connection_graph(conns, current_asset_id="cif")
        self.assertEqual(
            out["action"][0],
            {
                "id": "relaxed",
                "name": "Relaxed",
                "asset_type": "file",
                "action_id": "act-1",
            },
        )

    def test_non_list_passthrough(self) -> None:
        self.assertIsNone(slim_connection_graph(None))
        self.assertEqual(slim_connection_graph({}), {})

    def test_malformed_edge_preserved(self) -> None:
        self.assertEqual(slim_connection_graph(["keep"]), {"unknown": [{"value": "keep"}]})

    def test_omit_outgoing_references_keeps_incoming(self) -> None:
        conns = [
            {
                "id": "out",
                "type": "reference",
                "source_id": "dataset",
                "target_id": "cif",
                "source": {"id": "dataset", "name": "DS", "asset_type": "dataset"},
                "target": {"id": "cif", "name": "CIF", "asset_type": "file"},
            },
            {
                "id": "in",
                "type": "reference",
                "source_id": "other",
                "target_id": "dataset",
                "source": {"id": "other", "name": "Shortlist", "asset_type": "dataset"},
                "target": {"id": "dataset", "name": "DS", "asset_type": "dataset"},
            },
            {
                "id": "link",
                "type": "link",
                "source_id": "dataset",
                "target_id": "post",
                "source": {"id": "dataset", "name": "DS", "asset_type": "dataset"},
                "target": {"id": "post", "name": "Writeup", "asset_type": "post"},
            },
        ]
        out = slim_connection_graph(
            conns,
            current_asset_id="dataset",
            omit_outgoing_references=True,
        )
        self.assertEqual(set(out), {"reference", "link"})
        self.assertEqual(
            out["reference"],
            [{"id": "other", "name": "Shortlist", "asset_type": "dataset"}],
        )
        self.assertEqual(
            out["link"],
            [{"id": "post", "name": "Writeup", "asset_type": "post"}],
        )

    def test_omit_outgoing_references_can_empty_graph(self) -> None:
        conns = [
            {
                "id": "out",
                "type": "reference",
                "source_id": "dataset",
                "target_id": "cif",
                "source": {"id": "dataset", "asset_type": "dataset"},
                "target": {"id": "cif", "asset_type": "file"},
            }
        ]
        out = slim_connection_graph(
            conns,
            current_asset_id="dataset",
            omit_outgoing_references=True,
        )
        self.assertEqual(out, {})

    def test_omit_comments_drops_comment_edges_keeps_others(self) -> None:
        conns = [
            {
                "id": "c1",
                "type": "comment",
                "source_id": "comment-1",
                "target_id": "post-1",
                "source": {"id": "comment-1", "name": "", "asset_type": "comment"},
                "target": {"id": "post-1", "name": "Post", "asset_type": "post"},
            },
            {
                "id": "l1",
                "type": "link",
                "source_id": "post-1",
                "target_id": "file-1",
                "source": {"id": "post-1", "name": "Post", "asset_type": "post"},
                "target": {"id": "file-1", "name": "CIF", "asset_type": "file"},
            },
        ]
        out = slim_connection_graph(
            conns,
            current_asset_id="post-1",
            omit_comments=True,
        )
        self.assertEqual(set(out), {"link"})
        self.assertEqual(
            out["link"],
            [{"id": "file-1", "name": "CIF", "asset_type": "file"}],
        )

    def test_omit_comments_false_keeps_comment_edges(self) -> None:
        conns = [
            {
                "id": "c1",
                "type": "comment",
                "source": {"id": "comment-1", "asset_type": "comment"},
            }
        ]
        out = slim_connection_graph(conns, omit_comments=False)
        self.assertEqual(out["comment"][0], {"id": "comment-1", "asset_type": "comment"})


if __name__ == "__main__":
    unittest.main()
