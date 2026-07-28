"""Notification tools — list and mark as read."""

from __future__ import annotations

from typing import Annotated, Optional, Union

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, list_response, truncate_response


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_notifications(
        ctx: Context,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        org_id: Annotated[
            Optional[str], Field(description="Filter by organization UUID")
        ] = None,
        unread_only: Annotated[
            bool, Field(description="Only return unread notifications")
        ] = False,
        category: Annotated[
            Optional[str],
            Field(
                description=(
                    "Comma-separated categories: mentions, comments, shares, money"
                )
            ),
        ] = None,
    ) -> str:
        """List notifications for the authenticated user, newest first."""
        ouro = ctx.request_context.lifespan_context.ouro

        response = ouro.notifications.list(
            offset=offset,
            limit=limit,
            org_id=org_id,
            unread_only=unread_only,
            category=category,
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

        return truncate_response(
            dump_json(
                list_response(
                    results,
                    pagination=response.get("pagination") or {},
                    limit=limit,
                )
            )
        )

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def read_notification(
        ids: Annotated[
            Union[list[str], str],
            Field(
                description="One notification UUID or a list of UUIDs to mark read"
            ),
        ],
        ctx: Context,
    ) -> str:
        """Mark one or many notifications as read in a single call.

        Pass every id you have handled or dismissed; omit ids you want to keep
        unread so they surface again later.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        id_list = [ids] if isinstance(ids, str) else list(dict.fromkeys(ids))
        read, failed = [], []
        for nid in id_list:
            try:
                ouro.notifications.read(nid)
                read.append(nid)
            except Exception as exc:
                failed.append({"id": nid, "error": str(exc)})
        return dump_json({"read": len(read), "read_ids": read, "failed": failed})
