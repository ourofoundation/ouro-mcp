"""Dataset tools — query, create, update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import pandas as pd
from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    dump_json,
    format_asset_summary,
    optional_kwargs,
    resolve_local_path,
    slim_connection_graph,
    truncate_response,
)
from pydantic import BeforeValidator, Field


def _dataframe_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not isinstance(rows, list):
        raise ValueError("data must be a list of objects (rows).")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Each item in data must be an object (dict).")
    return pd.DataFrame(rows)


def _dataframe_from_json(data_json: str) -> pd.DataFrame:
    try:
        parsed = json.loads(data_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"data_json must be valid JSON: {e}") from e

    if isinstance(parsed, list):
        return _dataframe_from_rows(parsed)

    if isinstance(parsed, dict):
        rows = parsed.get("rows")
        if isinstance(rows, list):
            return _dataframe_from_rows(rows)
        return _dataframe_from_rows([parsed])

    raise ValueError("data_json must be a JSON object, or a JSON array of objects.")


def _dataframe_from_path(data_path: str) -> pd.DataFrame:
    path = resolve_local_path(data_path)
    if not path.exists():
        raise ValueError(f"data_path not found: {data_path} (resolved to {path})")
    if not path.is_file():
        raise ValueError(f"data_path must point to a file: {data_path} (resolved to {path})")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(path, lines=True)
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            return _dataframe_from_json(f.read())
    if suffix == ".parquet":
        return pd.read_parquet(path)

    raise ValueError(
        "Unsupported data_path file type. Use .csv, .json, .jsonl/.ndjson, or .parquet."
    )


def _coerce_data(data: Any) -> Any:
    """Accept JSON text or a parsed JSON value and return JSON text.

    Also used as a Pydantic BeforeValidator so list/dict values from callers
    that pass parsed JSON (e.g. smolagents) are coerced before type checking.
    """
    if data is None:
        return data
    if isinstance(data, str):
        return data
    if isinstance(data, (list, dict)):
        return json.dumps(data)
    raise ValueError(f"data must be a JSON string, list, or dict — got {type(data).__name__}")


def _coerce_string_list(value: Any, *, parameter_name: str) -> Optional[list[str]]:
    """Accept a list of strings or a JSON array string and return a list."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Allow a bare comma-separated list as a convenience.
            return [part.strip() for part in text.split(",") if part.strip()]
        value = parsed
    if isinstance(value, list):
        if any(not isinstance(item, str) for item in value):
            raise ValueError(f"{parameter_name} must be a list of column-name strings.")
        return list(value)
    raise ValueError(f"{parameter_name} must be a JSON array of column-name strings.")


def _coerce_json_object(value: Any, *, parameter_name: str) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{parameter_name} must be valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError(f"{parameter_name} must decode to a JSON object.")
        return parsed
    raise ValueError(f"{parameter_name} must be a JSON object or JSON string.")


_COLUMN_OPS = ("add", "update", "rename", "drop")


def _coerce_column_operations(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"operations must be valid JSON: {e}") from e
    if not isinstance(value, list):
        raise ValueError("operations must be a JSON array of column operations.")
    if not value:
        raise ValueError("operations must contain at least one column operation.")
    if any(not isinstance(op, dict) for op in value):
        raise ValueError("each operation must be a JSON object.")
    return value


def _apply_column_op(ouro: Any, dataset_id: str, op: dict[str, Any]) -> dict[str, Any]:
    kind = op.get("op")
    if kind not in _COLUMN_OPS:
        raise ValueError(f"operation.op must be one of: {', '.join(_COLUMN_OPS)}.")

    name = str(op.get("name") or "").strip()
    if not name:
        raise ValueError("operation.name is required.")

    datasets = ouro.datasets
    if kind == "drop":
        return datasets.drop_column(dataset_id, name)

    if kind == "add":
        return datasets.add_column(
            dataset_id,
            name,
            **optional_kwargs(
                type=op.get("type"),
                nullable=op.get("nullable"),
                label=op.get("label"),
                enum_values=op.get("enum_values"),
            ),
        )

    new_name = op.get("new_name")
    if kind == "rename" and not (isinstance(new_name, str) and new_name.strip()):
        raise ValueError("rename requires a non-empty new_name.")
    return datasets.update_column(
        dataset_id,
        name,
        **optional_kwargs(
            new_name=new_name,
            type=op.get("type"),
            label=op.get("label"),
            enum_values=op.get("enum_values"),
        ),
    )


def _resolve_dataset_data(
    data: Any = None,
    data_path: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    provided = [
        ("data", data is not None),
        ("data_path", data_path is not None),
    ]
    selected = [name for name, is_set in provided if is_set]
    if len(selected) > 1:
        raise ValueError(f"Provide only one of data or data_path (got: {', '.join(selected)}).")
    if not selected:
        return None

    if data is not None:
        return _dataframe_from_json(_coerce_data(data))
    if data_path is not None:
        return _dataframe_from_path(data_path)

    return None


def _json_records_from_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = df.to_dict(orient="records")
    for row in rows:
        for k, v in row.items():
            if pd.isna(v):
                row[k] = None
            elif hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return rows


def _refs_from_schema(schema: Any) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for field in schema or []:
        if not isinstance(field, dict) or field.get("semantic_type") != "reference":
            continue
        column = field.get("column_name")
        if not column:
            continue
        kind = field.get("ref_kind") or "asset"
        entry: dict[str, Any] = {"kind": kind}
        if kind == "asset" and field.get("asset_type"):
            entry["asset_type"] = field["asset_type"]
        refs[str(column)] = entry
    return refs


def _enum_columns_from_schema(schema: Any) -> dict[str, dict[str, list[str]]]:
    columns: dict[str, dict[str, list[str]]] = {}
    for field in schema or []:
        if not isinstance(field, dict) or field.get("semantic_type") != "enum":
            continue
        column = field.get("column_name")
        values = field.get("enum_values")
        if not column or not isinstance(values, list):
            continue
        columns[str(column)] = {"values": [str(value) for value in values]}
    return columns


def _normalize_refs_for_result(value: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for column, hint in (value or {}).items():
        if hint is None:
            refs[str(column)] = {"kind": "asset"}
        elif isinstance(hint, str):
            if hint in ("asset", "action"):
                refs[str(column)] = {"kind": hint}
            else:
                refs[str(column)] = {"kind": "asset", "asset_type": hint}
        elif isinstance(hint, dict):
            kind = hint.get("kind", "asset")
            entry: dict[str, Any] = {"kind": kind}
            if kind == "asset" and hint.get("asset_type"):
                entry["asset_type"] = hint["asset_type"]
            refs[str(column)] = entry
    return refs


def _normalize_enum_columns_for_result(value: Optional[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    columns: dict[str, dict[str, list[str]]] = {}
    for column, declaration in (value or {}).items():
        raw_values = declaration.get("values") if isinstance(declaration, dict) else None
        if isinstance(raw_values, list):
            columns[str(column)] = {"values": [str(v) for v in raw_values]}
    return columns


def _merge_ref_hints(
    refs: dict[str, dict[str, Any]],
    hints: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {column: dict(value) for column, value in refs.items()}
    for column, hint in _normalize_refs_for_result(hints).items():
        current = merged.get(column, {})
        if not current.get("kind"):
            current["kind"] = hint.get("kind", "asset")
        if hint.get("asset_type") and not current.get("asset_type"):
            current["asset_type"] = hint["asset_type"]
        merged[column] = current
    return merged


def _merge_enum_column_hints(
    columns: dict[str, dict[str, list[str]]],
    hints: Optional[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    merged = {column: {"values": list(value["values"])} for column, value in columns.items()}
    for column, hint in _normalize_enum_columns_for_result(hints).items():
        if column not in merged:
            merged[column] = hint
    return merged


def _ingest_summary(dataset: Any) -> dict[str, Any]:
    """Surface partial-success ingest info stashed on the dataset by ouro-py.

    Reference columns are FK-enforced, so rows with bad/missing ref ids are
    skipped rather than failing the whole write. ``row_ingest`` reports
    {inserted, skipped} and ``ingest_warning`` lists the offending ids per
    column so an agent can fix and retry them.
    """
    summary: dict[str, Any] = {}
    row_ingest = getattr(dataset, "row_ingest", None)
    if row_ingest:
        summary["row_ingest"] = row_ingest
    warning = getattr(dataset, "ingest_warning", None)
    if warning:
        summary["ingest_warning"] = warning
    return summary


def _dataset_proof(
    ouro: Any,
    dataset_id: str,
    declared_refs: Optional[dict[str, Any]] = None,
    declared_enum_columns: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Collect lightweight verification data after create/update."""
    proof: dict[str, Any] = {}

    try:
        schema = ouro.datasets.schema(dataset_id)
        proof["schema"] = schema
        proof["refs"] = _merge_ref_hints(
            _refs_from_schema(schema),
            declared_refs,
        )
        proof["enum_columns"] = _merge_enum_column_hints(
            _enum_columns_from_schema(schema),
            declared_enum_columns,
        )
    except Exception:
        proof["schema"] = None
        proof["refs"] = _merge_ref_hints({}, declared_refs)
        proof["enum_columns"] = _merge_enum_column_hints({}, declared_enum_columns)

    try:
        page = ouro.datasets.query(
            dataset_id,
            limit=5,
            with_pagination=True,
            resolve_refs=True,
        )
        proof["resolved_refs_preview"] = page.get("resolved_refs") or {}
    except Exception:
        proof["resolved_refs_preview"] = {}

    try:
        if hasattr(ouro, "assets"):
            proof["connections"] = slim_connection_graph(
                ouro.assets.connections(dataset_id),
                current_asset_id=dataset_id,
            )
    except Exception:
        proof["connections"] = {}

    return proof


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def query_dataset(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        ctx: Context,
        sql: Annotated[
            Optional[str],
            Field(
                description=(
                    "Optional read-only SQL query. Use `{{table}}` as a placeholder "
                    "for the dataset table. When provided, include LIMIT/OFFSET in "
                    "the SQL instead of using the limit/offset parameters."
                )
            ),
        ] = None,
        limit: Annotated[int, Field(description="Max rows to return (1-1000)")] = 100,
        offset: Annotated[int, Field(description="Row offset for pagination")] = 0,
        resolve_refs: Annotated[
            bool,
            Field(
                description=(
                    "Resolve reference columns (columns with semantic_type "
                    '"reference" in the schema, which hold Ouro object ids — '
                    "ref_kind names the kind: asset or action) into "
                    "names/types/URLs. Returns a `resolved_refs` sidecar map "
                    "(column -> id -> {kind, id, name, web_url, ...}). "
                    "Permission-aware: ids you can't see are omitted. Not "
                    "supported with sql."
                )
            ),
        ] = False,
    ) -> str:
        """Query a dataset's contents as JSON records.

        By default this pages server-side so large datasets don't load fully
        into memory. Pass ``sql`` to run a read-only PostgreSQL query, mirroring
        ``ouro.datasets.query(dataset_id, sql=...)`` from ouro-py. Always
        reference the table as ``{{table}}`` in SQL mode. Writes are rejected
        server-side and queries time out after 10 seconds.

        Inspect the schema first (``ouro://datasets/{id}/schema``): columns with
        ``semantic_type: "reference"`` hold Ouro object ids (``ref_kind`` is
        "asset" or "action"). Pass ``resolve_refs=true`` when you need their
        names, types, or URLs.
        """
        if sql is not None:
            if not sql.strip():
                raise ValueError("sql query is required when sql is provided.")
            if limit != 100 or offset != 0:
                raise ValueError(
                    "limit/offset are not compatible with sql; include "
                    "LIMIT/OFFSET in the SQL query instead."
                )
            if resolve_refs:
                raise ValueError(
                    "resolve_refs is not supported with sql; use the "
                    "paginated (non-sql) query mode instead."
                )

            ouro = ctx.request_context.lifespan_context.ouro
            df = ouro.datasets.query(dataset_id, sql=sql)
            rows = _json_records_from_dataframe(df)
            result = dump_json({"rows": rows, "row_count": len(rows)})
            return truncate_response(
                result,
                context=(
                    "Refine the SQL with LIMIT, WHERE, or aggregations to reduce "
                    "the response size."
                ),
            )

        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")

        ouro = ctx.request_context.lifespan_context.ouro

        page = ouro.datasets.query(
            dataset_id,
            limit=limit,
            offset=offset,
            with_pagination=True,
            resolve_refs=resolve_refs,
        )
        df = page["data"]
        pagination = page.get("pagination") or {}

        rows = _json_records_from_dataframe(df)

        payload: dict[str, Any] = {
            "rows": rows,
            "offset": offset,
            "limit": limit,
            "hasMore": bool(pagination.get("hasMore")),
        }
        if resolve_refs:
            payload["resolved_refs"] = page.get("resolved_refs") or {}

        result = dump_json(payload)

        return truncate_response(
            result,
            context="Use the offset parameter to load more rows.",
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_dataset(
        name: Annotated[str, Field(description="Dataset name")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        data: Annotated[
            Optional[str | list[dict[str, Any]]],
            BeforeValidator(_coerce_data),
            Field(
                description='JSON row array as a string or parsed array: \'[{"col": "val"}, ...]\''
            ),
        ] = None,
        data_path: Annotated[
            Optional[str],
            Field(description="Local file path (.csv, .json, .jsonl, .parquet)"),
        ] = None,
        visibility: Annotated[
            str, Field(description='"public" | "private" | "organization"')
        ] = "private",
        description: Annotated[Optional[str], Field(description="Dataset description")] = None,
        refs: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Columns that hold Ouro object ids, keyed by column name. "
                    "Each becomes a real FK (ON DELETE SET NULL) to the table "
                    "for its kind: asset -> public.assets(id), action -> "
                    "public.actions(id). Values may be a kind string "
                    '("asset"/"action"), an asset target type ("file"), or a '
                    'mapping like \'{"file_id": {"kind": "asset", "asset_type": '
                    '"file"}, "run_id": {"kind": "action"}}\'.'
                )
            ),
        ] = None,
        enum_columns: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Columns with a closed set of string values, keyed by "
                    "column name. Each column gets a DB CHECK constraint and "
                    'schema metadata. Example: \'{"status": {"values": '
                    '["todo", "done"]}}\'.'
                )
            ),
        ] = None,
    ) -> str:
        """Create a new dataset on Ouro. Provide data or data_path (one required).

        To make a column reference Ouro objects, pass ``refs``. Reference
        columns get a real DB foreign key, show up as
        ``semantic_type: "reference"`` (with ``ref_kind``) in the schema, and
        resolve to names/URLs via ``query_dataset(resolve_refs=true)``.

        To make a column categorical, pass ``enum_columns``. Enum columns show
        up as ``semantic_type: "enum"`` with ``enum_values`` in the schema.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is None:
            raise ValueError("No dataset rows provided. Pass one of: data or data_path.")
        if df.empty or len(df.columns) == 0:
            raise ValueError("Dataset data must include at least one column and one row.")

        declared_refs = _coerce_json_object(refs, parameter_name="refs")
        declared_enum_columns = _coerce_json_object(
            enum_columns, parameter_name="enum_columns"
        )

        dataset = ouro.datasets.create(
            name=name,
            visibility=visibility,
            data=df,
            description=description,
            org_id=org_id,
            team_id=team_id,
            **optional_kwargs(
                refs=declared_refs,
                enum_columns=declared_enum_columns,
            ),
        )

        result = format_asset_summary(dataset)
        result["table_name"] = dataset.metadata.get("table_name") if dataset.metadata else None
        result.update(
            _dataset_proof(
                ouro,
                str(dataset.id),
                declared_refs,
                declared_enum_columns,
            )
        )
        result.update(_ingest_summary(dataset))
        return dump_json(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def update_dataset(
        id: Annotated[str, Field(description="Dataset UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization"')
        ] = None,
        data: Annotated[
            Optional[str | list[dict[str, Any]]],
            BeforeValidator(_coerce_data),
            Field(description="JSON row array for dataset ingest (string or parsed array)"),
        ] = None,
        data_path: Annotated[
            Optional[str],
            Field(description="Local file path for dataset ingest (.csv, .json, .jsonl, .parquet)"),
        ] = None,
        data_mode: Annotated[
            str, Field(description='"append" | "overwrite" | "upsert"')
        ] = "append",
        description: Annotated[Optional[str], Field(description="New description")] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
        refs: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Promote existing columns to references. Every value in "
                    "each column must already be a valid id of that kind or "
                    "null. Values may be a kind string ('asset'/'action'), an "
                    'asset target type ("file"), or a mapping like '
                    '\'{"file_id": {"kind": "asset", "asset_type": "file"}, '
                    '"run_id": {"kind": "action"}}\'.'
                )
            ),
        ] = None,
        enum_columns: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Promote existing columns to enum columns. Existing values "
                    "must be null or in the declared values list. Example: "
                    '\'{"status": {"values": ["todo", "done"]}}\'.'
                )
            ),
        ] = None,
    ) -> str:
        """Update a dataset's data or metadata.

        Pass data/data_path for row ingest and choose data_mode:
        - append (default): add rows
        - overwrite: replace existing rows
        - upsert: merge rows by id

        Pass refs to promote existing columns to native references (adds a DB
        foreign key to public.assets for the asset kind, or public.actions for
        the action kind).

        Pass enum_columns to promote existing columns to categorical enum
        columns (adds a DB CHECK constraint and schema metadata).
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is not None and (df.empty or len(df.columns) == 0):
            raise ValueError("Dataset row updates must include at least one column and one row.")

        declared_refs = _coerce_json_object(refs, parameter_name="refs")
        declared_enum_columns = _coerce_json_object(
            enum_columns, parameter_name="enum_columns"
        )

        dataset = ouro.datasets.update(
            id,
            data=df,
            data_mode=data_mode,
            **optional_kwargs(
                name=name,
                visibility=visibility,
                description=description,
                org_id=org_id,
                team_id=team_id,
                refs=declared_refs,
                enum_columns=declared_enum_columns,
            ),
        )

        result = format_asset_summary(dataset)
        result.update(
            _dataset_proof(
                ouro,
                str(dataset.id),
                declared_refs,
                declared_enum_columns,
            )
        )
        result.update(_ingest_summary(dataset))
        return dump_json(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def edit_dataset_columns(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        operations: Annotated[
            str | list[dict[str, Any]],
            Field(
                description=(
                    "Column operations applied in order. A JSON array (or "
                    "array string) of objects, each with an `op` field:\n"
                    '- {"op": "add", "name": "...", "type": "text"|"numeric"|'
                    '"boolean"|"timestamptz"|"enum", "nullable": true, '
                    '"label": "...", "enum_values": ["..."]}\n'
                    '- {"op": "update", "name": "...", "new_name": "...", '
                    '"type": "...", "label": "...", "enum_values": ["..."]}\n'
                    '- {"op": "rename", "name": "...", "new_name": "..."}\n'
                    '- {"op": "drop", "name": "..."}'
                )
            ),
        ],
        ctx: Context,
    ) -> str:
        """Add, update, rename, or drop columns on an existing dataset's table.

        This is the structural counterpart to ``update_dataset`` (which handles
        row ingest and whole-dataset metadata). Operations run in the given
        order against the live table.

        Pass ``enum_values`` on an add/update op to make a column categorical:
        ``type`` defaults to ``"enum"``, a DB CHECK constraint is added, and the
        column reads back as ``semantic_type: "enum"`` with ``enum_values`` in
        the schema. For an existing column, every current value must be null or
        in the supplied list.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        ops = _coerce_column_operations(operations)

        applied: list[dict[str, Any]] = []
        for op in ops:
            result = _apply_column_op(ouro, dataset_id, op)
            applied.append({"op": op.get("op"), "result": result})

        payload: dict[str, Any] = {"dataset_id": dataset_id, "operations": applied}
        payload.update(_dataset_proof(ouro, dataset_id))
        return dump_json(payload)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def list_dataset_views(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        ctx: Context,
    ) -> str:
        """List saved views for a dataset.

        A dataset view is a saved visualization definition with SQL and chart config.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        views = ouro.datasets.list_views(dataset_id)
        return dump_json({"dataset_id": dataset_id, "views": views})

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def write_dataset_view(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        ctx: Context,
        view_id: Annotated[
            Optional[str],
            Field(description="Omit to create a new view; pass a view UUID to update that view."),
        ] = None,
        name: Annotated[
            Optional[str],
            Field(description="View name. Required when creating (view_id omitted)."),
        ] = None,
        description: Annotated[Optional[str], Field(description="Short view description")] = None,
        sql_query: Annotated[
            Optional[str],
            Field(description="Read-only PostgreSQL query using {{table}} as the dataset table name"),
        ] = None,
        config: Annotated[
            Optional[Any],
            Field(description="Chart config as a JSON object or JSON string"),
        ] = None,
        prompt: Annotated[
            Optional[str],
            Field(description="Natural-language prompt to guide AI generation of the view's SQL and chart config"),
        ] = None,
    ) -> str:
        """Create or update a saved dataset view.

        Omit view_id to create a new view; pass view_id to update an existing one.
        A view is a (sql_query, config) pair: it runs the SQL and renders the chart
        config. Provide both, or pass a prompt to have the API generate them via AI.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        cfg = _coerce_json_object(config, parameter_name="config")

        if view_id is None:
            if not name:
                raise ValueError("name is required when creating a dataset view (omit view_id to create).")
            result = ouro.datasets.create_view(
                dataset_id,
                name=name,
                description=description,
                sql_query=sql_query,
                config=cfg,
                prompt=prompt,
            )
        else:
            result = ouro.datasets.update_view(
                dataset_id,
                view_id,
                name=name,
                description=description,
                sql_query=sql_query,
                config=cfg,
                prompt=prompt,
            )
        return dump_json(result)

    @mcp.tool(annotations={"destructiveHint": True})
    @handle_ouro_errors
    def delete_dataset_view(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        view_id: Annotated[str, Field(description="Dataset view UUID")],
        ctx: Context,
    ) -> str:
        """Delete a saved dataset view."""
        ouro = ctx.request_context.lifespan_context.ouro
        ouro.datasets.delete_view(dataset_id, view_id)
        return dump_json(
            {
                "deleted": True,
                "dataset_id": dataset_id,
                "view_id": view_id,
            }
        )
