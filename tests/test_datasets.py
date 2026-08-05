from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from ouro_mcp.tools.datasets import _resolve_dataset_data, register
from ouro_mcp.utils import slim_dataset_schema


def test_slim_dataset_schema_keeps_name_type_and_semantics() -> None:
    assert slim_dataset_schema(
        [
            {
                "column_name": "file_id",
                "data_type": "uuid",
                "fk_constraint_name": "fk_ref_file_id",
                "foreign_table_schema": "public",
                "foreign_table_name": "assets",
                "foreign_column_name": "id",
                "name": "file_id",
                "type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "asset",
                "asset_type": "file",
            },
            {
                "column_name": "status",
                "data_type": "text",
                "fk_constraint_name": None,
                "foreign_table_schema": None,
                "foreign_table_name": None,
                "foreign_column_name": None,
                "name": "status",
                "type": "text",
                "semantic_type": "enum",
                "enum_values": ["todo", "done"],
            },
            {
                "column_name": "score",
                "data_type": "real",
            },
        ]
    ) == [
        {
            "name": "file_id",
            "type": "uuid",
            "semantic_type": "reference",
            "ref_kind": "asset",
            "asset_type": "file",
        },
        {
            "name": "status",
            "type": "text",
            "semantic_type": "enum",
            "enum_values": ["todo", "done"],
        },
        {"name": "score", "type": "real"},
    ]


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
        ingest: dict | None = None,
        ingest_warning: dict | None = None,
    ) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.query_page = query_page
        self.schema_response = schema_response
        self.query_calls: list[dict] = []
        self.column_calls: list[dict] = []
        self.ingest = ingest
        self.ingest_warning = ingest_warning

    def create(self, **kwargs):
        self.created.append(kwargs)
        dataset = SimpleNamespace(
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
        # ouro-py stashes partial-success ingest info out-of-band on the model.
        if self.ingest is not None:
            dataset.row_ingest = self.ingest
        if self.ingest_warning is not None:
            dataset.ingest_warning = self.ingest_warning
        return dataset

    def query(
        self,
        dataset_id: str,
        sql: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        with_pagination: bool = False,
        resolve_refs: bool = False,
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
            if resolve_refs:
                call["resolve_refs"] = resolve_refs
        self.query_calls.append(call)
        return self.query_page

    def add_column(
        self,
        dataset_id: str,
        name: str,
        *,
        type: str = "text",
        nullable: bool = True,
        label: str | None = None,
        enum_values: list[str] | None = None,
    ):
        self.column_calls.append(
            {
                "method": "add",
                "dataset_id": dataset_id,
                "name": name,
                "type": type,
                "nullable": nullable,
                "label": label,
                "enum_values": enum_values,
            }
        )
        return {"name": name}

    def update_column(
        self,
        dataset_id: str,
        column: str,
        *,
        new_name: str | None = None,
        type: str | None = None,
        label: str | None = None,
        enum_values: list[str] | None = None,
    ):
        self.column_calls.append(
            {
                "method": "update",
                "dataset_id": dataset_id,
                "column": column,
                "new_name": new_name,
                "type": type,
                "label": label,
                "enum_values": enum_values,
            }
        )
        return {"name": new_name or column}

    def drop_column(self, dataset_id: str, column: str):
        self.column_calls.append(
            {"method": "drop", "dataset_id": dataset_id, "column": column}
        )
        return {"dropped": column}

    def schema(self, dataset_id: str):
        return self.schema_response or []

    def update(self, dataset_id: str, **kwargs):
        self.updated.append({"id": dataset_id, **kwargs})
        dataset = SimpleNamespace(
            id=dataset_id,
            name=kwargs.get("name") or "dataset",
            asset_type="dataset",
            visibility=kwargs.get("visibility") or "private",
            created_at=None,
            last_updated=None,
            state="success",
            source="api",
            description=None,
            metadata={"table_name": "table_1"},
        )
        if self.ingest is not None:
            dataset.row_ingest = self.ingest
        if self.ingest_warning is not None:
            dataset.ingest_warning = self.ingest_warning
        return dataset


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


def test_query_dataset_returns_markdown_table_with_pagination_and_nulls() -> None:
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

    result = tools["query_dataset"]("dataset-1", _ctx(datasets), limit=1, offset=2)

    assert datasets.query_calls == [
        {
            "dataset_id": "dataset-1",
            "limit": 1,
            "offset": 2,
            "with_pagination": True,
        }
    ]
    assert result.startswith(
        "Found 1 rows (offset=2; limit=1; more available — call again with offset=3)"
    )
    assert "| name | value | missing | seen_at |" in result
    assert "| alpha | 1.5 |  | 2026-05-02T12:00:00+00:00 |" in result


def test_query_dataset_response_format_json() -> None:
    page = {
        "data": pd.DataFrame([{"name": "alpha", "value": 1}]),
        "pagination": {"hasMore": False},
    }
    datasets = _FakeDatasets(query_page=page)
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(datasets),
            limit=10,
            response_format="json",
        )
    )

    assert result == {
        "rows": [{"name": "alpha", "value": 1}],
        "offset": 0,
        "limit": 10,
        "hasMore": False,
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

    result = tools["query_dataset"](
        "dataset-1",
        _ctx(datasets),
        sql="SELECT category, count(*) AS count FROM {{table}} GROUP BY category",
    )

    assert "query_dataset_sql" not in tools
    assert datasets.query_calls == [
        {
            "dataset_id": "dataset-1",
            "sql": (
                "SELECT category, count(*) AS count FROM {{table}} "
                "GROUP BY category LIMIT 100"
            ),
        }
    ]
    assert result.startswith("Found 1 rows")
    assert "| category | count | missing | seen_at |" in result
    assert "| alpha | 2 |  | 2026-05-02T12:00:00+00:00 |" in result


def test_query_dataset_sql_response_format_json() -> None:
    datasets = _FakeDatasets(
        query_page=pd.DataFrame([{"category": "alpha", "count": 2}])
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(datasets),
            sql="SELECT category, count(*) AS count FROM {{table}} GROUP BY category",
            response_format="json",
        )
    )

    assert result == {
        "rows": [{"category": "alpha", "count": 2}],
        "row_count": 1,
    }
    assert datasets.query_calls[0]["sql"].endswith("LIMIT 100")


def test_create_dataset_forwards_refs() -> None:
    sidecar = {
        "file_id": {
            "019df875-7957-7888-888f-f8140ff62564": {
                "kind": "asset",
                "id": "019df875-7957-7888-888f-f8140ff62564",
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
            "resolved_refs": sidecar,
        },
        schema_response=[
            {
                "column_name": "file_id",
                "data_type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "asset",
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
            refs='{"file_id": {"kind": "asset", "asset_type": "file"}}',
        )
    )

    created = datasets.created[0]
    assert created["refs"] == {"file_id": {"kind": "asset", "asset_type": "file"}}
    assert result["refs"] == {"file_id": {"kind": "asset", "asset_type": "file"}}
    assert result["schema"] == [
        {
            "name": "file_id",
            "type": "uuid",
            "semantic_type": "reference",
            "ref_kind": "asset",
            "asset_type": "file",
        }
    ]
    assert result["resolved_refs_preview"] == sidecar
    # Outgoing dataset→file reference edges are omitted; schema/refs cover them.
    assert "connections" not in result


def test_create_dataset_forwards_action_refs() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
        schema_response=[
            {
                "column_name": "run_id",
                "data_type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "action",
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
            data='[{"run_id":"019df875-7957-7888-888f-f8140ff62565"}]',
            refs='{"run_id": "action"}',
        )
    )

    created = datasets.created[0]
    assert created["refs"] == {"run_id": "action"}
    assert result["refs"] == {"run_id": {"kind": "action"}}
    assert result["schema"] == [
        {
            "name": "run_id",
            "type": "uuid",
            "semantic_type": "reference",
            "ref_kind": "action",
        }
    ]
    assert "resolved_refs_preview" not in result


def test_create_dataset_omits_empty_proof_sidecars() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
        schema_response=[{"column_name": "value", "data_type": "numeric"}],
    )
    tools = _dataset_tools()
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                ouro=SimpleNamespace(
                    datasets=datasets,
                    assets=SimpleNamespace(connections=lambda _id: []),
                )
            )
        )
    )

    result = json.loads(
        tools["create_dataset"](
            name="plain",
            org_id="org-1",
            team_id="team-1",
            ctx=ctx,
            data='[{"value": 1}]',
        )
    )

    assert result["schema"] == [{"name": "value", "type": "numeric"}]
    assert "resolved_refs_preview" not in result
    assert "connections" not in result


def test_create_dataset_surfaces_partial_ingest_warning() -> None:
    warning = {
        "message": "1 rows were skipped because a reference value was missing.",
        "refs": {
            "missing_count": 1,
            "type_mismatch_count": 0,
            "malformed_count": 0,
            "skipped_row_count": 1,
            "columns": [
                {
                    "column": "run_id",
                    "kind": "action",
                    "missing": ["00000000-0000-0000-0000-000000000099"],
                }
            ],
        },
    }
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
        schema_response=[
            {
                "column_name": "run_id",
                "data_type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "action",
            }
        ],
        ingest={"inserted": 1, "skipped": 1},
        ingest_warning=warning,
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["create_dataset"](
            name="refs",
            org_id="org-1",
            team_id="team-1",
            ctx=_ctx(datasets),
            data=(
                '[{"run_id":"019df875-7957-7888-888f-f8140ff62565"},'
                '{"run_id":"00000000-0000-0000-0000-000000000099"}]'
            ),
            refs='{"run_id": "action"}',
        )
    )

    assert result["row_ingest"] == {"inserted": 1, "skipped": 1}
    assert result["ingest_warning"] == warning


def test_create_dataset_preserves_declared_asset_type_when_schema_omits_hint() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
        schema_response=[
            {
                "column_name": "post_id",
                "data_type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "asset",
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
            refs='{"post_id": {"kind": "asset", "asset_type": "post"}}',
        )
    )

    assert result["refs"] == {"post_id": {"kind": "asset", "asset_type": "post"}}


def test_create_dataset_forwards_enum_columns() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
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
    assert result["schema"] == [
        {
            "name": "status",
            "type": "text",
            "semantic_type": "enum",
            "enum_values": ["todo", "done"],
        }
    ]
    assert "resolved_refs_preview" not in result


def test_query_dataset_resolve_refs_passes_flag_and_returns_sidecar() -> None:
    sidecar = {
        "file_id": {
            "019df875-7957-7888-888f-f8140ff62564": {
                "kind": "asset",
                "id": "019df875-7957-7888-888f-f8140ff62564",
                "asset_type": "file",
                "name": "sample.cif",
                "web_url": "https://ouro.foundation/files/a/sample-cif",
            }
        }
    }
    page = {
        "data": pd.DataFrame([{"file_id": "019df875-7957-7888-888f-f8140ff62564"}]),
        "pagination": {"hasMore": False},
        "resolved_refs": sidecar,
    }
    datasets = _FakeDatasets(query_page=page)
    tools = _dataset_tools()

    result = tools["query_dataset"](
        "dataset-1", _ctx(datasets), limit=10, resolve_refs=True
    )

    assert datasets.query_calls[0]["resolve_refs"] is True
    assert "| file_id |" in result
    assert "## resolved_refs" in result
    assert "### file_id" in result
    assert "**sample.cif** (file)" in result
    assert "id: `019df875-7957-7888-888f-f8140ff62564`" in result

    as_json = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(datasets),
            limit=10,
            resolve_refs=True,
            response_format="json",
        )
    )
    assert as_json["resolved_refs"] == sidecar


def test_query_dataset_resolve_refs_rejected_with_sql() -> None:
    tools = _dataset_tools()

    result = json.loads(
        tools["query_dataset"](
            "dataset-1",
            _ctx(_FakeDatasets()),
            sql="SELECT * FROM {{table}}",
            resolve_refs=True,
        )
    )

    assert result["error"] == "invalid_arguments"
    assert "not supported with sql" in result["message"]


def test_query_dataset_sql_folds_limit_offset() -> None:
    datasets = _FakeDatasets(query_page=pd.DataFrame([{"id": 1}]))
    tools = _dataset_tools()

    tools["query_dataset"](
        "dataset-1",
        _ctx(datasets),
        sql="SELECT id, name FROM {{table}}",
        limit=50,
        offset=10,
    )

    assert datasets.query_calls == [
        {
            "dataset_id": "dataset-1",
            "sql": "SELECT id, name FROM {{table}} LIMIT 50 OFFSET 10",
        }
    ]


def test_query_dataset_sql_treats_limit_zero_as_default() -> None:
    """Agents sometimes pass limit=0 to 'clear' the param after a rejection."""
    datasets = _FakeDatasets(query_page=pd.DataFrame([{"id": 1}]))
    tools = _dataset_tools()

    tools["query_dataset"](
        "dataset-1",
        _ctx(datasets),
        sql="SELECT id FROM {{table}}",
        limit=0,
        offset=0,
    )

    assert datasets.query_calls[0]["sql"] == "SELECT id FROM {{table}} LIMIT 100"


def test_query_dataset_sql_keeps_existing_limit() -> None:
    datasets = _FakeDatasets(query_page=pd.DataFrame([{"id": 1}]))
    tools = _dataset_tools()

    tools["query_dataset"](
        "dataset-1",
        _ctx(datasets),
        sql="SELECT id FROM {{table}} LIMIT 5",
        limit=50,
        offset=10,
    )

    assert datasets.query_calls[0]["sql"] == "SELECT id FROM {{table}} LIMIT 5"

def test_edit_dataset_columns_applies_operations_in_order() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {}},
        schema_response=[
            {
                "column_name": "priority",
                "data_type": "text",
                "semantic_type": "enum",
                "enum_values": ["low", "high"],
            }
        ],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["edit_dataset_columns"](
            "dataset-1",
            [
                {
                    "op": "add",
                    "name": "priority",
                    "type": "enum",
                    "enum_values": ["low", "high"],
                },
                {"op": "rename", "name": "qty", "new_name": "quantity"},
                {"op": "drop", "name": "scratch"},
            ],
            _ctx(datasets),
        )
    )

    assert [c["method"] for c in datasets.column_calls] == ["add", "update", "drop"]
    add_call = datasets.column_calls[0]
    assert add_call["type"] == "enum"
    assert add_call["enum_values"] == ["low", "high"]
    assert add_call["nullable"] is True  # default supplied by ouro-py, not the op
    rename_call = datasets.column_calls[1]
    assert rename_call["column"] == "qty"
    assert rename_call["new_name"] == "quantity"
    assert result["operations"][0]["op"] == "add"
    assert result["schema"] == [
        {
            "name": "priority",
            "type": "text",
            "semantic_type": "enum",
            "enum_values": ["low", "high"],
        }
    ]
    assert "resolved_refs_preview" not in result


def test_edit_dataset_columns_accepts_json_string() -> None:
    datasets = _FakeDatasets(query_page={"data": pd.DataFrame([])})
    tools = _dataset_tools()

    json.loads(
        tools["edit_dataset_columns"](
            "dataset-1",
            '[{"op": "update", "name": "status", "enum_values": ["todo", "done"]}]',
            _ctx(datasets),
        )
    )

    assert datasets.column_calls == [
        {
            "method": "update",
            "dataset_id": "dataset-1",
            "column": "status",
            "new_name": None,
            "type": None,
            "label": None,
            "enum_values": ["todo", "done"],
        }
    ]


def test_edit_dataset_columns_rejects_unknown_op() -> None:
    datasets = _FakeDatasets()
    tools = _dataset_tools()

    result = json.loads(
        tools["edit_dataset_columns"](
            "dataset-1",
            [{"op": "frobnicate", "name": "status"}],
            _ctx(datasets),
        )
    )

    assert result["error"] == "invalid_arguments"
    assert datasets.column_calls == []


def test_edit_dataset_columns_rename_requires_new_name() -> None:
    datasets = _FakeDatasets()
    tools = _dataset_tools()

    result = json.loads(
        tools["edit_dataset_columns"](
            "dataset-1",
            [{"op": "rename", "name": "status"}],
            _ctx(datasets),
        )
    )

    assert result["error"] == "invalid_arguments"
    assert "new_name" in result["message"]


def test_update_dataset_row_only_skips_verification() -> None:
    datasets = _FakeDatasets(
        query_page={"data": pd.DataFrame([]), "resolved_refs": {"x": {}}},
        schema_response=[{"column_name": "value", "data_type": "numeric"}],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["update_dataset"](
            "dataset-1",
            _ctx(datasets),
            data='[{"value": 1}]',
        )
    )

    assert "schema" not in result
    assert "refs" not in result
    assert "enum_columns" not in result
    assert "resolved_refs_preview" not in result
    assert "connections" not in result
    assert datasets.query_calls == []


def test_update_dataset_refs_includes_verification() -> None:
    sidecar = {
        "file_id": {
            "019df875-7957-7888-888f-f8140ff62564": {
                "kind": "asset",
                "id": "019df875-7957-7888-888f-f8140ff62564",
                "asset_type": "file",
                "name": "sample.cif",
            }
        }
    }
    datasets = _FakeDatasets(
        query_page={
            "data": pd.DataFrame([]),
            "pagination": {"hasMore": False},
            "resolved_refs": sidecar,
        },
        schema_response=[
            {
                "column_name": "file_id",
                "data_type": "uuid",
                "semantic_type": "reference",
                "ref_kind": "asset",
                "asset_type": "file",
            }
        ],
    )
    tools = _dataset_tools()

    result = json.loads(
        tools["update_dataset"](
            "dataset-1",
            _ctx(datasets),
            refs='{"file_id": {"kind": "asset", "asset_type": "file"}}',
        )
    )

    assert result["schema"] == [
        {
            "name": "file_id",
            "type": "uuid",
            "semantic_type": "reference",
            "ref_kind": "asset",
            "asset_type": "file",
        }
    ]
    assert result["refs"] == {"file_id": {"kind": "asset", "asset_type": "file"}}
    assert result["resolved_refs_preview"] == sidecar
    # Outgoing dataset→file reference edges are omitted; schema/refs cover them.
    assert "connections" not in result
