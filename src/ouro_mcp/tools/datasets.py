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


def _asset_refs_from_schema(schema: Any) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for field in schema or []:
        if not isinstance(field, dict) or field.get("semantic_type") != "asset_ref":
            continue
        column = field.get("column_name")
        if not column:
            continue
        entry: dict[str, Any] = {}
        if field.get("asset_type"):
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


def _normalize_asset_refs_for_result(value: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for column, hint in (value or {}).items():
        if hint is None:
            refs[str(column)] = {}
        elif isinstance(hint, str):
            refs[str(column)] = {"asset_type": hint}
        elif isinstance(hint, dict):
            entry: dict[str, Any] = {}
            if hint.get("asset_type"):
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


def _merge_asset_ref_hints(
    refs: dict[str, dict[str, Any]],
    hints: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {column: dict(value) for column, value in refs.items()}
    for column, hint in _normalize_asset_refs_for_result(hints).items():
        current = merged.get(column, {})
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


def _dataset_proof(
    ouro: Any,
    dataset_id: str,
    declared_asset_refs: Optional[dict[str, Any]] = None,
    declared_enum_columns: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Collect lightweight verification data after create/update."""
    proof: dict[str, Any] = {}

    try:
        schema = ouro.datasets.schema(dataset_id)
        proof["schema"] = schema
        proof["asset_refs"] = _merge_asset_ref_hints(
            _asset_refs_from_schema(schema),
            declared_asset_refs,
        )
        proof["enum_columns"] = _merge_enum_column_hints(
            _enum_columns_from_schema(schema),
            declared_enum_columns,
        )
    except Exception:
        proof["schema"] = None
        proof["asset_refs"] = _merge_asset_ref_hints({}, declared_asset_refs)
        proof["enum_columns"] = _merge_enum_column_hints({}, declared_enum_columns)

    try:
        page = ouro.datasets.query(
            dataset_id,
            limit=5,
            with_pagination=True,
            resolve_asset_refs=True,
        )
        proof["resolved_asset_refs_preview"] = page.get("resolved_asset_refs") or {}
    except Exception:
        proof["resolved_asset_refs_preview"] = {}

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
        resolve_asset_refs: Annotated[
            bool,
            Field(
                description=(
                    "Resolve asset-reference columns (columns with "
                    'semantic_type "asset_ref" in the schema, which hold Ouro '
                    "asset ids) into names/types/URLs. Returns a "
                    "`resolved_asset_refs` sidecar map (column -> id -> "
                    "{asset_id, asset_type, name, web_url}). Permission-aware: "
                    "ids you can't see are omitted. Not supported with sql."
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
        ``semantic_type: "asset_ref"`` hold Ouro asset ids. Pass
        ``resolve_asset_refs=true`` when you need their names, types, or URLs.
        """
        if sql is not None:
            if not sql.strip():
                raise ValueError("sql query is required when sql is provided.")
            if limit != 100 or offset != 0:
                raise ValueError(
                    "limit/offset are not compatible with sql; include "
                    "LIMIT/OFFSET in the SQL query instead."
                )
            if resolve_asset_refs:
                raise ValueError(
                    "resolve_asset_refs is not supported with sql; use the "
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
            resolve_asset_refs=resolve_asset_refs,
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
        if resolve_asset_refs:
            payload["resolved_asset_refs"] = page.get("resolved_asset_refs") or {}

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
        asset_refs: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Columns that hold Ouro asset ids, keyed by column name. "
                    "Each backend-promoted column becomes a real FK to "
                    "public.assets(id) with ON DELETE SET NULL. Example: "
                    '\'{"file_id": {"asset_type": "file"}}\'.'
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

        To make a column reference Ouro assets, pass ``asset_refs``. Asset-ref
        columns get a real DB foreign key, show up as
        ``semantic_type: "asset_ref"`` in the schema, and resolve to names/URLs
        via ``query_dataset(resolve_asset_refs=true)``.

        To make a column categorical, pass ``enum_columns``. Enum columns show
        up as ``semantic_type: "enum"`` with ``enum_values`` in the schema.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is None:
            raise ValueError("No dataset rows provided. Pass one of: data or data_path.")
        if df.empty or len(df.columns) == 0:
            raise ValueError("Dataset data must include at least one column and one row.")

        declared_asset_refs = _coerce_json_object(
            asset_refs, parameter_name="asset_refs"
        )
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
                asset_refs=declared_asset_refs,
                enum_columns=declared_enum_columns,
            ),
        )

        result = format_asset_summary(dataset)
        result["table_name"] = dataset.metadata.get("table_name") if dataset.metadata else None
        result.update(
            _dataset_proof(
                ouro,
                str(dataset.id),
                declared_asset_refs,
                declared_enum_columns,
            )
        )
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
        asset_refs: Annotated[
            Optional[str | dict[str, Any]],
            Field(
                description=(
                    "Promote existing columns to asset references. Every value "
                    "in each column must already be a valid asset id or null. "
                    'Example: \'{"file_id": {"asset_type": "file"}}\'.'
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

        Pass asset_refs to promote existing columns to native asset references
        (adds a DB foreign key to public.assets).

        Pass enum_columns to promote existing columns to categorical enum
        columns (adds a DB CHECK constraint and schema metadata).
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is not None and (df.empty or len(df.columns) == 0):
            raise ValueError("Dataset row updates must include at least one column and one row.")

        declared_asset_refs = _coerce_json_object(
            asset_refs, parameter_name="asset_refs"
        )
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
                asset_refs=declared_asset_refs,
                enum_columns=declared_enum_columns,
            ),
        )

        result = format_asset_summary(dataset)
        result.update(
            _dataset_proof(
                ouro,
                str(dataset.id),
                declared_asset_refs,
                declared_enum_columns,
            )
        )
        return dump_json(result)

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
    def create_dataset_view(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        name: Annotated[str, Field(description="View name")],
        ctx: Context,
        description: Annotated[Optional[str], Field(description="Short view description")] = None,
        sql_query: Annotated[
            Optional[str],
            Field(description="Read-only PostgreSQL query using {{table}} as the dataset table name"),
        ] = None,
        engine_type: Annotated[
            str,
            Field(description='"auto" | "recharts_json"'),
        ] = "auto",
        config: Annotated[
            Optional[Any],
            Field(description="Chart config as a JSON object or JSON string"),
        ] = None,
        prompt: Annotated[
            Optional[str],
            Field(description="Natural-language prompt to guide AI generation of the view's SQL and chart config"),
        ] = None,
    ) -> str:
        """Create a saved view for a dataset.

        Views are stored visualizations with an optional SQL query and chart configuration.
        Pass a prompt to let the API auto-generate the SQL and config via AI.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        created = ouro.datasets.create_view(
            dataset_id,
            name=name,
            description=description,
            sql_query=sql_query,
            engine_type=engine_type,
            config=_coerce_json_object(config, parameter_name="config"),
            prompt=prompt,
        )
        return dump_json(created)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def update_dataset_view(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        view_id: Annotated[str, Field(description="Dataset view UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="Updated view name")] = None,
        description: Annotated[Optional[str], Field(description="Updated description")] = None,
        sql_query: Annotated[
            Optional[str],
            Field(description="Updated read-only SQL query using {{table}}"),
        ] = None,
        engine_type: Annotated[
            Optional[str],
            Field(description='"auto" | "recharts_json"'),
        ] = None,
        config: Annotated[
            Optional[Any],
            Field(description="Updated chart config as a JSON object or JSON string"),
        ] = None,
        prompt: Annotated[
            Optional[str],
            Field(description="Natural-language prompt to guide AI re-generation of the view's SQL and chart config"),
        ] = None,
    ) -> str:
        """Update a saved dataset view."""
        ouro = ctx.request_context.lifespan_context.ouro
        updated = ouro.datasets.update_view(
            dataset_id,
            view_id,
            name=name,
            description=description,
            sql_query=sql_query,
            engine_type=engine_type,
            config=_coerce_json_object(config, parameter_name="config"),
            prompt=prompt,
        )
        return dump_json(updated)

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
