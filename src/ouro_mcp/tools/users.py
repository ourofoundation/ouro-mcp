"""User search and lookup tools — tools/users.py"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.summaries import absolute_web_url
from ouro_mcp.rendering import (
    collapse_whitespace,
    dump_json,
    markdown_bullet,
    markdown_id,
    render_markdown_list,
)


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
        """Search for users on Ouro by name or username. `get_user` has one user's profile."""
        ouro = ctx.request_context.lifespan_context.ouro
        users = [
            {
                "user_id": str(u.user_id),
                "username": u.username,
                "name": u.name if u.name and u.name != u.username else None,
                "actor_type": u.actor_type,
                "bio": u.bio,
            }
            for u in ouro.users.search(query)
        ]

        def _user_line(row: dict) -> str:
            return markdown_bullet(
                f"@{row.get('username') or '(unknown)'}",
                row.get("name"),
                "agent" if row.get("actor_type") == "agent" else None,
                markdown_id(row.get("user_id")),
                body=collapse_whitespace(row.get("bio"), max_length=160) or None,
            )

        return render_markdown_list(
            users,
            line_fn=_user_line,
            noun="users",
            empty_text="No users found.",
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_user(
        username: Annotated[str, Field(description="Username (with or without @) or user UUID")],
        ctx: Context,
    ) -> str:
        """Get a user's profile: who they are, whether they're an agent, and what they've made."""
        ouro = ctx.request_context.lifespan_context.ouro
        user = ouro.users.get(username.lstrip("@"))
        metrics = dict(getattr(user, "metrics", None) or {})
        xp = getattr(user, "xp_totals", None) or {}
        profile = {
            "user_id": str(user.user_id),
            "username": user.username,
            "name": user.name,
            "bio": user.bio,
            "actor_type": user.actor_type,
            "url": absolute_web_url(f"/{user.username}") if user.username else None,
            "level": user.level,
            "h_index": xp.get("h_index") if isinstance(xp, dict) else None,
            "followers": user.followers,
            "following": user.following,
            "last_active": metrics.pop("last_active", None),
            "made": {
                k.removesuffix("_count"): v
                for k, v in metrics.items()
                if k.endswith("_count") and v and k not in ("followers_count", "following_count")
            },
            "total_views": metrics.get("total_views"),
            "badges": [b.get("name") for b in (user.badges or []) if b.get("name")] or None,
        }
        return dump_json({k: v for k, v in profile.items() if v not in (None, "", {}, [])})

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
            int, Field(ge=1, le=50, description="Max assets to include when asset_ids is omitted")
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
