"""Slim agent-facing summaries of Ouro assets, users, teams, tags and connections."""

from __future__ import annotations

import os
from typing import Any, Iterable

from ouro.models import AssetRef, AssetTag, Connection, DatasetColumn

from ouro_mcp.constants import (
    DEFAULT_OURO_FRONTEND_URL,
    ENV_OURO_FRONTEND_URL,
    GLOBAL_ORG_ID,
)
from ouro_mcp.pricing import (
    format_monetization_block,
)
from ouro_mcp.rendering import (
    collapse_whitespace,
)
from ouro_mcp.utils import (
    as_dict,
    getv,
    optional_kwargs,
)

_HEAVY_RESPONSE_KEYS = frozenset({"embedding", "fts"})

# Asset descriptions in summaries and list rows; get_asset(detail="full") has the rest.
DESCRIPTION_CHARS = 200


def short_description(value: Any, *, max_length: int = DESCRIPTION_CHARS) -> str | None:
    """An asset description (any shape) as one line, cut with "…"."""
    from ouro.utils.content import description_to_markdown

    return collapse_whitespace(description_to_markdown(value), max_length=max_length) or None


def strip_heavy_fields(value: Any) -> Any:
    """Recursively drop vector/search fields that waste agent context.

    Tag catalogue rows and nested ``tag:tags(*)`` joins include 768-dim
    ``embedding`` vectors (and generated ``fts``). Those are for search
    indexing only — agents never need them in tool responses.
    """
    if isinstance(value, list):
        return [strip_heavy_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in _HEAVY_RESPONSE_KEYS:
            continue
        cleaned[key] = strip_heavy_fields(item)
    return cleaned


def slim_dataset_schema(columns: list[DatasetColumn]) -> list[dict[str, Any]]:
    """Agent-facing column schema: ``name``/``type`` plus semantic hints.

    Keeps only the declared ``DatasetColumn`` fields, dropping the FK
    plumbing the backend also returns (``fk_constraint_name``,
    ``foreign_table_*``, ``foreign_column_name``). Column names are
    lowercase snake_case. Columns are nullable unless marked
    ``is_nullable: false``.
    """
    fields = set(DatasetColumn.model_fields)
    slim = []
    for column in columns:
        entry = column.model_dump(mode="json", include=fields, exclude_none=True)
        if entry.get("is_nullable"):
            del entry["is_nullable"]
        slim.append(entry)
    return slim


def refs_from_schema(columns: list[DatasetColumn]) -> dict[str, dict[str, Any]]:
    """Reference columns as ``{column: {kind, asset_type?}}``."""
    refs: dict[str, dict[str, Any]] = {}
    for column in columns:
        if column.semantic_type != "reference":
            continue
        kind = column.ref_kind or "asset"
        refs[column.name] = {"kind": kind}
        if kind == "asset" and column.asset_type:
            refs[column.name]["asset_type"] = column.asset_type
    return refs


def enum_columns_from_schema(columns: list[DatasetColumn]) -> dict[str, dict[str, list[str]]]:
    """Enum columns as ``{column: {values}}``."""
    return {
        column.name: {"values": column.enum_values}
        for column in columns
        if column.semantic_type == "enum" and column.enum_values
    }


def slim_asset_tags(tags: list[AssetTag]) -> list[dict[str, Any]] | None:
    """Shrink asset tag rows for MCP to what says what the asset is about.

    Tag ids, descriptions and vectors are catalog detail: the name and type
    already say what the tag means.
    """
    slimmed = [
        optional_kwargs(
            name=row.tag.name,
            type=row.tag.type,
            source=row.source,
            confidence=row.confidence,
        )
        for row in tags
    ]
    return slimmed or None


def _connection_endpoint(
    asset: AssetRef | None, asset_id: Any, asset_type: str | None
) -> dict[str, Any]:
    # `asset_type` is the discriminator agents need to decide which follow-up
    # tool to call — always emit it (possibly null). `name` is display-only
    # and dropped when empty; the backend stores "" for nameless types like
    # comments.
    row: dict[str, Any] = {
        "id": str(asset_id),
        "asset_type": asset.asset_type if asset else asset_type,
    }
    if asset and asset.name:
        row["name"] = asset.name
    elif asset and asset.asset_type == "user" and asset.user:
        row["name"] = f"@{asset.user.username}"
    if asset and asset.created_at:
        row["created_at"] = asset.created_at
    return row


def slim_connection_graph(
    connections: Iterable[Connection],
    current_asset_id: str | None = None,
    *,
    omit_outgoing_references: bool = False,
    omit_comments: bool = False,
    omit_outgoing_types: Iterable[str] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Shrink connection payloads from the Ouro API for MCP tool responses.

    Each edge may include full ``source`` and ``target`` asset records, which
    routinely pushes ``get_asset(detail=\"full\")`` past agent context
    limits even for modest graphs. Group edges by relationship type and keep
    only the connected asset summary: ``id`` and ``asset_type`` always,
    ``name`` when set, and the connected asset's ``created_at`` when known.
    For ``type == "action"`` edges, ``action_id`` is preserved so agents can
    follow up with ``get_action``.

    When ``omit_outgoing_references`` is true (datasets), skip ``reference``
    edges where the current asset is the source. Those edges duplicate IDs
    already stored in dataset ref columns and routinely number in the
    thousands. Incoming references (who points at this asset) are kept.

    ``omit_outgoing_types`` skips outgoing edges of those types. A post's
    outgoing links and embeds are already in its body.

    When ``omit_comments`` is true, skip ``type == "comment"`` edges.
    Comment bodies already ship via the ``comments`` preview on
    ``get_asset(detail=\"full\")`` and via ``list_comments``; keeping the
    connection stubs just duplicates IDs without text.
    """
    current_id = str(current_asset_id) if current_asset_id is not None else None
    skip_outgoing = set(omit_outgoing_types)
    if omit_outgoing_references:
        skip_outgoing.add("reference")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in connections:
        if omit_comments and edge.type == "comment":
            continue

        is_outgoing = str(edge.source_id) == current_id
        if is_outgoing and edge.type in skip_outgoing:
            continue

        if is_outgoing:
            row = _connection_endpoint(edge.target, edge.target_id, edge.target_asset_type)
        else:
            row = _connection_endpoint(edge.source, edge.source_id, edge.source_asset_type)
        if edge.type == "action" and edge.action_id is not None:
            row["action_id"] = str(edge.action_id)

        grouped.setdefault(edge.type, []).append(row)
    return grouped


def frontend_origin() -> str:
    """Public web origin (no trailing slash) for absolute asset/team URLs."""
    raw = (
        os.environ.get(ENV_OURO_FRONTEND_URL)
        or DEFAULT_OURO_FRONTEND_URL
    ).strip()
    return raw.rstrip("/") or DEFAULT_OURO_FRONTEND_URL


def absolute_web_url(path_or_url: str | None) -> str | None:
    """Return an absolute https URL for a site path or already-absolute URL."""
    if not path_or_url:
        return None
    value = str(path_or_url).strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if not value.startswith("/"):
        value = f"/{value}"
    return f"{frontend_origin()}{value}"


def asset_web_url(asset: Any) -> str | None:
    """Public URL for an asset model or search/feed dict.

    The backend already attaches absolute ``url`` (and relative ``slug``) on
    retrieve/search/activity. Prefer ``url``; only fall back to joining
    ``slug`` to the frontend origin when ``url`` is missing.
    """
    if asset is None:
        return None
    return absolute_web_url(getv(asset, "url") or getv(asset, "slug"))


def team_web_url(
    *,
    name: str | None,
    org_id: str | None = None,
    org_name: str | None = None,
) -> str | None:
    """Canonical public URL for a team.

    Global-org teams use ``/teams/<slug>``. Org-scoped teams use
    ``/<org-slug>/teams/<team-slug>`` when the org name is known; otherwise
    return None rather than inventing a global path.
    """
    if not name:
        return None
    org_id_str = str(org_id) if org_id is not None else ""
    org_slug = (org_name or "").strip()
    if org_id_str == GLOBAL_ORG_ID or org_slug == "all":
        path = f"/teams/{name}"
    elif org_slug:
        path = f"/{org_slug}/teams/{name}"
    else:
        return None
    return absolute_web_url(path)


def user_summary(source: Any) -> dict | None:
    """Build a standard {id, username, is_agent} dict from a model or raw dict.

    Handles both typed UserProfile objects (attribute access) and raw API
    response dicts where user info may be nested or flattened.
    """
    if source is None:
        return None

    user_obj = getv(source, "user") or getv(source, "author") or {}
    username = getv(source, "username") or getv(user_obj, "username")
    if not username:
        return None

    user_id = (
        getv(source, "user_id")
        or getv(user_obj, "user_id")
        or getv(user_obj, "id")
        or ""
    )
    is_agent = getv(user_obj, "is_agent", None)
    if is_agent is None:
        is_agent = getv(user_obj, "actor_type") == "agent"

    return {
        "id": str(user_id),
        "username": username,
        "is_agent": bool(is_agent),
    }


def org_summary(source: Any) -> dict | None:
    """Build a standard {id, name} dict from a model or raw dict.

    Handles OrganizationProfile objects and raw dicts where org info
    may be a nested object or a flat org_id field.
    """
    if source is None:
        return None

    org = getv(source, "organization")
    org_id = getv(source, "org_id") or getv(org, "id") if org else getv(source, "org_id")
    if not org_id:
        return None

    result: dict[str, Any] = {"id": str(org_id)}
    org_name = getv(org, "name") if org else None
    if org_name:
        result["name"] = org_name
    return result


def team_summary(source: Any) -> dict | None:
    """Build a standard {id, name} dict from a model or raw dict."""
    if source is None:
        return None
    team_obj = getv(source, "team") or {}
    team_id = getv(source, "team_id") or getv(team_obj, "id")
    if not team_id:
        return None

    result: dict[str, Any] = {"id": str(team_id)}
    team_name = getv(team_obj, "name")
    if team_name:
        result["name"] = team_name
    return result


def _attribution_summary(asset: Any) -> dict[str, Any]:
    """Pull provenance from assets.attribution (legacy keys may still be on metadata)."""
    attr = as_dict(getattr(asset, "attribution", None))
    legacy = as_dict(getattr(asset, "metadata", None))

    def pick(key: str) -> Any:
        if attr.get(key) is not None:
            return attr.get(key)
        return legacy.get(key)

    citation = pick("citation")
    if hasattr(citation, "model_dump"):
        citation = citation.model_dump()
    elif citation is not None and not isinstance(citation, dict):
        citation = None

    return optional_kwargs(
        originality=pick("originality"),
        github_url=pick("github_url"),
        paper_url=pick("paper_url"),
        doi_url=pick("doi_url"),
        external_url=pick("external_url"),
        relation_type=pick("relation_type"),
        doi=pick("doi"),
        citation=citation,
    )


# Shorter than this after the repeats come out, a snippet tells the agent
# nothing the row doesn't (e.g. a route chunk's "POST /path").
_MIN_SNIPPET_CHARS = 40


def _novel_snippet(snippet: str, name: str | None, description: str | None) -> str | None:
    """The part of a search snippet that the row's name and description don't repeat.

    Route and dataset chunks open with the name (``"X route: X POST /path"``)
    and then the description, so the raw snippet doubles the row.
    """
    original = text = collapse_whitespace(snippet)
    if name:
        for lead in (f"{name} route:", name):
            if text.startswith(lead):
                text = text[len(lead) :].lstrip(" :—-")
    if description:
        probe = collapse_whitespace(description).rstrip(".…")[:60]
        at = text.find(probe) if probe else -1
        if at >= 0:
            text = text[:at].rstrip()
    if text == original:
        return snippet
    return text if len(text.strip(" .…")) >= _MIN_SNIPPET_CHARS else None


def format_search_hit(item: Any) -> dict[str, Any]:
    """Slim discovery row for ``search_assets``.

    Keep only what an agent needs to pick a hit and call ``get_asset``:
    id, type, name, short description, username, created_at, plus chunk
    ``snippet`` / ``match_source`` when the backend returned them.
    """

    row: dict[str, Any] = {
        "id": str(getv(item, "id") or ""),
        "name": getv(item, "name"),
        "asset_type": getv(item, "asset_type"),
        "created_at": getv(item, "created_at"),
    }

    description = short_description(getv(item, "description"))
    if description:
        row["description"] = description

    user = user_summary(item)
    if user and user.get("username"):
        row["username"] = user["username"]

    # A summary chunk is just the name and description already in the row.
    snippet = getv(item, "snippet")
    match_source = getv(item, "match_source")
    if snippet and match_source != "summary":
        snippet = _novel_snippet(snippet, row.get("name"), row.get("description"))
    if snippet and match_source != "summary":
        row["snippet"] = snippet
        if match_source:
            row["match_source"] = match_source

    return row


def format_asset_summary(asset: Any) -> dict:
    """Compact agent-facing summary for get_asset / create / update returns.

    Flat location fields (username, org_id, team_id) instead of nested objects;
    no web ``url`` (typed download / get_asset full cover that). Description
    capped at 200 chars to match ``format_search_hit``.
    """

    summary: dict[str, Any] = {
        "id": str(asset.id),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "visibility": asset.visibility,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }
    # `state` / `source` are nullable per asset type and emit as `null` for most
    # rows (posts, files, comments, etc.). Skip them when absent to keep summary
    # rows compact in list/search responses.
    state = getattr(asset, "state", None)
    if state is not None:
        summary["state"] = state
    source = getattr(asset, "source", None)
    if source is not None:
        summary["source"] = source

    if asset.description:
        summary["description"] = short_description(asset.description)

    license_id = getattr(asset, "license_id", None)
    if license_id:
        summary["license_id"] = license_id

    attribution = _attribution_summary(asset)
    if attribution:
        summary["attribution"] = attribution

    user = user_summary(asset)
    if user and user.get("username"):
        summary["username"] = user["username"]

    org = org_summary(asset)
    if org and org.get("id"):
        summary["org_id"] = org["id"]
        if org.get("name"):
            summary["org_name"] = org["name"]

    team = team_summary(asset)
    if team and team.get("id"):
        summary["team_id"] = team["id"]
        if team.get("name"):
            summary["team_name"] = team["name"]

    parent_id = getattr(asset, "parent_id", None)
    if parent_id:
        summary["parent_id"] = str(parent_id)

    # Lineage: what this asset is to the one it came from (a relaxed CIF is a
    # "version" of its input, phonons a "result"). Shown only when it has one.
    source_id = getattr(asset, "source_id", None)
    if source_id:
        summary["lineage_role"] = getattr(asset, "lineage_role", None)
        summary["source_id"] = str(source_id)

    monetization_block = format_monetization_block(asset)
    if monetization_block:
        summary.update(monetization_block)

    return summary


def format_asset_properties(properties: list[Any]) -> dict[str, Any] | None:
    """Compact asset properties for agents: ``{key: value}`` for single
    platform values, ``{key: {unit, values: [...]}}`` when a property has
    route values (one per route and method, newest first)."""
    result: dict[str, Any] = {}
    for prop in properties or []:
        if hasattr(prop, "model_dump"):
            prop = prop.model_dump(mode="json", exclude_none=True)
        values = prop.get("values") or []
        if not values:
            continue
        if len(values) == 1 and values[0].get("source") == "platform":
            result[prop["key"]] = values[0].get("value")
            continue
        entries = []
        for value in values:
            route = value.get("route") or {}
            entries.append(
                optional_kwargs(
                    value=value.get("value"),
                    uncertainty=value.get("uncertainty"),
                    method=value.get("method"),
                    conditions=value.get("conditions"),
                    route=route.get("name"),
                    route_id=route.get("id"),
                    action_id=value.get("action_id"),
                    created_at=value.get("created_at"),
                )
            )
        result[prop["key"]] = optional_kwargs(unit=prop.get("unit"), values=entries)
    return result or None


def file_result(file: Any) -> dict:
    """Build a standard result dict for a file asset, including data URL and metadata."""
    result = format_asset_summary(file)
    if file.data:
        result["file_url"] = file.data.url
    if file.metadata and hasattr(file.metadata, "type"):
        result["mime_type"] = file.metadata.type
    if file.metadata and hasattr(file.metadata, "size"):
        result["size"] = file.metadata.size
    return result
