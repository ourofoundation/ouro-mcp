from __future__ import annotations

import json
import logging
import math
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from ouro.models import AssetRef, AssetTag, Connection, DatasetColumn
from ouro_mcp.constants import (
    DEFAULT_OURO_FRONTEND_URL,
    DEFAULT_RESPONSE_FORMAT,
    ENV_OURO_FRONTEND_URL,
    ENV_OURO_MCP_LOCAL_FILES,
    ENV_OURO_MCP_MAX_RESPONSE_SIZE,
    ENV_OURO_MCP_RESPONSE_FORMAT,
    ENV_OURO_MCP_TIMEZONE,
    ENV_WORKSPACE_MOUNT,
    ENV_WORKSPACE_ROOT,
    GLOBAL_ORG_ID,
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
    """Shrink asset tag rows for MCP — metadata only, no vectors."""
    slimmed = [
        optional_kwargs(
            source=row.source,
            confidence=row.confidence,
            tag=optional_kwargs(
                id=str(row.tag.id),
                name=row.tag.name,
                slug=row.tag.slug,
                type=row.tag.type,
                description=row.tag.description,
            ),
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

    When ``omit_comments`` is true, skip ``type == "comment"`` edges.
    Comment bodies already ship via the ``comments`` preview on
    ``get_asset(detail=\"full\")`` and via ``get_comments``; keeping the
    connection stubs just duplicates IDs without text.
    """
    current_id = str(current_asset_id) if current_asset_id is not None else None
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in connections:
        if omit_comments and edge.type == "comment":
            continue

        is_outgoing = str(edge.source_id) == current_id
        if omit_outgoing_references and edge.type == "reference" and is_outgoing:
            continue

        if is_outgoing:
            row = _connection_endpoint(edge.target, edge.target_id, edge.target_asset_type)
        else:
            row = _connection_endpoint(edge.source, edge.source_id, edge.source_asset_type)
        if edge.type == "action" and edge.action_id is not None:
            row["action_id"] = str(edge.action_id)

        grouped.setdefault(edge.type, []).append(row)
    return grouped


_TRUNCATION_FOOTER = "\n… [truncated — call with smaller limit/offset]"
_PARALLEL_HEADER_PREFIX = "=== "
_MD_TABLE_SEP_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def resolve_response_format(override: str | None = None) -> str:
    """Return ``md`` or ``json`` for list/table tool responses.

    Precedence: explicit ``override`` → ``OURO_MCP_RESPONSE_FORMAT`` →
    ``md`` (agent-friendly default).
    """
    raw = (override if override is not None else os.environ.get(ENV_OURO_MCP_RESPONSE_FORMAT, ""))
    value = str(raw or "").strip().lower()
    if value in {"md", "markdown"}:
        return "md"
    if value in {"json", "application/json"}:
        return "json"
    if override is not None and value:
        raise ValueError(
            f"Invalid response_format={override!r}. Use 'md' or 'json'."
        )
    return DEFAULT_RESPONSE_FORMAT


def resolve_max_response_size() -> int | None:
    """Return the soft character budget for ``truncate_response``, or ``None`` if off.

    ``OURO_MCP_MAX_RESPONSE_SIZE``: unset or ``<= 0`` disables truncation so
    clients can apply their own context budgets. A positive int is a
    ``len(response)`` cap (e.g. ``50000``).
    """
    raw = os.environ.get(ENV_OURO_MCP_MAX_RESPONSE_SIZE, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def truncate_response(data: str, context: str = "") -> str:
    """Optionally truncate oversized responses when a size budget is configured.

    Off by default (``OURO_MCP_MAX_RESPONSE_SIZE`` unset/0). When enabled,
    JSON payloads with a ``rows`` list shrink by dropping rows; markdown
    tables drop data rows from the end; other text is cut at a line boundary.
    """
    max_size = resolve_max_response_size()
    if max_size is None or len(data) <= max_size:
        return data
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "rows" in parsed:
            rows = parsed["rows"]
            while len(json.dumps(parsed)) > max_size and rows:
                rows.pop()
            parsed["truncated"] = True
            if context:
                parsed["note"] = f"Response truncated. {context}"
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass

    trimmed = _truncate_markdown_table(data, max_size)
    if trimmed is not None and len(trimmed) <= max_size:
        return trimmed
    if trimmed is not None:
        data = trimmed

    # Prefer a clean line cut for markdown / plain-text responses so agents
    # don't get a half-rendered bullet.
    budget = max_size - len(_TRUNCATION_FOOTER)
    if budget <= 0:
        return data[:max_size] + "\n... [truncated]"
    cut = data[:budget]
    last_nl = cut.rfind("\n")
    if last_nl > budget // 2:
        cut = cut[:last_nl]
    return cut + _TRUNCATION_FOOTER


def _truncate_markdown_table(data: str, max_size: int) -> str | None:
    """Drop trailing markdown table data rows until under the size budget."""
    lines = data.splitlines()
    sep_idx = next(
        (i for i, line in enumerate(lines) if _MD_TABLE_SEP_RE.match(line)),
        None,
    )
    if sep_idx is None or sep_idx == 0:
        return None

    # Keep header + separator; drop from the last data row of the first table.
    # Stop at a blank line or non-table line after the table body.
    end = sep_idx + 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    if end <= sep_idx + 1:
        return None

    prefix = lines[: sep_idx + 1]
    body = lines[sep_idx + 1 : end]
    suffix = lines[end:]
    while body and len("\n".join(prefix + body + suffix)) + len(_TRUNCATION_FOOTER) > max_size:
        body.pop()
    if len(body) == end - (sep_idx + 1):
        return None
    return "\n".join(prefix + body + suffix) + _TRUNCATION_FOOTER


def _configured_timezone_name() -> str:
    return os.environ.get(ENV_OURO_MCP_TIMEZONE, "").strip() or "UTC"


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

    Every recognized timestamp key is replaced *in place* with a single
    offset-bearing ISO string in ``OURO_MCP_TIMEZONE`` (default UTC), e.g.
    ``2026-04-06T21:02:19-05:00``. The offset preserves the absolute
    instant, and using one field instead of a UTC value plus ``_local`` /
    ``_local_label`` siblings keeps tool responses small enough for agents
    listing many assets at once. Existing ``_local`` / ``_local_label``
    fields on the input are dropped so older callers don't double up.
    """
    active_tz = tz_name or _configured_timezone_name()
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
    ``json.dumps`` so every response gets compact timestamps in
    ``OURO_MCP_TIMEZONE`` (see ``enrich_timestamps``).
    """
    return json.dumps(enrich_timestamps(data), default=_json_default, ensure_ascii=False, **kwargs)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


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
    resolved_total, resolved_has_more, resolved_next_cursor = resolve_list_pagination(
        pagination,
        total=total,
        has_more=has_more,
    )

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


def page_pagination(page: Any) -> dict[str, Any]:
    """The list-envelope pagination fields for an ouro-py ``Page``."""
    return {
        "total": page.total,
        "hasMore": page.has_more,
        "nextCursor": page.next_cursor,
    }


def resolve_list_pagination(
    pagination: dict | None = None,
    *,
    total: int | None = None,
    has_more: bool | None = None,
) -> tuple[Any, bool, Any]:
    """Resolve ``(total, has_more, next_cursor)`` from kwargs + server pagination."""
    pag = pagination or {}
    resolved_total = total if total is not None else pag.get("total")
    if has_more is not None:
        resolved_has_more = bool(has_more)
    elif "hasMore" in pag:
        resolved_has_more = bool(pag["hasMore"])
    else:
        resolved_has_more = False
    return resolved_total, resolved_has_more, pag.get("nextCursor")


def collapse_whitespace(text: Any, max_length: int | None = None) -> str:
    """Collapse newlines/runs of whitespace into a single readable line."""
    if text is None:
        return ""
    collapsed = re.sub(r"\s+", " ", str(text)).strip()
    if max_length is not None and len(collapsed) > max_length:
        return collapsed[: max_length - 1].rstrip() + "…"
    return collapsed


def _assert_no_parallel_header(text: str) -> None:
    """Reject text that would break ouro-agents parallel result splitting.

    Parallel tool results are labeled with lines starting ``=== Tool result:``.
    Emitting the same prefix inside a tool body would split observations
    incorrectly.
    """
    for line in text.splitlines():
        if line.startswith(_PARALLEL_HEADER_PREFIX):
            raise ValueError(
                "Markdown tool responses must not contain lines starting with "
                f"{_PARALLEL_HEADER_PREFIX!r} (conflicts with parallel tool "
                "result headers)."
            )


def _format_timestamp_for_md(value: Any) -> str | None:
    if value is None:
        return None
    localized = _localize_timestamp(value, _configured_timezone_name())
    if localized is not None:
        return localized
    text = str(value).strip()
    return text or None


def markdown_id(value: Any) -> str | None:
    """Render an id as ``id: `uuid``` for verbatim agent copying."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return f"id: `{text}`"


def markdown_bullet(
    primary: str,
    *parts: Any,
    kind: str | None = None,
    body: str | None = None,
) -> str:
    """Build a compact markdown list item.

    ``primary`` is the bold lead (name / title). Optional ``kind`` renders
    immediately after as ``(kind)`` — matching the discovery contract
    ``**Name** (type) — id: ...``. Additional ``parts`` are joined with
    em-dashes. Optional ``body`` becomes an indented second line.
    """
    lead = f"**{collapse_whitespace(primary) or '(untitled)'}**"
    if kind:
        kind_text = collapse_whitespace(kind)
        if kind_text:
            lead += f" ({kind_text})"
    segments = [lead]
    for part in parts:
        if part is None:
            continue
        text = collapse_whitespace(_format_timestamp_for_md(part) if isinstance(part, datetime) else part)
        if text:
            segments.append(text)
    line = "- " + " — ".join(segments)
    if body:
        body_text = collapse_whitespace(body)
        if body_text:
            line += f"\n  {body_text}"
    _assert_no_parallel_header(line)
    return line


def search_hit_line(hit: dict[str, Any]) -> str:
    """Render one ``format_search_hit`` row as a markdown bullet."""
    name = hit.get("name") or "(untitled)"
    parts: list[Any] = [markdown_id(hit.get("id"))]
    username = hit.get("username")
    if username:
        parts.append(f"by @{username}")
    created = _format_timestamp_for_md(hit.get("created_at"))
    if created:
        parts.append(created)

    body_bits: list[str] = []
    description = hit.get("description")
    if description:
        body_bits.append(str(description))
    snippet = hit.get("snippet")
    if snippet:
        match_source = hit.get("match_source")
        prefix = f"[{match_source}] " if match_source else ""
        body_bits.append(f"{prefix}{snippet}")
    body = " · ".join(body_bits) if body_bits else None
    return markdown_bullet(
        str(name),
        *parts,
        kind=hit.get("asset_type"),
        body=body,
    )


def _jsonable_list_item(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        try:
            return item.model_dump(mode="json")
        except TypeError:
            return item.model_dump()
    return item


def format_markdown_list_header(
    *,
    shown: int,
    total: int | None = None,
    has_more: bool = False,
    offset: int | None = None,
    noun: str = "results",
    empty_text: str = "No results.",
    extras: list[str] | None = None,
) -> str:
    """Build the header line(s) for a markdown list response."""
    if shown == 0 and not has_more and (total is None or total == 0):
        header = empty_text
    elif total is not None:
        header = f"Found {total} {noun}"
        if shown != total or has_more:
            header += f" (showing {shown}"
            if has_more:
                next_offset = (offset or 0) + shown
                header += f"; more available — call again with offset={next_offset}"
            header += ")"
    else:
        header = f"Found {shown} {noun}"
        if has_more:
            next_offset = (offset or 0) + shown
            header += f" (more available — call again with offset={next_offset})"
    lines = [header]
    if extras:
        for extra in extras:
            text = collapse_whitespace(extra)
            if text:
                lines.append(text)
    result = "\n".join(lines)
    _assert_no_parallel_header(result)
    return result


def render_markdown_list(
    items: list[Any],
    *,
    line_fn: Any,
    total: int | None = None,
    has_more: bool | None = None,
    offset: int | None = None,
    limit: int | None = None,
    noun: str = "results",
    empty_text: str = "No results.",
    extras: list[str] | None = None,
    extra: dict | None = None,
    pagination: dict | None = None,
    response_format: str | None = None,
) -> str:
    """Render a list of rows as compact markdown (or JSON) for agents.

    ``line_fn`` maps each item to a markdown bullet string (typically via
    :func:`markdown_bullet` or :func:`search_hit_line`). Pagination metadata
    mirrors :func:`resolve_list_pagination`.

    Format is controlled by ``response_format`` or ``OURO_MCP_RESPONSE_FORMAT``
    (``md`` default, or ``json``). JSON uses the :func:`list_response` envelope
    and dumps ``items`` (via ``model_dump`` when available). Pass ``extra`` for
    JSON-only sidecar fields (markdown ``extras`` stay display-only).
    """
    resolved_total, resolved_has_more, _cursor = resolve_list_pagination(
        pagination,
        total=total,
        has_more=has_more,
    )

    if resolve_response_format(response_format) == "json":
        return dump_json(
            list_response(
                [_jsonable_list_item(item) for item in items],
                pagination=pagination,
                limit=limit,
                total=resolved_total,
                has_more=resolved_has_more,
                extra=extra,
            )
        )

    lines = [line for line in (line_fn(enrich_timestamps(item)) for item in items) if line]

    header = format_markdown_list_header(
        shown=len(lines),
        total=resolved_total if resolved_total is not None else None,
        has_more=resolved_has_more,
        offset=offset,
        noun=noun,
        empty_text=empty_text,
        extras=extras,
    )
    if not lines:
        return header

    result = f"{header}\n\n" + "\n".join(lines)
    _assert_no_parallel_header(result)
    return result


def _escape_md_table_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", " ").replace("|", "\\|")
    return collapse_whitespace(text)


def table_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Stable column order: first-seen key across rows."""
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def render_markdown_table(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
) -> str:
    """Render row dicts as a GitHub-flavored markdown table."""
    cols = list(columns) if columns is not None else table_columns(rows)
    if not cols:
        return ""

    header = "| " + " | ".join(_escape_md_table_cell(c) for c in cols) + " |"
    separator = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| "
        + " | ".join(_escape_md_table_cell(row.get(c) if isinstance(row, dict) else None) for c in cols)
        + " |"
        for row in rows
    ]
    result = "\n".join([header, separator, *body])
    _assert_no_parallel_header(result)
    return result


def _render_resolved_refs_md(resolved_refs: dict[str, Any]) -> str:
    """Compact markdown for the query_dataset ``resolved_refs`` sidecar."""
    parts: list[str] = ["## resolved_refs"]
    for column, id_map in resolved_refs.items():
        parts.append(f"### {column}")
        if not isinstance(id_map, dict) or not id_map:
            parts.append("- (none)")
            continue
        for ref_id, info in id_map.items():
            if not isinstance(info, dict):
                parts.append(markdown_bullet(str(ref_id), str(info)))
                continue
            name = info.get("name") or ref_id
            kind = info.get("asset_type") or info.get("kind")
            parts.append(
                markdown_bullet(
                    str(name),
                    markdown_id(info.get("id") or ref_id),
                    info.get("web_url"),
                    kind=str(kind) if kind else None,
                )
            )
    result = "\n".join(parts)
    _assert_no_parallel_header(result)
    return result


def format_table_response(
    rows: list[dict[str, Any]],
    *,
    offset: int | None = None,
    limit: int | None = None,
    has_more: bool | None = None,
    row_count: int | None = None,
    resolved_refs: dict[str, Any] | None = None,
    empty_text: str = "No rows.",
    response_format: str | None = None,
) -> str:
    """Render dataset query rows as a markdown table or JSON envelope.

    Markdown is typically much smaller than JSON for wide tables (no
    repeated keys per row). Set ``OURO_MCP_RESPONSE_FORMAT=json`` or pass
    ``response_format=\"json\"`` for the legacy JSON shape.
    """
    fmt = resolve_response_format(response_format)
    shown = len(rows)
    payload: dict[str, Any] = {"rows": rows}
    if row_count is not None:
        payload["row_count"] = row_count
    if offset is not None:
        payload["offset"] = offset
    if limit is not None:
        payload["limit"] = limit
    if has_more is not None:
        payload["hasMore"] = bool(has_more)
    if resolved_refs is not None:
        payload["resolved_refs"] = resolved_refs

    if fmt == "json":
        return dump_json(payload)

    if shown == 0 and not has_more:
        header = empty_text
    elif row_count is not None and offset is None:
        header = f"Found {row_count} rows"
    else:
        header = f"Found {shown} rows"
        bits: list[str] = []
        if offset is not None:
            bits.append(f"offset={offset}")
        if limit is not None:
            bits.append(f"limit={limit}")
        if has_more:
            next_offset = (offset or 0) + shown
            bits.append(f"more available — call again with offset={next_offset}")
        if bits:
            header += f" ({'; '.join(bits)})"

    parts = [header]
    if rows:
        parts.append("")
        parts.append(render_markdown_table(rows))
    if resolved_refs:
        parts.append("")
        parts.append(_render_resolved_refs_md(resolved_refs))

    result = "\n".join(parts)
    _assert_no_parallel_header(result)
    return result


def render_markdown_sections(
    sections: dict[str, list[Any]],
    *,
    line_fn: Any,
    preamble: str | None = None,
    empty_text: str = "No results.",
) -> str:
    """Render grouped payloads (connections, asset actions) as ``##`` sections."""
    parts: list[str] = []
    if preamble:
        parts.append(collapse_whitespace(preamble) or preamble.strip())

    any_items = False
    for title, items in sections.items():
        if not items:
            continue
        any_items = True
        parts.append(f"## {collapse_whitespace(title) or title}")
        for item in items:
            line = line_fn(item)
            if line:
                parts.append(line)

    if not any_items:
        parts.append(empty_text)

    result = "\n".join(parts)
    _assert_no_parallel_header(result)
    return result


def local_files_enabled() -> bool:
    raw = os.environ.get(ENV_OURO_MCP_LOCAL_FILES, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def source_names(path_param: str, *others: str) -> str:
    """The content sources a tool accepts on this server, for error messages.

    ``path_param`` is the local-path parameter, which the hosted server does
    not offer.
    """
    names = ([path_param] if local_files_enabled() else []) + list(others)
    if len(names) < 3:
        return " or ".join(names)
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def read_upload(ouro: Any, upload_id: str, suffixes: tuple[str, ...]) -> bytes:
    """Bytes uploaded through ``create_upload_url``, checked by file extension.

    The upload is kept: call ``discard_upload`` once the content has been
    used, so a failed create does not cost the caller a second upload.
    """
    suffix = Path(upload_id).suffix.lower()
    if suffix not in suffixes:
        raise ValueError(
            f"upload_id is for a '{suffix or 'no extension'}' file; expected {' or '.join(suffixes)}. "
            "Name the file accordingly in create_upload_url."
        )
    return ouro.files.read_upload(upload_id, discard=False)


def discard_upload(ouro: Any, upload_id: Optional[str]) -> None:
    """Delete an upload whose content has been used. A failure only leaves an orphan."""
    if not upload_id:
        return
    try:
        ouro.files.discard_upload(upload_id)
    except Exception:
        log.warning("Failed to discard upload %s", upload_id, exc_info=True)


def resolve_local_path(raw: str) -> Path:
    """Resolve a user-supplied file path, sandboxing to WORKSPACE_ROOT when set.

    The hosted HTTP server sets ``OURO_MCP_LOCAL_FILES=0``. Remote clients
    must send file contents inline; a path would otherwise read or write the
    machine that runs the server.

    When WORKSPACE_ROOT is set (typically to the calling agent's workspace
    directory) the resolved path MUST stay inside that root: relative paths
    are joined to it, absolute and ``~``-relative paths are accepted only
    when they already point inside it, and ``..`` traversal that escapes
    the root is rejected with ``PermissionError``.

    When WORKSPACE_MOUNT is also set (e.g. ``/workspace`` for a Docker
    sandbox), absolute paths under that mount are rewritten onto
    WORKSPACE_ROOT so container-style paths work from a host-side MCP
    process.

    When WORKSPACE_ROOT is not set (e.g. a desktop user running the MCP
    standalone) the path is returned as-is after ``~`` expansion and
    resolution, with no sandboxing.
    """
    if not local_files_enabled():
        raise PermissionError(
            "Local file paths are disabled on this MCP server. To send a file, upload it with "
            "create_upload_url and pass the upload_id; to fetch one, call download_asset "
            "without output_path for a link."
        )

    p = Path(raw).expanduser()
    workspace_env = os.environ.get(ENV_WORKSPACE_ROOT)

    if not workspace_env:
        return p.resolve()

    workspace = Path(workspace_env).expanduser().resolve()

    mount_env = (os.environ.get(ENV_WORKSPACE_MOUNT) or "").strip()
    if mount_env and p.is_absolute():
        mount = Path(mount_env)
        try:
            rel = p.relative_to(mount)
        except ValueError:
            rel = None
        if rel is not None:
            p = workspace / rel

    candidate = (workspace / p) if not p.is_absolute() else p
    resolved = candidate.resolve()

    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(
            f"Path '{raw}' escapes the agent workspace. "
            "Use a relative path or a path under the workspace root."
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


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def _attribution_summary(asset: Any) -> dict[str, Any]:
    """Pull provenance from assets.attribution (legacy keys may still be on metadata)."""
    attr = _as_dict(getattr(asset, "attribution", None))
    legacy = _as_dict(getattr(asset, "metadata", None))

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


def format_search_hit(item: Any) -> dict[str, Any]:
    """Slim discovery row for ``search_assets``.

    Keep only what an agent needs to pick a hit and call ``get_asset``:
    id, type, name, short description, username, created_at, plus chunk
    ``snippet`` / ``match_source`` when the backend returned them.
    """
    from ouro.utils.content import description_to_markdown

    row: dict[str, Any] = {
        "id": str(_getv(item, "id") or ""),
        "name": _getv(item, "name"),
        "asset_type": _getv(item, "asset_type"),
        "created_at": _getv(item, "created_at"),
    }

    description = description_to_markdown(_getv(item, "description"), max_length=200)
    if description:
        row["description"] = description

    user = user_summary(item)
    if user and user.get("username"):
        row["username"] = user["username"]

    # A summary chunk is just the name and description already in the row.
    snippet = _getv(item, "snippet")
    match_source = _getv(item, "match_source")
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
    from ouro.utils.content import description_to_markdown

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
        summary["description"] = description_to_markdown(
            asset.description, max_length=200
        )

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


def _format_usd_rate(value: Any) -> str:
    """Dollars, keeping sub-cent precision for per-second rates ($0.0005)."""
    amount = float(value)
    if 0 < amount < 0.01:
        return f"${amount:.8f}".rstrip("0")
    return f"${amount:.2f}"


def runtime_max_charge(unit_cost: Any, max_seconds: Any, currency: str) -> float:
    """Most one runtime-priced run can cost (dollars or sats), rounded up like the hold."""
    rate = float(unit_cost)
    seconds = float(max_seconds)
    if str(currency).lower() == "usd":
        return math.ceil(round(rate * 100 * seconds, 6)) / 100
    return float(math.ceil(round(rate * seconds, 6)))


def format_pay_per_use_cost_summary(
    unit_cost: Any,
    cost_unit: str,
    currency: str,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> str:
    """Human-readable pay-per-use cost summary for agent-facing tools."""
    currency_upper = str(currency).upper()
    currency_lower = str(currency).lower()
    if cost_accounting == "runtime":
        # Per second of runtime, capped per run; the cap is held up front
        if currency_lower == "usd":
            rate = _format_usd_rate(unit_cost)
        else:
            rate = f"{_format_compact_number(unit_cost)} sats"
        summary = f"{rate} per second of runtime"
        if max_billable_seconds:
            max_charge = runtime_max_charge(unit_cost, max_billable_seconds, currency_lower)
            max_label = (
                f"${max_charge:.2f}"
                if currency_lower == "usd"
                else f"{_format_compact_number(int(max_charge))} sats"
            )
            summary += (
                f", up to {max_label} per run (max {int(max_billable_seconds)}s; "
                "that much, or what you can afford, is held until the run finishes)"
            )
        return f"{summary} ({currency_upper})"
    if currency_lower == "usd":
        return f"{_format_usd_rate(unit_cost)} per {cost_unit} (USD)"
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


def _amount_label(amount: Any, currency: str, *, rate: bool = False) -> str:
    """One amount in its currency: "$0.05" / "50 sats" (rates keep sub-cent USD)."""
    if currency == "usd":
        return _format_usd_rate(amount) if rate else f"${float(amount):.2f}"
    return f"{_format_compact_number(amount)} sats"


def format_dual_cost_summary(
    prices: list[tuple[str, Any]],
    cost_unit: Optional[str] = None,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> str:
    """Cost of something sold in both currencies, e.g. "$0.05 or 50 sats per call".

    ``prices`` is (currency, amount) pairs, primary currency first. Without
    ``cost_unit`` the amounts are one-time prices.
    """
    if cost_unit is None:
        return " or ".join(_amount_label(amount, cur) for cur, amount in prices)
    rates = " or ".join(_amount_label(amount, cur, rate=True) for cur, amount in prices)
    if cost_accounting != "runtime":
        return f"{rates} per {cost_unit}"
    summary = f"{rates} per second of runtime"
    if max_billable_seconds:
        caps = " or ".join(
            _amount_label(
                runtime_max_charge(amount, max_billable_seconds, cur)
                if cur == "usd"
                else int(runtime_max_charge(amount, max_billable_seconds, cur)),
                cur,
            )
            for cur, amount in prices
        )
        summary += (
            f", up to {caps} per run (max {int(max_billable_seconds)}s; "
            "that much, or what you can afford, is held until the run finishes)"
        )
    return summary


def dual_price_fields(
    asset: Any,
    kind: str,
    cost_unit: Optional[str] = None,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> dict[str, Any]:
    """Fields for an asset sold in both currencies; empty when it's sold in one.

    ``kind`` is "price" (one-time unlock) or "unit_cost" (pay-per-use, with
    its ``cost_unit``). The primary currency (``price_currency``) is listed
    first: that is what's charged when the buyer doesn't pick.
    """
    amounts = {
        "usd": _getv(asset, f"{kind}_usd"),
        "btc": _getv(asset, f"{kind}_sats"),
    }
    if not all(amount is not None and float(amount) > 0 for amount in amounts.values()):
        return {}
    primary = str(_getv(asset, "price_currency") or "usd").lower()
    primary = primary if primary in amounts else "usd"
    other = "btc" if primary == "usd" else "usd"
    summary = format_dual_cost_summary(
        [(primary, amounts[primary]), (other, amounts[other])],
        cost_unit,
        cost_accounting,
        max_billable_seconds,
    )
    return {
        f"{kind}_usd": amounts["usd"],
        f"{kind}_sats": amounts["btc"],
        "currencies": [primary, other],
        "cost_summary": (
            f"{summary}. Sold in both currencies: pass currency to pick, "
            f"otherwise {primary.upper()} is charged."
        ),
    }


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
        cost_accounting = _getv(asset, "cost_accounting")
        max_billable_seconds = _getv(asset, "max_billable_seconds")
        block["cost_accounting"] = cost_accounting
        if cost_accounting == "runtime":
            block["max_billable_seconds"] = max_billable_seconds
        if unit_cost is not None:
            block["cost_summary"] = format_pay_per_use_cost_summary(
                unit_cost,
                cost_unit,
                currency,
                cost_accounting,
                max_billable_seconds,
            )
        block.update(
            dual_price_fields(
                asset, "unit_cost", cost_unit, cost_accounting, max_billable_seconds
            )
        )
    else:
        # pay-to-unlock and any other one-time-price monetization.
        price = _getv(asset, "price")
        block["price"] = price
        if price is not None:
            block["cost_summary"] = format_one_time_cost_summary(price, currency)
        block.update(dual_price_fields(asset, "price"))

    return {k: v for k, v in block.items() if v is not None}


# Models fill unused optionals with blanks or string-nulls ("null", "/null")
# instead of omitting the key or sending JSON null. Filter params only — never
# a real org/team/user id, enum, or time_window.
_ABSENT_OPTIONAL_STRINGS = frozenset({"null", "none", "undefined", "/null"})


def is_absent_optional(value: Any) -> bool:
    """True when an optional filter should be treated as omitted."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or stripped.lower() in _ABSENT_OPTIONAL_STRINGS
    return False


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


PRICE_CURRENCY_DESC = (
    'Currency for the price: "usd" (price in dollars) or "btc" (price in sats). '
    "Defaults to usd. For an asset sold in both currencies, the one charged when "
    "the buyer doesn't pick."
)
PRICE_USD_DESC = (
    "One-time unlock price in dollars. Give with price_sats to sell in both "
    "currencies (the buyer picks); 0 stops selling in USD."
)
PRICE_SATS_DESC = (
    "One-time unlock price in whole sats. Give with price_usd to sell in both "
    "currencies (the buyer picks); 0 stops selling in Bitcoin."
)
UNIT_COST_USD_DESC = (
    "Price per call (or per second) in dollars. Give with unit_cost_sats to sell "
    "in both currencies (the caller picks); 0 stops selling in USD."
)
UNIT_COST_SATS_DESC = (
    "Price per call (or per second) in sats. Give with unit_cost_usd to sell in "
    "both currencies (the caller picks); 0 stops selling in Bitcoin."
)
UNLOCK_PRICE_DESC = (
    "One-time unlock price in price_currency units. A monetized asset needs this, "
    "or price_usd / price_sats."
)
UNIT_COST_DESC = (
    "Price per call (or per second with pricing=\"per_second\") in price_currency "
    "units. A monetized route needs this, or unit_cost_usd / unit_cost_sats."
)
ROUTE_PRICING_DESC = (
    '"per_call" charges unit_cost each run. "per_second" charges unit_cost per second '
    "the service runs (from dispatch to completion), capped at max_billable_seconds; "
    "each call holds the max (or what the caller can afford) and charges the seconds "
    "used; failed runs are free. The service gets the call's budget in the "
    "ouro-max-billable-seconds header. Suits long jobs of unknown length (e.g. GPU "
    "work on Modal)."
)
MAX_BILLABLE_SECONDS_DESC = (
    'Most seconds one run can be billed (1-86400). Required with pricing="per_second".'
)


def _pricing_kwargs(visibility: Optional[str], monetization: str, **prices: Any) -> dict:
    """Monetization fields implied by ``visibility``, plus any explicit prices.

    "monetized" applies ``monetization``; any other visibility makes the asset
    free again. Omitting visibility leaves the monetization model unchanged.
    """
    fields = optional_kwargs(**prices)
    if visibility == "monetized":
        fields["monetization"] = monetization
    elif visibility is not None:
        fields["monetization"] = "none"
    return fields


def _check_price_inputs(
    visibility: Optional[str],
    name: str,
    single: Optional[float],
    usd: Optional[float],
    sats: Optional[float],
) -> None:
    """A price is given one way: ``name`` + price_currency, or per currency."""
    if single is not None and (usd is not None or sats is not None):
        raise ValueError(
            f"Give either {name} with price_currency, or {name}_usd / {name}_sats, not both."
        )
    if visibility == "monetized" and not any(
        amount is not None and amount > 0 for amount in (single, usd, sats)
    ):
        raise ValueError(
            f'visibility "monetized" requires {name} (or {name}_usd / {name}_sats).'
        )


def resolve_location(
    ouro: Any,
    org_id: Optional[str],
    team_id: Optional[str] = None,
    *,
    need_team: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    """Where a new asset goes: the caller's choice, else the pinned organization.

    When the client is pinned (OURO_ORG_ID, or X-Ouro-Org over HTTP) the SDK
    fills in the organization and default team and refuses any other
    organization. Unpinned, the caller has to say where to publish.
    """
    pinned = getattr(ouro, "organization", None)
    if isinstance(pinned, str) and pinned:
        return org_id or pinned, team_id
    if not org_id or (need_team and not team_id):
        needed = "org_id and team_id are" if need_team else "org_id is"
        raise ValueError(
            f"{needed} required: this server is not pinned to an organization. "
            "Call get_organizations() and get_teams(org_id=...) to choose where to publish."
        )
    return org_id, team_id


def unlock_pricing_kwargs(
    visibility: Optional[str],
    price: Optional[float],
    price_currency: Optional[str],
    price_usd: Optional[float] = None,
    price_sats: Optional[int] = None,
) -> dict:
    """SDK kwargs for a pay-to-unlock asset (post, file, dataset).

    ``price`` + ``price_currency`` sets a single price; ``price_usd`` and
    ``price_sats`` price the asset per currency (both = the buyer picks).
    """
    _check_price_inputs(visibility, "price", price, price_usd, price_sats)
    return _pricing_kwargs(
        visibility,
        "pay-to-unlock",
        price=price,
        price_currency=price_currency,
        price_usd=price_usd,
        price_sats=price_sats,
    )


def per_use_pricing_kwargs(
    visibility: Optional[str],
    unit_cost: Optional[float],
    price_currency: Optional[str],
    cost_unit: Optional[str],
    pricing: Optional[str] = None,
    max_billable_seconds: Optional[int] = None,
    default_pricing: Optional[str] = "per_call",
    unit_cost_usd: Optional[float] = None,
    unit_cost_sats: Optional[float] = None,
) -> dict:
    """SDK kwargs for a pay-per-use route, per call or per second of runtime.

    ``pricing`` picks the model; when omitted, a newly monetized route uses
    ``default_pricing``. Updates pass ``default_pricing=None`` so re-sending
    visibility doesn't reset a per-second route to per-call.
    """
    _check_price_inputs(visibility, "unit_cost", unit_cost, unit_cost_usd, unit_cost_sats)
    if pricing not in (None, "per_call", "per_second"):
        raise ValueError('pricing must be "per_call" or "per_second".')
    if pricing == "per_second" and max_billable_seconds is None:
        raise ValueError('pricing "per_second" requires max_billable_seconds.')
    fields = _pricing_kwargs(
        visibility,
        "pay-per-use",
        unit_cost=unit_cost,
        unit_cost_usd=unit_cost_usd,
        unit_cost_sats=unit_cost_sats,
        price_currency=price_currency,
        cost_unit=cost_unit,
    )
    model = pricing or (default_pricing if visibility == "monetized" else None)
    if model == "per_second":
        fields["cost_accounting"] = "runtime"
        fields["cost_unit"] = "seconds"
        fields["max_billable_seconds"] = max_billable_seconds
    elif model == "per_call":
        fields["cost_accounting"] = "fixed"
        fields.setdefault("cost_unit", "call")
    elif max_billable_seconds is not None:
        # Adjusting the cap of an existing per-second route
        fields["max_billable_seconds"] = max_billable_seconds
    return fields


def present_kwargs(**kw: Any) -> dict:
    """Like optional_kwargs, also dropping blank and string-null sentinels.

    Use for filter/search params where models often send ``""``, ``"null"``,
    or ``"/null"`` for unused optionals. Do **not** use for update fields
    that treat ``""`` as an explicit clear (e.g. quest waiting_*).
    """
    return {k: v for k, v in kw.items() if not is_absent_optional(v)}


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
                contains_file_extensions=config.get("contains_file_extensions")
                or config.get("containsFileExtensions"),
                # Not every run produces this output
                optional=config.get("optional"),
                # Delivered inside another output (a post) rather than on its own
                embedded_in=config.get("embedded_in") or config.get("embeddedIn"),
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


def route_description_markdown(description: str | None) -> str | None:
    """Render ``RouteData.description``, which stores RichText as a JSON string."""
    from ouro.utils.content import description_to_markdown

    if not description:
        return None
    try:
        parsed = json.loads(description)
    except ValueError:
        return description
    if not isinstance(parsed, dict):
        return description
    return description_to_markdown(parsed) or None


def route_request_body_without_input_assets(route: Any) -> Any:
    """Hide Ouro-resolved asset object schemas from route execution metadata.

    Agents should pass IDs via the canonical keyed ``input_assets`` mapping.
    The backend expands those IDs into the service-facing body object.
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


def resolve_team_policy(team: Any, field: str, default: str = "any") -> str:
    """Return the effective policy for a team, falling back to the org's policy."""
    return getattr(team, field) or getattr(team.organization, field, None) or default
