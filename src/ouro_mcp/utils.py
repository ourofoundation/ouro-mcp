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

from ouro_mcp.constants import ENV_OURO_MCP_TIMEZONE, ENV_WORKSPACE_ROOT, MAX_RESPONSE_SIZE

log = logging.getLogger(__name__)

_TIMESTAMP_KEYS = {"created_at", "last_updated", "updated_at", "timestamp"}


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


def _local_timestamp_fields(value: Any, tz_name: str) -> dict[str, str] | None:
    dt = _parse_timestamp_value(value)
    if dt is None:
        return None

    try:
        local_dt = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None

    return {
        "local": local_dt.isoformat(),
        "local_label": local_dt.strftime("%Y-%m-%d %I:%M %p %Z"),
    }


def enrich_timestamps(data: Any, tz_name: str | None = None) -> Any:
    """Recursively add local-time siblings for common UTC timestamp fields."""
    active_tz = tz_name or _configured_timezone_name()
    if not active_tz:
        return data

    if isinstance(data, list):
        return [enrich_timestamps(item, active_tz) for item in data]

    if not isinstance(data, dict):
        return data

    enriched: dict[str, Any] = {}
    for key, value in data.items():
        transformed = enrich_timestamps(value, active_tz)
        enriched[key] = transformed
        if (
            key in _TIMESTAMP_KEYS
            and value is not None
            and f"{key}_local" not in data
            and f"{key}_local_label" not in data
        ):
            local_fields = _local_timestamp_fields(value, active_tz)
            if local_fields:
                enriched[f"{key}_local"] = local_fields["local"]
                enriched[f"{key}_local_label"] = local_fields["local_label"]

    return enriched


def dump_json(data: Any, **kwargs: Any) -> str:
    """JSON-encode a payload after enriching timestamp fields for local display.

    This is the canonical tool-response serializer. Prefer it over
    ``json.dumps`` so every response picks up local-time siblings when
    ``OURO_MCP_TIMEZONE`` is set.
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
    """Resolve a user-supplied file path, respecting WORKSPACE_ROOT for relative paths.

    When WORKSPACE_ROOT is set (typically to the calling agent's workspace
    directory), relative paths are resolved against it instead of the MCP
    server's CWD.  Absolute and home-relative (~) paths are unaffected.
    """
    p = Path(raw).expanduser()
    if not p.is_absolute():
        workspace = os.environ.get(ENV_WORKSPACE_ROOT)
        if workspace:
            p = Path(workspace).expanduser() / p
    return p.resolve()


def _getv(obj: Any, key: str, default: Any = None) -> Any:
    """Get a value from a dict or object attribute, whichever applies."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
        "state": getattr(asset, "state", None),
        "source": getattr(asset, "source", None),
    }

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

    if getattr(asset, "monetization", None) and asset.monetization != "none":
        summary["monetization"] = asset.monetization
        summary["price"] = getattr(asset, "price", None)

    return summary


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


def route_input_assets_summary(route: Any) -> dict[str, Any] | None:
    """Return the simple keyed asset-input contract an agent should use."""
    raw = _getv(route, "input_assets") or {}
    result: dict[str, Any] = {}

    if isinstance(raw, dict):
        for name, config in raw.items():
            config = config if isinstance(config, dict) else {}
            result[name] = optional_kwargs(
                asset_type=config.get("asset_type") or config.get("assetType"),
                input_filter=config.get("input_filter") or config.get("inputFilter"),
                input_file_extension=config.get("input_file_extension")
                or config.get("inputFileExtension"),
                input_file_extensions=config.get("input_file_extensions")
                or config.get("inputFileExtensions"),
                body_path=config.get("body_path") or config.get("bodyPath") or name,
            )

    input_type = _getv(route, "input_type")
    if input_type and not result:
        result[str(input_type)] = {"asset_type": input_type, "body_path": str(input_type)}

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
    # Agents should send mentions as @username. Convert that single input form
    # into the parser's canonical mention syntax before conversion.
    normalized = re.sub(
        r"(?<![\w`{])@([A-Za-z0-9_]{1,64})\b",
        r"`{@\1}`",
        normalized,
    )
    return normalized


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
