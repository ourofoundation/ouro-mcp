"""Comment tools — list, create, and update."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
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

        response_data = {"results": results}
        if parent_context:
            response_data["parent"] = parent_context

        return truncate_response(dump_json(response_data))

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
            comment = ouro.comments.update(id, content=content)
        else:
            comment = ouro.comments.create(content=content, parent_id=parent_id)

        return dump_json(format_asset_summary(comment))
