"""Notification resources — unread counts."""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://notifications/unread",
        name="Unread Notifications",
        description="Count of unread notifications for the authenticated user.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_unread_notifications(ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro
        count = ouro.notifications.unreads()
        return dump_json({"unread_count": count})
