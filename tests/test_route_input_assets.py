from __future__ import annotations

from types import SimpleNamespace

from ouro_mcp.pricing import (
    format_monetization_block,
)
from ouro_mcp.routes import (
    route_input_assets_summary,
    route_output_assets_for_body,
    route_output_assets_summary,
    route_properties_for_body,
    route_request_body_without_input_assets,
    route_response_charts_summary,
)
from ouro_mcp.tools.assets import _format_asset_detail
from ouro_mcp.tools.services import _format_route_cost_preview


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


def test_route_input_assets_summary_returns_supported_metadata() -> None:
    route = SimpleNamespace(
        input_type=None,
        input_assets={
            "file": {
                "asset_type": "file",
                "file_extensions": ["cif"],
            },
        },
    )

    assert route_input_assets_summary(route) == {
        "file": {"asset_type": "file", "file_extensions": ["cif"]}
    }


def test_route_input_assets_summary_synthesizes_legacy_input_type() -> None:
    route = SimpleNamespace(
        input_type="file",
        input_assets=None,
        input_filter=None,
        input_file_extension="cif",
        input_file_extensions=None,
    )

    assert route_input_assets_summary(route) == {
        "file": {
            "asset_type": "file",
            "primary": True,
            "file_extensions": ["cif"],
        }
    }


def test_route_input_assets_summary_fills_sparse_keyed_from_legacy() -> None:
    route = SimpleNamespace(
        input_type="file",
        input_assets={"file": {}},
        input_filter=None,
        input_file_extension="cif",
        input_file_extensions=None,
    )

    assert route_input_assets_summary(route) == {
        "file": {
            "asset_type": "file",
            "primary": True,
            "file_extensions": ["cif"],
        }
    }


def test_route_input_assets_summary_reads_pydantic_declarations() -> None:
    declaration = SimpleNamespace(
        model_dump=lambda exclude_none=True: {
            "asset_type": "file",
            "primary": True,
            "file_extensions": ["cif"],
        }
    )
    route = SimpleNamespace(
        input_type="file",
        input_assets={"file": declaration},
        input_filter=None,
        input_file_extension=None,
        input_file_extensions=None,
    )

    assert route_input_assets_summary(route) == {
        "file": {
            "asset_type": "file",
            "primary": True,
            "file_extensions": ["cif"],
        }
    }


def test_route_output_assets_summary_prefers_plural_declarations() -> None:
    route = SimpleNamespace(
        output_type="file",
        output_assets={
            "primary_file": {
                "asset_type": "file",
                "primary": True,
                "file_extensions": ["cif"],
            },
            "audit_log": {"asset_type": "post"},
        },
        output_file_extension=None,
    )

    assert route_output_assets_summary(route) == {
        "primary_file": {
            "asset_type": "file",
            "primary": True,
            "file_extensions": ["cif"],
        },
        "audit_log": {"asset_type": "post"},
    }


def test_route_output_assets_summary_keeps_optional_and_embedding() -> None:
    declarations = {
        "report": {"asset_type": "post", "primary": True, "optional": True},
        "candidate_cifs": {
            "asset_type": "file",
            "file_extensions": ["zip"],
            "contains_file_extensions": ["cif"],
            "optional": True,
            "embedded_in": "report",
        },
    }
    route = SimpleNamespace(
        output_type="post", output_assets=declarations, output_file_extension=None
    )

    assert route_output_assets_summary(route) == declarations


def test_route_output_assets_for_body_resolves_when() -> None:
    declarations = {
        "report": {"asset_type": "post", "primary": True, "when": {"include_report": True}},
        "summary": {"asset_type": "file", "embedded_in": "report"},
        "phases_dataset": {"asset_type": "dataset", "when": {"include_dataset": True}},
    }
    route = SimpleNamespace(
        output_type="post",
        output_assets=declarations,
        output_file_extension=None,
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "include_report": {"type": "boolean", "default": False},
                            "include_dataset": {"type": "boolean", "default": False},
                        },
                    }
                }
            }
        },
    )

    assert route_output_assets_summary(route)["report"]["when"] == {"include_report": True}

    off = route_output_assets_for_body(route, {})
    assert off["report"]["will_create"] is False
    assert off["summary"]["will_create"] is True
    assert "embedded" not in off["summary"]
    assert off["phases_dataset"]["will_create"] is False

    on = route_output_assets_for_body(route, {"include_report": True})
    assert on["report"]["will_create"] is True
    assert on["summary"]["embedded"] is True
    assert on["phases_dataset"]["will_create"] is False


def test_route_output_assets_summary_synthesizes_legacy_output_type() -> None:
    route = SimpleNamespace(
        output_type="post",
        output_assets=None,
        output_file_extension=None,
    )

    assert route_output_assets_summary(route) == {
        "post": {"asset_type": "post", "primary": True}
    }


def test_route_output_assets_summary_returns_none_when_no_outputs() -> None:
    route = SimpleNamespace(output_type=None, output_assets=None)
    assert route_output_assets_summary(route) is None


def test_btc_pay_per_use_cost_summary_uses_sats() -> None:
    asset = SimpleNamespace(
        monetization="pay-per-use",
        price_currency="btc",
        unit_cost=100.0,
        cost_unit="call",
        cost_accounting="fixed",
    )

    assert format_monetization_block(asset) == {
        "monetization": "pay-per-use",
        "price_currency": "btc",
        "unit_cost": 100.0,
        "cost_unit": "call",
        "cost_accounting": "fixed",
        "cost_summary": "100 sats per call (BTC)",
    }


def test_btc_route_cost_preview_uses_sats() -> None:
    route = SimpleNamespace(
        monetization="pay-per-use",
        price_currency="btc",
        unit_cost=100.0,
        cost_unit="call",
        cost_accounting="fixed",
    )

    assert _format_route_cost_preview(route) == {
        "monetization": "pay-per-use",
        "price_currency": "btc",
        "warning": "This route will charge the caller when executed.",
        "unit_cost": 100.0,
        "cost_unit": "call",
        "cost_accounting": "fixed",
        "cost_summary": "100 sats per call (BTC)",
    }


class _FakeAssets:
    def actions(self, asset_id: str, *, role: str = "both", **kwargs):  # noqa: ARG002
        return {"created_by": None, "as_input": []}

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


def test_composed_request_body_still_hides_input_assets() -> None:
    route = SimpleNamespace(
        input_type=None,
        input_assets={"structure": {"asset_type": "file", "primary": True}},
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": [
                            {
                                "type": "object",
                                "required": ["structure", "steps"],
                                "properties": {
                                    "structure": {"type": "object"},
                                    "steps": {"type": "integer"},
                                },
                            },
                            {"type": "null"},
                        ]
                    }
                }
            }
        },
    )

    cleaned = route_request_body_without_input_assets(route)
    variant = cleaned["content"]["application/json"]["schema"]["anyOf"][0]
    assert set(variant["properties"]) == {"steps"}
    assert variant["required"] == ["steps"]
    # The route's own schema is untouched
    original = route.request_body["content"]["application/json"]["schema"]["anyOf"][0]
    assert "structure" in original["properties"]


def test_legacy_only_output_is_always_created() -> None:
    route = SimpleNamespace(output_type="file", output_assets=None, output_file_extension="cif")
    assert route_output_assets_for_body(route, {})["file"]["will_create"] is True


def test_properties_for_body_follow_when() -> None:
    route = SimpleNamespace(
        properties=[
            {"property": "energy_above_hull", "value": "e", "method": "Orb v3",
             "when": {"energy_surface": "orb"}},
            {"property": "energy_above_hull", "value": "e", "method": "PBE",
             "when": {"energy_surface": "pbe"}, "uncertainty": "e_err"},
        ],
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"energy_surface": {"type": "string", "default": "orb"}},
                    }
                }
            }
        },
    )

    default = route_properties_for_body(route, {})
    assert [p["will_record"] for p in default] == [True, False]
    assert default[1]["uncertainty"] == "e_err"
    pbe = route_properties_for_body(route, {"energy_surface": "pbe"})
    assert [p["will_record"] for p in pbe] == [False, True]


def test_response_charts_summary_names_list_and_dataset() -> None:
    route = SimpleNamespace(
        response_charts=[
            {"data": "magnetization_curve", "title": "Magnetization curve",
             "dataset": "magnetization_curve_dataset", "config": {"type": "line"}},
            {"data": "shells", "title": "Exchange", "config": {"type": "bar"}},
        ]
    )
    assert route_response_charts_summary(route) == [
        {"title": "Magnetization curve", "data": "magnetization_curve",
         "dataset": "magnetization_curve_dataset"},
        {"title": "Exchange", "data": "shells"},
    ]
    assert route_response_charts_summary(SimpleNamespace(response_charts=None)) is None


def test_route_output_assets_summary_keeps_role() -> None:
    route = SimpleNamespace(
        output_type="file",
        output_assets={
            "file": {"asset_type": "file", "primary": True, "role": "version"},
            "trajectory": {"asset_type": "file", "role": "supporting"},
        },
        output_file_extension=None,
    )

    summary = route_output_assets_summary(route)
    assert summary["file"]["role"] == "version"
    assert summary["trajectory"]["role"] == "supporting"


def test_full_asset_lists_what_came_from_it() -> None:
    from ouro_mcp.tools.assets import _derived_part

    run = SimpleNamespace(action_id="act-1", route={"id": "r", "name": "Phonon dispersion"})
    derived = [
        SimpleNamespace(
            id=f"d{i}", name=f"Derived {i}", asset_type="file", lineage_role="result", run=run
        )
        for i in range(25)
    ]
    assets = SimpleNamespace(
        actions=lambda *a, **k: SimpleNamespace(created_by=None),
        connections=lambda *a, **k: [],
        tags=lambda *a, **k: [],
        lineage=lambda *a, **k: SimpleNamespace(derived=derived, truncated=False),
    )
    result = _derived_part(SimpleNamespace(assets=assets), "a")

    assert len(result["derived"]) == 20
    assert result["derived_has_more"] is True
    assert result["derived"][0] == {
        "id": "d0",
        "name": "Derived 0",
        "asset_type": "file",
        "lineage_role": "result",
        "action_id": "act-1",
        "route_name": "Phonon dispersion",
    }
