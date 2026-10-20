"""Notification tools — list and mark as read."""

from __future__ import annotations

from typing import Annotated, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.params import NotificationCategories
from ouro_mcp.rendering import (
    clip,
    collapse_whitespace,
    dump_json,
    markdown_bullet,
    markdown_id,
    render_markdown_list,
    truncate_response,
)

# A notification's text leads its row; the rest is in the app.
NOTIFICATION_TEXT_CHARS = 160


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_notifications(
        ctx: Context,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
        limit: Annotated[int, Field(ge=1, le=100, description="Max results to return")] = 20,
        org_id: Annotated[
            Optional[str], Field(description="Filter by organization UUID")
        ] = None,
        unread_only: Annotated[
            bool, Field(description="Only return unread notifications")
        ] = False,
        category: Annotated[
            Optional[NotificationCategories], Field(description='Only these kinds, e.g. ["mentions", "comments"]')
        ] = None,
    ) -> str:
        """List notifications for the authenticated user, newest first."""
        ouro = ctx.request_context.lifespan_context.ouro

        page = ouro.notifications.list(
            offset=offset,
            limit=limit,
            org_id=org_id,
            unread_only=unread_only,
            category=",".join(category) if category else None,
        )

        me_id = str(getattr(getattr(ouro, "user", None), "id", ""))
        results = []
        for n in page:
            entry = {
                "id": str(n.id),
                "type": n.type,
                "viewed": n.viewed,
                "created_at": n.created_at,
            }

            # Your own runs notify you; "from @you" says nothing
            if n.source_user and str(getattr(n.source_user, "user_id", "")) != me_id:
                entry["from"] = n.source_user.username or n.source_user.name

            text = (n.content or {}).get("text")
            if text:
                entry["text"] = text

            # Lets an action-complete notification lead straight to get_action
            if getattr(n, "action_id", None):
                entry["action_id"] = str(n.action_id)

            if n.asset:
                entry["asset"] = {
                    "id": str(n.asset.id),
                    "name": n.asset.name,
                    "asset_type": n.asset.asset_type,
                }

            results.append(entry)

        def _notification_line(row: dict) -> str:
            text = collapse_whitespace(row.get("text"))
            sender = row.get("from")
            parts = [
                markdown_id(row.get("id")),
                "unread" if row.get("viewed") is False else None,
                f"from @{sender}" if sender else None,
                row.get("created_at"),
            ]
            asset = row.get("asset") if isinstance(row.get("asset"), dict) else None
            body_bits = []
            if asset and asset.get("id"):
                # The text usually names the asset already; then the id is enough
                name = asset.get("name")
                label = asset.get("asset_type") if name and name in text else f"{name} ({asset.get('asset_type')})"
                body_bits.append(f"{label} `{asset['id']}`")
            if row.get("action_id"):
                body_bits.append(f"action_id: `{row['action_id']}`")
            return markdown_bullet(
                clip(text, NOTIFICATION_TEXT_CHARS) or str(row.get("type") or "notification"),
                *parts,
                kind=row.get("type") if text else None,
                body=" · ".join(body_bits) if body_bits else None,
            )

        return truncate_response(
            render_markdown_list(
                results,
                line_fn=_notification_line,
                has_more=page.has_more,
                offset=offset,
                noun="notifications",
                empty_text="No notifications.",
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
