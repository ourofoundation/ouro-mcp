"""Comment tools — list, create, and update."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Comment
from ouro.utils.content import description_to_markdown
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
    truncate_response,
)
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_comments(
        parent_id: Annotated[str, Field(description="Asset ID for top-level comments, or comment ID for replies")],
        ctx: Context,
        limit: Annotated[int, Field(description="Page size, 1-200")] = 20,
        offset: Annotated[int, Field(description="Offset for pagination")] = 0,
    ) -> str:
        """List comments on an asset or replies to a comment, oldest first.

        Pass the asset ID (e.g. a post) to get top-level comments, or a
        comment ID to get its replies. The first page also shows the parent.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        page = ouro.comments.list(parent_id, limit=limit, offset=offset)

        parent_context = None
        try:
            parent = ouro.assets.retrieve(parent_id) if offset == 0 else None
            if parent:
                parent_context = {
                    "id": str(parent.id),
                    "asset_type": parent.asset_type,
                    "name": parent.name,
                    "username": parent.user.username,
                }
                if isinstance(parent, Comment):
                    text = description_to_markdown(parent.content or parent.text)
                    if text:
                        parent_context["text"] = text[:500]
        except Exception:
            pass

        results = []
        for c in page:
            entry = {
                "id": str(c.id),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }

            if c.user:
                entry["author"] = c.user.username

            text = description_to_markdown(c.content or c.text)
            if text:
                entry["text"] = text[:500]

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

        parts.append(
            render_markdown_list(
                results,
                line_fn=_comment_line,
                pagination=page_pagination(page),
                offset=offset,
                noun="comments",
                empty_text="No comments.",
            )
        )
        return truncate_response("\n".join(parts))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def write_comment(
        content_markdown: Annotated[
            str,
            Field(description="Extended markdown (same syntax as create_post)"),
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

        @mentions on private/organization-only assets do not notify the
        mentioned user unless they can already see the parent asset.
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
