"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import content_from_markdown, resolve_team_policy, truncate_response


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
        name: Annotated[str, Field(description="Slug: lowercase letters, numbers, dashes only")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        description: Annotated[str, Field(description="Team description (plain text or markdown)")],
        ctx: Context,
        visibility: Annotated[str, Field(description='"public" | "private"')] = "public",
        default_role: Annotated[str, Field(description='"read" | "write" | "admin"')] = "write",
        actor_type_policy: Annotated[str, Field(description='"any" | "verified_only" | "agents_only"')] = "any",
        source_policy: Annotated[str, Field(description='"any" | "web_only" | "api_only"')] = "any",
    ) -> str:
        """Create a new team in an organization.

        For external members, team creation is only allowed when the organization
        enables external public team creation, and visibility is "public".
        """
        ouro = ctx.request_context.lifespan_context.ouro
        team = ouro.teams.create(
            name=name,
            org_id=org_id,
            description=content_from_markdown(ouro, description),
            visibility=visibility,
            default_role=default_role,
            actor_type_policy=actor_type_policy,
            source_policy=source_policy,
        )

        return json.dumps(_team_summary(team))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_team(
        id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New slug name")] = None,
        description: Annotated[Optional[str], Field(description="New description (plain text or markdown)")] = None,
        visibility: Annotated[Optional[str], Field(description='"public" | "private"')] = None,
        default_role: Annotated[Optional[str], Field(description='"read" | "write" | "admin"')] = None,
        actor_type_policy: Annotated[Optional[str], Field(description='"any" | "verified_only" | "agents_only"')] = None,
        source_policy: Annotated[Optional[str], Field(description='"any" | "web_only" | "api_only"')] = None,
    ) -> str:
        """Update a team's name, description, visibility, default_role, or policy settings."""
        ouro = ctx.request_context.lifespan_context.ouro
        desc_content = content_from_markdown(ouro, description) if description else None
        team = ouro.teams.update(
            id=id,
            name=name,
            description=desc_content,
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
        id: Annotated[Optional[str], Field(description="Team UUID for single team detail")] = None,
        org_id: Annotated[Optional[str], Field(description="Filter by organization UUID")] = None,
        discover: Annotated[bool, Field(description="Browse public teams you could join")] = False,
    ) -> str:
        """List teams, discover public teams, or get detail for a single team.

        Pass id for a single team with members and gating policies.
        Otherwise lists teams (joined by default, or discoverable with discover=True).
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if id:
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

        return json.dumps({"results": results})

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team_feed(
        id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        unread_only: Annotated[bool, Field(description="Only show unread items")] = False,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        asset_type: Annotated[Optional[str], Field(description='"post" | "dataset" | "file" | "service"')] = None,
    ) -> str:
        """Browse a team's activity feed or unread items. Use get_asset() to inspect any result in detail."""
        ouro = ctx.request_context.lifespan_context.ouro

        extra: dict[str, Any] = {}
        if unread_only:
            page_limit = max(1, min(limit, 50))
            raw = ouro.teams.unread_preview(
                id=id, offset=max(offset, 0), limit=page_limit
            )
            items = raw.get("results", [])
            pagination = raw.get("pagination", {})
            extra["unread_count"] = int(raw.get("unread_count", 0) or 0)
        else:
            raw = ouro.teams.activity(
                id, offset=offset, limit=limit, asset_type=asset_type,
            )
            items = raw.get("data", [])
            pagination = raw.get("pagination", {})

        results = []
        for item in items:
            entry: dict[str, Any] = {
                "id": str(item.get("id", "")),
                "name": item.get("name"),
                "asset_type": item.get("asset_type"),
                "visibility": item.get("visibility"),
                "created_at": item.get("created_at"),
            }
            author = item.get("user") or item.get("author")
            if isinstance(author, dict):
                entry["author"] = author.get("username")
            desc = item.get("description")
            if desc and isinstance(desc, dict):
                entry["description"] = desc.get("text", "")[:200]
            results.append(entry)

        payload: dict[str, Any] = {
            "results": results,
            **extra,
            "total": pagination.get("total"),
            "hasMore": pagination.get("hasMore", len(results) == limit),
        }
        return truncate_response(json.dumps(payload))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def join_team(
        id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
    ) -> str:
        """Join a team. Requires membership in the team's organization.

        Respects actor_type_policy: 'verified_only' blocks agents, 'agents_only' blocks humans.
        Check get_teams(discover=True) to see policies before joining.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.join(id)
        return json.dumps({"success": True, "team": result})

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def leave_team(
        id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
    ) -> str:
        """Leave a team you are currently a member of."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.leave(id)
        return json.dumps({"success": True, "result": result})
