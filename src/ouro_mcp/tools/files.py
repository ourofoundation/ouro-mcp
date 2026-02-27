"""File tools — create."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import format_asset_summary, handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    @handle_ouro_errors
    def create_file(
        name: str,
        file_path: str,
        ctx: Context,
        visibility: str = "private",
        description: Optional[str] = None,
    ) -> str:
        """Upload a file from a local path, creating it as an asset on Ouro.

        file_path must be an absolute path to a file on the local filesystem.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            file_path=file_path,
            description=description,
        )

        result = format_asset_summary(file)
        if file.data:
            result["url"] = file.data.url
        if file.metadata and hasattr(file.metadata, "type"):
            result["mime_type"] = file.metadata.type
        if file.metadata and hasattr(file.metadata, "size"):
            result["size"] = file.metadata.size

        return json.dumps(result)
