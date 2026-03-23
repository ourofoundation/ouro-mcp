"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.utils.content import description_to_markdown
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import format_asset_summary, optional_kwargs
from pydantic import Field

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_asset(
        id: Annotated[str, Field(description="UUID of any asset type")],
        ctx: Context,
    ) -> str:
        """Get any asset by ID. Returns metadata and type-appropriate detail.

        For datasets: includes schema and stats.
        For posts: includes text content.
        For files: includes URL, size, and MIME type.
        For services: includes list of routes.
        For routes: includes parameter schema, method, and path.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        asset = ouro.assets.retrieve(id)
        return json.dumps(_format_asset_detail(asset, ouro))

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_assets(
        ctx: Context,
        query: Annotated[str, Field(description="Search query or UUID for direct lookup")] = "",
        asset_type: Annotated[
            Optional[str], Field(description='"dataset" | "post" | "file" | "service" | "route"')
        ] = None,
        scope: Annotated[Optional[str], Field(description='"personal" | "org" | "global" | "all"')] = None,
        org_id: Annotated[Optional[str], Field(description="Organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Team UUID")] = None,
        user_id: Annotated[Optional[str], Field(description="Asset owner UUID")] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization" | "monetized"')
        ] = None,
        file_type: Annotated[
            Optional[str], Field(description='File category: "image" | "video" | "audio" | "pdf"')
        ] = None,
        extension: Annotated[Optional[str], Field(description='File extension, e.g. "csv", "json", "png"')] = None,
        metadata_filters: Annotated[
            Optional[Any],
            Field(description='Metadata key/value filters as JSON object or string, e.g. \'{"key": "value"}\''),
        ] = None,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """Search or browse assets on Ouro. Supports hybrid semantic + full-text search.

        Without a query: returns recent assets by creation date.
        With a UUID as query: direct asset lookup.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        merged_metadata: dict[str, Any] = {}
        if metadata_filters:
            if isinstance(metadata_filters, dict):
                merged_metadata.update(metadata_filters)
            elif isinstance(metadata_filters, str):
                try:
                    parsed = json.loads(metadata_filters)
                    if isinstance(parsed, dict):
                        merged_metadata.update(parsed)
                except (json.JSONDecodeError, TypeError):
                    log.warning("Ignoring invalid metadata_filters JSON: %s", metadata_filters)
        if file_type:
            merged_metadata["file_type"] = file_type
        if extension:
            merged_metadata["extension"] = extension

        response = ouro.assets.search(
            query,
            limit=limit,
            offset=offset,
            with_pagination=True,
            **optional_kwargs(
                asset_type=asset_type,
                scope=scope,
                org_id=org_id,
                team_id=team_id,
                user_id=user_id,
                visibility=visibility,
                metadata_filters=merged_metadata or None,
            ),
        )

        assets = []
        for item in response.get("data", []):
            assets.append(
                {
                    "id": str(item.get("id", "")),
                    "name": item.get("name"),
                    "asset_type": item.get("asset_type"),
                    "description": description_to_markdown(item.get("description"), max_length=200),
                    "visibility": item.get("visibility"),
                    "user": item.get("username") or item.get("user", {}).get("username"),
                }
            )

        return json.dumps(
            {
                "results": assets,
                "total": response.get("pagination", {}).get("total"),
                "hasMore": response.get("pagination", {}).get("hasMore", len(assets) == limit),
            }
        )

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    @handle_ouro_errors
    def delete_asset(
        id: Annotated[str, Field(description="UUID of the asset to delete")],
        ctx: Context,
    ) -> str:
        """Delete an asset by ID. Auto-detects the asset type and routes to the appropriate delete method."""
        ouro = ctx.request_context.lifespan_context.ouro

        asset = ouro.assets.retrieve(id)
        asset_type = asset.asset_type
        name = asset.name

        if asset_type == "dataset":
            ouro.datasets.delete(id)
        elif asset_type == "post":
            ouro.posts.delete(id)
        elif asset_type == "file":
            ouro.files.delete(id)
        else:
            return json.dumps(
                {
                    "error": "unsupported_type",
                    "message": f"Cannot delete asset of type '{asset_type}' via this tool.",
                }
            )

        return json.dumps(
            {
                "deleted": True,
                "id": id,
                "name": name,
                "asset_type": asset_type,
            }
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def download_asset(
        id: Annotated[str, Field(description="UUID of the asset to download")],
        output_path: Annotated[
            str,
            Field(description="Local file path or existing directory where the asset should be saved"),
        ],
        ctx: Context,
        asset_type: Annotated[
            Optional[str],
            Field(description='Optional override: "file" | "dataset" | "post"'),
        ] = None,
    ) -> str:
        """Download an asset to the local filesystem.

        Files keep their original bytes, datasets download as CSV, and posts as HTML.
        If output_path is a directory, the server-provided filename is used.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.assets.download(id, output_path=output_path, asset_type=asset_type)
        return json.dumps(
            {
                "downloaded": True,
                **result,
            }
        )


def _format_asset_detail(asset, ouro) -> dict:
    """Build a type-appropriate detail response for any asset."""
    base = format_asset_summary(asset)

    asset_type = asset.asset_type

    if asset_type == "dataset":
        try:
            schema = ouro.datasets.schema(str(asset.id))
            base["schema"] = schema
        except Exception:
            log.debug("Failed to fetch schema for dataset %s", asset.id, exc_info=True)
            base["schema"] = None
        try:
            stats = ouro.datasets.stats(str(asset.id))
            base["stats"] = stats
        except Exception:
            log.debug("Failed to fetch stats for dataset %s", asset.id, exc_info=True)
            base["stats"] = None
        if asset.preview:
            base["preview"] = asset.preview[:5]

    elif asset_type in {"post", "comment"}:
        if asset.content:
            # TODO: markdown version???
            base["content_text"] = asset.content.text
        else:
            base["content_text"] = None

    elif asset_type == "file":
        if asset.data:
            base["url"] = asset.data.url
        if asset.metadata:
            meta = asset.metadata
            if hasattr(meta, "size"):
                base["size"] = meta.size
            if hasattr(meta, "type"):
                base["mime_type"] = meta.type

    elif asset_type == "service":
        try:
            routes = ouro.services.read_routes(str(asset.id))
            base["routes"] = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "method": r.route.method if r.route else None,
                    "path": r.route.path if r.route else None,
                    "description": r.route.description if r.route else None,
                }
                for r in routes
            ]
        except Exception:
            log.debug("Failed to fetch routes for service %s", asset.id, exc_info=True)
            base["routes"] = []

    elif asset_type == "route":
        if asset.route:
            base["method"] = asset.route.method
            base["path"] = asset.route.path
            base["route_description"] = asset.route.description
            base["parameters"] = asset.route.parameters
            base["request_body"] = asset.route.request_body
            base["input_type"] = asset.route.input_type
            base["output_type"] = asset.route.output_type
        if asset.monetization and asset.monetization != "none":
            base["monetization"] = asset.monetization
            base["price"] = asset.price

    return base
