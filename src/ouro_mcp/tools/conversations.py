"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.resources.conversations import Messages
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import content_from_markdown, truncate_response
from pydantic import Field


def _conversation_summary(conversation: Any) -> dict:
    if isinstance(conversation, dict):
        metadata = conversation.get("metadata")
        members = metadata.get("members") if isinstance(metadata, dict) else []
        result: dict[str, Any] = {
            "id": str(conversation.get("id", "")),
            "name": conversation.get("name"),
            "summary": conversation.get("summary"),
            "created_at": conversation.get("created_at"),
            "last_updated": conversation.get("last_updated"),
            "member_user_ids": [str(member) for member in (members or [])],
        }
        org_id = conversation.get("org_id")
        if org_id:
            result["org_id"] = str(org_id)
        team_id = conversation.get("team_id")
        if team_id:
            result["team_id"] = str(team_id)
        return result

    metadata = getattr(conversation, "metadata", None)
    members = getattr(metadata, "members", None) if metadata is not None else []

    result: dict[str, Any] = {
        "id": str(getattr(conversation, "id", "")),
        "name": getattr(conversation, "name", None),
        "summary": getattr(conversation, "summary", None),
        "created_at": (conversation.created_at.isoformat() if getattr(conversation, "created_at", None) else None),
        "last_updated": (
            conversation.last_updated.isoformat() if getattr(conversation, "last_updated", None) else None
        ),
        "member_user_ids": [str(member) for member in (members or [])],
    }
    org_id = getattr(conversation, "org_id", None)
    if org_id:
        result["org_id"] = str(org_id)
    team_id = getattr(conversation, "team_id", None)
    if team_id:
        result["team_id"] = str(team_id)
    return result


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
    def get_conversations(
        ctx: Context,
        id: Annotated[Optional[str], Field(description="Conversation UUID for single lookup")] = None,
        org_id: Annotated[Optional[str], Field(description="Filter by organization UUID")] = None,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """Get a conversation by ID, or list conversations you belong to."""
        ouro = ctx.request_context.lifespan_context.ouro

        if id:
            conversation = ouro.conversations.retrieve(id)
            return json.dumps(_conversation_summary(conversation))

        conversations = ouro.conversations.list(
            org_id=org_id,
            limit=limit,
            offset=offset,
        )

        results = [_conversation_summary(conversation) for conversation in conversations]
        return truncate_response(
            json.dumps(
                {
                    "results": results,
                    "hasMore": len(results) == limit,
                }
            )
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_conversation(
        member_user_ids: Annotated[list[str], Field(description="User UUIDs to include")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="Conversation name")] = None,
        summary: Annotated[Optional[str], Field(description="Conversation summary")] = None,
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
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        text: Annotated[
            str,
            Field(
                description=(
                    "Message body as extended Ouro markdown: @mentions, LaTeX ($inline$, $$display$$), "
                    "asset link shorthands [label](post:|file:|dataset:|route:|service:|asset:<uuid>), "
                    "```assetComponent``` blocks for embeds, etc. Converted via the Ouro API "
                    "(link shorthands resolved server-side)."
                )
            ),
        ],
        ctx: Context,
        message_id: Annotated[
            Optional[str],
            Field(
                description=(
                    "Optional UUID for the new message row. Use when the client already "
                    "assigned an id (e.g. websocket streaming id) so the persisted message "
                    "matches realtime events."
                )
            ),
        ] = None,
    ) -> str:
        """Send a message to a conversation. The body is extended Ouro markdown, converted server-side."""
        ouro = ctx.request_context.lifespan_context.ouro

        content = content_from_markdown(ouro, text)
        create_kw: dict = {
            "text": content.text,
            "json": content.json,
        }
        if message_id:
            create_kw["id"] = message_id
        message = Messages(ouro).create(
            conversation_id=conversation_id,
            **create_kw,
        )
        return json.dumps(_message_summary(message))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_messages(
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
        limit: Annotated[int, Field(description="Max messages to return")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """List messages in a conversation with pagination."""
        ouro = ctx.request_context.lifespan_context.ouro

        messages = Messages(ouro).list(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        results = [_message_summary(message) for message in messages]
        return truncate_response(
            json.dumps(
                {
                    "results": results,
                    "hasMore": len(results) == limit,
                }
            )
        )
