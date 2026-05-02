from __future__ import annotations

from types import SimpleNamespace

import pytest

from ouro_mcp.tools.assets import _format_asset_detail
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
        "structure": {"asset_type": "file"},
        "reference_dataset": {"asset_type": "dataset"},
    }


def test_route_input_assets_summary_omits_body_path_metadata() -> None:
    route = SimpleNamespace(
        input_type=None,
        input_assets={
            "file": {
                "asset_type": "file",
                "body_path": "service_specific_body_field",
            },
        },
    )

    assert route_input_assets_summary(route) == {"file": {"asset_type": "file"}}


class _FakeAssets:
    def creation_actions(self, asset_id: str):  # noqa: ARG002
        return None

    def connections(self, asset_id: str):  # noqa: ARG002
        return None

    def tags(self, asset_id: str):  # noqa: ARG002
        return None


class _FakeServices:
    def read_routes(self, service_id: str):  # noqa: ARG002
        return [
            SimpleNamespace(
                id="route-1",
                name="Analyze File",
                route=SimpleNamespace(
                    method="POST",
                    path="/internal/analyze",
                    description="Analyze a file",
                ),
            )
        ]


def _asset(**overrides):
    base = {
        "id": "asset-1",
        "name": "Asset",
        "asset_type": "route",
        "visibility": "public",
        "created_at": None,
        "last_updated": None,
        "description": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_route_detail_hides_http_method_and_path() -> None:
    detail = _format_asset_detail(
        _asset(
            route=SimpleNamespace(
                method="POST",
                path="/internal/analyze",
                description="Analyze a file",
                parameters=None,
                request_body={},
                input_assets={"file": {"asset_type": "file"}},
                input_type="file",
                output_type=None,
            )
        ),
        SimpleNamespace(assets=_FakeAssets()),
    )

    assert "method" not in detail
    assert "path" not in detail
    assert "input_type" not in detail
    assert detail["input_assets"] == {"file": {"asset_type": "file"}}


def test_service_route_list_hides_http_method_and_path() -> None:
    detail = _format_asset_detail(
        _asset(asset_type="service"),
        SimpleNamespace(assets=_FakeAssets(), services=_FakeServices()),
    )

    assert detail["routes"] == [
        {"id": "route-1", "name": "Analyze File", "description": "Analyze a file"}
    ]
