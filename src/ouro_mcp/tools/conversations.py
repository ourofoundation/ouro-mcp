"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.resources.conversations import Messages
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    list_response,
    truncate_response,
)
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
        "type": message.get("type", "message"),
        "text": message.get("text"),
        "json": message.get("json"),
        "created_at": message.get("created_at"),
    }


def _list_conversations(
    ouro: Any,
    *,
    org_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    page = ouro.conversations.list(
        org_id=org_id,
        limit=limit,
        offset=offset,
        with_pagination=True,
    )
    conversations = page.get("data") or []
    pagination = page.get("pagination")

    results = [_conversation_summary(conversation) for conversation in conversations]
    return truncate_response(
        dump_json(list_response(results, pagination=pagination, limit=limit))
    )


def _get_conversation(ouro: Any, conversation_id: str) -> str:
    conversation = ouro.conversations.retrieve(conversation_id)
    return dump_json(_conversation_summary(conversation))


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_conversations(
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Filter by organization UUID"),
        ] = None,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """List conversations you belong to."""
        ouro = ctx.request_context.lifespan_context.ouro
        return _list_conversations(ouro, org_id=org_id, limit=limit, offset=offset)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_conversation(
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
    ) -> str:
        """Get one conversation by ID, including member user IDs."""
        ouro = ctx.request_context.lifespan_context.ouro
        return _get_conversation(ouro, conversation_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_conversations(
        ctx: Context,
        id: Annotated[
            Optional[str],
            Field(description="Conversation UUID for single lookup"),
        ] = None,
        org_id: Annotated[
            Optional[str],
            Field(description="Filter by organization UUID"),
        ] = None,
        limit: Annotated[int, Field(description="Max results to return")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """Compatibility wrapper. Prefer list_conversations() or get_conversation()."""
        ouro = ctx.request_context.lifespan_context.ouro
        if id:
            return _get_conversation(ouro, id)
        return _list_conversations(ouro, org_id=org_id, limit=limit, offset=offset)

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
        return dump_json(_conversation_summary(conversation))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def send_message(
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        text: Annotated[
            str,
            Field(
                description=(
                    "Message body as extended Ouro markdown: @mentions, LaTeX ($inline$, $$display$$), "
                    "typed asset link shorthands [label](post:|file:|dataset:|route:|service:<uuid>). "
                    "Use [label](asset:<uuid>) only when the asset type is unknown. "
                    "```assetComponent``` blocks for embeds, etc."
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
        type: Annotated[
            Optional[str],
            Field(
                description="Message type: 'message' (default), 'reasoning', or 'tool_call'."
            ),
        ] = None,
    ) -> str:
        """Send a message to a conversation using extended Ouro markdown."""
        ouro = ctx.request_context.lifespan_context.ouro

        content = content_from_markdown(ouro, text)
        create_kw: dict = {
            "text": content.text,
            "json": content.json,
        }
        if message_id:
            create_kw["id"] = message_id
        if type:
            create_kw["type"] = type
        message = Messages(ouro).create(
            conversation_id=conversation_id,
            **create_kw,
        )
        return dump_json(_message_summary(message))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_messages(
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
        limit: Annotated[int, Field(description="Max messages to return (1-200)")] = 20,
        before: Annotated[
            Optional[str],
            Field(
                description=(
                    "ISO timestamp cursor: return only messages strictly older than this. "
                    "Pass the `created_at` of the oldest message in the previous page to "
                    "load the next page. Omit for the newest page."
                )
            ),
        ] = None,
    ) -> str:
        """List messages in a conversation, newest-first.

        The backend uses a `before` timestamp cursor (not offset/limit) — page
        by taking the `created_at` of the oldest message in the previous page
        and passing it as `before` on the next call.
        """
        if limit <= 0 or limit > 200:
            raise ValueError("limit must be between 1 and 200.")

        ouro = ctx.request_context.lifespan_context.ouro

        page = Messages(ouro).list(
            conversation_id=conversation_id,
            limit=limit,
            before=before,
            with_pagination=True,
        )
        messages = page.get("data") or []
        pagination = page.get("pagination")

        results = [_message_summary(message) for message in messages]
        return truncate_response(
            dump_json(list_response(results, pagination=pagination, limit=limit))
        )
