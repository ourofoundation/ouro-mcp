"""Dataset tools — query, create, update."""

from __future__ import annotations

import json
from typing import Optional

import pandas as pd
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import format_asset_summary, optional_kwargs, truncate_response


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
    def create_dataset(
        name: str,
        ctx: Context,
        data: Optional[list[dict]] = None,
        visibility: str = "private",
        description: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> str:
        """Create a new dataset on Ouro from JSON records.

        data should be a list of dicts where each dict is a row.
        Example: [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        Use org_id and team_id to control where the dataset is created.
        Call get_organizations() and get_teams() first to find the right location.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = pd.DataFrame(data) if data else None

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
        description: Optional[str] = None,
    ) -> str:
        """Update a dataset's data or metadata.

        Pass data as a list of dicts to append new rows.
        Pass name, visibility, or description to update metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        df = pd.DataFrame(data) if data else None

        dataset = ouro.datasets.update(
            id,
            data=df,
            **optional_kwargs(name=name, visibility=visibility, description=description),
        )

        return json.dumps(format_asset_summary(dataset))
