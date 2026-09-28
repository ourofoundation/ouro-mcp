"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Conversation, Message
from ouro.resources.conversations import Messages
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
    truncate_response,
)
from pydantic import Field


def _conversation_summary(conversation: Conversation) -> dict:
    return {
        "id": str(conversation.id),
        "name": conversation.name,
        "summary": conversation.summary,
        "created_at": conversation.created_at,
        "last_updated": conversation.last_updated,
        "member_user_ids": [str(member) for member in conversation.metadata.members],
        "org_id": str(conversation.org_id),
        "team_id": str(conversation.team_id),
    }


def _message_summary(message: Message) -> dict:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "user_id": str(message.user_id),
        "type": message.type or "message",
        "text": message.text,
        "json": message.data,
        "created_at": message.created_at,
    }


def _list_conversations(
    ouro: Any,
    *,
    org_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    page = ouro.conversations.list(org_id=org_id, limit=limit, offset=offset)
    results = [_conversation_summary(conversation) for conversation in page]

    def _conversation_line(row: dict) -> str:
        parts = [markdown_id(row.get("id"))]
        if row.get("org_id"):
            parts.append(f"org_id: `{row['org_id']}`")
        if row.get("team_id"):
            parts.append(f"team_id: `{row['team_id']}`")
        member_ids = row.get("member_user_ids") or []
        if member_ids:
            parts.append(f"members: {len(member_ids)}")
        if row.get("last_updated"):
            parts.append(row["last_updated"])
        return markdown_bullet(
            str(row.get("name") or "(unnamed conversation)"),
            *parts,
            body=row.get("summary"),
        )

    return truncate_response(
        render_markdown_list(
            results,
            line_fn=_conversation_line,
            pagination=page_pagination(page),
            offset=offset,
            noun="conversations",
            empty_text="No conversations.",
        )
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
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Top-level provenance object; separate from conversation metadata"),
        ] = None,
    ) -> str:
        """Create a conversation with the specified member user IDs."""
        ouro = ctx.request_context.lifespan_context.ouro

        conversation = ouro.conversations.create(
            member_user_ids=member_user_ids,
            name=name,
            summary=summary,
            org_id=org_id,
            license_id=license_id,
            attribution=attribution,
        )
        return dump_json(_conversation_summary(conversation))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def send_message(
        conversation_id: Annotated[str, Field(description="Conversation UUID")],
        text: Annotated[
            str,
            Field(description="Message body in extended markdown (same syntax as create_post)"),
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
        )
        results = [_message_summary(message) for message in page]

        def _message_line(row: dict) -> str:
            parts = [
                markdown_id(row.get("id")),
                f"user_id: `{row['user_id']}`" if row.get("user_id") else None,
            ]
            if row.get("type") and row.get("type") != "message":
                parts.append(f"type: {row['type']}")
            if row.get("created_at"):
                parts.append(row["created_at"])
            text = row.get("text") or ""
            primary = text[:80] + ("…" if len(text) > 80 else "") if text else "(empty)"
            body = text if len(text) > 80 else None
            return markdown_bullet(primary, *parts, body=body)

        return truncate_response(
            render_markdown_list(
                results,
                line_fn=_message_line,
                pagination=page_pagination(page),
                noun="messages",
                empty_text="No messages.",
                extras=[
                    f"conversation_id: `{conversation_id}`",
                    "Newest first. Page with before=<oldest created_at>.",
                ],
            )
        )
