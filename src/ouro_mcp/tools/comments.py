"""Comment tools — list, create, and update."""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Comment
from ouro.utils.content import description_to_markdown
from pydantic import Field

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.markdown_input import (
    content_from_markdown,
)
from ouro_mcp.params import ATTRIBUTION_DESC, Attribution
from ouro_mcp.rendering import (
    clip,
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
    truncate_response,
)
from ouro_mcp.summaries import (
    format_asset_summary,
)
from ouro_mcp.utils import (
    retrieve_without_view,
)

log = logging.getLogger(__name__)

# Comment text in list rows; longer comments end in "…".
COMMENT_TEXT_CHARS = 500


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_comments(
        parent_id: Annotated[str, Field(description="Asset ID for top-level comments, or comment ID for replies")],
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=200, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """List comments on an asset or replies to a comment, oldest first.

        Pass the asset ID (e.g. a post) to get top-level comments, or a
        comment ID to get its replies. The first page also shows the parent.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        page = ouro.comments.list(parent_id, limit=limit, offset=offset)

        # The parent is context for the comments, so skip it when there are none
        parent_context = None
        if offset == 0 and len(page):
            try:
                parent = retrieve_without_view(ouro, parent_id)
                parent_context = {
                    "id": str(parent.id),
                    "asset_type": parent.asset_type,
                    "name": parent.name,
                    "username": parent.user.username if parent.user else None,
                }
                if isinstance(parent, Comment):
                    text = description_to_markdown(parent.content or parent.text, embedded_assets=parent.assets)
                    if text:
                        parent_context["text"] = clip(text, COMMENT_TEXT_CHARS)
            except Exception:
                log.debug("Could not read comment parent %s", parent_id, exc_info=True)

        results = []
        for c in page:
            entry = {
                "id": str(c.id),
                "created_at": c.created_at,
            }

            if c.user:
                entry["author"] = c.user.username

            text = description_to_markdown(c.content or c.text, embedded_assets=c.assets)
            if text:
                entry["text"] = clip(text, COMMENT_TEXT_CHARS)

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

        parts: list[str] = []
        if parent_context:
            by = f"@{parent_context['username']}" if parent_context.get("username") else None
            if parent_context.get("asset_type") == "comment":
                lead = f"Replies to {by or 'a comment'}"
            else:
                lead = f"Comments on {parent_context.get('name') or 'this asset'}"
            parts.append(
                markdown_bullet(
                    lead,
                    markdown_id(parent_context.get("id")),
                    None if parent_context.get("asset_type") == "comment" else by and f"by {by}",
                    kind=None if parent_context.get("asset_type") == "comment" else parent_context.get("asset_type"),
                    body=parent_context.get("text"),
                )[2:]
            )
            parts.append("")

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
        attribution: Annotated[Optional[Attribution], Field(description=ATTRIBUTION_DESC)] = None,
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
