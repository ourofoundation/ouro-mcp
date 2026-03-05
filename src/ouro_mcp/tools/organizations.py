"""Organization tools — list and discover organizations."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_organizations(
        ctx: Context,
        discover: Annotated[bool, Field(description="Browse discoverable orgs you could join")] = False,
    ) -> str:
        """List organizations.

        By default, returns the organizations you belong to with your role and membership info.
        Set discover=True to browse discoverable organizations you could join.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if discover:
            orgs = ouro.organizations.list_discoverable()
        else:
            orgs = ouro.organizations.list()

        results = []
        for org in orgs:
            entry = {
                "id": str(org.get("id", "")),
                "name": org.get("name"),
                "display_name": org.get("display_name"),
                "mission": org.get("mission"),
                "join_policy": org.get("join_policy"),
            }
            if not discover:
                membership = org.get("membership", {})
                if membership:
                    entry["role"] = membership.get("role")
                    entry["membership_type"] = membership.get("membership_type")
            results.append(entry)

        return json.dumps({"results": results})
