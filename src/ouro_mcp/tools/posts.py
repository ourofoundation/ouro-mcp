"""Post tools — create and update."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import format_asset_summary, handle_ouro_errors

# Extended markdown guide for the MCP — used in tool docstrings and server instructions.
POST_CONTENT_GUIDE = """
Write post content in **extended markdown** with Ouro-specific syntax:

**User mentions** — use inline code with @username:
  `{@username}`  (e.g. `{@mmoderwell}`)
  Call search_users(query=...) first to find the correct username.

**Embed Ouro assets** — use a fenced code block with language assetComponent:
  ```assetComponent
  {"id": "<uuid>", "assetType": "post"|"file"|"dataset"|"route"|"service", "viewMode": "chart"|"default"}
  ```
  Use search_assets() or get_asset() to find asset IDs. For files and datasets, prefer viewMode "chart"; otherwise "default".

**Standard markdown** — headings (# ## ###), **bold**, *italic*, lists, code blocks, tables, blockquotes, links, images.

**Math** — LaTeX: \\(inline\\) and \\[display\\].
""".strip()


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
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"file"|"dataset"|"post"|"route"|"service","viewMode":"chart"|"default"}``` — use search_assets() or get_asset() for IDs
        - Standard markdown: headings, bold, italic, lists, code blocks, tables, links
        - Math: \\(inline\\) and \\[display\\] LaTeX

        Use org_id and team_id to control where the post is created.
        Call get_organizations() and get_teams() first to find the right location.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = ouro.posts.Content()
        content.from_markdown(content_markdown)

        kwargs = {}
        if org_id is not None:
            kwargs["org_id"] = org_id
        if team_id is not None:
            kwargs["team_id"] = team_id

        post = ouro.posts.create(
            content=content,
            name=name,
            visibility=visibility,
            description=description,
            **kwargs,
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
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"chart"|"default"}```
        - Standard markdown and LaTeX math

        Pass name, visibility, or description to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = None
        if content_markdown is not None:
            content = ouro.posts.Content()
            content.from_markdown(content_markdown)

        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if visibility is not None:
            kwargs["visibility"] = visibility
        if description is not None:
            kwargs["description"] = description

        post = ouro.posts.update(id, content=content, **kwargs)

        return json.dumps(format_asset_summary(post))
