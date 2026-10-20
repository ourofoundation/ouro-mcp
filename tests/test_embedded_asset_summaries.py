"""Agents learn what a post embeds from the same read, without a get_asset per embed."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from ouro.models import Comment, Post, QuestItem

from ouro_mcp.tools import assets as assets_module
from ouro_mcp.tools.quests import _item_description_text


def _id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, name))


DATASET_ID = _id("structures")
HIDDEN_ID = _id("hidden")


def _doc(*ids: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Data:"}]},
            *(
                {"type": "assetComponent", "attrs": {"id": i, "assetType": "dataset", "viewMode": "card"}}
                for i in ids
            ),
        ],
    }


RESOLVED = [
    {
        "id": DATASET_ID,
        "asset_type": "dataset",
        "name": "structures",
        "visibility": "private",
        "description": {"text": "One row per unique structure."},
        "user": {"username": "mmoderwell"},
        "team": {"name": "materials-science"},
    }
]


def _asset(model, **extra):
    now = datetime.now(UTC).isoformat()
    return model.model_validate(
        {
            "id": _id(model.__name__),
            "user_id": _id("mmoderwell"),
            "org_id": _id("org"),
            "team_id": _id("team"),
            "name": "Crystal DB",
            "asset_type": "comment" if model is Comment else "post",
            "visibility": "public",
            "created_at": now,
            "last_updated": now,
            "content": {"json": _doc(DATASET_ID, HIDDEN_ID), "text": ""},
            **extra,
        }
    )


SUMMARY = "↳ structures (dataset · @mmoderwell · #materials-science · private) — One row per unique structure."
HIDDEN = "↳ Not available to you (deleted, or you don't have access)."


def test_post_detail_summarizes_each_embed() -> None:
    post = _asset(Post, assets=RESOLVED)

    detail = assets_module._format_asset_detail(post, ouro=SimpleNamespace(comments=SimpleNamespace(list_by_parent=lambda _id: [])))

    assert SUMMARY in detail["content_text"]
    assert HIDDEN in detail["content_text"]


def test_post_without_resolved_assets_renders_plain_blocks() -> None:
    post = _asset(Post)

    detail = assets_module._format_asset_detail(post, ouro=SimpleNamespace(comments=SimpleNamespace(list_by_parent=lambda _id: [])))

    assert "↳" not in detail["content_text"]


def test_quest_item_description_summarizes_embeds() -> None:
    raw = {"description": {"json": _doc(DATASET_ID), "text": ""}, "embedded_assets": RESOLVED}

    # Parsed items and the raw dicts some write responses return.
    assert SUMMARY in _item_description_text(QuestItem.model_validate(raw))
    assert SUMMARY in _item_description_text(raw)
