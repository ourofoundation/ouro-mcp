"""What tools send back: compact markdown lists and tables, JSON, timestamps and size limits."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ouro_mcp.constants import (
    DEFAULT_RESPONSE_FORMAT,
    ENV_OURO_MCP_MAX_RESPONSE_SIZE,
    ENV_OURO_MCP_RESPONSE_FORMAT,
    ENV_OURO_MCP_TIMEZONE,
)

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
    "last_active",
}


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


def _localize_timestamp(value: Any, tz_name: str, *, compact: bool = False) -> str | None:
    """Render a UTC timestamp in the configured zone.

    JSON gets a local ISO string with its offset (``2026-04-06T21:02:19-05:00``).
    ``compact`` is for markdown rows, where a reader scans many at once:
    ``2026-04-06 21:02 CDT``. Microseconds are dropped either way.
    """
    dt = _parse_timestamp_value(value)
    if dt is None:
        return None

    try:
        local_dt = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None

    if compact:
        return f"{local_dt:%Y-%m-%d %H:%M} {local_dt.tzname() or tz_name}"
    return local_dt.replace(microsecond=0).isoformat()


def enrich_timestamps(data: Any, tz_name: str | None = None, *, compact: bool = False) -> Any:
    """Recursively rewrite common UTC timestamp fields as compact local ISO.

    Every recognized timestamp key is replaced *in place* with a single
    offset-bearing ISO string in ``OURO_MCP_TIMEZONE`` (default UTC), e.g.
    ``2026-04-06T21:02:19-05:00``. The offset preserves the absolute
    instant, and using one field instead of a UTC value plus ``_local`` /
    ``_local_label`` siblings keeps tool responses small enough for agents
    listing many assets at once. Existing ``_local`` / ``_local_label``
    fields on the input are dropped so older callers don't double up.
    ``compact`` uses the markdown-row style (see ``_localize_timestamp``).
    """
    active_tz = tz_name or _configured_timezone_name()
    if isinstance(data, list):
        return [enrich_timestamps(item, active_tz, compact=compact) for item in data]

    if not isinstance(data, dict):
        return data

    enriched: dict[str, Any] = {}
    for key, value in data.items():
        if key.endswith("_local") or key.endswith("_local_label"):
            base = key.rsplit("_local", 1)[0]
            if base in _TIMESTAMP_KEYS:
                continue
        if key in _TIMESTAMP_KEYS and value is not None:
            localized = _localize_timestamp(value, active_tz, compact=compact)
            enriched[key] = localized if localized is not None else value
            continue
        enriched[key] = enrich_timestamps(value, active_tz, compact=compact)

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

    Shape: ``{"results": [...], "total": int | None, "has_more": bool,
    "next_cursor": Any | None, **extra}``. The input ``pagination`` keeps the
    backend's camelCase (``hasMore``, ``nextCursor``).

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
        "has_more": resolved_has_more,
    }
    if resolved_next_cursor is not None:
        payload["next_cursor"] = resolved_next_cursor
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


def clip(text: str, max_length: int | None) -> str:
    """``text`` cut to ``max_length`` characters, ending in "…" when cut."""
    if max_length is None or len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def collapse_whitespace(text: Any, max_length: int | None = None) -> str:
    """Collapse newlines/runs of whitespace into a single readable line."""
    if text is None:
        return ""
    return clip(re.sub(r"\s+", " ", str(text)).strip(), max_length)


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
    localized = _localize_timestamp(value, _configured_timezone_name(), compact=True)
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


def counted(noun: str, count: int | None) -> str:
    """A plural noun ("rows", "leaderboard entries") made singular for a count of 1."""
    if count != 1:
        return noun
    if noun.endswith("ies"):
        return noun[:-3] + "y"
    if noun.endswith("s") and not noun.endswith("ss"):
        return noun[:-1]
    return noun


def format_markdown_list_header(
    *,
    shown: int,
    total: int | None = None,
    has_more: bool = False,
    offset: int | None = None,
    noun: str = "results",
    empty_text: str = "No results.",
    extras: list[str] | None = None,
    next_page: str | None = None,
) -> str:
    """Build the header line(s) for a markdown list response.

    ``next_page`` is the argument that loads the next page (e.g.
    ``before=`...```) for tools that page by cursor; it defaults to the next
    ``offset``.
    """
    next_hint = next_page or f"offset={(offset or 0) + shown}"
    if shown == 0 and not has_more and (total is None or total == 0):
        header = empty_text
    elif total is not None:
        header = f"Found {total} {counted(noun, total)}"
        if shown != total or has_more:
            header += f" (showing {shown}"
            if has_more:
                header += f"; more available — call again with {next_hint}"
            header += ")"
    else:
        header = f"Found {shown} {counted(noun, shown)}"
        if has_more:
            header += f" (more available — call again with {next_hint})"
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
    next_page: str | None = None,
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

    lines = [line for line in (line_fn(enrich_timestamps(item, compact=True)) for item in items) if line]

    header = format_markdown_list_header(
        shown=len(lines),
        total=resolved_total if resolved_total is not None else None,
        has_more=resolved_has_more,
        offset=offset,
        noun=noun,
        empty_text=empty_text,
        extras=extras,
        next_page=next_page,
    )
    if not lines:
        return header

    result = f"{header}\n\n" + "\n".join(lines)
    _assert_no_parallel_header(result)
    return result


# Enough digits for energy differences; drops float noise like 0.30000000000000004
TABLE_FLOAT_DIGITS = 10


def _format_table_float(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.{TABLE_FLOAT_DIGITS}g}"
    # %g switches to exponents past its digits; large values read better whole
    return f"{value:.0f}" if "e+" in text else text


def _escape_md_table_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return _format_table_float(value)
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
    total: int | None = None,
    sql: str | None = None,
    resolved_refs: dict[str, Any] | None = None,
    next_cursor: str | None = None,
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
        payload["has_more"] = bool(has_more)
    if total is not None:
        payload["total"] = total
    if sql:
        payload["sql"] = sql
    if resolved_refs is not None:
        payload["resolved_refs"] = resolved_refs
    if next_cursor:
        payload["next_cursor"] = next_cursor

    if fmt == "json":
        return dump_json(payload)

    if shown == 0 and not has_more:
        header = empty_text
    elif row_count is not None and offset is None:
        header = f"Found {row_count} {counted('rows', row_count)}"
    else:
        header = (
            f"Found {shown} of {total} rows"
            if total is not None and total != shown
            else f"Found {shown} {counted('rows', shown)}"
        )
        bits: list[str] = []
        if offset is not None:
            bits.append(f"offset={offset}")
        if limit is not None:
            bits.append(f"limit={limit}")
        if has_more and next_cursor:
            bits.append(f"more available — call again with cursor={next_cursor}")
        elif has_more:
            next_offset = (offset or 0) + shown
            bits.append(f"more available — call again with offset={next_offset}")
        if bits:
            header += f" ({'; '.join(bits)})"

    parts = [header]
    if sql:
        parts.append(f"SQL: `{sql}`")
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
        parts.extend(text for text in (collapse_whitespace(line) for line in preamble.splitlines()) if text)

    any_items = False
    for title, items in sections.items():
        if not items:
            continue
        any_items = True
        parts.append(f"## {collapse_whitespace(title) or title}")
        for item in items:
            line = line_fn(enrich_timestamps(item, compact=True))
            if line:
                parts.append(line)

    if not any_items:
        parts.append(empty_text)

    result = "\n".join(parts)
    _assert_no_parallel_header(result)
    return result
