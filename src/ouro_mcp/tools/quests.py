"""Quest tools — create, update, and item management."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
    list_response,
    optional_kwargs,
)
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_quest(
        name: Annotated[str, Field(description="Quest title")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        description_markdown: Annotated[
            Optional[str],
            Field(
                description=(
                    "Extended markdown body for the quest description. Supports "
                    "@mentions, LaTeX ($inline$, $$display$$), "
                    "typed asset link shorthands [text](post:|file:|dataset:|route:|service:<uuid>). "
                    "Use [text](asset:<uuid>) only when the asset type is unknown. "
                    "and block-level asset embeds via ```assetComponent``` using "
                    '{"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}.'
                )
            ),
        ] = None,
        items: Annotated[
            Optional[List[str]],
            Field(
                description=(
                    "List of task descriptions to create as quest items. "
                    "Each string becomes an item with status 'pending'."
                )
            ),
        ] = None,
        visibility: Annotated[str, Field(description='"public" | "private" | "organization"')] = "public",
        type: Annotated[str, Field(description='"closable" | "continuous"')] = "closable",
        status: Annotated[
            str,
            Field(description='"draft" | "open" | "closed" | "cancelled"'),
        ] = "open",
    ) -> str:
        """Create a new quest on Ouro with optional task items.

        The description is prose context. Items are the structured work plan —
        each item becomes a trackable task that can be completed or assigned.

        Asset references in description:
        - Inline links: prefer [label](post:|file:|dataset:|route:|service:<uuid>).
        - Use [label](asset:<uuid>) only when the asset type is unknown.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        description = None
        if description_markdown is not None:
            description = content_from_markdown(ouro, description_markdown)

        quest = ouro.quests.create(
            name=name,
            description=description,
            visibility=visibility,
            type=type,
            status=status,
            org_id=org_id,
            team_id=team_id,
            items=items,
        )

        result = format_asset_summary(quest)
        if quest.items:
            result["items"] = [{"id": str(i.id), "description": i.description, "status": i.status} for i in quest.items]
        return dump_json(result)

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_quest(
        id: Annotated[str, Field(description="Quest UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New title")] = None,
        description_markdown: Annotated[
            Optional[str],
            Field(
                description=(
                    "Replacement extended markdown body for the quest description. Supports "
                    "@mentions, LaTeX ($inline$, $$display$$), "
                    "typed asset link shorthands [text](post:|file:|dataset:|route:|service:<uuid>). "
                    "Use [text](asset:<uuid>) only when the asset type is unknown. "
                    "and block-level asset embeds via ```assetComponent``` using "
                    '{"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}.'
                )
            ),
        ] = None,
        visibility: Annotated[Optional[str], Field(description='"public" | "private" | "organization"')] = None,
        status: Annotated[
            Optional[str],
            Field(description='"draft" | "open" | "closed" | "cancelled" ("closed" and "cancelled" are terminal)'),
        ] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
    ) -> str:
        """Update a quest's description or metadata. Pass description_markdown to replace the body.

        Inline links: prefer typed post:/file:/dataset:/route:/service: shorthands.
        Use asset:<uuid> only when the asset type is unknown.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        description = None
        if description_markdown is not None:
            description = content_from_markdown(ouro, description_markdown)

        quest = ouro.quests.update(
            id,
            **optional_kwargs(
                name=name,
                description=description,
                visibility=visibility,
                status=status,
                org_id=org_id,
                team_id=team_id,
            ),
        )

        return dump_json(format_asset_summary(quest))

    # ── Quest Item tools ──

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_quest_items(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        ctx: Context,
    ) -> str:
        """List items for a quest with their status and progress summary."""
        ouro = ctx.request_context.lifespan_context.ouro
        items = ouro.quests.list_items(quest_id)
        total = len(items)
        done = sum(1 for i in items if i.status == "done")
        result = {
            "quest_id": quest_id,
            "progress": f"{done}/{total}",
            "items": [
                {
                    "id": str(i.id),
                    "description": i.description,
                    "status": i.status,
                    "sort_order": i.sort_order,
                    "reward_currency": i.reward_currency,
                    "reward_amount": i.reward_amount,
                    **({"notes": i.notes} if i.notes else {}),
                    **({"assignee_id": str(i.assignee_id)} if i.assignee_id else {}),
                    **({"child_quest_id": str(i.child_quest_id)} if i.child_quest_id else {}),
                }
                for i in items
            ],
        }
        return dump_json(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_quest_items(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        items: Annotated[
            List[Union[str, Dict[str, Any]]],
            Field(
                description=(
                    "Items to add. Each element is either a plain description "
                    "string or a full item object with any of: description, "
                    "expected_asset_type, reward_currency ('btc'|'usd'), "
                    "reward_amount (sats for btc, cents for usd), reward_xp, "
                    "eval_route_id, eval_score_path, eval_pass_min, "
                    "eval_pass_max, eval_input_key."
                )
            ),
        ],
        ctx: Context,
    ) -> str:
        """Batch-add items to an existing quest (for replanning, decomposition, or attaching rewards/eval)."""
        ouro = ctx.request_context.lifespan_context.ouro
        created = ouro.quests.create_items(quest_id, items)
        return dump_json(
            [
                {
                    "id": str(i.id),
                    "description": i.description,
                    "status": i.status,
                    "sort_order": i.sort_order,
                    "expected_asset_type": getattr(i, "expected_asset_type", None),
                    "reward_currency": i.reward_currency,
                    "reward_amount": i.reward_amount,
                    "reward_xp": getattr(i, "reward_xp", None),
                    "eval_route_id": getattr(i, "eval_route_id", None),
                    "eval_score_path": getattr(i, "eval_score_path", None),
                    "eval_pass_min": getattr(i, "eval_pass_min", None),
                    "eval_pass_max": getattr(i, "eval_pass_max", None),
                    "eval_input_key": getattr(i, "eval_input_key", None),
                }
                for i in created
            ]
        )

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_quest_item(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Item UUID")],
        ctx: Context,
        status: Annotated[
            Optional[str],
            Field(description='"pending" | "in_progress" | "done" | "skipped"'),
        ] = None,
        description: Annotated[Optional[str], Field(description="Updated task description")] = None,
        notes: Annotated[Optional[str], Field(description="Internal notes on this item")] = None,
        sort_order: Annotated[
            Optional[int],
            Field(description="1-indexed display order for the item in quest lists"),
        ] = None,
        eval_route_id: Annotated[
            Optional[str],
            Field(description="Route UUID used to auto-evaluate entries"),
        ] = None,
        eval_score_path: Annotated[
            Optional[str],
            Field(description="JSON path into the action response, defaults to $.score"),
        ] = None,
        eval_pass_min: Annotated[
            Optional[float],
            Field(description="Inclusive minimum passing score"),
        ] = None,
        eval_pass_max: Annotated[
            Optional[float],
            Field(description="Inclusive maximum passing score"),
        ] = None,
        eval_input_key: Annotated[
            Optional[str],
            Field(description="Route input_assets key to bind the submitted asset to"),
        ] = None,
        expected_asset_type: Annotated[
            Optional[str],
            Field(description="Asset type expected on submitted entries"),
        ] = None,
        reward_currency: Annotated[
            Optional[str],
            Field(description='"btc" or "usd" — currency of the per-entry reward'),
        ] = None,
        reward_amount: Annotated[
            Optional[int],
            Field(
                description=(
                    "Reward in minor units of reward_currency (sats for BTC, "
                    "cents for USD). Funds are reserved from the quest owner's "
                    "wallet in escrow at item create/update time."
                )
            ),
        ] = None,
        reward_xp: Annotated[
            Optional[int],
            Field(description="XP awarded to the contributor when the entry is accepted"),
        ] = None,
    ) -> str:
        """Update an item's metadata, status, reward, or auto-eval config. For completions with provenance, use complete_quest_item instead."""
        ouro = ctx.request_context.lifespan_context.ouro
        updated = ouro.quests.update_item(
            quest_id,
            item_id,
            **optional_kwargs(
                status=status,
                description=description,
                notes=notes,
                sort_order=sort_order,
                eval_route_id=eval_route_id,
                eval_score_path=eval_score_path,
                eval_pass_min=eval_pass_min,
                eval_pass_max=eval_pass_max,
                eval_input_key=eval_input_key,
                expected_asset_type=expected_asset_type,
                reward_currency=reward_currency,
                reward_amount=reward_amount,
                reward_xp=reward_xp,
            ),
        )
        return dump_json(
            {
                "id": str(updated.id),
                "description": updated.description,
                "status": updated.status,
                "sort_order": updated.sort_order,
                "reward_currency": updated.reward_currency,
                "reward_amount": updated.reward_amount,
            }
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def complete_quest_item(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Item UUID")],
        ctx: Context,
        description: Annotated[
            Optional[str],
            Field(
                description=(
                    "Extended markdown completion note: what was done, what was tried, "
                    "what was learned. Parsed into Ouro rich content, so formatting like "
                    "headings, lists, code, @mentions, LaTeX, and typed asset links are supported. "
                    "This becomes the platform's long-term work memory — be substantive."
                )
            ),
        ] = None,
        asset_id: Annotated[
            Optional[str],
            Field(description="UUID of the produced asset (post, file, dataset, etc.)"),
        ] = None,
        asset_type: Annotated[
            Optional[str],
            Field(description="Asset type of the produced asset (required if asset_id is set)"),
        ] = None,
    ) -> str:
        """Self-complete an item. Creates an auto-accepted entry and marks the item done.

        Provide either an asset_id (for items that produce an asset) or a description (for items that
        don't), or both. The description accepts extended markdown and should explain
        what was done and why.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        content = content_from_markdown(ouro, description) if description else None
        result = ouro.quests.complete_item(
            quest_id,
            item_id,
            asset_id=asset_id,
            asset_type=asset_type,
            description=content,
        )
        return dump_json(result)

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def delete_quest_item(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Item UUID")],
        ctx: Context,
    ) -> str:
        """Remove an item from a quest. Blocked if the item has entries — handle entries first."""
        ouro = ctx.request_context.lifespan_context.ouro
        ouro.quests.delete_item(quest_id, item_id)
        return dump_json({"deleted": item_id})

    # ── Quest Entry tools ──

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def submit_quest_entry(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        ctx: Context,
        item_id: Annotated[
            Optional[str],
            Field(description="Optional quest item UUID this entry addresses"),
        ] = None,
        asset_id: Annotated[
            Optional[str],
            Field(description="Optional submitted asset UUID"),
        ] = None,
        asset_type: Annotated[
            Optional[str],
            Field(description="Asset type of asset_id, required when asset_id is set"),
        ] = None,
        description_markdown: Annotated[
            Optional[str],
            Field(description="Extended markdown describing the submission"),
        ] = None,
    ) -> str:
        """Submit an entry to a quest. Use item_id when addressing a specific quest item."""
        ouro = ctx.request_context.lifespan_context.ouro
        content = (
            content_from_markdown(ouro, description_markdown)
            if description_markdown
            else None
        )
        entry = ouro.quests.create_entry(
            quest_id,
            item_id=item_id,
            asset_id=asset_id,
            asset_type=asset_type,
            description=content,
        )
        return dump_json(entry.model_dump(mode="json"))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_quest_entries(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        ctx: Context,
        status: Annotated[
            Optional[str],
            Field(description='"submitted" | "accepted" | "rejected"'),
        ] = None,
        limit: Annotated[int, Field(description="Page size, 1-200")] = 50,
        offset: Annotated[int, Field(description="Offset for pagination")] = 0,
    ) -> str:
        """List contributor entries for a quest."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.quests.list_entries(
            quest_id,
            status=status,
            limit=limit,
            offset=offset,
            with_pagination=True,
        )
        entries = [
            entry.model_dump(mode="json")
            for entry in result.get("data", [])
        ]
        return dump_json(
            list_response(
                entries,
                pagination=result.get("pagination"),
                limit=limit,
            )
        )

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def review_quest_entry(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        entry_id: Annotated[str, Field(description="Quest entry UUID")],
        status: Annotated[str, Field(description='"accepted" | "rejected"')],
        ctx: Context,
        review_markdown: Annotated[
            Optional[str],
            Field(description="Optional extended markdown review note"),
        ] = None,
    ) -> str:
        """Accept or reject a quest entry."""
        ouro = ctx.request_context.lifespan_context.ouro
        review = (
            content_from_markdown(ouro, review_markdown)
            if review_markdown
            else None
        )
        entry = ouro.quests.review_entry(
            quest_id,
            entry_id,
            status=status,
            review=review,
        )
        return dump_json(entry.model_dump(mode="json"))
