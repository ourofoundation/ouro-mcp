"""Quest tools — create, update, and item management."""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from ouro.utils.content import description_to_markdown
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    content_from_markdown,
    dump_json,
    format_asset_summary,
    markdown_bullet,
    markdown_id,
    optional_kwargs,
    render_markdown_list,
)
from pydantic import BaseModel, ConfigDict, Field


AssetType = Literal["file", "dataset", "post"]


class SubmissionAssetDeclaration(BaseModel):
    """One declaration in a keyed quest-item submission_assets record."""

    model_config = ConfigDict(extra="allow")

    asset_type: AssetType = Field(
        description="Asset type accepted for this contributor input"
    )
    required: bool = Field(
        default=True, description="Whether contributors must provide this input"
    )
    primary: Optional[bool] = Field(
        default=None, description="Whether this is the item's primary submitted asset"
    )
    input_filter: Optional[
        Literal["audio", "video", "image", "pdf", "3d model", "atomic structure"]
    ] = Field(default=None, description="Optional file-category constraint")
    file_extensions: Optional[List[str]] = Field(
        default=None,
        description="Optional accepted file extensions, without leading dots",
    )
    contains_file_extensions: Optional[List[str]] = Field(
        default=None,
        description="Optional extensions that an archive/container must contain",
    )
    label: Optional[str] = Field(
        default=None,
        description="Contributor-facing title shown instead of the JSON key",
    )


class EvalStaticInput(BaseModel):
    """A route input pinned by the quest author rather than a contributor."""

    asset_id: str = Field(description="Pinned asset UUID")
    asset_type: AssetType = Field(description="Pinned asset type")


class QuestItemInput(BaseModel):
    """Structured quest item accepted by create_quest and create_quest_items."""

    # Keep forward compatibility with quest item fields added by the API while
    # still publishing concrete schemas for the fields agents use today.
    model_config = ConfigDict(extra="allow")

    description: Union[str, Dict[str, Any]] = Field(
        description="Task description as markdown or TipTap content"
    )
    type: Optional[str] = Field(default=None, description="Item type; usually 'task'")
    sort_order: Optional[int] = Field(default=None, description="1-indexed display order")
    assignee_id: Optional[str] = Field(default=None, description="Assigned user UUID")
    expected_asset_type: Optional[AssetType] = Field(
        default=None, description="Primary expected asset type"
    )
    reward_xp: Optional[int] = Field(default=None, ge=0)
    reward_currency: Optional[Literal["btc", "usd"]] = None
    reward_amount: Optional[int] = Field(default=None, ge=0)
    child_quest_id: Optional[str] = None
    eval_route_id: Optional[str] = Field(
        default=None,
        description=(
            "Route UUID used for auto-evaluation. When set, contributor input keys "
            "are derived from the route minus eval_static_inputs by the server."
        ),
    )
    eval_score_path: Optional[str] = None
    eval_categories_path: Optional[str] = None
    eval_pass_min: Optional[float] = None
    eval_pass_max: Optional[float] = None
    leaderboard_enabled: Optional[bool] = None
    leaderboard_order: Optional[Literal["desc", "asc"]] = None
    submission_assets: Optional[Dict[str, SubmissionAssetDeclaration]] = Field(
        default=None,
        description=(
            "Keyed record of contributor input names to declaration objects. Each "
            "declaration requires asset_type and may set required, primary, "
            "input_filter, file_extensions, contains_file_extensions, or label. "
            "Use this for non-eval items. With eval_route_id, you may overlay "
            "label on the derived keys (same names); do not change keys or "
            "constraints — the server owns those from the route."
        ),
    )
    eval_static_inputs: Optional[Dict[str, EvalStaticInput]] = Field(
        default=None,
        description=(
            "Keyed route inputs pinned by the author. Only valid with eval_route_id; "
            "remaining route input keys become contributor-owned submission slots."
        ),
    )
    notes: Optional[str] = None


def _item_description_text(description: Any, *, max_length: Optional[int] = None) -> str:
    """Readable markdown for quest item descriptions (string or TipTap Content)."""
    if description is not None and hasattr(description, "text"):
        if not isinstance(description, (str, dict)):
            description = getattr(description, "text", None) or ""
    return description_to_markdown(description, max_length=max_length) or ""


def _item_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _contributor_keys(item: Any) -> Any:
    keys = _item_field(item, "contributor_keys")
    if keys is not None:
        return keys
    declarations = _item_field(item, "submission_assets")
    if not isinstance(declarations, dict):
        return None
    return [
        {
            "key": key,
            **(
                {"label": declaration["label"]}
                if isinstance(declaration, dict) and declaration.get("label")
                else {}
            ),
            "required": (
                declaration.get("required", True)
                if isinstance(declaration, dict)
                else True
            ),
        }
        for key, declaration in declarations.items()
    ]


def _dump_model_record(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    return {
        key: (
            item.model_dump(exclude_none=True)
            if isinstance(item, BaseModel)
            else item
        )
        for key, item in value.items()
    }


def _item_summary(item: Any) -> Dict[str, Any]:
    return {
        "id": str(_item_field(item, "id")),
        "description": _item_description_text(_item_field(item, "description")),
        "status": _item_field(item, "status"),
        "sort_order": _item_field(item, "sort_order"),
        "assignee_id": (
            str(_item_field(item, "assignee_id"))
            if _item_field(item, "assignee_id")
            else None
        ),
        "waiting_on": _item_field(item, "waiting_on"),
        "waiting_until": _item_field(item, "waiting_until"),
        "waiting_check_every": _item_field(item, "waiting_check_every"),
        "reward_currency": _item_field(item, "reward_currency"),
        "reward_amount": _item_field(item, "reward_amount"),
        "expected_asset_type": _item_field(item, "expected_asset_type"),
        "eval_route_id": (
            str(_item_field(item, "eval_route_id"))
            if _item_field(item, "eval_route_id")
            else None
        ),
        "eval_score_path": _item_field(item, "eval_score_path"),
        "eval_categories_path": _item_field(item, "eval_categories_path"),
        "eval_pass_min": _item_field(item, "eval_pass_min"),
        "eval_pass_max": _item_field(item, "eval_pass_max"),
        "leaderboard_enabled": _item_field(item, "leaderboard_enabled"),
        "leaderboard_order": _item_field(item, "leaderboard_order"),
        "submission_assets": _item_field(item, "submission_assets"),
        "eval_static_inputs": _item_field(item, "eval_static_inputs"),
        "contributor_keys": _contributor_keys(item),
    }


def _category_scores_part(scores: Any) -> Optional[str]:
    if not isinstance(scores, dict) or not scores:
        return None
    return "categories: " + ", ".join(
        f"{key}={value}" for key, value in scores.items()
    )


def _normalize_create_items(
    ouro: Any, items: List[Union[str, QuestItemInput]]
) -> List[Dict[str, Any]]:
    """Convert plain description strings to rich Content before create."""
    rows: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            rows.append({"description": content_from_markdown(ouro, item)})
            continue
        row = (
            item.model_dump(exclude_unset=True)
            if isinstance(item, QuestItemInput)
            else dict(item)
        )
        description = row.get("description")
        if isinstance(description, str):
            row["description"] = content_from_markdown(ouro, description)
        rows.append(row)
    return rows


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
                    "@mentions, LaTeX (\\(inline\\), \\[display\\]), "
                    "typed asset link shorthands [text](post:|file:|dataset:|route:|service:|quest:<uuid>). "
                    "Use [text](asset:<uuid>) only when the asset type is unknown. "
                    "and block-level asset embeds via ```assetComponent``` using "
                    '{"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card"}.'
                )
            ),
        ] = None,
        items: Annotated[
            Optional[List[Union[str, QuestItemInput]]],
            Field(
                description=(
                    "List of task descriptions to create as quest items. "
                    "Each string (or object.description) is markdown TipTap content — "
                    "supports typed asset link shorthands "
                    "[text](post:|file:|dataset:|route:|service:|quest:<uuid>) and "
                    "becomes an item with status 'pending'. "
                    "Objects may also include assignee_id, reward_amount, "
                    "eval_route_id, or submission_assets. submission_assets is "
                    "a keyed record whose values are declaration objects with "
                    "asset_type and optional required/file constraints and label. "
                    "When eval_route_id is set, you may overlay label on derived "
                    "keys; do not change keys or constraints."
                )
            ),
        ] = None,
        visibility: Annotated[str, Field(description='"public" | "private" | "organization"')] = "public",
        type: Annotated[
            str,
            Field(
                description=(
                    '"closable" | "continuous". Closable: one active entry per '
                    "contributor per item. Continuous: unlimited entries per item."
                )
            ),
        ] = "closable",
        status: Annotated[
            str,
            Field(description='"draft" | "open" | "closed" | "cancelled"'),
        ] = "open",
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Top-level provenance object; separate from quest metadata"),
        ] = None,
    ) -> str:
        """Create a new quest on Ouro with optional task items.

        The description is prose context. Items are the structured work plan —
        each item becomes a trackable task that can be completed or assigned.

        Quest type controls entry limits: closable allows one active submission per
        contributor per item; continuous allows unlimited submissions per item.

        Asset references in description:
        - Inline links: prefer [label](post:|file:|dataset:|route:|service:|quest:<uuid>).
        - Use [label](asset:<uuid>) only when the asset type is unknown.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        description = None
        if description_markdown is not None:
            description = content_from_markdown(ouro, description_markdown)

        create_items = (
            _normalize_create_items(ouro, items) if items else None
        )

        quest = ouro.quests.create(
            name=name,
            description=description,
            visibility=visibility,
            type=type,
            status=status,
            org_id=org_id,
            team_id=team_id,
            items=create_items,
            license_id=license_id,
            attribution=attribution,
        )

        result = format_asset_summary(quest)
        if quest.items:
            result["items"] = [_item_summary(i) for i in quest.items]
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
                    "@mentions, LaTeX (\\(inline\\), \\[display\\]), "
                    "typed asset link shorthands [text](post:|file:|dataset:|route:|service:|quest:<uuid>). "
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
        license_id: Annotated[Optional[str], Field(description="New asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Updated top-level provenance object"),
        ] = None,
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
                license_id=license_id,
                attribution=attribution,
            ),
        )

        return dump_json(format_asset_summary(quest))

    # ── Quest Item tools ──

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_assigned_quest_items(
        ctx: Context,
        status: Annotated[
            Optional[str],
            Field(
                description=(
                    "Comma-separated item statuses to include. Defaults to "
                    "'pending,in_progress'. Pass 'all' to include terminal items."
                )
            ),
        ] = None,
        assignee_id: Annotated[
            Optional[str],
            Field(description="User UUID to filter by; defaults to the authenticated user"),
        ] = None,
        org_id: Annotated[Optional[str], Field(description="Organization UUID filter")] = None,
        team_id: Annotated[Optional[str], Field(description="Team UUID filter")] = None,
        limit: Annotated[int, Field(description="Page size, 1-100")] = 20,
        offset: Annotated[int, Field(description="Offset for pagination")] = 0,
    ) -> str:
        """List actionable quest items assigned to the authenticated user.

        Use this as an agent work inbox for quests planned by someone else.
        The returned item IDs can be used with list_quest_items, update_quest_item,
        submit_quest_entry, or complete_quest_item depending on permissions.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.quests.list_assigned_items(
            status=status,
            assignee_id=assignee_id,
            org_id=org_id,
            team_id=team_id,
            limit=limit,
            offset=offset,
            with_pagination=True,
        )
        if isinstance(result, dict):
            items = result.get("data") or result.get("results") or []
            pagination = result.get("pagination") or {}
        else:
            items = list(result or [])
            pagination = {}

        def _item_line(item: Any) -> str:
            if hasattr(item, "model_dump"):
                row = item.model_dump(mode="json")
            elif isinstance(item, dict):
                row = item
            else:
                row = {
                    "id": getattr(item, "id", None),
                    "description": getattr(item, "description", None),
                    "status": getattr(item, "status", None),
                    "quest_id": getattr(item, "quest_id", None),
                }
            parts = [
                markdown_id(row.get("id")),
                f"status: {row['status']}" if row.get("status") else None,
            ]
            if row.get("quest_id"):
                parts.append(f"quest_id: `{row['quest_id']}`")
            body_bits = []
            for key in ("submission_assets", "eval_static_inputs"):
                if row.get(key):
                    body_bits.append(f"{key}: {json.dumps(row[key], default=str)}")
            contributor_keys = _contributor_keys(row)
            if contributor_keys:
                body_bits.append(
                    f"contributor_keys: {json.dumps(contributor_keys, default=str)}"
                )
            return markdown_bullet(
                _item_description_text(row.get("description")) or "(no description)",
                *parts,
                body=" · ".join(body_bits) if body_bits else None,
            )

        return render_markdown_list(
            items,
            line_fn=_item_line,
            pagination=pagination,
            offset=offset,
            noun="assigned quest items",
            empty_text="No assigned quest items.",
        )

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

        def _item_line(i: Any) -> str:
            parts = [
                markdown_id(i.id),
                f"status: {i.status}",
                f"order: {i.sort_order}",
            ]
            if i.reward_currency and i.reward_amount is not None:
                parts.append(f"reward: {i.reward_amount} {i.reward_currency}")
            if getattr(i, "assignee_id", None):
                parts.append(f"assignee: `{i.assignee_id}`")
            if getattr(i, "waiting_on", None):
                parts.append(f"waiting_on: {i.waiting_on}")
            if getattr(i, "leaderboard_enabled", False):
                order = getattr(i, "leaderboard_order", None) or "desc"
                parts.append(f"leaderboard: {order}")
            body_bits = []
            if i.notes:
                body_bits.append(str(i.notes))
            if getattr(i, "waiting_until", None):
                body_bits.append(f"waiting_until: {i.waiting_until}")
            if getattr(i, "child_quest_id", None):
                body_bits.append(f"child_quest_id: `{i.child_quest_id}`")
            if getattr(i, "submission_assets", None):
                body_bits.append(
                    "submission_assets: "
                    + json.dumps(i.submission_assets, default=str)
                )
            if getattr(i, "eval_static_inputs", None):
                body_bits.append(
                    "eval_static_inputs: "
                    + json.dumps(i.eval_static_inputs, default=str)
                )
            contributor_keys = _contributor_keys(i)
            if contributor_keys:
                body_bits.append(
                    "contributor_keys: "
                    + json.dumps(contributor_keys, default=str)
                )
            return markdown_bullet(
                _item_description_text(i.description) or "(no description)",
                *parts,
                body=" · ".join(body_bits) if body_bits else None,
            )

        return render_markdown_list(
            items,
            line_fn=_item_line,
            total=total,
            noun="quest items",
            empty_text="No quest items.",
            extras=[f"quest_id: `{quest_id}`", f"progress: {done}/{total}"],
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_quest_items(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        items: Annotated[
            List[Union[str, QuestItemInput]],
            Field(
                description=(
                    "Items to add. Each element is either a markdown description "
                    "string (TipTap; supports typed asset link shorthands "
                    "[text](post:|file:|dataset:|route:|service:|quest:<uuid>)) "
                    "or a full item object with any of: description, "
                    "assignee_id, "
                    "expected_asset_type, reward_currency ('btc'|'usd'), "
                    "reward_amount (sats for btc, cents for usd), "
                    "eval_route_id, eval_score_path, eval_categories_path, "
                    "eval_pass_min, "
                    "eval_pass_max, leaderboard_enabled, leaderboard_order "
                    "('desc' higher wins, 'asc' lower wins), "
                    "submission_assets, eval_static_inputs. submission_assets "
                    "must be a keyed record of declaration objects, each with "
                    "asset_type and optional required, primary, input_filter, "
                    "file_extensions, contains_file_extensions, or label. For an "
                    "item with eval_route_id, you may overlay label on derived "
                    "keys; do not change keys or constraints."
                )
            ),
        ],
        ctx: Context,
    ) -> str:
        """Batch-add items to an existing quest.

        For non-eval items, ``submission_assets`` declares a keyed record of
        contributor inputs. For eval items, set ``eval_route_id`` and optional
        ``eval_static_inputs``; the server derives contributor keys from the
        route. You may overlay ``label`` on those derived keys.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        created = ouro.quests.create_items(
            quest_id, _normalize_create_items(ouro, items)
        )
        return dump_json([_item_summary(i) for i in created])

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
        description: Annotated[
            Optional[str],
            Field(
                description=(
                    "Updated task description as markdown TipTap content. Supports "
                    "typed asset link shorthands "
                    "[text](post:|file:|dataset:|route:|service:|quest:<uuid>)."
                )
            ),
        ] = None,
        notes: Annotated[Optional[str], Field(description="Internal notes on this item")] = None,
        waiting_on: Annotated[
            Optional[str],
            Field(
                description=(
                    "What this item is parked/blocked on (e.g. 'reply from the "
                    "authors', 'sponsor decision'). The item stays 'in_progress' — "
                    "this just records why it is waiting. Pass an empty string to "
                    "clear it."
                )
            ),
        ] = None,
        waiting_until: Annotated[
            Optional[str],
            Field(
                description=(
                    "ISO 8601 timestamp for when to reconsider a waiting item "
                    "(e.g. '2026-07-07T14:00:00Z'). Until this passes the item is "
                    "treated as non-actionable, so it will not block starting new "
                    "work. Pass an empty string to clear it."
                )
            ),
        ] = None,
        waiting_check_every: Annotated[
            Optional[str],
            Field(
                description=(
                    "Interval shorthand ('1d', '6h', '30m') that turns this into a "
                    "recurring check. Each time `waiting_until` comes due the item "
                    "surfaces for one work session and `waiting_until` is advanced "
                    "by this interval automatically, so it polls on a cadence "
                    "(e.g. scan for a reply once a day) without taking up every "
                    "heartbeat. Complete the item to stop the recurrence. Pass an "
                    "empty string to clear it."
                )
            ),
        ] = None,
        assignee_id: Annotated[
            Optional[str],
            Field(description="User UUID assigned to this item"),
        ] = None,
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
        eval_categories_path: Annotated[
            Optional[str],
            Field(
                description=(
                    "JSON path to a map of subcategory scores in the action "
                    "response. Defaults to $.categories. Ranking still uses "
                    "the main score."
                )
            ),
        ] = None,
        eval_pass_min: Annotated[
            Optional[float],
            Field(
                description=(
                    "Inclusive minimum passing score. Omit both min and max to "
                    "pass every scored submission."
                )
            ),
        ] = None,
        eval_pass_max: Annotated[
            Optional[float],
            Field(
                description=(
                    "Inclusive maximum passing score. Omit both min and max to "
                    "pass every scored submission."
                )
            ),
        ] = None,
        leaderboard_enabled: Annotated[
            Optional[bool],
            Field(
                description=(
                    "When true, show a public ranked list of scored submissions "
                    "for this item. Requires an eval route when enabling."
                )
            ),
        ] = None,
        leaderboard_order: Annotated[
            Optional[str],
            Field(
                description=(
                    '"desc" (higher score wins) or "asc" (lower score wins)'
                )
            ),
        ] = None,
        submission_assets: Annotated[
            Optional[Dict[str, SubmissionAssetDeclaration]],
            Field(
                description=(
                    "For non-eval items, a keyed record mapping each contributor "
                    "input name to a declaration object. Each value requires "
                    "asset_type and may include required, primary, input_filter, "
                    "file_extensions, contains_file_extensions, or label. With "
                    "eval_route_id, you may overlay label on the derived keys; "
                    "the server still owns keys and constraints from the route."
                )
            ),
        ] = None,
        eval_static_inputs: Annotated[
            Optional[Dict[str, EvalStaticInput]],
            Field(
                description=(
                    "Pinned eval-route inputs keyed by route input name; each "
                    "value contains asset_id and asset_type. Inspect the route "
                    "before pinning. Unpinned route keys become contributor inputs."
                )
            ),
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
    ) -> str:
        """Update an item's metadata, status, reward, or auto-eval config.

        ``submission_assets`` is for explicit non-eval declarations. With an
        ``eval_route_id``, the server owns contributor keys and derives them
        from route inputs minus ``eval_static_inputs``. For completions with
        provenance, use complete_quest_item instead.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        description_content = (
            content_from_markdown(ouro, description)
            if description is not None
            else None
        )
        updated = ouro.quests.update_item(
            quest_id,
            item_id,
            **optional_kwargs(
                status=status,
                description=description_content,
                notes=notes,
                waiting_on=waiting_on,
                waiting_until=waiting_until,
                waiting_check_every=waiting_check_every,
                assignee_id=assignee_id,
                sort_order=sort_order,
                eval_route_id=eval_route_id,
                eval_score_path=eval_score_path,
                eval_categories_path=eval_categories_path,
                eval_pass_min=eval_pass_min,
                eval_pass_max=eval_pass_max,
                leaderboard_enabled=leaderboard_enabled,
                leaderboard_order=leaderboard_order,
                submission_assets=_dump_model_record(submission_assets),
                eval_static_inputs=_dump_model_record(eval_static_inputs),
                reward_currency=reward_currency,
                reward_amount=reward_amount,
            ),
        )
        return dump_json(_item_summary(updated))

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
        assets: Annotated[
            Optional[Dict[str, Union[str, Dict[str, str]]]],
            Field(
                description='Optional keyed assets, e.g. {"file": "<uuid>"}.'
            ),
        ] = None,
    ) -> str:
        """Self-complete an item. Creates an auto-accepted entry and marks the item done.

        The quest must be open; draft quests reject entry-producing actions until
        they are published.

        Provide ``assets`` when linking produced files or other inputs, and/or a
        substantive ``description`` markdown note.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        content = content_from_markdown(ouro, description) if description else None
        result = ouro.quests.complete_item(
            quest_id,
            item_id,
            assets=assets,
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
        item_id: Annotated[str, Field(description="Quest item UUID to submit against")],
        description_markdown: Annotated[
            str,
            Field(
                description=(
                    "Required markdown explanation of the submission for the "
                    "reviewer to read before accepting."
                )
            ),
        ],
        assets: Annotated[
            Optional[Dict[str, Union[str, Dict[str, str]]]],
            Field(
                description=(
                    'Asset submissions: {"<contributor_key>": "<asset_uuid>"}. '
                    "First inspect list_quest_items (and the eval route when "
                    "present), then use the exact contributor_keys returned "
                    "for that item."
                )
            ),
        ] = None,
    ) -> str:
        """Submit an entry to a quest item.

        The quest must be open. Draft quests are not accepting submissions until
        the owner publishes them.

        Pass ``item_id`` and ``assets`` with one UUID per exact contributor key.
        Inspect ``list_quest_items`` first; for eval items, also inspect the route.
        The server derives these keys from unpinned route inputs, so do not guess
        a generic file or artifact key.

        Provide ``description_markdown`` with the contributor's explanation. For
        paid items, the quest author reviews that description and deterministic
        judge signals, not the underlying asset contents, until they accept the
        entry. Private assets stay private until accept.

        Closable quests: one active (submitted/accepted) entry per contributor per
        item — a second submit for the same item_id fails until the prior entry is
        rejected. Continuous quests: unlimited entries per item. Each asset can
        only be on one active entry per quest. Check quest type via get_asset(quest_id)
        before retrying.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        content = content_from_markdown(ouro, description_markdown)
        entry = ouro.quests.create_entry(
            quest_id,
            item_id=item_id,
            assets=assets,
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

        def _entry_line(row: dict[str, Any]) -> str:
            parts = [
                markdown_id(row.get("id")),
                f"status: {row['status']}" if row.get("status") else None,
            ]
            if row.get("item_id"):
                parts.append(f"item_id: `{row['item_id']}`")
            if row.get("user_id"):
                parts.append(f"user_id: `{row['user_id']}`")
            if row.get("eval_score") is not None:
                parts.append(f"score: {row['eval_score']}")
            categories = _category_scores_part(row.get("eval_category_scores"))
            if categories:
                parts.append(categories)
            if row.get("eval_status"):
                parts.append(f"eval: {row['eval_status']}")
            if row.get("eval_action_id"):
                parts.append(f"action_id: `{row['eval_action_id']}`")
            assets = row.get("assets")
            body = None
            if assets:
                body = f"assets: {assets}"
            return markdown_bullet(
                str(row.get("description") or row.get("status") or "entry"),
                *parts,
                body=body,
            )

        return render_markdown_list(
            entries,
            line_fn=_entry_line,
            pagination=result.get("pagination"),
            offset=offset,
            noun="quest entries",
            empty_text="No quest entries.",
            extras=[f"quest_id: `{quest_id}`"],
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_quest_leaderboard(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Leaderboard-enabled quest item UUID")],
        ctx: Context,
        limit: Annotated[int, Field(description="Page size, 1-200")] = 50,
        offset: Annotated[int, Field(description="Offset for pagination")] = 0,
    ) -> str:
        """List ranked scored submissions for a quest item leaderboard.

        Each row is one scored entry (not rolled up per user). Rejected entries
        are omitted. Ranking uses the item's leaderboard_order (desc = higher
        wins, asc = lower wins), then earliest submission. Subcategory scores
        from the eval route appear on each row when present.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.quests.list_leaderboard(
            quest_id,
            item_id,
            limit=limit,
            offset=offset,
            with_pagination=True,
        )
        rows = result.get("data", []) if isinstance(result, dict) else result
        item = result.get("item") if isinstance(result, dict) else None
        pagination = result.get("pagination") if isinstance(result, dict) else {}

        def _row_line(row: Any) -> str:
            data = row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row)
            user = data.get("user") or {}
            username = user.get("username")
            parts = [
                f"#{data.get('placement')}",
                markdown_id(data.get("entry_id")),
                f"score: {data.get('score')}" if data.get("score") is not None else None,
            ]
            categories = _category_scores_part(data.get("category_scores"))
            if categories:
                parts.append(categories)
            if username:
                parts.append(f"@{username}")
            elif data.get("user"):
                parts.append(f"user_id: `{user.get('user_id')}`")
            if data.get("eval_status"):
                parts.append(f"eval: {data['eval_status']}")
            if data.get("eval_action_id"):
                parts.append(f"action_id: `{data['eval_action_id']}`")
            return markdown_bullet(
                f"placement {data.get('placement')}",
                *parts,
            )

        extras = [f"quest_id: `{quest_id}`", f"item_id: `{item_id}`"]
        if isinstance(item, dict) and item.get("leaderboard_order"):
            extras.append(f"order: {item['leaderboard_order']}")

        return render_markdown_list(
            rows,
            line_fn=_row_line,
            pagination=pagination,
            offset=offset,
            noun="leaderboard entries",
            empty_text="No scored submissions on this leaderboard.",
            extras=extras,
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
