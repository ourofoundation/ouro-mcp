"""Dataset resources — full detail and schema-only views."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, format_asset_summary

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

        return dump_json(result)

    @mcp.resource(
        "ouro://datasets/{id}/schema",
        name="Dataset Schema",
        description=(
            "Column schema for a dataset — names, types, and foreign keys. "
            'Columns with semantic_type "reference" hold Ouro object ids '
            "(backed by a foreign key); ref_kind names the kind (asset -> "
            "public.assets, action -> public.actions) and refs maps those "
            "columns to {kind, asset_type?}. Use "
            "query_dataset(resolve_refs=true) to resolve those ids to "
            "names, types, and URLs. Columns with semantic_type \"enum\" include "
            "enum_values for agent-friendly categorical queries."
        ),
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_dataset_schema(id: str, ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro
        schema = ouro.datasets.schema(id)
        # The backend already enriches FK-to-referenceable-table columns with
        # semantic_type="reference" (+ ref_kind and optional target asset_type),
        # so this is surfaced verbatim.
        refs = {}
        enum_columns = {}
        for field in schema or []:
            if not isinstance(field, dict):
                continue
            if field.get("semantic_type") == "reference":
                column = field.get("column_name")
                if not column:
                    continue
                kind = field.get("ref_kind") or "asset"
                entry = {"kind": kind}
                if kind == "asset" and field.get("asset_type"):
                    entry["asset_type"] = field["asset_type"]
                refs[column] = entry
            elif field.get("semantic_type") == "enum":
                column = field.get("column_name")
                values = field.get("enum_values")
                if column and isinstance(values, list):
                    enum_columns[column] = {"values": values}
        return dump_json(
            {
                "dataset_id": id,
                "schema": schema,
                "refs": refs,
                "enum_columns": enum_columns,
            }
        )
