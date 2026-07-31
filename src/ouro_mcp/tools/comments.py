"""Comment tools — list, create, and update."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
    markdown_bullet,
    markdown_id,
    truncate_response,
)
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_comments(
        parent_id: Annotated[str, Field(description="Asset ID for top-level comments, or comment ID for replies")],
        ctx: Context,
    ) -> str:
        """List comments on an asset or replies to a comment.

        Pass the asset ID (e.g. a post) to get top-level comments, or a
        comment ID to get its replies.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        comments = ouro.comments.list_by_parent(parent_id)

        # Try to fetch the parent asset for context
        parent_context = None
        try:
            parent = ouro.assets.retrieve(parent_id)
            if parent:
                parent_context = {
                    "id": str(parent.id),
                    "asset_type": parent.asset_type,
                    "name": parent.name,
                    "username": parent.user.username,
                }
                if parent.asset_type == "comment" and parent.content:
                    parent_context["text"] = parent.content.text[:500] if parent.content.text else None
        except Exception:
            pass

        results = []
        for c in comments:
            entry = {
                "id": str(c.id),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }

            if c.user:
                entry["author"] = c.user.username

            if c.content:
                entry["text"] = c.content.text[:500] if c.content.text else None

            replies = getattr(c, "replies", None)
            if replies is not None:
                entry["reply_count"] = replies if isinstance(replies, int) else len(replies)

            results.append(entry)

        def _comment_line(row: dict) -> str:
            author = row.get("author") or "unknown"
            parts = [
                markdown_id(row.get("id")),
                row.get("created_at"),
            ]
            if row.get("reply_count"):
                parts.append(f"replies: {row['reply_count']}")
            return markdown_bullet(
                f"@{author}",
                *parts,
                body=row.get("text"),
            )

        parts: list[str] = [f"Comments on parent_id: `{parent_id}`"]
        if parent_context:
            parent_parts = [markdown_id(parent_context.get("id"))]
            if parent_context.get("name") and parent_context.get("username"):
                parent_parts.insert(0, parent_context["name"])
            primary = (
                f"@{parent_context['username']}"
                if parent_context.get("username")
                else (parent_context.get("name") or "parent")
            )
            parts.append("## Parent")
            parts.append(
                markdown_bullet(
                    str(primary),
                    *parent_parts,
                    kind=parent_context.get("asset_type"),
                    body=parent_context.get("text"),
                )
            )
            parts.append("## Comments")

        if not results:
            parts.append("No comments.")
        else:
            parts.append(f"Found {len(results)} comments")
            for row in results:
                parts.append(_comment_line(row))

        return truncate_response("\n".join(parts))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def write_comment(
        content_markdown: Annotated[
            str,
            Field(
                description=(
                    "Extended markdown. Supports @mentions, LaTeX (\\(inline\\), "
                    "\\[display\\]), "
                    "typed asset link shorthands [text](post:|file:|dataset:|route:|service:|quest:<uuid>). "
                    "Use [text](asset:<uuid>) only when the asset type is unknown. "
                    "and block-level asset embeds via ```assetComponent```."
                )
            ),
        ],
        ctx: Context,
        parent_id: Annotated[
            Optional[str],
            Field(description="Asset ID or comment ID to comment on / reply to. Provide to create a new comment."),
        ] = None,
        id: Annotated[
            Optional[str],
            Field(description="Comment UUID to edit. Provide to replace an existing comment's content."),
        ] = None,
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Top-level provenance object; separate from comment metadata"),
        ] = None,
    ) -> str:
        """Create a comment/reply, or edit an existing comment.

        Provide exactly one of:
        - parent_id — the asset or comment to comment on / reply to (creates a comment).
        - id — the comment to edit (replaces its content).

        If you are creating an asset and want to reference it in a comment, you MUST
        wait for the asset creation tool to return the ID before calling write_comment.
        Do not use placeholder IDs or call them in parallel.

        content_markdown supports extended markdown:
        - User mentions: @username
        - Asset links: prefer [text](post:|file:|dataset:|route:|service:|quest:<uuid>) shorthands; use [text](asset:<uuid>) only when the asset type is unknown
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"preview"|"card"}```
        - LaTeX: \\(inline\\), \\[display\\]
        """
        if (parent_id is None) == (id is None):
            raise ValueError("Provide exactly one of parent_id (to create) or id (to edit).")

        ouro = ctx.request_context.lifespan_context.ouro
        content = content_from_markdown(ouro, content_markdown)

        if id is not None:
            comment = ouro.comments.update(
                id,
                content=content,
                license_id=license_id,
                attribution=attribution,
            )
        else:
            comment = ouro.comments.create(
                content=content,
                parent_id=parent_id,
                license_id=license_id,
                attribution=attribution,
            )

        return dump_json(format_asset_summary(comment))
