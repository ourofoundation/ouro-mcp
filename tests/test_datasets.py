from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from ouro_mcp.tools.datasets import _resolve_dataset_data, register


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeDatasets:
    def __init__(
        self,
        query_page: dict | None = None,
        schema_response: list[dict] | None = None,
    ) -> None:
        self.created: list[dict] = []
        self.query_page = query_page
        self.schema_response = schema_response
        self.query_calls: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            id="dataset-1",
            name=kwargs["name"],
            asset_type="dataset",
            visibility=kwargs["visibility"],
            created_at=None,
            last_updated=None,
            state="success",
            source="api",
            description=None,
            metadata={"table_name": "table_1"},
        )

    def query(
        self,
        dataset_id: str,
        sql: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        with_pagination: bool = False,
        resolve_asset_refs: bool = False,
    ):
        call = {"dataset_id": dataset_id}
        if sql is not None:
            call["sql"] = sql
        else:
            call.update(
                {
                    "limit": limit,
                    "offset": offset,
                    "with_pagination": with_pagination,
                }
            )
            # Only record when set so existing assertions stay stable.
            if resolve_asset_refs:
                call["resolve_asset_refs"] = resolve_asset_refs
        self.query_calls.append(call)
        return self.query_page

    def schema(self, dataset_id: str):
        return self.schema_response or []


class _FakeAssets:
    def connections(self, dataset_id: str):
        return [
            {
                "type": "reference",
                "source_id": dataset_id,
                "target_id": "file-1",
                "target_asset_type": "file",
                "target": {"id": "file-1", "asset_type": "file", "name": "sample.cif"},
            }
        ]


def _ctx(datasets: _FakeDatasets) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                ouro=SimpleNamespace(datasets=datasets, assets=_FakeAssets())
            )
        )
    )


def _dataset_tools() -> dict[str, object]:
    mcp = _CaptureMCP()
    register(mcp)
    return mcp.tools


@pytest.mark.parametrize(
    ("kwargs", "expected_format"),
    [
        (
            {"data": '[{"format":"json-string","row":1,"value":10}]'},
            "json-string",
        ),
        (
            {"data": [{"format": "data-array", "row": 1, "value": 11}]},
            "data-array",
        ),
        (
            {
                "data_path": str(
                    Path(__file__).parent / "fixtures" / "dataset_rows.csv"
                )
            },
            "csv",
        ),
        (
            {
                "data_path": str(
                    Path(__file__).parent / "fixtures" / "dataset_rows.json"
                )
            },
            "json-path",
        ),
        (
            {
                "data_path": str(
                    Path(__file__).parent / "fixtures" / "dataset_rows.jsonl"
                )
            },
            "jsonl",
        ),
        (
            {
                "data_path": str(
                    Path(__file__).parent / "fixtures" / "dataset_rows.ndjson"
                )
            },
            "ndjson",
        ),
    ],
)
def test_create_dataset_accepts_advertised_ingest_formats(
    kwargs: dict, expected_format: str
) -> None:
    datasets = _FakeDatasets()
    tools = _dataset_tools()

    result = json.loads(
        tools["create_dataset"](
            name=f"test {expected_format}",
            org_id="org-1",
            team_id="team-1",
            ctx=_ctx(datasets),
            visibility="private",
            **kwargs,
        )
    )

    created = datasets.created[0]
    rows = created["data"].to_dict(orient="records")
    assert rows[0]["format"] == expected_format
    assert created["org_id"] == "org-1"
    assert created["team_id"] == "team-1"
    assert result["table_name"] == "table_1"


def test_resolve_dataset_data_accepts_parquet(tmp_path: Path) -> None:
    path = tmp_path / "dataset_rows.parquet"
    pd.DataFrame([{"format": "parquet", "row": 1, "value": 17}]).to_parquet(path)

    df = _resolve_dataset_data(data_path=str(path))

    assert df.to_dict(orient="records") == [
        {"format": "parquet", "row": 1, "value": 17}
    ]


def test_resolve_dataset_data_rejects_multiple_sources() -> None:
    with pytest.raises(ValueError, match="Provide only one of data or data_path"):
        _resolve_dataset_data(data=[{"row": 1}], data_path="rows.csv")


def test_query_dataset_returns_json_rows_with_pagination_and_nulls() -> None:
    page = {
        "data": pd.DataFrame(
            [
                {
                    "name": "alpha",
                    "value": 1.5,
                    "missing": float("nan"),
                    "seen_at": pd.Timestamp("2026-05-02T12:00:00Z"),
                }
            ]
        ),
        "pagination": {"hasMore": True},
    }
    datasets = _FakeDatasets(query_page=page)
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"]("dataset-1", _ctx(datasets), limit=1, offset=2)
    )

    assert datasets.query_calls == [
        {
            "dataset_id": "dataset-1",
            "limit": 1,
            "offset": 2,
            "with_pagination": True,
        }
    ]
    assert result == {
        "rows": [
            {
                "name": "alpha",
                "value": 1.5,
                "missing": None,
                "seen_at": "2026-05-02T12:00:00+00:00",
            }
        ],
        "offset": 2,
        "limit": 1,
        "hasMore": True,
    }


def test_query_dataset_validates_pagination_arguments() -> None:
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"]("dataset-1", _ctx(_FakeDatasets()), limit=1001)
    )

    assert result["error"] == "invalid_arguments"
    assert result["retryable"] is False


def test_query_dataset_runs_optional_sql_query() -> None:
    datasets = _FakeDatasets(
        query_page=pd.DataFrame(
            [
                {
                    "category": "alpha",
                    "count": 2,
                    "missing": float("nan"),
                    "seen_at": pd.Timestamp("2026-05-02T12:00:00Z"),
                }
            ]
        )
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(datasets),
            sql="SELECT category, count(*) AS count FROM {{table}} GROUP BY category",
        )
    )

    assert "query_dataset_sql" not in tools
    assert datasets.query_calls == [
        {
            "dataset_id": "dataset-1",
            "sql": "SELECT category, count(*) AS count FROM {{table}} GROUP BY category",
        }
    ]
    assert result == {
        "rows": [
            {
                "category": "alpha",
                "count": 2,
                "missing": None,
                "seen_at": "2026-05-02T12:00:00+00:00",
            }
        ],
        "row_count": 1,
    }


def test_create_dataset_forwards_asset_refs() -> None:
    sidecar = {
        "file_id": {
            "019df875-7957-7888-888f-f8140ff62564": {
                "asset_id": "019df875-7957-7888-888f-f8140ff62564",
                "asset_type": "file",
                "name": "sample.cif",
                "web_url": "https://ouro.foundation/files/a/sample-cif",
            }
        }
    }
    datasets = _FakeDatasets(
        query_page={
            "data": pd.DataFrame(
                [{"file_id": "019df875-7957-7888-888f-f8140ff62564"}]
            ),
            "pagination": {"hasMore": False},
            "resolved_asset_refs": sidecar,
        },
        schema_response=[
            {
                "column_name": "file_id",
                "data_type": "uuid",
                "semantic_type": "asset_ref",
                "asset_type": "file",
            }
        ],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["create_dataset"](
            name="refs",
            org_id="org-1",
            team_id="team-1",
            ctx=_ctx(datasets),
            data='[{"file_id":"019df875-7957-7888-888f-f8140ff62564"}]',
            asset_refs='{"file_id": {"asset_type": "file"}}',
        )
    )

    created = datasets.created[0]
    assert created["asset_refs"] == {"file_id": {"asset_type": "file"}}
    assert result["asset_refs"] == {"file_id": {"asset_type": "file"}}
    assert result["resolved_asset_refs_preview"] == sidecar
    assert result["connections"]["reference"][0]["id"] == "file-1"


def test_create_dataset_preserves_declared_asset_type_when_schema_omits_hint() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_asset_refs": {}},
        schema_response=[
            {
                "column_name": "post_id",
                "data_type": "uuid",
                "semantic_type": "asset_ref",
            }
        ],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["create_dataset"](
            name="refs",
            org_id="org-1",
            team_id="team-1",
            ctx=_ctx(datasets),
            data='[{"post_id":"019df875-7957-7888-888f-f8140ff62564"}]',
            asset_refs='{"post_id": {"asset_type": "post"}}',
        )
    )

    assert result["asset_refs"] == {"post_id": {"asset_type": "post"}}


def test_create_dataset_forwards_enum_columns() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_asset_refs": {}},
        schema_response=[
            {
                "column_name": "status",
                "data_type": "text",
                "semantic_type": "enum",
                "enum_values": ["todo", "done"],
            }
        ],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["create_dataset"](
            name="statuses",
            org_id="org-1",
            team_id="team-1",
            ctx=_ctx(datasets),
            data='[{"status":"todo"}]',
            enum_columns='{"status": {"values": ["todo", "done"]}}',
        )
    )

    created = datasets.created[0]
    assert created["enum_columns"] == {"status": {"values": ["todo", "done"]}}
    assert result["enum_columns"] == {"status": {"values": ["todo", "done"]}}
    assert result["schema"][0]["enum_values"] == ["todo", "done"]


def test_query_dataset_resolve_asset_refs_passes_flag_and_returns_sidecar() -> None:
    sidecar = {
        "file_id": {
            "019df875-7957-7888-888f-f8140ff62564": {
                "asset_id": "019df875-7957-7888-888f-f8140ff62564",
                "asset_type": "file",
                "name": "sample.cif",
                "web_url": "https://ouro.foundation/files/a/sample-cif",
            }
        }
    }
    page = {
        "data": pd.DataFrame([{"file_id": "019df875-7957-7888-888f-f8140ff62564"}]),
        "pagination": {"hasMore": False},
        "resolved_asset_refs": sidecar,
    }
    datasets = _FakeDatasets(query_page=page)
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1", _ctx(datasets), limit=10, resolve_asset_refs=True
        )
    )

    assert datasets.query_calls[0]["resolve_asset_refs"] is True
    assert result["resolved_asset_refs"] == sidecar


def test_query_dataset_resolve_asset_refs_rejected_with_sql() -> None:
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(_FakeDatasets()),
            sql="SELECT * FROM {{table}}",
            resolve_asset_refs=True,
        )
    )

    assert result["error"] == "invalid_arguments"
    assert "not supported with sql" in result["message"]


def test_query_dataset_sql_rejects_pagination_arguments() -> None:
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(_FakeDatasets()),
            sql="SELECT * FROM {{table}}",
            limit=10,
        )
    )

    assert result["error"] == "invalid_arguments"
    assert "limit/offset are not compatible with sql" in result["message"]
