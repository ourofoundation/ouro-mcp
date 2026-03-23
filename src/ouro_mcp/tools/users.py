"""User search and lookup tools — tools/users.py"""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_me(ctx: Context) -> str:
        """Get the authenticated user's own profile (user ID, username, email)."""
        ouro = ctx.request_context.lifespan_context.ouro
        profile = ouro.users.me() or {}
        auth_user = ouro.user

        return json.dumps(
            {
                "id": str(profile.get("user_id", getattr(auth_user, "id", "?"))),
                "username": profile.get("username"),
                "email": profile.get("email") or getattr(auth_user, "email", None),
                "display_name": profile.get("display_name"),
                "bio": profile.get("bio"),
                "is_agent": profile.get("is_agent"),
            }
        )

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
