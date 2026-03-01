"""Post tools — create and update."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import content_from_markdown, format_asset_summary, optional_kwargs


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    @handle_ouro_errors
    def create_post(
        name: str,
        content_markdown: str,
        ctx: Context,
        visibility: str = "private",
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Create a new post on Ouro from extended markdown.

        content_markdown is converted via Ouro's from-markdown API, which supports:
        - User mentions: `{@username}` — call search_users() first to get usernames
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"file"|"dataset"|"post"|"route"|"service","viewMode":"preview"|"card"}``` — use search_assets() or get_asset() for IDs
        - Standard markdown: headings, bold, italic, lists, code blocks, tables, links
        - Math: \\(inline\\) and \\[display\\] LaTeX

        Use org_id and team_id to control where the post is created.
        Call get_organizations() and get_teams() first to find the right location.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = content_from_markdown(ouro, content_markdown)

        post = ouro.posts.create(
            content=content,
            name=name,
            visibility=visibility,
            description=description,
            **optional_kwargs(org_id=org_id, team_id=team_id),
        )

        return json.dumps(format_asset_summary(post))

    @mcp.tool()
    @handle_ouro_errors
    def update_post(
        id: str,
        ctx: Context,
        name: Optional[str] = None,
        content_markdown: Optional[str] = None,
        visibility: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """Update a post's content or metadata.

        Pass content_markdown to replace the post body. Supports extended markdown:
        - User mentions: `{@username}` — call search_users() for usernames
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"preview"|"card"}```
        - Standard markdown and LaTeX math

        Pass name, visibility, or description to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = (
            content_from_markdown(ouro, content_markdown)
            if content_markdown is not None
            else None
        )

        post = ouro.posts.update(
            id,
            content=content,
            **optional_kwargs(name=name, visibility=visibility, description=description),
        )

        return json.dumps(format_asset_summary(post))
