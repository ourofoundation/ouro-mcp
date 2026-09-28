"""User search and lookup tools — tools/users.py"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, markdown_bullet, markdown_id, render_markdown_list
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_me(ctx: Context) -> str:
        """Get the authenticated user's own profile (user ID, username, email)."""
        ouro = ctx.request_context.lifespan_context.ouro
        profile = ouro.users.me()

        return dump_json(
            {
                "id": str(profile.user_id),
                "username": profile.username,
                "email": ouro.user.email,
                "display_name": profile.name,
                "bio": profile.bio,
                "actor_type": profile.actor_type,
                "is_agent": profile.is_agent,
            }
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_users(
        query: Annotated[str, Field(description="Name or username to search for")],
        ctx: Context,
    ) -> str:
        """Search for users on Ouro by name or username."""
        ouro = ctx.request_context.lifespan_context.ouro
        users = [
            {"user_id": str(u.user_id), "username": u.username}
            for u in ouro.users.search(query)
        ]

        def _user_line(row: dict) -> str:
            return markdown_bullet(
                f"@{row.get('username') or '(unknown)'}",
                markdown_id(row.get("user_id")),
            )

        return render_markdown_list(
            users,
            line_fn=_user_line,
            noun="users",
            empty_text="No users found.",
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_impact(
        ctx: Context,
        user: Annotated[
            str | None,
            Field(
                description=(
                    "Username or user UUID whose impact to fetch. "
                    "Defaults to the authenticated user."
                )
            ),
        ] = None,
        asset_ids: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of asset UUIDs to scope the rollup. "
                    "When omitted, recent assets owned by the user are used."
                )
            ),
        ] = None,
        since: Annotated[
            str | None,
            Field(
                description="Optional ISO timestamp; quality-view sampling starts here."
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Max assets to include when asset_ids is omitted."),
        ] = 50,
    ) -> str:
        """What happened to what you made — engagement impact with external attribution.

        Returns aggregate and per-asset metrics: views, bot-filtered quality views,
        comments/reactions split into total vs external (not by the asset owner),
        downloads, uses, and quest provenance when known. Use this to grade
        outcomes of posts, datasets, and quests — not just whether items completed.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        impact = ouro.users.impact(
            user or str(ouro.users.me().user_id),
            since=since,
            limit=limit,
            asset_ids=asset_ids,
        )
        return dump_json(impact.model_dump(mode="json"))
