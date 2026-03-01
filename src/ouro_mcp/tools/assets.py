"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.utils.content import description_to_markdown

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import format_asset_summary, optional_kwargs

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_asset(id: str, ctx: Context) -> str:
        """Get any asset by ID. Returns metadata and type-appropriate detail.

        For datasets: includes schema and stats.
        For posts: includes text content.
        For files: includes URL, size, and MIME type.
        For services: includes list of routes.
        For routes: includes parameter schema, method, and path.

        Accepts a UUID for any asset type.
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
        query: str = "",
        asset_type: Optional[str] = None,
        scope: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        user_id: Optional[str] = None,
        visibility: Optional[str] = None,
        file_type: Optional[str] = None,
        extension: Optional[str] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Search or browse assets on Ouro (datasets, posts, files, services, routes).

        With a query: performs hybrid semantic + full-text search.
        Without a query: returns recent assets sorted by creation date.
        With a UUID as query: looks up that single asset directly.

        Filters (all optional):
        - asset_type: "dataset", "post", "file", "service", "route"
        - scope: "personal", "org", "global", "all"
        - org_id: scope to an organization (UUID)
        - team_id: scope to a team within an org (UUID)
        - user_id: filter by asset owner (UUID)
        - visibility: "public", "private", "organization", "monetized"
        - file_type: filter files by category: "image", "video", "audio", "pdf"
        - extension: filter files by extension, e.g. "csv", "json", "png"
        - metadata_filters: other metadata key/values (e.g. {"custom_key": "value"})

        Examples:
          Browse recent datasets: search_assets(asset_type="dataset")
          Find CSV files: search_assets(query="sales data", file_type="image", extension="csv")
          Browse all services: search_assets(asset_type="service")
        """
        ouro = ctx.request_context.lifespan_context.ouro

        merged_metadata = dict(metadata_filters or {})
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
                metadata_filters=merged_metadata if merged_metadata else None,
            ),
        )

        assets = []
        for item in response.get("data", []):
            assets.append(
                {
                    "id": str(item.get("id", "")),
                    "name": item.get("name"),
                    "asset_type": item.get("asset_type"),
                    "description": description_to_markdown(
                        item.get("description"), max_length=200
                    ),
                    "visibility": item.get("visibility"),
                    "user": item.get("username")
                    or item.get("user", {}).get("username"),
                }
            )

        return json.dumps(
            {
                "results": assets,
                "count": len(assets),
                "pagination": {
                    "offset": response.get("pagination", {}).get("offset", offset),
                    "limit": response.get("pagination", {}).get("limit", limit),
                    "hasMore": response.get("pagination", {}).get("hasMore", len(assets) == limit),
                    "total": response.get("pagination", {}).get("total"),
                },
            }
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_users(query: str, ctx: Context) -> str:
        """Search for users on Ouro by name or username."""
        ouro = ctx.request_context.lifespan_context.ouro
        results = ouro.users.search(query)

        users = []
        for u in results:
            users.append(
                {
                    "id": str(u.get("user_id", u.get("id", ""))),
                    "username": u.get("username"),
                    "display_name": u.get("display_name"),
                }
            )

        return json.dumps({"results": users, "count": len(users)})

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    @handle_ouro_errors
    def delete_asset(id: str, ctx: Context) -> str:
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

    elif asset_type == "post":
        if asset.content:
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
