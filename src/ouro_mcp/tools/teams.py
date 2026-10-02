"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Team
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.constants import GLOBAL_ORG_ID
from ouro_mcp.utils import (
    resolve_location,
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


def _org_name(team: Team) -> str | None:
    org = team.organization
    return (org.name or org.display_name) if org else None


def _team_summary(team: Team) -> dict[str, Any]:
    source = resolve_team_policy(team, "source_policy")
    actor = resolve_team_policy(team, "actor_type_policy")
    result = {
        "id": str(team.id),
        "name": team.name,
        "org_id": str(team.org_id or ""),
        "visibility": team.visibility,
        "default_role": team.default_role,
        "source_policy": source,
        "actor_type_policy": actor,
        "join_policy": team.join_policy or "open",
        "agent_can_create": source != "web_only",
    }
    url = team_web_url(name=team.name, org_id=team.org_id, org_name=_org_name(team))
    if url:
        result["url"] = url
    if team.description:
        result["description"] = team.description.text
    if team.user_join_request:
        result["join_request"] = {
            "id": str(team.user_join_request.id),
            "status": team.user_join_request.status,
        }
    return result


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_team(
        name: Annotated[str, Field(description="Slug: lowercase letters, numbers, dashes only")],
        description: Annotated[str, Field(description="Team description (plain text or markdown)")],
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Organization UUID. Omit when the server is pinned to an organization"),
        ] = None,
        visibility: Annotated[
            Optional[str],
            Field(
                description='"public" (anyone can see it) | "organization" (internal members only). '
                "Omit for organization-only inside an organization, public in the global org"
            ),
        ] = None,
        default_role: Annotated[str, Field(description='"read" | "write" | "admin"')] = "write",
        actor_type_policy: Annotated[str, Field(description='"any" | "verified_only" | "agents_only"')] = "any",
        source_policy: Annotated[str, Field(description='"any" | "web_only" | "api_only"')] = "any",
        join_policy: Annotated[str, Field(description='"open" | "request" | "invite_only"')] = "open",
    ) -> str:
        """Create a new team in an organization.

        For external members, team creation is only allowed when the organization
        enables external public team creation, and visibility is "public".
        Public teams follow the organization's public publishing setting, so
        creating one can be refused; everything in an "organization" team stays
        inside the organization.
        join_policy controls membership: open (self-join), request (admin approval),
        or invite_only (admins add members). Reading is unchanged.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        org_id, _ = resolve_location(ouro, org_id, need_team=False)
        if visibility is None:
            visibility = "public" if org_id == GLOBAL_ORG_ID else "organization"
        team = ouro.teams.create(
            name=name,
            org_id=org_id,
            description=content_from_markdown(ouro, description),
            visibility=visibility,
            default_role=default_role,
            actor_type_policy=actor_type_policy,
            source_policy=source_policy,
            join_policy=join_policy,
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
        join_policy: Annotated[Optional[str], Field(description='"open" | "request" | "invite_only"')] = None,
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
            join_policy=join_policy,
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
            if team.organization:
                result["organization_name"] = _org_name(team)
            members = team.members or []
            result["member_count"] = (
                team.member_count if team.member_count is not None else len(members)
            )
            if include_members:
                result["members"] = [
                    {
                        "user_id": str(m.user_id),
                        "role": m.role,
                        "username": m.user.username if m.user else None,
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

            if team.organization:
                entry["organization_name"] = _org_name(team)

            if team.user_membership and not discover:
                entry["role"] = team.user_membership.role

            if team.member_count is not None:
                entry["member_count"] = team.member_count

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
            if row.get("join_policy") and row.get("join_policy") != "open":
                parts.append(f"join: {row['join_policy']}")
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
            page = ouro.teams.unread_preview(
                id=id, offset=max(offset, 0), limit=max(1, min(limit, 50))
            )
            extras.append(f"unread_count: {page.unread_count}")
        else:
            page = ouro.teams.activity(
                id, offset=offset, limit=limit, asset_type=asset_type,
            )

        return truncate_response(
            render_markdown_list(
                [format_search_hit(item) for item in page],
                line_fn=search_hit_line,
                total=page.total,
                has_more=page.has_more,
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
        humans. join_policy further gates membership: 'request' submits a join
        request for admin approval instead of joining immediately; 'invite_only'
        and team bans return an error. Check get_teams(discover=True) before joining.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        if not member:
            ouro.teams.leave(id)
            return dump_json({"success": True, "member": False})
        team = ouro.teams.join(id)
        return dump_json({"success": True, "member": True, "team": _team_summary(team)})
