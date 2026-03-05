"""File tools — create and update."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import elicit_asset_location, file_result, optional_kwargs


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    async def create_file(
        name: Annotated[str, Field(description="File asset name")],
        file_path: Annotated[str, Field(description="Absolute local filesystem path to upload")],
        ctx: Context,
        visibility: Annotated[str, Field(description='"public" | "private" | "organization"')] = "private",
        description: Annotated[Optional[str], Field(description="File description")] = None,
        org_id: Annotated[str, Field(description="Organization UUID")] = "",
        team_id: Annotated[str, Field(description="Team UUID")] = "",
    ) -> str:
        """Upload a local file as an asset on Ouro.

        Call get_organizations() and get_teams() first to pick org_id and team_id.
        Only target teams where agent_can_create is true.
        """
        if not org_id or not team_id:
            elicited_org, elicited_team = await elicit_asset_location(ctx)
            org_id = org_id or elicited_org
            team_id = team_id or elicited_team

        ouro = ctx.request_context.lifespan_context.ouro

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            file_path=file_path,
            description=description,
            **optional_kwargs(org_id=org_id or None, team_id=team_id or None),
        )

        return json.dumps(file_result(file))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_file(
        id: Annotated[str, Field(description="File asset UUID")],
        ctx: Context,
        file_path: Annotated[Optional[str], Field(description="Local path to replacement file")] = None,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        description: Annotated[Optional[str], Field(description="New description")] = None,
        visibility: Annotated[Optional[str], Field(description='"public" | "private" | "organization"')] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
    ) -> str:
        """Update a file's content or metadata. Pass file_path to replace the file data."""
        ouro = ctx.request_context.lifespan_context.ouro

        file = ouro.files.update(
            id,
            file_path=file_path,
            **optional_kwargs(
                name=name,
                description=description,
                visibility=visibility,
                org_id=org_id,
                team_id=team_id,
            ),
        )

        return json.dumps(file_result(file))
