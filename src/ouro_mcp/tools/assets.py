"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import format_asset_summary, handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_asset(id: str, ctx: Context) -> str:
        """Get any asset by ID or name. Returns metadata and type-appropriate detail.

        For datasets: includes schema and stats.
        For posts: includes text content.
        For files: includes URL, size, and MIME type.
        For services: includes list of routes.
        For routes: includes parameter schema, method, and path.

        Accepts a UUID for any asset type, or "entity/route_name" format for routes.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if "/" in id and not _is_uuid(id):
            asset = ouro.routes.retrieve(id)
        else:
            asset = ouro.assets.retrieve(id)

        return json.dumps(_format_asset_detail(asset, ouro))

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_assets(
        query: str,
        ctx: Context,
        asset_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Search for assets on Ouro (datasets, posts, files, services, routes).

        Use asset_type to filter results (e.g. "dataset", "service", "post").
        To browse all available API services, use: search_assets(query="", asset_type="service")
        """
        ouro = ctx.request_context.lifespan_context.ouro

        kwargs = {"limit": limit, "offset": offset}
        if asset_type:
            kwargs["asset_type"] = asset_type

        results = ouro.assets.search(query, **kwargs)

        assets = []
        for item in results:
            assets.append({
                "id": str(item.get("id", "")),
                "name": item.get("name"),
                "asset_type": item.get("asset_type"),
                "description": _truncate_str(item.get("description"), 200),
                "visibility": item.get("visibility"),
                "owner": item.get("username") or item.get("user", {}).get("username"),
            })

        return json.dumps({
            "results": assets,
            "returned": len(assets),
            "offset": offset,
            "limit": limit,
            "has_more": len(assets) == limit,
        })

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
            users.append({
                "id": str(u.get("user_id", u.get("id", ""))),
                "username": u.get("username"),
                "display_name": u.get("display_name") or u.get("username"),
            })

        return json.dumps({"results": users, "returned": len(users)})

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
            return json.dumps({
                "error": "unsupported_type",
                "message": f"Cannot delete asset of type '{asset_type}' via this tool.",
            })

        return json.dumps({
            "deleted": True,
            "id": id,
            "name": name,
            "asset_type": asset_type,
        })


def _format_asset_detail(asset, ouro) -> dict:
    """Build a type-appropriate detail response for any asset."""
    base = format_asset_summary(asset)

    asset_type = asset.asset_type

    if asset_type == "dataset":
        try:
            schema = ouro.datasets.schema(str(asset.id))
            base["schema"] = schema
        except Exception:
            base["schema"] = None
        try:
            stats = ouro.datasets.stats(str(asset.id))
            base["stats"] = stats
        except Exception:
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


def _is_uuid(s: str) -> bool:
    """Quick check if a string looks like a UUID."""
    import re
    return bool(re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        s.lower(),
    ))


def _truncate_str(s, max_len: int) -> str | None:
    if s is None:
        return None
    s = str(s)
    if isinstance(s, str) and len(s) > max_len:
        return s[:max_len] + "..."
    return s
