from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ouro_mcp.constants import (
    DEFAULT_OURO_FRONTEND_URL,
    ENV_OURO_FRONTEND_URL,
    ENV_OURO_MCP_TIMEZONE,
    ENV_WORKSPACE_ROOT,
    GLOBAL_ORG_ID,
    MAX_RESPONSE_SIZE,
)

log = logging.getLogger(__name__)

_TIMESTAMP_KEYS = {
    "created_at",
    "last_updated",
    "updated_at",
    "timestamp",
    # Action lifecycle (services.py / ouro-py Action model)
    "started_at",
    "finished_at",
    # Notifications / quest reviews
    "read_at",
    "reviewed_at",
}


_HEAVY_RESPONSE_KEYS = frozenset({"embedding", "fts"})


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


def slim_asset_tags(tags: Any) -> list[dict[str, Any]] | None:
    """Shrink asset tag rows for MCP — metadata only, no vectors."""
    if not isinstance(tags, list) or not tags:
        return None

    slimmed: list[dict[str, Any]] = []
    for row in tags:
        if not isinstance(row, dict):
            continue
        tag = row.get("tag") if isinstance(row.get("tag"), dict) else {}
        tag_summary = optional_kwargs(
            id=str(tag["id"]) if tag.get("id") is not None else None,
            name=tag.get("name"),
            slug=tag.get("slug"),
            type=tag.get("type"),
            description=tag.get("description"),
        )
        entry = optional_kwargs(
            source=row.get("source"),
            confidence=row.get("confidence"),
            tag=tag_summary or None,
        )
        if entry:
            slimmed.append(entry)
    return slimmed or None


def slim_connection_graph(connections: Any, current_asset_id: str | None = None) -> Any:
    """Shrink connection payloads from the Ouro API for MCP tool responses.

    Each edge may include full ``source`` and ``target`` asset records
    (descriptions, previews, metadata, pricing, etc.). That duplication
    routinely pushes ``get_asset(detail=\"full\")`` past agent context limits
    even for modest graphs.     Group edges by relationship type and store only
    the connected asset summary. ``id`` and ``asset_type`` are always
    present; ``name`` is omitted when null (display-only). ``created_at``,
    when available, is the connected asset's timestamp, not the edge
    timestamp. For ``type == "action"`` edges, ``action_id`` is preserved
    when present so agents can follow up with ``get_action``.
    """
    if not isinstance(connections, list):
        return connections

    def _slim_endpoint(node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        aid = node.get("id")
        out: dict[str, Any] = {
            "id": str(aid) if aid is not None else None,
            # `asset_type` is the discriminator agents need to decide which
            # follow-up tool to call — always emit it (possibly null when the
            # backend has no record), never drop it.
            "asset_type": node.get("asset_type"),
        }
        # `name` is the only field we drop when missing — it's display-only
        # and frequently absent for endpoint stubs. Treat both `null` and the
        # empty string the same; the backend stores `""` for nameless types
        # like comments and we don't want either form to bloat the payload.
        name = node.get("name")
        if name:
            out["name"] = name
        if node.get("created_at") is not None:
            out["created_at"] = node["created_at"]
        return out

    def _endpoint_from_edge(edge: dict[str, Any], side: str) -> dict[str, Any] | None:
        endpoint = _slim_endpoint(edge.get(side))
        if endpoint is not None:
            return endpoint

        edge_id = edge.get(f"{side}_id")
        asset_type = edge.get(f"{side}_asset_type")
        if edge_id is None and asset_type is None:
            return None
        return {
            "id": str(edge_id) if edge_id is not None else None,
            "name": None,
            "asset_type": asset_type,
        }

    current_id = str(current_asset_id) if current_asset_id is not None else None
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in connections:
        connection_type = "unknown"
        if not isinstance(edge, dict):
            grouped.setdefault(connection_type, []).append({"value": edge})
            continue

        connection_type = str(edge.get("type") or "unknown")
        source = _endpoint_from_edge(edge, "source")
        target = _endpoint_from_edge(edge, "target")
        source_id = str(source["id"]) if source and source.get("id") is not None else None
        target_id = str(target["id"]) if target and target.get("id") is not None else None

        if current_id and source_id == current_id:
            row = dict(target or {})
        else:
            row = dict(source or {})

        # Action edges carry the route-execution id that produced the link.
        # Preserve it so agents can follow up with get_action without scraping
        # posts or guessing from lineage alone.
        if connection_type == "action" and edge.get("action_id") is not None:
            row["action_id"] = str(edge["action_id"])

        grouped.setdefault(connection_type, []).append(row)
    return grouped


def truncate_response(data: str, context: str = "") -> str:
    """If a JSON response exceeds the size threshold, truncate and flag it."""
    if len(data) <= MAX_RESPONSE_SIZE:
        return data
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "rows" in parsed:
            rows = parsed["rows"]
            while len(json.dumps(parsed)) > MAX_RESPONSE_SIZE and rows:
                rows.pop()
            parsed["truncated"] = True
            if context:
                parsed["note"] = f"Response truncated. {context}"
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return data[:MAX_RESPONSE_SIZE] + "\n... [truncated]"


def _configured_timezone_name() -> str | None:
    raw = os.environ.get(ENV_OURO_MCP_TIMEZONE, "").strip()
    return raw or None


def _parse_timestamp_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _localize_timestamp(value: Any, tz_name: str) -> str | None:
    """Render a UTC timestamp as a compact local ISO string (offset preserved).

    Microseconds are dropped — Ouro timestamps don't carry meaningful
    sub-second precision for agents, and stripping them shaves ~7 chars
    off every timestamp in tool responses.
    """
    dt = _parse_timestamp_value(value)
    if dt is None:
        return None

    try:
        local_dt = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None

    return local_dt.replace(microsecond=0).isoformat()


def enrich_timestamps(data: Any, tz_name: str | None = None) -> Any:
    """Recursively rewrite common UTC timestamp fields as compact local ISO.

    With ``OURO_MCP_TIMEZONE`` set, every recognized timestamp key is
    replaced *in place* with a single offset-bearing local ISO string
    (e.g. ``2026-04-06T21:02:19-05:00``). The offset preserves the absolute
    instant, and using one field instead of a UTC value plus ``_local`` /
    ``_local_label`` siblings keeps tool responses small enough for agents
    listing many assets at once. Existing ``_local`` / ``_local_label``
    fields on the input are dropped so older callers don't double up.
    """
    active_tz = tz_name or _configured_timezone_name()
    if not active_tz:
        return data

    if isinstance(data, list):
        return [enrich_timestamps(item, active_tz) for item in data]

    if not isinstance(data, dict):
        return data

    enriched: dict[str, Any] = {}
    for key, value in data.items():
        if key.endswith("_local") or key.endswith("_local_label"):
            base = key.rsplit("_local", 1)[0]
            if base in _TIMESTAMP_KEYS:
                continue
        if key in _TIMESTAMP_KEYS and value is not None:
            localized = _localize_timestamp(value, active_tz)
            enriched[key] = localized if localized is not None else value
            continue
        enriched[key] = enrich_timestamps(value, active_tz)

    return enriched


def dump_json(data: Any, **kwargs: Any) -> str:
    """JSON-encode a payload after rewriting timestamps to local ISO.

    This is the canonical tool-response serializer. Prefer it over
    ``json.dumps`` so every response gets compact local-timezone timestamps
    when ``OURO_MCP_TIMEZONE`` is set (see ``enrich_timestamps``).
    """
    return json.dumps(enrich_timestamps(data), default=str, **kwargs)


def list_response(
    results: list,
    *,
    pagination: dict | None = None,
    limit: int | None = None,
    total: int | None = None,
    has_more: bool | None = None,
    extra: dict | None = None,
) -> dict:
    """Build the canonical list-response envelope used across all MCP tools.

    Shape: ``{"results": [...], "total": int | None, "hasMore": bool,
    "nextCursor": Any | None, **extra}``.

    Precedence for ``hasMore`` / ``total`` / ``nextCursor``:
      1. Explicit ``has_more`` / ``total`` kwargs (caller already resolved them).
      2. Server-provided values from the ``pagination`` envelope
         (``pagination["hasMore"]`` / ``["total"]`` / ``["nextCursor"]``).
      3. Fallback: ``hasMore=False``, ``total=None``, ``nextCursor=None``.

    There is no ``len(results) == limit`` heuristic — if the server didn't
    give us a definitive ``hasMore`` and the caller didn't either, we say
    there's nothing more. ``limit`` is still accepted for symmetry with the
    callsite signature but is not consulted when deriving ``hasMore``.
    """
    pag = pagination or {}

    resolved_total = total if total is not None else pag.get("total")

    if has_more is not None:
        resolved_has_more = bool(has_more)
    elif "hasMore" in pag:
        resolved_has_more = bool(pag["hasMore"])
    else:
        resolved_has_more = False

    resolved_next_cursor = pag.get("nextCursor")

    payload: dict[str, Any] = {
        "results": results,
        "total": resolved_total,
        "hasMore": resolved_has_more,
    }
    if resolved_next_cursor is not None:
        payload["nextCursor"] = resolved_next_cursor
    if limit is not None:
        payload["limit"] = limit
    if extra:
        payload.update(extra)
    return payload


def resolve_local_path(raw: str) -> Path:
    """Resolve a user-supplied file path, sandboxing to WORKSPACE_ROOT when set.

    When WORKSPACE_ROOT is set (typically to the calling agent's workspace
    directory) the resolved path MUST stay inside that root: relative paths
    are joined to it, absolute and ``~``-relative paths are accepted only
    when they already point inside it, and ``..`` traversal that escapes
    the root is rejected with ``PermissionError``.

    When WORKSPACE_ROOT is not set (e.g. a desktop user running the MCP
    standalone) the path is returned as-is after ``~`` expansion and
    resolution, with no sandboxing.
    """
    p = Path(raw).expanduser()
    workspace_env = os.environ.get(ENV_WORKSPACE_ROOT)

    if not workspace_env:
        return p.resolve()

    workspace = Path(workspace_env).expanduser().resolve()
    candidate = (workspace / p) if not p.is_absolute() else p
    resolved = candidate.resolve()

    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(
            f"Path '{raw}' escapes the agent workspace ({workspace}). "
            "Use a path inside the workspace."
        ) from exc

    return resolved


def _getv(obj: Any, key: str, default: Any = None) -> Any:
    """Get a value from a dict or object attribute, whichever applies."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
    return absolute_web_url(_getv(asset, "url") or _getv(asset, "slug"))


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

    user_obj = _getv(source, "user") or _getv(source, "author") or {}
    username = _getv(source, "username") or _getv(user_obj, "username")
    if not username:
        return None

    user_id = (
        _getv(source, "user_id")
        or _getv(user_obj, "user_id")
        or _getv(user_obj, "id")
        or ""
    )
    is_agent = _getv(user_obj, "is_agent", None)
    if is_agent is None:
        is_agent = _getv(user_obj, "actor_type") == "agent"

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

    org = _getv(source, "organization")
    org_id = _getv(source, "org_id") or _getv(org, "id") if org else _getv(source, "org_id")
    if not org_id:
        return None

    result: dict[str, Any] = {"id": str(org_id)}
    org_name = _getv(org, "name") if org else None
    if org_name:
        result["name"] = org_name
    return result


def team_summary(source: Any) -> dict | None:
    """Build a standard {id, name} dict from a model or raw dict."""
    if source is None:
        return None
    team_obj = _getv(source, "team") or {}
    team_id = _getv(source, "team_id") or _getv(team_obj, "id")
    if not team_id:
        return None

    result: dict[str, Any] = {"id": str(team_id)}
    team_name = _getv(team_obj, "name")
    if team_name:
        result["name"] = team_name
    return result


def format_asset_summary(asset: Any) -> dict:
    """Extract a consistent summary dict from any ouro-py asset model."""
    from ouro.utils.content import description_to_markdown

    summary: dict[str, Any] = {
        "id": str(asset.id),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "visibility": asset.visibility,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "last_updated": asset.last_updated.isoformat() if asset.last_updated else None,
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
        summary["description"] = description_to_markdown(asset.description, max_length=500)

    user = user_summary(asset)
    if user:
        summary["user"] = user

    org = org_summary(asset)
    if org:
        summary["organization"] = org

    team = team_summary(asset)
    if team:
        summary["team"] = team

    parent_id = getattr(asset, "parent_id", None)
    if parent_id:
        summary["parent_id"] = str(parent_id)

    url = asset_web_url(asset)
    if url:
        summary["url"] = url

    monetization_block = format_monetization_block(asset)
    if monetization_block:
        summary.update(monetization_block)

    return summary


def _format_compact_number(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def format_pay_per_use_cost_summary(
    unit_cost: Any,
    cost_unit: str,
    currency: str,
) -> str:
    """Human-readable pay-per-use cost summary for agent-facing tools."""
    currency_upper = str(currency).upper()
    currency_lower = str(currency).lower()
    if currency_lower == "usd":
        return f"${unit_cost:.2f} per {cost_unit} (USD)"
    if currency_lower == "btc":
        return f"{_format_compact_number(unit_cost)} sats per {cost_unit} (BTC)"
    return f"{_format_compact_number(unit_cost)} per {cost_unit} ({currency_upper})"


def format_one_time_cost_summary(price: Any, currency: str) -> str:
    """Human-readable one-time cost summary for agent-facing tools."""
    currency_upper = str(currency).upper()
    currency_lower = str(currency).lower()
    if currency_lower == "usd":
        return f"${price:.2f} (USD)"
    if currency_lower == "btc":
        return f"{_format_compact_number(price)} sats (BTC)"
    return f"{_format_compact_number(price)} {currency_upper}"


def format_monetization_block(asset: Any) -> dict[str, Any]:
    """Build the monetization fields for an asset (free or paid).

    Returns an empty dict for free assets. For paid assets, returns the
    structured cost fields PLUS a human-readable `cost_summary` so an agent
    that ignores structured data still sees that the asset isn't free.

    Accepts either a typed Asset model or a dict (search results).
    """
    monetization = _getv(asset, "monetization")
    if not monetization or monetization == "none":
        return {}

    block: dict[str, Any] = {"monetization": monetization}
    currency = _getv(asset, "price_currency") or "usd"
    block["price_currency"] = currency

    if monetization == "pay-per-use":
        # `unit_cost` is dollars for USD and sats for BTC. Surface all four
        # fields so agents can compare costs without N+1 lookups.
        unit_cost = _getv(asset, "unit_cost")
        cost_unit = _getv(asset, "cost_unit") or "call"
        block["unit_cost"] = unit_cost
        block["cost_unit"] = cost_unit
        block["cost_accounting"] = _getv(asset, "cost_accounting")
        if unit_cost is not None:
            block["cost_summary"] = format_pay_per_use_cost_summary(
                unit_cost,
                cost_unit,
                currency,
            )
    else:
        # pay-to-unlock and any other one-time-price monetization.
        price = _getv(asset, "price")
        block["price"] = price
        if price is not None:
            block["cost_summary"] = format_one_time_cost_summary(price, currency)

    return {k: v for k, v in block.items() if v is not None}


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


def route_input_assets_summary(route: Any) -> dict[str, Any] | None:
    """Return the simple keyed asset-input contract an agent should use.

    Prefers ``input_assets`` (plural keyed declarations). Falls back to a
    single-entry summary synthesized from the legacy ``input_type`` column
    when the route hasn't been migrated yet. The result is keyed by the
    request body field name the route expects.
    """
    raw = _getv(route, "input_assets") or {}
    result: dict[str, Any] = {}

    if isinstance(raw, dict):
        for name, config in raw.items():
            if hasattr(config, "model_dump"):
                config = config.model_dump(exclude_none=True)
            elif not isinstance(config, dict):
                config = {}
            result[name] = optional_kwargs(
                asset_type=config.get("asset_type") or config.get("assetType"),
                primary=config.get("primary"),
                input_filter=config.get("input_filter") or config.get("inputFilter"),
                file_extensions=config.get("file_extensions")
                or config.get("fileExtensions")
                or config.get("input_file_extensions")
                or config.get("inputFileExtensions"),
                contains_file_extensions=config.get("contains_file_extensions")
                or config.get("containsFileExtensions"),
            )

    input_type = _getv(route, "input_type")
    if input_type and not result:
        legacy_extensions = (
            _getv(route, "input_file_extensions")
            or ([_getv(route, "input_file_extension")] if _getv(route, "input_file_extension") else None)
        )
        result[str(input_type)] = optional_kwargs(
            asset_type=input_type,
            primary=True,
            input_filter=_getv(route, "input_filter"),
            file_extensions=legacy_extensions,
        )
    elif input_type and len(result) == 1:
        # Sparse keyed rows (e.g. `{file: {}}`) inherit the legacy primary
        # projection so agents still see the declared extension filter.
        sole_name, sole_config = next(iter(result.items()))
        sparse = not sole_config.get("asset_type") and not sole_config.get(
            "file_extensions"
        )
        if sparse or not sole_config.get("asset_type"):
            sole_config["asset_type"] = sole_config.get("asset_type") or input_type
        if not sole_config.get("file_extensions"):
            legacy_extensions = (
                _getv(route, "input_file_extensions")
                or (
                    [_getv(route, "input_file_extension")]
                    if _getv(route, "input_file_extension")
                    else None
                )
            )
            if legacy_extensions:
                sole_config["file_extensions"] = legacy_extensions
        if not sole_config.get("input_filter"):
            legacy_filter = _getv(route, "input_filter")
            if legacy_filter:
                sole_config["input_filter"] = legacy_filter
        if sparse and sole_config.get("primary") is None:
            sole_config["primary"] = True
        result[sole_name] = optional_kwargs(**sole_config)

    return result or None


def route_output_assets_summary(route: Any) -> dict[str, Any] | None:
    """Return the keyed asset-output contract a route will produce.

    Mirrors :func:`route_input_assets_summary`. Prefers plural keyed
    ``output_assets`` and falls back to a single-entry summary synthesized
    from the legacy ``output_type`` and ``output_file_extension`` columns.
    The result tells the agent which response body fields will resolve to
    Ouro assets, and what each declared asset looks like.
    """
    raw = _getv(route, "output_assets") or {}
    result: dict[str, Any] = {}

    if isinstance(raw, dict):
        for name, config in raw.items():
            if hasattr(config, "model_dump"):
                config = config.model_dump(exclude_none=True)
            elif not isinstance(config, dict):
                config = {}
            result[name] = optional_kwargs(
                asset_type=config.get("asset_type") or config.get("assetType"),
                primary=config.get("primary"),
                file_extensions=config.get("file_extensions")
                or config.get("fileExtensions")
                or config.get("output_file_extensions")
                or config.get("outputFileExtensions"),
            )

    output_type = _getv(route, "output_type")
    if output_type and not result:
        legacy_extension = _getv(route, "output_file_extension")
        result[str(output_type)] = optional_kwargs(
            asset_type=output_type,
            primary=True,
            file_extensions=[legacy_extension] if legacy_extension else None,
        )

    return result or None


def route_request_body_without_input_assets(route: Any) -> Any:
    """Hide Ouro-resolved asset object schemas from route execution metadata.

    Agents should pass IDs via ``input_assets``/``input_asset``. The backend
    expands those IDs into the service-facing body object.
    """
    request_body = _getv(route, "request_body")
    if not isinstance(request_body, dict):
        return request_body

    cleaned = deepcopy(request_body)
    schema = (
        cleaned.get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if not isinstance(schema, dict):
        return cleaned

    handled_keys = set((route_input_assets_summary(route) or {}).keys())
    input_type = _getv(route, "input_type")
    if input_type:
        handled_keys.add(str(input_type))

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key in handled_keys:
            properties.pop(key, None)

    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [key for key in required if key not in handled_keys]

    return cleaned


def normalize_markdown_input(markdown: str) -> str:
    """Normalize common shell-escaped markdown sequences.

    Agents frequently pass markdown via shell CLI args (e.g. content_markdown="..."),
    where escaped sequences like ``\\n`` are sent literally. Convert those back to
    markdown-friendly characters so the backend receives the intended content.
    """
    normalized = markdown.replace("\\`", "`")
    if "\\r\\n" in normalized:
        normalized = normalized.replace("\\r\\n", "\n")
    if "\\n" in normalized:
        normalized = normalized.replace("\\n", "\n")
    normalized = _normalize_mentions(normalized)
    return normalized


# Ouro's markdown parser only recognizes one mention spelling: the
# backtick-wrapped brace-at form `{@username}`. Models reliably get this wrong,
# emitting @username, @{username}, {@username}, or `@username` instead, none of
# which notify the user. Normalize every supported spelling to the canonical
# form so a mention works regardless of how the agent wrote it.
_MENTION_USER = r"([A-Za-z0-9_]{1,64})"
_MENTION_REDUCERS = (
    re.compile(r"`\{@" + _MENTION_USER + r"\}`"),  # `{@u}` (already canonical)
    re.compile(r"`@" + _MENTION_USER + r"`"),  # `@u`
    re.compile(r"\{@" + _MENTION_USER + r"\}"),  # {@u}
    re.compile(r"@\{" + _MENTION_USER + r"\}"),  # @{u}
)
# Bare @username, but not mid-word, not an email local part, and not already
# wrapped in a brace or backtick.
_MENTION_BARE = re.compile(r"(?<![\w`{])@" + _MENTION_USER + r"\b")


def _normalize_mentions(text: str) -> str:
    # Phase 1: strip any wrapping spelling back down to a bare @username token.
    for pattern in _MENTION_REDUCERS:
        text = pattern.sub(r"@\1", text)
    # Phase 2: wrap bare @username uniformly into the canonical mention syntax.
    return _MENTION_BARE.sub(r"`{@\1}`", text)


def content_from_markdown(ouro: Any, markdown: str) -> Any:
    """Create a Content object from markdown using the Ouro client."""
    content = ouro.posts.Content()
    content.from_markdown(normalize_markdown_input(markdown))
    return content


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


def resolve_team_policy(team: dict, field: str, default: str = "any") -> str:
    """Return the effective policy for a team, falling back to the org's policy."""
    value = team.get(field)
    if value:
        return value
    org = team.get("organization") or {}
    return org.get(field) or default
