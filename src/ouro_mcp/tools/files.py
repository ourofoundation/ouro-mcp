"""File tools — create and update."""

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
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Upload a file from a local path, creating it as an asset on Ouro.

        file_path must be an absolute path to a file on the local filesystem.
        Use org_id and team_id to control where the file is created.
        Call get_organizations() and get_teams() first to find the right location.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        kwargs = {}
        if org_id is not None:
            kwargs["org_id"] = org_id
        if team_id is not None:
            kwargs["team_id"] = team_id

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            file_path=file_path,
            description=description,
            **kwargs,
        )

        result = format_asset_summary(file)
        if file.data:
            result["url"] = file.data.url
        if file.metadata and hasattr(file.metadata, "type"):
            result["mime_type"] = file.metadata.type
        if file.metadata and hasattr(file.metadata, "size"):
            result["size"] = file.metadata.size

        return json.dumps(result)

    @mcp.tool()
    @handle_ouro_errors
    def update_file(
        id: str,
        ctx: Context,
        file_path: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> str:
        """Update a file's content or metadata.

        Pass file_path to replace the file data with a new file from the local filesystem.
        Pass name, description, or visibility to update metadata.
        Requires admin or write permission on the file.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if visibility is not None:
            kwargs["visibility"] = visibility

        file = ouro.files.update(id, file_path=file_path, **kwargs)

        result = format_asset_summary(file)
        if file.data:
            result["url"] = file.data.url
        if file.metadata and hasattr(file.metadata, "type"):
            result["mime_type"] = file.metadata.type
        if file.metadata and hasattr(file.metadata, "size"):
            result["size"] = file.metadata.size

        return json.dumps(result)
