"""Notification resources — unread counts."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.http_auth import current_ouro
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
    def get_unread_notifications() -> str:
        ouro = current_ouro()
        count = ouro.notifications.unreads()
        return dump_json({"unread_count": count})
