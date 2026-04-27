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
    """Accept a JSON string, list, or dict and always return a JSON string.

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


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def query_dataset(
        dataset_id: Annotated[str, Field(description="Dataset UUID")],
        ctx: Context,
        limit: Annotated[int, Field(description="Max rows to return (1-1000)")] = 100,
        offset: Annotated[int, Field(description="Row offset for pagination")] = 0,
    ) -> str:
        """Query a dataset's contents as JSON records.

        Pages server-side so large datasets don't load fully into memory.
        Use get_asset(id) first to see the column schema; call with
        increasing ``offset`` to walk through subsequent pages while
        ``hasMore`` is true.
        """
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
        )
        df = page["data"]
        pagination = page.get("pagination") or {}

        rows = df.to_dict(orient="records")
        for row in rows:
            for k, v in row.items():
                if pd.isna(v):
                    row[k] = None
                elif hasattr(v, "isoformat"):
                    row[k] = v.isoformat()

        result = dump_json(
            {
                "rows": rows,
                "offset": offset,
                "limit": limit,
                "hasMore": bool(pagination.get("hasMore")),
            }
        )

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
            Optional[str],
            BeforeValidator(_coerce_data),
            Field(
                description='JSON rows as a string or array: \'[{"col": "val"}, ...]\' or \'{"rows": [...]}\''
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
    ) -> str:
        """Create a new dataset on Ouro. Provide data or data_path (one required)."""
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is None:
            raise ValueError("No dataset rows provided. Pass one of: data or data_path.")
        if df.empty or len(df.columns) == 0:
            raise ValueError("Dataset data must include at least one column and one row.")

        dataset = ouro.datasets.create(
            name=name,
            visibility=visibility,
            data=df,
            description=description,
            org_id=org_id,
            team_id=team_id,
        )

        result = format_asset_summary(dataset)
        result["table_name"] = dataset.metadata.get("table_name") if dataset.metadata else None
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
            Optional[str],
            BeforeValidator(_coerce_data),
            Field(description="JSON rows for dataset ingest (string or array)"),
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
    ) -> str:
        """Update a dataset's data or metadata.

        Pass data/data_path for row ingest and choose data_mode:
        - append (default): add rows
        - overwrite: replace existing rows
        - upsert: merge rows by id
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is not None and (df.empty or len(df.columns) == 0):
            raise ValueError("Dataset row updates must include at least one column and one row.")

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
            ),
        )

        return dump_json(format_asset_summary(dataset))

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
