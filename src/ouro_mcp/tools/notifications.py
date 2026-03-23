"""Notification tools — list and mark as read."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import truncate_response


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_notifications(
        ctx: Context,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        org_id: Annotated[Optional[str], Field(description="Filter by organization UUID")] = None,
        unread_only: Annotated[bool, Field(description="Only return unread notifications")] = False,
    ) -> str:
        """List notifications for the authenticated user, newest first."""
        ouro = ctx.request_context.lifespan_context.ouro

        response = ouro.notifications.list(
            offset=offset,
            limit=limit,
            org_id=org_id,
            unread_only=unread_only,
            with_pagination=True,
        )

        results = []
        for n in response.get("data", []):
            entry = {
                "id": str(n.get("id", "")),
                "type": n.get("type"),
                "viewed": n.get("viewed"),
                "created_at": n.get("created_at"),
            }

            source = n.get("source_user")
            if source:
                entry["from"] = source.get("username") or source.get("name")

            content = n.get("content", {})
            if content.get("text"):
                entry["text"] = content["text"]

            asset = content.get("asset") if isinstance(content, dict) else None
            if asset and isinstance(asset, dict):
                entry["asset"] = {
                    "id": str(asset.get("id", "")),
                    "name": asset.get("name"),
                    "asset_type": asset.get("asset_type"),
                }

            results.append(entry)

        return truncate_response(json.dumps({
            "results": results,
            "total": response.get("pagination", {}).get("total"),
            "hasMore": response.get("pagination", {}).get("hasMore", len(results) == limit),
        }))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def read_notification(
        id: Annotated[str, Field(description="Notification UUID")],
        ctx: Context,
    ) -> str:
        """Mark a notification as read and return it."""
        ouro = ctx.request_context.lifespan_context.ouro

        notification = ouro.notifications.read(id)

        result = {
            "id": str(notification.get("id", "")),
            "type": notification.get("type"),
            "viewed": notification.get("viewed"),
            "created_at": notification.get("created_at"),
        }

        source = notification.get("source_user")
        if source:
            result["from"] = source.get("username") or source.get("name")

        content = notification.get("content", {})
        if content.get("text"):
            result["text"] = content["text"]

        return json.dumps(result)
