from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from ouro.models import Comment, Post

from ouro_mcp.tools import assets as assets_module


def _id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, name))


def _asset(model, name: str, text: str, username: str = "mmoderwell"):
    now = datetime.now(UTC).isoformat()
    return model.model_validate(
        {
            "id": _id(name),
            "user_id": _id(username),
            "user": {"user_id": _id(username), "username": username},
            "org_id": _id("org"),
            "team_id": _id("team"),
            "name": "" if model is Comment else "Asset",
            "asset_type": "comment" if model is Comment else "post",
            "visibility": "public",
            "created_at": now,
            "last_updated": now,
            "content": {"text": text},
        }
    )


class _FakeComments:
    def __init__(self, by_parent):
        self.by_parent = by_parent

    def list_by_parent(self, parent_id):
        return self.by_parent.get(str(parent_id), [])


class TestGetAssetCommentDetail(unittest.TestCase):
    def test_comment_includes_content_text(self) -> None:
        comment = _asset(Comment, "comment-1", "hello from a comment")

        detail = assets_module._format_asset_detail(
            comment, ouro=SimpleNamespace(comments=_FakeComments({}))
        )

        self.assertEqual(detail["asset_type"], "comment")
        self.assertEqual(detail["content_text"], "hello from a comment")

    def test_mentions_render_from_rich_content_not_stored_plaintext(self) -> None:
        comment = _asset(Comment, "comment-1", "`{@hermes}` what is this?")
        comment.content.data = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "mention", "attrs": {"username": "hermes"}},
                        {"type": "text", "text": " what is this?"},
                    ],
                }
            ],
        }

        detail = assets_module._format_asset_detail(
            comment, ouro=SimpleNamespace(comments=_FakeComments({}))
        )

        self.assertEqual(detail["content_text"], "@hermes what is this?")

    def test_comment_detail_includes_reply_preview(self) -> None:
        comment = _asset(Comment, "comment-1", "cc: @hermes")
        reply = _asset(Comment, "reply-1", "Already replied here.", username="hermes")

        detail = assets_module._format_asset_detail(
            comment,
            ouro=SimpleNamespace(comments=_FakeComments({_id("comment-1"): [reply]})),
        )

        self.assertEqual(detail["comments"][0]["id"], _id("reply-1"))
        self.assertEqual(detail["comments"][0]["author"], "hermes")
        self.assertEqual(detail["comments"][0]["text"], "Already replied here.")

    def test_post_detail_includes_top_level_comments_with_reply_preview(self) -> None:
        post = _asset(Post, "post-1", "Feature post")
        mention = _asset(Comment, "comment-1", "cc: @hermes")
        reply = _asset(Comment, "reply-1", "This is great to see.", username="hermes")

        detail = assets_module._format_asset_detail(
            post,
            ouro=SimpleNamespace(
                comments=_FakeComments(
                    {_id("post-1"): [mention], _id("comment-1"): [reply]}
                )
            ),
        )

        comment_preview = detail["comments"][0]
        self.assertEqual(comment_preview["id"], _id("comment-1"))
        self.assertEqual(comment_preview["text"], "cc: @hermes")
        self.assertEqual(comment_preview["replies"][0]["id"], _id("reply-1"))
        self.assertEqual(comment_preview["replies"][0]["author"], "hermes")


if __name__ == "__main__":
    unittest.main()
