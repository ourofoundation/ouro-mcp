from __future__ import annotations

import pytest

from ouro_mcp.utils import json_arg


def test_accepts_objects_and_json_strings() -> None:
    value = {"type": "bar", "series": [{"dataKey": "total"}]}
    assert json_arg(value, "config", kind=dict) == value
    assert json_arg('{"type":"bar","series":[{"dataKey":"total"}]}', "config", kind=dict) == value


def test_keyed_input_assets() -> None:
    assert json_arg(
        '{"structure": "file-id", "reference_dataset": {"assetId": "dataset-id"}}',
        "input_assets",
        kind=dict,
    ) == {"structure": "file-id", "reference_dataset": {"assetId": "dataset-id"}}


def test_omitted_is_none() -> None:
    assert json_arg(None, "body") is None
    assert json_arg("", "body") is None


def test_rejects_the_wrong_kind() -> None:
    with pytest.raises(ValueError, match="input_assets must be a JSON object"):
        json_arg('["file-id"]', "input_assets", kind=dict)
    with pytest.raises(ValueError, match="operations must be a JSON array"):
        json_arg('{"op": "add"}', "operations", kind=list)


def test_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="metadata_filters is not valid JSON"):
        json_arg("{not json", "metadata_filters", kind=dict)
