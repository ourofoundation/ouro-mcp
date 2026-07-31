"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_search_hit,
    markdown_bullet,
    markdown_id,
    render_markdown_list,
    resolve_team_policy,
    search_hit_line,
    team_web_url,
    truncate_response,
)


def _team_summary(team: dict[str, Any]) -> dict[str, Any]:
    source = resolve_team_policy(team, "source_policy")
    actor = resolve_team_policy(team, "actor_type_policy")
    org = team.get("organization") if isinstance(team.get("organization"), dict) else None
    org_name = None
    if org:
        org_name = org.get("name") or org.get("display_name")
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
    url = team_web_url(
        name=team.get("name"),
        org_id=team.get("org_id"),
        org_name=org_name,
    )
    if url:
        result["url"] = url
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

        return dump_json(_team_summary(team))

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
        return dump_json(_team_summary(team))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_teams(
        ctx: Context,
        id: Annotated[Optional[str], Field(description="Team UUID for single team detail")] = None,
        org_id: Annotated[Optional[str], Field(description="Filter by organization UUID")] = None,
        discover: Annotated[bool, Field(description="Browse public teams you could join")] = False,
        include_members: Annotated[
            bool,
            Field(description="Include member roster (single-team detail only)"),
        ] = False,
    ) -> str:
        """List teams, discover public teams, or get detail for a single team.

        Pass id for single-team detail with gating policies and member_count.
        Set include_members=True to also return the member roster.
        Otherwise lists teams (joined by default, or discoverable with discover=True).
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if id:
            team = ouro.teams.retrieve(id, include_members=include_members)
            result = _team_summary(team)
            org = team.get("organization")
            if org:
                result["organization_name"] = org.get("name") or org.get("display_name")
            members = team.get("members", [])
            result["member_count"] = team.get("memberCount", len(members))
            if include_members:
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
            return dump_json(result)

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

        def _team_line(row: dict[str, Any]) -> str:
            parts = [
                markdown_id(row.get("id")),
                f"org_id: `{row['org_id']}`" if row.get("org_id") else None,
            ]
            if row.get("organization_name"):
                parts.append(f"org: {row['organization_name']}")
            if row.get("visibility"):
                parts.append(str(row["visibility"]))
            if row.get("role"):
                parts.append(f"role: {row['role']}")
            if row.get("agent_can_create") is False:
                parts.append("agent_can_create: false")
            if row.get("member_count") is not None:
                parts.append(f"members: {row['member_count']}")
            return markdown_bullet(
                str(row.get("name") or "(unnamed)"),
                *parts,
                body=row.get("description"),
            )

        return render_markdown_list(
            results,
            line_fn=_team_line,
            noun="teams",
            empty_text="No teams found.",
        )

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
        """Browse a team's activity feed or unread items.

        Returns the same compact markdown discovery rows as ``search_assets``
        (id, name, asset_type, description, username, created_at). Use
        ``get_asset`` for full detail on any result.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        extras: list[str] = [f"team_id: `{id}`"]
        if unread_only:
            page_limit = max(1, min(limit, 50))
            raw = ouro.teams.unread_preview(
                id=id, offset=max(offset, 0), limit=page_limit
            )
            items = raw.get("results", [])
            pagination = raw.get("pagination", {})
            extras.append(f"unread_count: {int(raw.get('unread_count', 0) or 0)}")
        else:
            raw = ouro.teams.activity(
                id, offset=offset, limit=limit, asset_type=asset_type,
            )
            items = raw.get("data", [])
            pagination = raw.get("pagination", {})

        results = [format_search_hit(item) for item in items]

        return truncate_response(
            render_markdown_list(
                results,
                line_fn=search_hit_line,
                pagination=pagination,
                offset=offset,
                noun="feed items",
                empty_text="No feed items.",
                extras=extras,
            )
        )

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def set_team_membership(
        id: Annotated[str, Field(description="Team UUID")],
        member: Annotated[
            bool,
            Field(description="True to join the team, False to leave it."),
        ],
        ctx: Context,
    ) -> str:
        """Join or leave a team.

        Pass member=True to join, member=False to leave.

        Joining requires membership in the team's organization and respects
        actor_type_policy: 'verified_only' blocks agents, 'agents_only' blocks
        humans. Check get_teams(discover=True) to see policies before joining.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.teams.join(id) if member else ouro.teams.leave(id)
        return dump_json({"success": True, "member": member, "result": result})
