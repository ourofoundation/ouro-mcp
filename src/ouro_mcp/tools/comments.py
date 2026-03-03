"""Comment tools — list, create, and update."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    format_asset_summary,
    truncate_response,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_comments(
        parent_id: str,
        ctx: Context,
    ) -> str:
        """List comments on an asset or replies to a comment.

        Pass the asset ID (e.g. a post) to get top-level comments, or a
        comment ID to get its replies.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        comments = ouro.comments.list_by_parent(parent_id)

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
                entry["reply_count"] = (
                    replies if isinstance(replies, int) else len(replies)
                )

            results.append(entry)

        result = json.dumps(
            {
                "results": results,
                "count": len(results),
                "parent_id": parent_id,
            }
        )

        return truncate_response(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_comment(
        parent_id: str,
        content_markdown: str,
        ctx: Context,
    ) -> str:
        """Create a comment on an asset or reply to an existing comment.

        parent_id is the ID of the asset being commented on, or the ID of a
        comment being replied to.

        content_markdown supports extended markdown:
        - User mentions: `{@username}` -- call search_users() for usernames
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"preview"|"card"}```
        - Standard markdown and LaTeX math
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = content_from_markdown(ouro, content_markdown)
        comment = ouro.comments.create(content=content, parent_id=parent_id)

        return json.dumps(format_asset_summary(comment))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_comment(
        id: str,
        content_markdown: str,
        ctx: Context,
    ) -> str:
        """Update a comment's content.

        content_markdown supports extended markdown:
        - User mentions: `{@username}` -- call search_users() for usernames
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"preview"|"card"}```
        - Standard markdown and LaTeX math
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = content_from_markdown(ouro, content_markdown)
        comment = ouro.comments.update(id, content=content)

        return json.dumps(format_asset_summary(comment))
