"""Compact creation_action pointer on get_asset(detail=full)."""

from __future__ import annotations

from types import SimpleNamespace

from ouro_mcp.tools.assets import _compact_creation_action


def test_compact_creation_action_from_dict():
    pointer = _compact_creation_action(
        {
            "id": "019fb98d-6617-7810-9731-aa370144e944",
            "status": "success",
            "route_id": "4623c347-ec8b-4516-81eb-c989cbf5c940",
            "response": {"scores": [{"x": 1}] * 100},
            "metadata": {"huge": True},
        }
    )
    assert pointer == {
        "action_id": "019fb98d-6617-7810-9731-aa370144e944",
        "action_status": "success",
        "route_id": "4623c347-ec8b-4516-81eb-c989cbf5c940",
    }


def test_compact_creation_action_from_model_like():
    action = SimpleNamespace(
        id="action-1",
        status="error",
        route_id=None,
        route=SimpleNamespace(id="route-9"),
    )
    assert _compact_creation_action(action) == {
        "action_id": "action-1",
        "action_status": "error",
        "route_id": "route-9",
    }


def test_compact_creation_action_none():
    assert _compact_creation_action(None) is None
