"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors, truncate_response


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_teams(
        ctx: Context,
        org_id: Optional[str] = None,
        discover: bool = False,
    ) -> str:
        """List teams.

        By default, returns teams you have joined. Set discover=True to browse
        public teams you could join. Use org_id to filter by organization.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if discover:
            teams = ouro.teams.list(org_id=org_id, public_only=True)
        else:
            teams = ouro.teams.list(org_id=org_id, joined=True)

        results = []
        for team in teams:
            entry = {
                "id": str(team.get("id", "")),
                "name": team.get("name"),
                "org_id": str(team.get("org_id", "")),
                "visibility": team.get("visibility"),
                "default_role": team.get("default_role"),
            }
            desc = team.get("description")
            if desc and isinstance(desc, dict):
                entry["description"] = desc.get("text", "")
            elif desc:
                entry["description"] = str(desc)

            org = team.get("organization")
            if org:
                entry["organization_name"] = org.get("name") or org.get("display_name")

            membership = team.get("userMembership")
            if membership and not discover:
                entry["role"] = membership.get("role")

            member_count = team.get("memberCount")
            if member_count is not None:
                entry["member_count"] = member_count

            results.append(entry)

        return json.dumps({
            "teams": results,
            "count": len(results),
            "mode": "discover" if discover else "mine",
        })

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Get detailed information about a specific team, including members and metrics."""
        ouro = ctx.request_context.lifespan_context.ouro

        team = ouro.teams.retrieve(id)

        result = {
            "id": str(team.get("id", "")),
            "name": team.get("name"),
            "org_id": str(team.get("org_id", "")),
            "visibility": team.get("visibility"),
            "default_role": team.get("default_role"),
        }

        desc = team.get("description")
        if desc and isinstance(desc, dict):
            result["description"] = desc.get("text", "")
        elif desc:
            result["description"] = str(desc)

        org = team.get("organization")
        if org:
            result["organization_name"] = org.get("name") or org.get("display_name")

        members = team.get("members", [])
        result["members"] = [
            {
                "user_id": str(m.get("user_id", "")),
                "role": m.get("role"),
                "username": m.get("user", {}).get("username") if m.get("user") else None,
            }
            for m in members
        ]
        result["member_count"] = team.get("memberCount", len(members))

        return json.dumps(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team_activity(
        id: str,
        ctx: Context,
        page: int = 1,
        page_size: int = 20,
        asset_type: Optional[str] = None,
    ) -> str:
        """Browse a team's activity feed. Returns recent assets created in the team.

        Use asset_type to filter (e.g. "post", "dataset", "file", "service").
        """
        ouro = ctx.request_context.lifespan_context.ouro

        response = ouro.teams.activity(
            id,
            page=page,
            page_size=page_size,
            asset_type=asset_type,
        )

        items = response.get("data", [])
        metadata = response.get("metadata", {})

        results = []
        for item in items:
            entry = {
                "id": str(item.get("id", "")),
                "name": item.get("name"),
                "asset_type": item.get("asset_type"),
                "visibility": item.get("visibility"),
                "created_at": item.get("created_at"),
            }
            user = item.get("user")
            if user:
                entry["author"] = user.get("username")
            desc = item.get("description")
            if desc and isinstance(desc, dict):
                entry["description"] = desc.get("text", "")[:200]
            results.append(entry)

        result = json.dumps({
            "activity": results,
            "count": len(results),
            "page": metadata.get("page", page),
            "page_size": metadata.get("pageSize", page_size),
        })

        return truncate_response(result)

    @mcp.tool()
    @handle_ouro_errors
    def join_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Join a team. You must be a member of the team's organization."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.join(id)
        return json.dumps({"success": True, "team": result})

    @mcp.tool()
    @handle_ouro_errors
    def leave_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Leave a team you are currently a member of."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.leave(id)
        return json.dumps({"success": True, "result": result})
