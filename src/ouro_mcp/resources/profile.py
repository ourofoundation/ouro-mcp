"""Profile resource — authenticated user context."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://profile",
        name="User Profile",
        description="The authenticated user's profile and organization memberships.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_profile(ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro

        user_profile = ouro.users.me() or {}
        auth_user = ouro.user
        profile = {
            "id": str(user_profile.get("user_id", getattr(auth_user, "id", "?"))),
            "username": user_profile.get("username"),
            "email": user_profile.get("email") or getattr(auth_user, "email", None),
            "display_name": user_profile.get("display_name"),
        }

        orgs = ouro.organizations.list()
        profile["organizations"] = [
            {
                "id": str(org.get("id", "")),
                "name": org.get("name"),
                "display_name": org.get("display_name"),
                "role": (org.get("membership") or {}).get("role"),
            }
            for org in orgs
        ]

        return json.dumps(profile)
