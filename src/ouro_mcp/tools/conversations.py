"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Conversation, Message
from ouro.resources.conversations import Messages
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    resolve_location,
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
        "username": message.user.username if message.user else None,
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
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Organization UUID. Omit when the server is pinned to an organization"),
        ] = None,
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
        org_id, _ = resolve_location(ouro, org_id, need_team=False)

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
                    "Cursor for older messages: pass the `before` value from the previous "
                    "page's header. Omit for the most recent messages."
                )
            ),
        ] = None,
    ) -> str:
        """List messages in a conversation, oldest first within each page.

        The first call returns the most recent messages. To read further back,
        pass the `before` value the header gives.
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
            author = f"@{row['username']}" if row.get("username") else "message"
            if row.get("type") and row.get("type") != "message":
                author += f" ({row['type']})"
            parts = [
                markdown_id(row.get("id")),
                None if row.get("username") else f"user_id: `{row['user_id']}`",
                row.get("created_at"),
            ]
            return markdown_bullet(author, *parts, body=row.get("text") or "(empty)")

        cursor = (page.next_cursor or {}).get("before") if page.has_more else None
        return truncate_response(
            render_markdown_list(
                results,
                line_fn=_message_line,
                pagination=page_pagination(page),
                noun="messages",
                empty_text="No messages.",
                extras=[f"conversation_id: `{conversation_id}`"],
                next_page=f"before=`{cursor}`" if cursor else None,
            )
        )
