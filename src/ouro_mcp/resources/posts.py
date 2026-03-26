"""Post resource — metadata and rendered content."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import format_asset_summary


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://posts/{id}",
        name="Post",
        description="Post asset detail: metadata and markdown content.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_post(id: str, ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro
        post = ouro.posts.retrieve(id)

        result = format_asset_summary(post)
        result["content_text"] = post.content.text if post.content else None

        return json.dumps(result)
