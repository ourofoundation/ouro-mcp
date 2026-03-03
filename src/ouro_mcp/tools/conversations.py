"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.resources.conversations import Messages

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import truncate_response


def _conversation_summary(conversation: Any) -> dict:
    if isinstance(conversation, dict):
        metadata = conversation.get("metadata")
        members = metadata.get("members") if isinstance(metadata, dict) else []
        created_at = conversation.get("created_at")
        last_updated = conversation.get("last_updated")
        return {
            "id": str(conversation.get("id", "")),
            "name": conversation.get("name"),
            "summary": conversation.get("summary"),
            "org_id": str(conversation.get("org_id", "")),
            "team_id": str(conversation.get("team_id", "")),
            "created_at": created_at,
            "last_updated": last_updated,
            "member_user_ids": [str(member) for member in (members or [])],
        }

    metadata = getattr(conversation, "metadata", None)
    members = getattr(metadata, "members", None) if metadata is not None else []

    return {
        "id": str(getattr(conversation, "id", "")),
        "name": getattr(conversation, "name", None),
        "summary": getattr(conversation, "summary", None),
        "org_id": str(getattr(conversation, "org_id", "")),
        "team_id": str(getattr(conversation, "team_id", "")),
        "created_at": (
            conversation.created_at.isoformat()
            if getattr(conversation, "created_at", None)
            else None
        ),
        "last_updated": (
            conversation.last_updated.isoformat()
            if getattr(conversation, "last_updated", None)
            else None
        ),
        "member_user_ids": [str(member) for member in (members or [])],
    }


def _message_summary(message: dict) -> dict:
    return {
        "id": str(message.get("id", "")),
        "conversation_id": str(message.get("conversation_id", "")),
        "user_id": str(message.get("user_id", "")),
        "text": message.get("text"),
        "json": message.get("json"),
        "created_at": message.get("created_at"),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_conversations(
        ctx: Context,
        org_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """List conversations the authenticated user belongs to."""
        ouro = ctx.request_context.lifespan_context.ouro

        conversations = ouro.conversations.list(
            org_id=org_id,
            limit=limit,
            offset=offset,
        )

        results = [_conversation_summary(conversation) for conversation in conversations]
        result = json.dumps({
            "results": results,
            "count": len(results),
            "pagination": {
                "offset": offset,
                "limit": limit,
                "hasMore": len(results) == limit,
            },
        })
        return truncate_response(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_conversation(
        id: str,
        ctx: Context,
    ) -> str:
        """Get a conversation by ID with metadata."""
        ouro = ctx.request_context.lifespan_context.ouro

        conversation = ouro.conversations.retrieve(id)
        return json.dumps(_conversation_summary(conversation))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_conversation(
        member_user_ids: list[str],
        ctx: Context,
        name: Optional[str] = None,
        summary: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> str:
        """Create a conversation with the specified member user IDs."""
        ouro = ctx.request_context.lifespan_context.ouro

        conversation = ouro.conversations.create(
            member_user_ids=member_user_ids,
            name=name,
            summary=summary,
            org_id=org_id,
        )
        return json.dumps(_conversation_summary(conversation))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def send_message(
        conversation_id: str,
        text: str,
        ctx: Context,
    ) -> str:
        """Send a text message to a conversation."""
        ouro = ctx.request_context.lifespan_context.ouro

        message = Messages(ouro).create(conversation_id=conversation_id, text=text)
        return json.dumps(_message_summary(message))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_messages(
        conversation_id: str,
        ctx: Context,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """List messages in a conversation with pagination."""
        ouro = ctx.request_context.lifespan_context.ouro

        messages = Messages(ouro).list(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        results = [_message_summary(message) for message in messages]

        result = json.dumps({
            "results": results,
            "count": len(results),
            "conversation_id": conversation_id,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "hasMore": len(results) == limit,
            },
        })
        return truncate_response(result)
