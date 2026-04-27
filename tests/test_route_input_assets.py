from __future__ import annotations

from types import SimpleNamespace

import pytest

from ouro_mcp.tools.services import _parse_json_param
from ouro_mcp.utils import route_input_assets_summary, route_request_body_without_input_assets


def test_parse_input_assets_allows_keyed_asset_ids() -> None:
    assert _parse_json_param(
        '{"structure": "file-id", "reference_dataset": {"assetId": "dataset-id"}}',
        "input_assets",
    ) == {
        "structure": "file-id",
        "reference_dataset": {"assetId": "dataset-id"},
    }


def test_parse_input_assets_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        _parse_json_param('["file-id"]', "input_assets")


def test_route_request_body_hides_asset_input_schema() -> None:
    route = SimpleNamespace(
        input_type=None,
        input_assets={
            "structure": {"asset_type": "file"},
            "reference_dataset": {"asset_type": "dataset"},
        },
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["structure", "threshold"],
                        "properties": {
                            "structure": {"type": "object"},
                            "reference_dataset": {"type": "object"},
                            "threshold": {"type": "number"},
                        },
                    }
                }
            }
        },
    )

    cleaned = route_request_body_without_input_assets(route)
    properties = cleaned["content"]["application/json"]["schema"]["properties"]

    assert set(properties) == {"threshold"}
    assert cleaned["content"]["application/json"]["schema"]["required"] == ["threshold"]
    assert route_input_assets_summary(route) == {
        "structure": {"asset_type": "file", "body_path": "structure"},
        "reference_dataset": {
            "asset_type": "dataset",
            "body_path": "reference_dataset",
        },
    }
