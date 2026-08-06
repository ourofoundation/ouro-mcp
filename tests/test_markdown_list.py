"""Markdown list renderer + truncate_response for hybrid MCP outputs."""

from __future__ import annotations

import json

import pytest

from ouro_mcp.constants import (
    ENV_OURO_MCP_MAX_RESPONSE_SIZE,
    ENV_OURO_MCP_RESPONSE_FORMAT,
    MAX_RESPONSE_SIZE,
)
from ouro_mcp.utils import (
    collapse_whitespace,
    format_search_hit,
    format_table_response,
    markdown_bullet,
    render_markdown_list,
    render_markdown_sections,
    render_markdown_table,
    resolve_max_response_size,
    resolve_response_format,
    search_hit_line,
    truncate_response,
)


def test_collapse_whitespace_and_cap():
    assert collapse_whitespace("a\n\nb\t c") == "a b c"
    assert collapse_whitespace("abcdefghij", max_length=6) == "abcde…"


def test_search_hit_line_format():
    hit = format_search_hit(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "name": "Energy gate notes",
            "asset_type": "post",
            "description": "A short\nsummary",
            "created_at": "2026-07-01T12:00:00+00:00",
            "user": {"username": "apollo"},
            "snippet": "matching\npassage",
            "match_source": "body",
        }
    )
    line = search_hit_line(hit)
    assert line.startswith(
        "- **Energy gate notes** (post) — id: "
        "`aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` — by @apollo — "
        "2026-07-01T12:00:00+00:00"
    )
    assert "\n  A short summary · [body] matching passage" in line
    assert "\n" in line  # body on second line


def test_render_markdown_list_header_and_pagination():
    hits = [
        {
            "id": "1",
            "name": "One",
            "asset_type": "post",
            "created_at": None,
        },
        {
            "id": "2",
            "name": "Two",
            "asset_type": "file",
            "created_at": None,
        },
    ]
    text = render_markdown_list(
        hits,
        line_fn=search_hit_line,
        total=24,
        has_more=True,
        offset=0,
        noun="assets",
    )
    assert text.startswith(
        "Found 24 assets (showing 2; more available — call again with offset=2)"
    )
    assert "- **One** (post) — id: `1`" in text
    assert "- **Two** (file) — id: `2`" in text


def test_render_markdown_list_json_format(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_RESPONSE_FORMAT, "json")
    hits = [
        {"id": "1", "name": "One", "asset_type": "post"},
        {"id": "2", "name": "Two", "asset_type": "file"},
    ]
    payload = json.loads(
        render_markdown_list(
            hits,
            line_fn=search_hit_line,
            total=24,
            has_more=True,
            offset=0,
            limit=2,
            noun="assets",
            extra={"scope": "public"},
        )
    )
    assert payload == {
        "results": hits,
        "total": 24,
        "hasMore": True,
        "limit": 2,
        "scope": "public",
    }


def test_resolve_response_format_override_and_env(monkeypatch):
    monkeypatch.delenv(ENV_OURO_MCP_RESPONSE_FORMAT, raising=False)
    assert resolve_response_format() == "md"
    monkeypatch.setenv(ENV_OURO_MCP_RESPONSE_FORMAT, "json")
    assert resolve_response_format() == "json"
    assert resolve_response_format("md") == "md"
    with pytest.raises(ValueError, match="Invalid response_format"):
        resolve_response_format("xml")


def test_render_markdown_list_empty():
    text = render_markdown_list(
        [],
        line_fn=search_hit_line,
        empty_text="No assets found.",
    )
    assert text == "No assets found."


def test_parallel_header_guard():
    with pytest.raises(ValueError, match="=== "):
        render_markdown_list(
            [{}],
            line_fn=lambda _: "=== Tool result: x ===\nhi",
        )


def test_render_markdown_sections():
    text = render_markdown_sections(
        {
            "references": [{"id": "a", "name": "Ref", "asset_type": "post"}],
            "derivatives": [],
        },
        line_fn=lambda row: markdown_bullet(
            row["name"], f"id: `{row['id']}`", kind=row["asset_type"]
        ),
        preamble="Connections for asset `parent`",
    )
    assert text.startswith("Connections for asset `parent`")
    assert "## references" in text
    assert "- **Ref** (post) — id: `a`" in text
    assert "## derivatives" not in text


def test_render_markdown_table_and_format_table_response():
    rows = [
        {"name": "a|b", "note": "line1\nline2"},
        {"name": "c", "note": None},
    ]
    table = render_markdown_table(rows)
    assert table.splitlines()[0] == "| name | note |"
    assert "| a\\|b | line1 line2 |" in table
    assert "| c |  |" in table

    md = format_table_response(
        rows,
        offset=0,
        limit=10,
        has_more=True,
    )
    assert md.startswith(
        "Found 2 rows (offset=0; limit=10; more available — call again with offset=2)"
    )
    assert "| name | note |" in md

    payload = json.loads(
        format_table_response(
            rows, offset=0, limit=10, has_more=False, response_format="json"
        )
    )
    assert payload["rows"] == rows
    assert payload["hasMore"] is False


def test_truncate_response_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENV_OURO_MCP_MAX_RESPONSE_SIZE, raising=False)
    assert resolve_max_response_size() is None

    lines = [f"- **item {i}** — id: `{i}`" for i in range(5000)]
    payload = "Found many results\n\n" + "\n".join(lines)
    assert len(payload) > MAX_RESPONSE_SIZE
    assert truncate_response(payload) == payload


def test_truncate_response_respects_zero(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_MAX_RESPONSE_SIZE, "0")
    assert resolve_max_response_size() is None
    payload = "x" * (MAX_RESPONSE_SIZE + 100)
    assert truncate_response(payload) == payload


def test_truncate_response_markdown_at_line_boundary(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_MAX_RESPONSE_SIZE, str(MAX_RESPONSE_SIZE))
    # Build a payload larger than MAX_RESPONSE_SIZE with clear line breaks.
    lines = [f"- **item {i}** — id: `{i}`" for i in range(5000)]
    payload = "Found many results\n\n" + "\n".join(lines)
    assert len(payload) > MAX_RESPONSE_SIZE

    out = truncate_response(payload)
    assert len(out) <= MAX_RESPONSE_SIZE + 80  # footer slack
    assert out.endswith("… [truncated — call with smaller limit/offset]")
    # Truncated body should not end mid-line (footer excluded).
    body = out[: -len("… [truncated — call with smaller limit/offset]")]
    assert not body.endswith("**")
    assert body.rstrip("\n").splitlines()[-1].startswith("- **")


def test_truncate_response_markdown_table_drops_rows(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_MAX_RESPONSE_SIZE, str(MAX_RESPONSE_SIZE))
    header = "Found many rows\n\n| col |\n| --- |\n"
    rows = "\n".join(f"| value-{i}-{'x' * 80} |" for i in range(2000))
    payload = header + rows
    assert len(payload) > MAX_RESPONSE_SIZE

    out = truncate_response(payload)
    assert out.endswith("… [truncated — call with smaller limit/offset]")
    assert "| col |" in out
    assert "| --- |" in out
    assert out.count("| value-") < 2000
    assert len(out) <= MAX_RESPONSE_SIZE + 80
