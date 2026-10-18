from ouro.models import AssetProperty, RouteData

from ouro_mcp.utils import format_asset_properties, route_properties_summary

ROUTE_ID = "00000000-0000-0000-0000-000000000002"
ACTION_ID = "00000000-0000-0000-0000-000000000011"


def _value(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000010",
        "value": 512.5,
        "source": "route",
        "status": "active",
    }
    base.update(overrides)
    return base


def test_platform_values_flatten_and_route_values_keep_provenance():
    properties = [
        AssetProperty.model_validate(
            {"key": "word_count", "label": "Word count", "value_type": "integer", "values": [_value(value=42, source="platform", producer="post-text")]}
        ),
        AssetProperty.model_validate(
            {
                "key": "curie_temperature",
                "label": "Curie temperature",
                "value_type": "number",
                "unit": "K",
                "values": [
                    _value(method="Monte Carlo", action_id=ACTION_ID, route={"id": ROUTE_ID, "name": "Curie"}),
                    _value(value=431.2, uncertainty=18, method="Mean field"),
                ],
            }
        ),
        AssetProperty.model_validate({"key": "band_gap", "label": "Band gap", "value_type": "number", "values": []}),
    ]
    assert format_asset_properties(properties) == {
        "word_count": 42,
        "curie_temperature": {
            "unit": "K",
            "values": [
                {"value": 512.5, "method": "Monte Carlo", "route": "Curie", "route_id": ROUTE_ID, "action_id": ACTION_ID},
                {"value": 431.2, "uncertainty": 18.0, "method": "Mean field"},
            ],
        },
    }
    assert format_asset_properties([]) is None


def test_route_properties_summary():
    route = RouteData(
        path="/curie",
        method="POST",
        properties=[{"property": "curie_temperature", "value": "tc", "unit": "K", "subject": {"output": "relaxed"}}],
    )
    assert route_properties_summary(route) == [
        {"property": "curie_temperature", "value": "tc", "unit": "K", "subject": {"output": "relaxed"}}
    ]
    assert route_properties_summary(RouteData(path="/x", method="GET")) is None
