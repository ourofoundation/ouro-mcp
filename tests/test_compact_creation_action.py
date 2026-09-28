"""Compact creation_action pointer on get_asset(detail=full)."""

from __future__ import annotations

from ouro.models import Action

from ouro_mcp.tools.assets import _compact_creation_action


def test_compact_creation_action_keeps_only_the_pointer():
    action = Action.model_validate(
        {
            "id": "019fb98d-6617-7810-9731-aa370144e944",
            "route_id": "4623c347-ec8b-4516-81eb-c989cbf5c940",
            "user_id": "71dbf145-4b05-42f7-be22-b65a2574e1a9",
            "status": "success",
            "response": {"scores": [{"x": 1}] * 100},
            "metadata": {"huge": True},
        }
    )
    assert _compact_creation_action(action) == {
        "action_id": "019fb98d-6617-7810-9731-aa370144e944",
        "action_status": "success",
        "route_id": "4623c347-ec8b-4516-81eb-c989cbf5c940",
    }
