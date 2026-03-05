"""User search and lookup tools — tools/users.py"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_users(
        query: Annotated[str, Field(description="Name or username to search for")],
        ctx: Context,
    ) -> str:
        """Search for users on Ouro by name or username."""
        ouro = ctx.request_context.lifespan_context.ouro
        results = ouro.users.search(query)

        users = []
        for u in results:
            users.append(
                {
                    "user_id": str(u.get("user_id", u.get("id", ""))),
                    "username": u.get("username"),
                }
            )

        return json.dumps(users)
