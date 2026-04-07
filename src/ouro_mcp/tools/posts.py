"""Post tools — create and update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
    optional_kwargs,
    resolve_local_path,
)
from pydantic import Field


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
        raise ValueError(f"Provide only one of content_markdown or content_path (got: {', '.join(selected)}).")

    if content_path is None:
        return content_markdown

    path = resolve_local_path(content_path)
    if not path.exists():
        raise ValueError(f"content_path not found: {content_path} (resolved to {path})")
    if not path.is_file():
        raise ValueError(f"content_path must point to a file: {content_path} (resolved to {path})")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("content_path must be a .md or .markdown file.")

    return path.read_text(encoding="utf-8")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_post(
        name: Annotated[str, Field(description="Post title")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        content_markdown: Annotated[
            Optional[str],
            Field(
                description=(
                    "Extended markdown body. Supports @mentions, LaTeX ($inline$, "
                    "$$display$$), "
                    "asset link shorthands [text](asset:<uuid>) or [text](post:|file:|dataset:|route:|service:<uuid>) "
                    "(resolved server-side), exact urls from tool results, "
                    "and block-level asset embeds via ```assetComponent``` using "
                    '{"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}.'
                )
            ),
        ] = None,
        content_path: Annotated[Optional[str], Field(description="Local .md/.markdown file path")] = None,
        visibility: Annotated[str, Field(description='"public" | "private" | "organization"')] = "public",
        description: Annotated[Optional[str], Field(description="Short description/subtitle")] = None,
    ) -> str:
        """Create a new post on Ouro from extended markdown. Provide content_markdown or content_path.

        Asset references:
        - Inline links: [label](asset:<uuid>) or [label](post:|file:|dataset:|route:|service:<uuid>); or paste the exact `url` from a tool response. Do not use placeholder URL segments such as `entity`.
        For embedded assets, use:
        ```assetComponent
        {"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}
        ```
        """
        ouro = ctx.request_context.lifespan_context.ouro

        markdown = _resolve_post_markdown(
            content_markdown=content_markdown,
            content_path=content_path,
        )
        if markdown is None:
            raise ValueError("No post body provided. Pass one of: content_markdown or content_path.")

        content = content_from_markdown(ouro, markdown)

        post = ouro.posts.create(
            content=content,
            name=name,
            visibility=visibility,
            description=description,
            org_id=org_id,
            team_id=team_id,
        )

        return dump_json(format_asset_summary(post))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_post(
        id: Annotated[str, Field(description="Post UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New title")] = None,
        content_markdown: Annotated[
            Optional[str],
            Field(
                description=(
                    "Replacement extended markdown body. Supports @mentions, LaTeX ($inline$, "
                    "$$display$$), "
                    "asset link shorthands [text](asset:<uuid>) or [text](post:|file:|dataset:|route:|service:<uuid>) "
                    "(resolved server-side), exact urls from tool results, "
                    "and block-level asset embeds via ```assetComponent``` using "
                    '{"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}.'
                )
            ),
        ] = None,
        content_path: Annotated[
            Optional[str],
            Field(description="Local .md/.markdown file with replacement body"),
        ] = None,
        visibility: Annotated[Optional[str], Field(description='"public" | "private" | "organization"')] = None,
        description: Annotated[Optional[str], Field(description="New description/subtitle")] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
    ) -> str:
        """Update a post's content or metadata. Pass content_markdown/content_path to replace the body.

        Inline links: [label](asset:<uuid>) or typed post:/file:/…; or exact `url` from tools — not placeholder paths.

        For embedded assets, use:
        ```assetComponent
        {"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}
        ```
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

        return dump_json(format_asset_summary(post))
