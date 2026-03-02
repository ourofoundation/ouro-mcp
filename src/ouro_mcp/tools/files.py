"""File tools — create and update."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import file_result, optional_kwargs


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
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

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            file_path=file_path,
            description=description,
            **optional_kwargs(org_id=org_id, team_id=team_id),
        )

        return json.dumps(file_result(file))

    @mcp.tool(annotations={"idempotentHint": True})
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

        file = ouro.files.update(
            id,
            file_path=file_path,
            **optional_kwargs(name=name, description=description, visibility=visibility),
        )

        return json.dumps(file_result(file))
