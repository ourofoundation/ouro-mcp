from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from ouro.models import Connection

from ouro_mcp.utils import dump_json, slim_connection_graph


def _id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, name))


def _edge(type: str, source: dict, target: dict, **extra) -> Connection:
    return Connection.model_validate(
        {
            "id": _id(f"{source['id']}->{target['id']}"),
            "type": type,
            "source_id": _id(source["id"]),
            "target_id": _id(target["id"]),
            "source": {**source, "id": _id(source["id"])},
            "target": {**target, "id": _id(target["id"])},
            **extra,
        }
    )


def test_strips_bloated_source_target() -> None:
    heavy = {"description": "x" * 5000, "visibility": "public"}
    conn = _edge(
        "derivative",
        {"id": "s1", "name": "Src", "asset_type": "file", "created_at": "2026-01-01T00:00:00+00:00", **heavy},
        {"id": "t1", "name": "Tgt", "asset_type": "dataset", **heavy},
    )

    out = slim_connection_graph([conn], current_asset_id=_id("t1"))

    assert out == {
        "derivative": [
            {
                "id": _id("s1"),
                "name": "Src",
                "asset_type": "file",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]
    }
    assert "x" * 100 not in dump_json(out)


def test_empty_or_missing_name_is_dropped_but_asset_type_is_kept() -> None:
    conns = [
        _edge("reference", {"id": "c1", "name": "", "asset_type": "comment"}, {"id": "p1"}),
        _edge("reference", {"id": "c2"}, {"id": "p1"}),
    ]

    out = slim_connection_graph(conns, current_asset_id=_id("p1"))

    assert out["reference"] == [
        {"id": _id("c1"), "asset_type": "comment"},
        {"id": _id("c2"), "asset_type": None},
    ]


def test_outgoing_connection_uses_other_endpoint() -> None:
    conn = _edge(
        "link",
        {"id": "current", "name": "Current", "asset_type": "post"},
        {"id": "other", "name": "Other", "asset_type": "route"},
    )

    out = slim_connection_graph([conn], current_asset_id=_id("current"))

    assert out["link"] == [{"id": _id("other"), "name": "Other", "asset_type": "route"}]


def test_endpoint_falls_back_to_edge_columns_without_join() -> None:
    conn = Connection.model_validate(
        {
            "id": _id("e"),
            "type": "link",
            "source_id": _id("a"),
            "target_id": _id("b"),
            "source_asset_type": "file",
        }
    )

    out = slim_connection_graph([conn], current_asset_id=_id("b"))

    assert out["link"] == [{"id": _id("a"), "asset_type": "file"}]


def test_action_edges_preserve_action_id() -> None:
    conn = _edge(
        "action",
        {"id": "cif", "name": "CIF", "asset_type": "file"},
        {"id": "relaxed", "name": "Relaxed", "asset_type": "file"},
        action_id=_id("act-1"),
    )

    out = slim_connection_graph([conn], current_asset_id=_id("cif"))

    assert out["action"] == [
        {"id": _id("relaxed"), "name": "Relaxed", "asset_type": "file", "action_id": _id("act-1")}
    ]


def test_omit_outgoing_references_keeps_incoming() -> None:
    dataset = {"id": "dataset", "name": "DS", "asset_type": "dataset"}
    conns = [
        _edge("reference", dataset, {"id": "cif", "name": "CIF", "asset_type": "file"}),
        _edge("reference", {"id": "other", "name": "Shortlist", "asset_type": "dataset"}, dataset),
        _edge("link", dataset, {"id": "post", "name": "Writeup", "asset_type": "post"}),
    ]

    out = slim_connection_graph(
        conns, current_asset_id=_id("dataset"), omit_outgoing_references=True
    )

    assert out == {
        "reference": [{"id": _id("other"), "name": "Shortlist", "asset_type": "dataset"}],
        "link": [{"id": _id("post"), "name": "Writeup", "asset_type": "post"}],
    }


def test_omit_comments_drops_only_comment_edges() -> None:
    post = {"id": "post-1", "name": "Post", "asset_type": "post"}
    conns = [
        _edge("comment", {"id": "comment-1", "name": "", "asset_type": "comment"}, post),
        _edge("link", post, {"id": "file-1", "name": "CIF", "asset_type": "file"}),
    ]

    kept = slim_connection_graph(conns, current_asset_id=_id("post-1"))
    omitted = slim_connection_graph(conns, current_asset_id=_id("post-1"), omit_comments=True)

    assert set(kept) == {"comment", "link"}
    assert omitted == {"link": [{"id": _id("file-1"), "name": "CIF", "asset_type": "file"}]}


def test_empty_graph() -> None:
    assert slim_connection_graph([]) == {}
    assert json.loads(dump_json(slim_connection_graph([]))) == {}
