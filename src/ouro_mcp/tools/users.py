"""User search and lookup tools — tools/users.py"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, list_response
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_me(ctx: Context) -> str:
        """Get the authenticated user's own profile (user ID, username, email)."""
        ouro = ctx.request_context.lifespan_context.ouro
        profile = ouro.users.me() or {}
        auth_user = ouro.user
        actor_type = profile.get("actor_type")

        return dump_json(
            {
                "id": str(profile.get("user_id", getattr(auth_user, "id", "?"))),
                "username": profile.get("username"),
                "email": profile.get("email") or getattr(auth_user, "email", None),
                "display_name": profile.get("display_name"),
                "bio": profile.get("bio"),
                "actor_type": actor_type,
                "is_agent": profile.get("is_agent", actor_type == "agent"),
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
        results = ouro.users.search(query)

        users = []
        for u in results:
            users.append(
                {
                    "user_id": str(u.get("user_id", u.get("id", ""))),
                    "username": u.get("username"),
                }
            )

        return dump_json(list_response(users))

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
        name_or_id = user
        if not name_or_id:
            profile = ouro.users.me() or {}
            name_or_id = str(
                profile.get("user_id")
                or profile.get("username")
                or getattr(ouro.user, "id", "")
            )
        if not name_or_id:
            return dump_json({"error": "Could not resolve user for impact"})

        # Prefer batch assets.impact when specific ids are given.
        if asset_ids:
            impact_fn = getattr(getattr(ouro, "assets", None), "impact", None)
            if callable(impact_fn):
                data = impact_fn(asset_ids, since=since) or {}
                return dump_json(data)

        data = ouro.users.impact(
            name_or_id,
            since=since,
            limit=limit,
            asset_ids=asset_ids,
        )
        return dump_json(data or {})
