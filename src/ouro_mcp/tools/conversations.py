"""Conversation tools — list, retrieve, create, and message conversations."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Conversation, Message
from ouro.resources.conversations import Messages
from pydantic import Field

from ouro_mcp.constants import GLOBAL_ORG_ID
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.markdown_input import (
    content_from_markdown,
)
from ouro_mcp.params import ATTRIBUTION_DESC, Attribution
from ouro_mcp.rendering import (
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
    truncate_response,
)
from ouro_mcp.utils import (
    optional_kwargs,
    resolve_location,
)

log = logging.getLogger(__name__)


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


# Member names are looked up one user at a time; past this many, rows give a count.
MAX_MEMBER_LOOKUPS = 12


def _member_usernames(ouro: Any, user_ids: set[str]) -> dict[str, str]:
    """``user_id -> username`` for conversation members, best effort."""
    if len(user_ids) > MAX_MEMBER_LOOKUPS:
        return {}
    names: dict[str, str] = {}
    for user_id in user_ids:
        try:
            user = ouro.users.get(user_id)
        except Exception:
            log.debug("Could not look up conversation member %s", user_id, exc_info=True)
            continue
        if getattr(user, "username", None):
            names[user_id] = user.username
    return names


def _list_conversations(
    ouro: Any,
    *,
    org_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    page = ouro.conversations.list(org_id=org_id, limit=limit, offset=offset)
    results = [_conversation_summary(conversation) for conversation in page]
    me_id = str(getattr(getattr(ouro, "user", None), "id", ""))
    usernames = _member_usernames(
        ouro, {m for row in results for m in row["member_user_ids"] if m != me_id}
    )

    def _conversation_line(row: dict) -> str:
        others = [m for m in row.get("member_user_ids") or [] if m != me_id]
        names = [f"@{usernames[m]}" for m in others if m in usernames]
        with_whom = (
            "with " + ", ".join(names)
            if names and len(names) == len(others)
            else f"{len(others) + 1} members"
        )
        parts = [markdown_id(row.get("id")), with_whom]
        if row.get("org_id") and row["org_id"] != GLOBAL_ORG_ID:
            parts.append(f"org_id: `{row['org_id']}`")
        if row.get("team_id") and row["team_id"] != GLOBAL_ORG_ID:
            parts.append(f"team_id: `{row['team_id']}`")
        if row.get("last_updated"):
            parts.append(f"updated {row['last_updated']}")
        return markdown_bullet(
            str(row.get("name") or "Conversation"),
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
        limit: Annotated[int, Field(ge=1, le=100, description="Max results to return")] = 20,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """List conversations you belong to."""
        ouro = ctx.request_context.lifespan_context.ouro
        return _list_conversations(ouro, org_id=org_id, limit=limit, offset=offset)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_conversation(
        id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
    ) -> str:
        """Get one conversation by ID, including member user IDs."""
        ouro = ctx.request_context.lifespan_context.ouro
        return _get_conversation(ouro, id)

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
        attribution: Annotated[Optional[Attribution], Field(description=ATTRIBUTION_DESC)] = None,
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

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_conversation(
        id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        summary: Annotated[Optional[str], Field(description="New summary")] = None,
    ) -> str:
        """Rename a conversation or change its summary."""
        if name is None and summary is None:
            raise ValueError("Pass name and/or summary.")
        ouro = ctx.request_context.lifespan_context.ouro
        conversation = ouro.conversations.update(id, **optional_kwargs(name=name, summary=summary))
        return dump_json(_conversation_summary(conversation))

    @mcp.tool(annotations={"destructiveHint": True, "idempotentHint": True})
    @handle_ouro_errors
    def leave_conversation(
        id: Annotated[str, Field(description="Conversation UUID")],
        ctx: Context,
    ) -> str:
        """Leave a conversation. If you are its last member, it is deleted with its messages."""
        ouro = ctx.request_context.lifespan_context.ouro
        ouro.conversations.delete(id)
        return dump_json({"left": True, "id": id})

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
            Optional[Literal["message", "reasoning", "tool_call"]], Field(description='Default "message"')
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
        limit: Annotated[int, Field(ge=1, le=200, description="Max messages to return")] = 20,
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
                next_page=f"before=`{cursor}`" if cursor else None,
            )
        )
