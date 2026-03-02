"""Dataset resources — full detail and schema-only views."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import format_asset_summary

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://datasets/{id}",
        name="Dataset",
        description="Full dataset detail: metadata, column schema, stats, and preview rows.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_dataset(id: str, ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro

        dataset = ouro.datasets.retrieve(id)
        result = format_asset_summary(dataset)

        try:
            result["schema"] = ouro.datasets.schema(id)
        except Exception:
            log.debug("Failed to fetch schema for dataset %s", id, exc_info=True)
            result["schema"] = None

        try:
            result["stats"] = ouro.datasets.stats(id)
        except Exception:
            log.debug("Failed to fetch stats for dataset %s", id, exc_info=True)
            result["stats"] = None

        if dataset.preview:
            result["preview"] = dataset.preview[:5]

        return json.dumps(result)

    @mcp.resource(
        "ouro://datasets/{id}/schema",
        name="Dataset Schema",
        description="Column schema for a dataset — names, types, and nullability.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_dataset_schema(id: str, ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro
        schema = ouro.datasets.schema(id)
        return json.dumps({"dataset_id": id, "schema": schema})
