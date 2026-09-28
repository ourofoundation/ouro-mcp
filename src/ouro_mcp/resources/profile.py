"""Profile resource — authenticated user context."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.http_auth import current_ouro
from ouro_mcp.utils import dump_json


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://profile",
        name="User Profile",
        description="The authenticated user's profile and organization memberships.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_profile() -> str:
        ouro = current_ouro()

        user = ouro.users.me()
        profile = {
            "id": str(user.user_id),
            "username": user.username,
            "email": ouro.user.email,
            "display_name": user.name,
            "organizations": [
                {
                    "id": str(org.id),
                    "name": org.name,
                    "display_name": org.display_name,
                    "role": org.membership.role if org.membership else None,
                }
                for org in ouro.organizations.list()
            ],
        }

        return dump_json(profile)
