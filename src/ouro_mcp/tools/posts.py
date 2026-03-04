"""Post tools — create and update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import content_from_markdown, elicit_asset_location, format_asset_summary, optional_kwargs


def _resolve_post_markdown(
    content_markdown: Optional[str],
    content_path: Optional[str],
) -> Optional[str]:
    provided = [
        ("content_markdown", content_markdown is not None),
        ("content_path", content_path is not None),
    ]
    selected = [name for name, is_set in provided if is_set]
    if len(selected) > 1:
        raise ValueError(
            f"Provide only one of content_markdown or content_path (got: {', '.join(selected)})."
        )

    if content_path is None:
        return content_markdown

    path = Path(content_path).expanduser()
    if not path.exists():
        raise ValueError(f"content_path not found: {content_path}")
    if not path.is_file():
        raise ValueError(f"content_path must point to a file: {content_path}")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("content_path must be a .md or .markdown file.")

    return path.read_text(encoding="utf-8")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    async def create_post(
        name: str,
        ctx: Context,
        content_markdown: Optional[str] = None,
        content_path: Optional[str] = None,
        visibility: str = "private",
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Create a new post on Ouro from extended markdown.

        Supported post body inputs (choose one):
        - content_markdown: markdown string
        - content_path: local .md/.markdown file path

        Markdown is converted via Ouro's from-markdown API, which supports:
        - User mentions: @username
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"file"|"dataset"|"post"|"route"|"service","viewMode":"preview"|"card"}``` — use search_assets() or get_asset() for IDs
        - Standard markdown: headings, bold, italic, lists, code blocks, tables, links
        - Math: \\(inline\\) and \\[display\\] LaTeX

        Use org_id and team_id to control where the post is created.
        Call get_organizations() and get_teams() first to find the right location.

        Teams with source_policy='web_only' block creation via API/MCP. Check
        get_teams() first — only target teams where agent_can_create is true.
        """
        if not org_id or not team_id:
            elicited_org, elicited_team = await elicit_asset_location(ctx)
            org_id = org_id or elicited_org
            team_id = team_id or elicited_team

        ouro = ctx.request_context.lifespan_context.ouro

        markdown = _resolve_post_markdown(
            content_markdown=content_markdown,
            content_path=content_path,
        )
        if markdown is None:
            raise ValueError(
                "No post body provided. Pass one of: content_markdown or content_path."
            )

        content = content_from_markdown(ouro, markdown)

        post = ouro.posts.create(
            content=content,
            name=name,
            visibility=visibility,
            description=description,
            **optional_kwargs(org_id=org_id, team_id=team_id),
        )

        return json.dumps(format_asset_summary(post))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_post(
        id: str,
        ctx: Context,
        name: Optional[str] = None,
        content_markdown: Optional[str] = None,
        content_path: Optional[str] = None,
        visibility: Optional[str] = None,
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Update a post's content or metadata.

        Pass content_markdown/content_path to replace the post body. Supports extended markdown:
        - User mentions: @username
        - Asset embeds: ```assetComponent\\n{"id":"<uuid>","assetType":"...","viewMode":"preview"|"card"}```
        - Standard markdown and LaTeX math

        Pass name, visibility, description, org_id, or team_id to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        markdown = _resolve_post_markdown(
            content_markdown=content_markdown,
            content_path=content_path,
        )
        content = content_from_markdown(ouro, markdown) if markdown is not None else None

        post = ouro.posts.update(
            id,
            content=content,
            **optional_kwargs(
                name=name,
                visibility=visibility,
                description=description,
                org_id=org_id,
                team_id=team_id,
            ),
        )

        return json.dumps(format_asset_summary(post))
