"""Dataset tools — query, create, update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import elicit_asset_location, format_asset_summary, optional_kwargs, truncate_response


def _dataframe_from_rows(rows: list[dict]) -> pd.DataFrame:
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
    path = Path(data_path).expanduser()
    if not path.exists():
        raise ValueError(f"data_path not found: {data_path}")
    if not path.is_file():
        raise ValueError(f"data_path must point to a file: {data_path}")

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


def _resolve_dataset_data(
    data: Optional[list[dict]] = None,
    data_path: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    provided = [
        ("data", data is not None),
        ("data_path", data_path is not None),
    ]
    selected = [name for name, is_set in provided if is_set]
    if len(selected) > 1:
        raise ValueError(
            f"Provide only one of data or data_path (got: {', '.join(selected)})."
        )
    if not selected:
        return None

    if data is not None:
        return _dataframe_from_rows(data)
    if data_path is not None:
        return _dataframe_from_path(data_path)

    return None


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def query_dataset(
        dataset_id: str,
        ctx: Context,
        limit: int = 100,
        offset: int = 0,
    ) -> str:
        """Query a dataset's contents as JSON records. Returns rows with pagination metadata.

        Use get_asset(id) first to see the dataset's schema before querying.
        Use limit and offset to paginate through large datasets.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = ouro.datasets.query(dataset_id)
        total_rows = len(df)

        page = df.iloc[offset : offset + limit]
        rows = page.to_dict(orient="records")

        for row in rows:
            for k, v in row.items():
                if pd.isna(v):
                    row[k] = None
                elif hasattr(v, "isoformat"):
                    row[k] = v.isoformat()

        result = json.dumps({
            "rows": rows,
            "total_rows": total_rows,
            "count": len(rows),
            "truncated": (offset + limit) < total_rows,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "hasMore": (offset + limit) < total_rows,
                "total": total_rows,
            },
        })

        return truncate_response(
            result,
            context="Use offset parameter to load more rows.",
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    async def create_dataset(
        name: str,
        ctx: Context,
        data: Optional[list[dict]] = None,
        data_path: Optional[str] = None,
        visibility: str = "private",
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Create a new dataset on Ouro from JSON records.

        Supported dataset inputs (choose one):
        - data: list of dicts where each dict is a row
        - data_path: local file path (.csv, .json, .jsonl/.ndjson, .parquet)

        Example data: [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        Use org_id and team_id to control where the dataset is created.
        Call get_organizations() and get_teams() first to find the right location.

        Teams with source_policy='web_only' block creation via API/MCP. Check
        get_teams() first — only target teams where agent_can_create is true.
        """
        if not org_id or not team_id:
            elicited_org, elicited_team = await elicit_asset_location(ctx)
            org_id = org_id or elicited_org
            team_id = team_id or elicited_team

        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is None:
            # ouro-py currently assumes a DataFrame in create(); provide a clear,
            # agent-friendly error instead of surfacing an internal AttributeError.
            raise ValueError(
                "No dataset rows provided. Pass one of: data or data_path."
            )
        if df.empty or len(df.columns) == 0:
            raise ValueError("Dataset data must include at least one column and one row.")

        dataset = ouro.datasets.create(
            name=name,
            visibility=visibility,
            data=df,
            description=description,
            **optional_kwargs(org_id=org_id, team_id=team_id),
        )

        result = format_asset_summary(dataset)
        result["table_name"] = dataset.metadata.get("table_name") if dataset.metadata else None
        return json.dumps(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def update_dataset(
        id: str,
        ctx: Context,
        name: Optional[str] = None,
        visibility: Optional[str] = None,
        data: Optional[list[dict]] = None,
        data_path: Optional[str] = None,
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Update a dataset's data or metadata.

        Pass data/data_path to append rows (same formats as create_dataset).
        Pass name, visibility, description, org_id, or team_id to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = _resolve_dataset_data(data=data, data_path=data_path)
        if df is not None and (df.empty or len(df.columns) == 0):
            raise ValueError("Dataset row updates must include at least one column and one row.")

        dataset = ouro.datasets.update(
            id,
            data=df,
            **optional_kwargs(
                name=name,
                visibility=visibility,
                description=description,
                org_id=org_id,
                team_id=team_id,
            ),
        )

        return json.dumps(format_asset_summary(dataset))
