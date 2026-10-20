"""format_search_hit keeps discovery fields only."""

from ouro_mcp.summaries import (
    format_search_hit,
)


def test_format_search_hit_keeps_slim_fields():
    hit = format_search_hit(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "name": "Energy gate notes",
            "asset_type": "post",
            "description": "A short summary of the gate.",
            "visibility": "public",
            "state": "published",
            "source": "web",
            "created_at": "2026-07-01T12:00:00+00:00",
            "last_updated": "2026-07-02T12:00:00+00:00",
            "parent_id": "ffffffff-0000-1111-2222-333333333333",
            "user": {"id": "user-1", "username": "apollo", "is_agent": True},
            "organization": {"id": "org-1", "name": "ouro"},
            "team": {"id": "team-1", "name": "research"},
            "monetization": "pay-per-use",
            "unit_cost": 0.01,
            "snippet": "...matching passage...",
            "match_source": "body",
        }
    )

    assert hit == {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "Energy gate notes",
        "asset_type": "post",
        "created_at": "2026-07-01T12:00:00+00:00",
        "description": "A short summary of the gate.",
        "username": "apollo",
        "snippet": "...matching passage...",
        "match_source": "body",
    }


def test_format_search_hit_drops_summary_snippets():
    hit = format_search_hit(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "name": "magnets",
            "asset_type": "dataset",
            "description": "Six magnet types",
            "snippet": "dataset: magnets Six magnet types",
            "match_source": "summary",
        }
    )

    assert "snippet" not in hit
    assert "match_source" not in hit


def test_format_search_hit_omits_absent_optional_fields():
    hit = format_search_hit(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "name": "Bare asset",
            "asset_type": "file",
            "created_at": None,
        }
    )

    assert hit == {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "Bare asset",
        "asset_type": "file",
        "created_at": None,
    }
    assert "description" not in hit
    assert "username" not in hit
    assert "snippet" not in hit
    assert "match_source" not in hit


def test_route_snippet_that_repeats_the_row_is_dropped() -> None:
    from ouro_mcp.summaries import (
        format_search_hit,
    )

    description = (
        "Optimize atomic positions and (optionally) unit-cell parameters of a crystal structure "
        "using a configurable machine learning interatomic potential such as Orb, MACE, or CHGNet."
    )
    row = format_search_hit(
        {
            "id": "d040d3b6-faad-40cf-9d7c-999a5c769ed8",
            "name": "Relax a crystal structure",
            "asset_type": "route",
            "description": description,
            "snippet": "Relax a crystal structure route: Relax a crystal structure "
            "POST /materials/structure/relax " + description[:150] + "…",
            "match_source": "route",
        }
    )

    assert "snippet" not in row
    assert "match_source" not in row
