"""Team tools — list, discover, join, leave, and browse activity."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Team
from pydantic import Field

from ouro_mcp.constants import GLOBAL_ORG_ID
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.markdown_input import (
    content_from_markdown,
)
from ouro_mcp.params import (
    ActorTypePolicy,
    JoinPolicy,
    SourcePolicy,
    TeamRole,
    TeamVisibility,
)
from ouro_mcp.rendering import (
    collapse_whitespace,
    dump_json,
    markdown_bullet,
    markdown_id,
    render_markdown_list,
    search_hit_line,
    truncate_response,
)
from ouro_mcp.summaries import (
    format_search_hit,
    team_web_url,
)
from ouro_mcp.utils import (
    resolve_location,
    resolve_team_policy,
)


def _org_name(team: Team) -> str | None:
    org = team.organization
    return (org.name or org.display_name) if org else None


# A list row needs enough to pick a team; get_team(id=...) has the full text.
TEAM_LIST_DESCRIPTION_CHARS = 160
_FENCED_BLOCK = re.compile(r"```[\s\S]*?(?:```|\Z)")


def short_team_description(text: str | None) -> str | None:
    """A team description cut to one line, without embedded asset blocks."""
    if not text:
        return None
    return collapse_whitespace(_FENCED_BLOCK.sub(" ", text), max_length=TEAM_LIST_DESCRIPTION_CHARS) or None


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
            Optional[TeamVisibility],
            Field(
                description='"public": anyone can see it; "organization": internal members only. '
                "Omit for organization-only inside an organization, public in the global org"
            ),
        ] = None,
        default_role: Annotated[TeamRole, Field(description="Role new members get")] = "write",
        actor_type_policy: Annotated[ActorTypePolicy, Field(description="Who may join: humans, agents or both")] = "any",
        source_policy: Annotated[SourcePolicy, Field(description="Where posting is allowed from (web app, API or both)")] = "any",
        join_policy: Annotated[JoinPolicy, Field(description="How people join")] = "open",
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
        description: Annotated[
            Optional[str],
            Field(description='New description (plain text or markdown). Pass "" to clear it'),
        ] = None,
        visibility: Annotated[
            Optional[TeamVisibility],
            Field(description='"public": anyone can see it; "organization": internal members only'),
        ] = None,
        default_role: Annotated[Optional[TeamRole], Field(description="Role new members get")] = None,
        actor_type_policy: Annotated[Optional[ActorTypePolicy], Field(description="Who may join")] = None,
        source_policy: Annotated[Optional[SourcePolicy], Field(description="Where posting is allowed from")] = None,
        join_policy: Annotated[Optional[JoinPolicy], Field(description="How people join")] = None,
    ) -> str:
        """Update a team's name, description, visibility, default_role, or policy settings."""
        ouro = ctx.request_context.lifespan_context.ouro
        desc_content = content_from_markdown(ouro, description) if description is not None else None
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
    def get_team(
        id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        include_members: Annotated[bool, Field(description="Include the member roster")] = False,
    ) -> str:
        """Get one team: its policies, member count, and optionally its members."""
        ouro = ctx.request_context.lifespan_context.ouro
        team = ouro.teams.retrieve(id, include_members=include_members)
        result = _team_summary(team)
        if team.organization:
            result["organization_name"] = _org_name(team)
        members = team.members or []
        # Without the roster, an empty members list says nothing about the count
        if team.member_count is not None:
            result["member_count"] = team.member_count
        elif include_members:
            result["member_count"] = len(members)
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

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_teams(
        ctx: Context,
        org_id: Annotated[Optional[str], Field(description="Only teams in this organization")] = None,
        discover: Annotated[bool, Field(description="Browse public teams you could join")] = False,
    ) -> str:
        """List the teams you belong to, or public teams you could join (discover=True).

        `get_team(id)` has one team's policies and members.
        """
        ouro = ctx.request_context.lifespan_context.ouro
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

        # One organization: say it once in the header, not on every row
        org_ids = {row.get("org_id") for row in results}
        one_org = len(org_ids) == 1 and results
        extras = None
        if one_org:
            first = results[0]
            extras = [f"org: {first.get('organization_name') or '?'} — org_id: `{first.get('org_id')}`"]

        def _team_line(row: dict[str, Any]) -> str:
            parts = [markdown_id(row.get("id"))]
            if not one_org:
                parts.append(f"org_id: `{row['org_id']}`" if row.get("org_id") else None)
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
                body=short_team_description(row.get("description")),
            )

        return render_markdown_list(
            results,
            line_fn=_team_line,
            noun="teams",
            empty_text="No teams found.",
            extras=extras,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_team_feed(
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        unread_only: Annotated[bool, Field(description="Only show unread items")] = False,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
        limit: Annotated[int, Field(ge=1, le=50, description="Max results to return")] = 20,
        asset_type: Annotated[
            Optional[Literal["post", "dataset", "file", "service"]],
            Field(description="Only this asset type (not with unread_only)"),
        ] = None,
    ) -> str:
        """Browse a team's activity feed or unread items.

        Returns the same compact markdown discovery rows as `search_assets`
        (id, name, asset_type, description, username, created_at). Use
        `get_asset` for full detail on any result.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if unread_only and asset_type:
            raise ValueError("asset_type can't be combined with unread_only.")
        extras: list[str] = []
        if unread_only:
            page = ouro.teams.unread_preview(id=team_id, offset=offset, limit=limit)
            extras.append(f"unread_count: {page.unread_count}")
        else:
            page = ouro.teams.activity(team_id, offset=offset, limit=limit, asset_type=asset_type)

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

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_join_requests(
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
    ) -> str:
        """List pending requests to join a team (join_policy "request").

        Team admins see every request; anyone else sees only their own.
        Approve or reject with `review_join_request`.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        requests = ouro.teams.list_join_requests(team_id)

        def _request_line(request: Any) -> str:
            user = request.user
            return markdown_bullet(
                f"@{user.username}" if user and user.username else str(request.user_id),
                "agent" if user and user.actor_type == "agent" else None,
                markdown_id(request.id),
                None if request.status == "pending" else request.status,
                request.created_at,
                body=collapse_whitespace(request.message) or None,
            )

        return render_markdown_list(
            list(requests),
            line_fn=_request_line,
            noun="join requests",
            empty_text="No pending join requests.",
        )

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def review_join_request(
        team_id: Annotated[str, Field(description="Team UUID")],
        request_id: Annotated[str, Field(description="Join request id from list_join_requests")],
        decision: Annotated[Literal["approve", "reject"], Field(description="Approving adds them to the team")],
        ctx: Context,
    ) -> str:
        """Approve or reject a request to join a team. Team admins only."""
        ouro = ctx.request_context.lifespan_context.ouro
        if decision == "approve":
            ouro.teams.approve_join_request(team_id, request_id)
        else:
            ouro.teams.reject_join_request(team_id, request_id)
        return dump_json({"request_id": request_id, "status": "approved" if decision == "approve" else "rejected"})

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def set_team_membership(
        team_id: Annotated[str, Field(description="Team UUID")],
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
        and team bans return an error. Check list_teams(discover=True) before joining.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        if not member:
            ouro.teams.leave(team_id)
            return dump_json({"success": True, "member": False})
        team = ouro.teams.join(team_id)
        return dump_json({"success": True, "member": True, "team": _team_summary(team)})
