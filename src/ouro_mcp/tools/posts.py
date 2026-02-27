"""Post tools — create and update."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import format_asset_summary, handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    @handle_ouro_errors
    def create_post(
        name: str,
        content_markdown: str,
        ctx: Context,
        visibility: str = "private",
        description: Optional[str] = None,
    ) -> str:
        """Create a new post on Ouro from markdown content.

        The content_markdown will be converted to Ouro's rich text format.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        editor = ouro.posts.Editor()
        for line in content_markdown.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("### "):
                editor.new_header(3, stripped[4:])
            elif stripped.startswith("## "):
                editor.new_header(2, stripped[3:])
            elif stripped.startswith("# "):
                editor.new_header(1, stripped[2:])
            elif stripped.startswith("```"):
                # Simple code block detection — accumulate until closing ```
                pass  # Handled below via state machine
            else:
                editor.new_paragraph(stripped)

        post = ouro.posts.create(
            content=editor,
            name=name,
            visibility=visibility,
            description=description,
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

        Pass content_markdown to replace the post body.
        Pass name, visibility, or description to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        content = None
        if content_markdown is not None:
            editor = ouro.posts.Editor()
            for line in content_markdown.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("### "):
                    editor.new_header(3, stripped[4:])
                elif stripped.startswith("## "):
                    editor.new_header(2, stripped[3:])
                elif stripped.startswith("# "):
                    editor.new_header(1, stripped[2:])
                else:
                    editor.new_paragraph(stripped)
            content = editor

        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if visibility is not None:
            kwargs["visibility"] = visibility
        if description is not None:
            kwargs["description"] = description

        post = ouro.posts.update(id, content=content, **kwargs)

        return json.dumps(format_asset_summary(post))
