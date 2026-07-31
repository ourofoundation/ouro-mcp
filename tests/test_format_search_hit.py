"""format_search_hit keeps discovery fields only."""

from ouro_mcp.utils import format_search_hit


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
