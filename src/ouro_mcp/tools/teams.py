"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

import json
from typing import Any, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import resolve_team_policy, truncate_response


def _team_summary(team: dict[str, Any]) -> dict[str, Any]:
    source = resolve_team_policy(team, "source_policy")
    actor = resolve_team_policy(team, "actor_type_policy")
    result = {
        "id": str(team.get("id", "")),
        "name": team.get("name"),
        "org_id": str(team.get("org_id", "")),
        "visibility": team.get("visibility"),
        "default_role": team.get("default_role"),
        "source_policy": source,
        "actor_type_policy": actor,
        "agent_can_create": source != "web_only",
    }
    desc = team.get("description")
    if desc and isinstance(desc, dict):
        result["description"] = desc.get("text", "")
    elif desc:
        result["description"] = str(desc)
    return result


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_team(
        name: str,
        org_id: str,
        ctx: Context,
        description: Union[str, dict],
        visibility: str = "public",
        default_role: str = "write",
        actor_type_policy: str = "any",
        source_policy: str = "any",
    ) -> str:
        """Create a new team in an organization.

        Call get_organizations() first to pick org_id.

        Description is required and supports:
        - markdown string (recommended): backend converts markdown to rich content
        - structured content JSON object (advanced)

        Important constraints:
        - name must be a slug using only lowercase letters, numbers, and dashes.
          Example: "research-lab-1".
        - For external members, team creation is only allowed when the organization
          enables external public team creation, and visibility is "public".
        """
        ouro = ctx.request_context.lifespan_context.ouro
        team = ouro.teams.create(
            name=name,
            org_id=org_id,
            description=description,
            visibility=visibility,
            default_role=default_role,
            actor_type_policy=actor_type_policy,
            source_policy=source_policy,
        )

        return json.dumps(_team_summary(team))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_team(
        id: str,
        ctx: Context,
        name: Optional[str] = None,
        description: Optional[Union[str, dict]] = None,
        visibility: Optional[str] = None,
        default_role: Optional[str] = None,
        actor_type_policy: Optional[str] = None,
        source_policy: Optional[str] = None,
    ) -> str:
        """Update a team.

        You can update name, visibility, default_role, and policy settings.
        Description supports either a markdown string or a structured content JSON object.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        team = ouro.teams.update(
            id=id,
            name=name,
            description=description,
            visibility=visibility,
            default_role=default_role,
            actor_type_policy=actor_type_policy,
            source_policy=source_policy,
        )
        return json.dumps(_team_summary(team))

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

        Each team includes resolved gating policies:
        - source_policy ('any' | 'web_only' | 'api_only'): controls how assets
          are created. MCP counts as API, so 'web_only' blocks this tool.
        - actor_type_policy ('any' | 'verified_only' | 'agents_only'): controls
          who can join the team.
        - agent_can_create: False when source_policy is 'web_only'.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if discover:
            teams = ouro.teams.list(org_id=org_id, public_only=True)
        else:
            teams = ouro.teams.list(org_id=org_id, joined=True)

        results = []
        for team in teams:
            entry = _team_summary(team)

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

        return json.dumps(
            {
                "results": results,
                "count": len(results),
                "mode": "discover" if discover else "mine",
            }
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Get detailed information about a specific team, including members, metrics, and gating policies.

        Gating policies (always resolved, never null):
        - source_policy ('any' | 'web_only' | 'api_only'): controls how assets
          are created. MCP counts as API, so 'web_only' blocks this tool.
        - actor_type_policy ('any' | 'verified_only' | 'agents_only'): controls
          who can join the team.
        - agent_can_create: False when source_policy is 'web_only'.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        team = ouro.teams.retrieve(id)

        result = _team_summary(team)

        org = team.get("organization")
        if org:
            result["organization_name"] = org.get("name") or org.get("display_name")

        members = team.get("members", [])
        result["members"] = [
            {
                "user_id": str(m.get("user_id", "")),
                "role": m.get("role"),
                "username": (
                    m.get("user", {}).get("username") if m.get("user") else None
                ),
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
        offset: int = 0,
        limit: int = 20,
        asset_type: Optional[str] = None,
    ) -> str:
        """Browse a team's activity feed. Returns recent assets created in the team.

        Use asset_type to filter (e.g. "post", "dataset", "file", "service").
        """
        ouro = ctx.request_context.lifespan_context.ouro

        response = ouro.teams.activity(
            id,
            offset=offset,
            limit=limit,
            asset_type=asset_type,
        )

        items = response.get("data", [])
        pagination = response.get("pagination", {})

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

        result = json.dumps(
            {
                "results": results,
                "count": len(results),
                "pagination": {
                    "offset": pagination.get("offset", offset),
                    "limit": pagination.get("limit", limit),
                    "hasMore": pagination.get("hasMore", len(results) == limit),
                    "total": pagination.get("total"),
                },
            }
        )

        return truncate_response(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team_unreads(
        id: str,
        ctx: Context,
        offset: int = 0,
        limit: int = 5,
    ) -> str:
        """Get paginated unread asset previews for one team.

        This is designed as a quick "what's going on?" view for agents.
        Use get_asset(asset_id) to inspect any item in full depth.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        team = ouro.teams.retrieve(id)
        page_limit = max(1, min(limit, 50))
        preview = ouro.teams.unread_preview(
            id=id, offset=max(offset, 0), limit=page_limit
        )
        unread_count = int(preview.get("unread_count", 0) or 0)
        pagination = preview.get("pagination", {})

        results = []
        for item in preview.get("results", []):
            entry = {
                "id": str(item.get("id", "")),
                "name": item.get("name"),
                "created_at": item.get("created_at"),
                "visibility": item.get("visibility"),
            }

            author = item.get("author")
            if author:
                entry["author"] = author.get("username")

            desc = item.get("description")
            if desc and isinstance(desc, dict):
                entry["description"] = desc.get("text", "")[:200]

            results.append(entry)

        payload = {
            "results": results,
            "count": len(results),
            "pagination": {
                "offset": pagination.get("offset", max(offset, 0)),
                "limit": pagination.get("limit", page_limit),
                "hasMore": pagination.get("hasMore", False),
                "total": pagination.get("total", unread_count),
            },
        }

        return truncate_response(json.dumps(payload))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def join_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Join a team. You must be a member of the team's organization.

        Teams with actor_type_policy='verified_only' only allow verified humans.
        Teams with actor_type_policy='agents_only' only allow agent accounts.
        Check get_teams(discover=True) to see policies before joining.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.join(id)
        return json.dumps({"success": True, "team": result})

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def leave_team(
        id: str,
        ctx: Context,
    ) -> str:
        """Leave a team you are currently a member of."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.leave(id)
        return json.dumps({"success": True, "result": result})
