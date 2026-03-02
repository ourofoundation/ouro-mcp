from __future__ import annotations

import json
import logging
from typing import Any

from mcp import types as mcp_types
from mcp.server.fastmcp import Context

from ouro_mcp.constants import MAX_RESPONSE_SIZE

log = logging.getLogger(__name__)


def truncate_response(data: str, context: str = "") -> str:
    """If a JSON response exceeds the size threshold, truncate and flag it."""
    if len(data) <= MAX_RESPONSE_SIZE:
        return data
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "rows" in parsed:
            # Progressively remove rows until under limit
            rows = parsed["rows"]
            while len(json.dumps(parsed)) > MAX_RESPONSE_SIZE and rows:
                rows.pop()
            parsed["count"] = len(rows)
            parsed["truncated"] = True
            if context:
                parsed["note"] = f"Response truncated to fit context window. {context}"
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return data[:MAX_RESPONSE_SIZE] + "\n... [truncated]"


def format_asset_summary(asset: Any) -> dict:
    """Extract a consistent summary dict from any ouro-py asset model."""
    from ouro.utils.content import description_to_markdown

    summary = {
        "id": str(asset.id),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "visibility": asset.visibility,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "last_updated": asset.last_updated.isoformat() if asset.last_updated else None,
    }
    if asset.description:
        summary["description"] = description_to_markdown(asset.description, max_length=500)
    if asset.user:
        summary["owner"] = asset.user.username
    return summary


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


def content_from_markdown(ouro: Any, markdown: str) -> Any:
    """Create a Content object from markdown using the Ouro client."""
    content = ouro.posts.Content()
    content.from_markdown(markdown)
    return content


def file_result(file: Any) -> dict:
    """Build a standard result dict for a file asset, including data URL and metadata."""
    result = format_asset_summary(file)
    if file.data:
        result["url"] = file.data.url
    if file.metadata and hasattr(file.metadata, "type"):
        result["mime_type"] = file.metadata.type
    if file.metadata and hasattr(file.metadata, "size"):
        result["size"] = file.metadata.size
    return result


def resolve_team_policy(team: dict, field: str, default: str = "any") -> str:
    """Return the effective policy for a team, falling back to the org's policy."""
    value = team.get(field)
    if value:
        return value
    org = team.get("organization") or {}
    return org.get(field) or default


_ELICITATION_CAP = mcp_types.ClientCapabilities(
    elicitation=mcp_types.ElicitationCapability()
)


async def elicit_asset_location(
    ctx: Context,
) -> tuple[str | None, str | None]:
    """Ask the user where an asset should be published, if the client supports elicitation.

    Returns (org_id, team_id). Both are None when elicitation is unavailable,
    the user declines, or there are no teams to choose from.
    """
    if not ctx.session.check_client_capability(_ELICITATION_CAP):
        return None, None

    ouro = ctx.request_context.lifespan_context.ouro

    try:
        teams = ouro.teams.list(joined=True)
    except Exception:
        log.debug("Failed to fetch teams for elicitation", exc_info=True)
        return None, None

    if not teams:
        return None, None

    teams = [t for t in teams if resolve_team_policy(t, "source_policy") != "web_only"]

    if not teams:
        return None, None

    if len(teams) == 1:
        team = teams[0]
        return str(team.get("org_id", "")), str(team.get("id", ""))

    team_to_org: dict[str, str] = {}
    options = []
    for team in teams:
        team_id = str(team.get("id", ""))
        org_id = str(team.get("org_id", ""))
        team_name = team.get("name", "Unknown")
        org = team.get("organization") or {}
        org_name = org.get("name") or org.get("display_name") or "Unknown Org"

        options.append({"const": team_id, "title": f"{org_name} / {team_name}"})
        team_to_org[team_id] = org_id

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "team": {
                "type": "string",
                "title": "Team",
                "description": "Choose which team to publish this asset in",
                "oneOf": options,
            },
        },
        "required": ["team"],
    }

    result = await ctx.session.elicit_form(
        message="Where should this asset be published?",
        requestedSchema=schema,
        related_request_id=ctx.request_id,
    )

    if result.action == "accept" and result.content:
        selected_team_id = str(result.content.get("team", ""))
        selected_org_id = team_to_org.get(selected_team_id)
        if selected_org_id and selected_team_id:
            return selected_org_id, selected_team_id

    return None, None
