"""Quest tools — create, update, and item management."""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Entry, QuestItem, QuestLeaderboardRow
from ouro.utils.content import description_to_markdown
from pydantic import BaseModel, ConfigDict, Field

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.markdown_input import (
    content_from_markdown,
)
from ouro_mcp.params import (
    ATTRIBUTION_DESC,
    Attribution,
    JsonObject,
    QuestItemStatus,
    QuestItemStatuses,
    QuestStatus,
    QuestType,
    QuestVisibility,
)
from ouro_mcp.pricing import (
    format_one_time_cost_summary,
)
from ouro_mcp.rendering import (
    clip,
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
)
from ouro_mcp.summaries import (
    format_asset_summary,
)
from ouro_mcp.utils import (
    optional_kwargs,
    resolve_location,
)

AssetType = Literal["file", "dataset", "post"]
# Contributors may also submit a route; the item's evaluator runs it.
SubmissionAssetType = Literal["file", "dataset", "post", "route"]

EVALUATOR_DESC = (
    "How entries are scored automatically: existing routes run as steps, then "
    "formulas over their JSON responses. Example: "
    '{"steps": {"hull": {"route_id": "<route uuid>", "inputs": {"structure": '
    '"inputs.structure"}, "body": {"relax": true}}, "mae": {"route_id": '
    '"<route uuid>", "inputs": {"structure": "hull.relaxed"}, "after": '
    '["stable"]}}, "gates": {"stable": "hull.e_above_hull <= 0.075"}, '
    '"categories": {"anisotropy": "min(mae.k1, 1)"}, "score": '
    '"100 * anisotropy", "pass": {"min": 50}}. '
    "Only steps and score are required. Inspect each route with get_asset: "
    "every asset input it declares needs a reference under inputs, one of "
    '"inputs.<key>" (an asset the entry submits; these keys become the '
    'item\'s contributor keys), "<step>.<field>" (an asset an earlier step '
    'returned) or {"asset_id": "<uuid>", "asset_type": "file"} (a fixed '
    "asset). body holds other fixed request parameters. gates are pass/fail "
    "formulas over step responses: a step waits for the gates named in its "
    "after, and a failed gate skips every step not yet started. categories "
    "are named sub-scores. Formulas read step responses by path "
    "(hull.e_above_hull), and score may also read gate and category names; "
    "they take + - * / % ^, comparisons, and/or/not, a ? b : c, and min, max, "
    "clamp, abs, round, floor, ceil, sqrt, exp, log, log10, pow, coalesce, if. "
    "A missing value is null and carries through. pass is the inclusive score "
    "band: an entry passes when every gate passes and its score is in the "
    "band. Step, gate and category names are lowercase identifiers. A "
    "definition the server refuses comes back with every problem listed."
)


class SubmissionAssetDeclaration(BaseModel):
    """One declaration in a keyed quest-item submission_assets record."""

    model_config = ConfigDict(extra="allow")

    asset_type: SubmissionAssetType = Field(
        description=(
            "Asset type accepted for this contributor input. 'route': the "
            "contributor submits a route (e.g. a model) and the item's "
            "evaluator runs it; pair with eval_trigger='owner'."
        )
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
    evaluator: Optional[JsonObject] = Field(default=None, description=EVALUATOR_DESC)
    eval_route_id: Optional[str] = Field(
        default=None,
        description=(
            "Short form of evaluator for one route call: the route UUID, with "
            "eval_score_path, eval_categories_path, eval_pass_min/max and "
            "eval_static_inputs. Contributor input keys are derived from the "
            "route minus eval_static_inputs by the server. Ignored when "
            "evaluator is set."
        ),
    )
    eval_score_path: Optional[str] = None
    eval_categories_path: Optional[str] = None
    eval_pass_min: Optional[float] = None
    eval_pass_max: Optional[float] = None
    leaderboard_enabled: Optional[bool] = None
    leaderboard_order: Optional[Literal["desc", "asc"]] = None
    eval_trigger: Optional[Literal["submit", "owner"]] = Field(
        default=None,
        description=(
            "When the evaluator runs: 'submit' (default) on every entry, or "
            "'owner': entries wait until the quest's owner starts each "
            "evaluation with evaluate_quest_entry (for evals that cost the owner "
            "compute, such as running submitted routes)."
        ),
    )
    submission_assets: Optional[Dict[str, SubmissionAssetDeclaration]] = Field(
        default=None,
        description=(
            "Keyed record of contributor input names to declaration objects. Each "
            "declaration requires asset_type and may set required, primary, "
            "input_filter, file_extensions, contains_file_extensions, or label. "
            "Use this for non-eval items. With an evaluator or eval_route_id, "
            "you may overlay label on the derived keys (same names); do not "
            "change keys or constraints — the server owns those from the "
            "routes."
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


def _item_description_text(item: Any, *, max_length: Optional[int] = None) -> str:
    """Readable markdown for a quest item's or entry's description, embeds summarized."""
    text = description_to_markdown(
        _item_field(item, "description"),
        embedded_assets=_item_field(item, "embedded_assets"),
    )
    return clip(text, max_length)


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


def _plain(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _multi_step_evaluator(item: Any) -> Optional[Dict[str, Any]]:
    """The item's evaluator when it is more than one plain route call.

    A one-route evaluator comes back in the flat eval_* fields instead.
    """
    evaluator = _plain(_item_field(item, "evaluator"))
    if not isinstance(evaluator, dict) or _item_field(item, "eval_route_id"):
        return None
    return evaluator


def _evaluator_text(item: Any) -> Optional[str]:
    """A multi-step evaluator in full, as it would be sent back to change it."""
    evaluator = _multi_step_evaluator(item)
    if not evaluator:
        return None
    return f"evaluator: {json.dumps(evaluator, default=str)}"


def _run_text(run: Any) -> Optional[str]:
    """One line for an evaluation run: status, each step's route run, failed gates, error."""
    run = _plain(run)
    if not isinstance(run, dict):
        return None
    bits = [f"eval run `{run.get('id')}`: {run.get('status')}"]
    steps = []
    for name, step in (run.get("steps") or {}).items():
        step = step if isinstance(step, dict) else {}
        detail = [
            f"action_id: `{step['action_id']}`" if step.get("action_id") else None,
            step.get("reason"),
        ]
        detail = [part for part in detail if part]
        steps.append(
            f"{name} {step.get('status')}" + (f" ({', '.join(detail)})" if detail else "")
        )
    if steps:
        bits.append("steps: " + ", ".join(steps))
    failed = [name for name, ok in (run.get("gates") or {}).items() if ok is False]
    if failed:
        bits.append("failed gates: " + ", ".join(failed))
    if run.get("error"):
        bits.append(f"error: {run['error']}")
    return " · ".join(bits)


def _item_summary(item: Any) -> Dict[str, Any]:
    return {
        "id": str(_item_field(item, "id")),
        "description": _item_description_text(item),
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
        # Set for an evaluator with several steps; one route call shows in eval_*
        "evaluator": _multi_step_evaluator(item),
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
        "eval_trigger": _item_field(item, "eval_trigger"),
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
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Organization UUID. Omit when the server is pinned to an organization"),
        ] = None,
        team_id: Annotated[
            Optional[str],
            Field(description="Team UUID. Omit to use the pinned organization's default team"),
        ] = None,
        description_markdown: Annotated[
            Optional[str],
            Field(description="Quest description in extended markdown (same syntax as create_post)"),
        ] = None,
        items: Annotated[
            Optional[List[Union[str, QuestItemInput]]],
            Field(
                description=(
                    "List of task descriptions to create as quest items. "
                    "Each string (or object.description) is extended markdown and "
                    "becomes an item with status 'pending'. "
                    "Objects may also include assignee_id, reward_amount, "
                    "evaluator (or eval_route_id for one route), or "
                    "submission_assets. submission_assets is "
                    "a keyed record whose values are declaration objects with "
                    "asset_type and optional required/file constraints and label. "
                    "When an evaluator is set, you may overlay label on derived "
                    "keys; do not change keys or constraints."
                )
            ),
        ] = None,
        visibility: Annotated[
            Optional[QuestVisibility],
            Field(description="Omit to follow the team: public in a public team, organization in an internal one"),
        ] = None,
        type: Annotated[
            QuestType,
            Field(
                description=(
                    "closable: one active entry per contributor per item; "
                    "continuous: unlimited entries per item"
                )
            ),
        ] = "closable",
        status: Annotated[QuestStatus, Field(description="Start as a draft, or open")] = "open",
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[Optional[Attribution], Field(description=ATTRIBUTION_DESC)] = None,
    ) -> str:
        """Create a new quest on Ouro with optional task items.

        The description is prose context. Items are the structured work plan —
        each item becomes a trackable task that can be completed or assigned.

        Quest type controls entry limits: closable allows one active submission per
        contributor per item; continuous allows unlimited submissions per item.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        org_id, team_id = resolve_location(ouro, org_id, team_id)

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
            Field(description="Replacement quest description in extended markdown (same syntax as create_post)"),
        ] = None,
        visibility: Annotated[Optional[QuestVisibility], Field(description="New visibility")] = None,
        status: Annotated[
            Optional[QuestStatus], Field(description='"closed" and "cancelled" are final')
        ] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
        license_id: Annotated[Optional[str], Field(description="New asset license identifier")] = None,
        attribution: Annotated[Optional[Attribution], Field(description=ATTRIBUTION_DESC)] = None,
    ) -> str:
        """Update a quest's description or metadata. Pass description_markdown to replace the body."""
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
            Optional[QuestItemStatuses],
            Field(description='Item statuses to include; default ["pending", "in_progress"], ["all"] for every item'),
        ] = None,
        assignee_id: Annotated[
            Optional[str],
            Field(description="User UUID to filter by; defaults to the authenticated user"),
        ] = None,
        org_id: Annotated[Optional[str], Field(description="Organization UUID filter")] = None,
        team_id: Annotated[Optional[str], Field(description="Team UUID filter")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """List actionable quest items assigned to the authenticated user.

        Use this as an agent work inbox for quests planned by someone else.
        The returned item IDs can be used with list_quest_items, update_quest_item,
        submit_quest_entry, or complete_quest_item depending on permissions.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        page = ouro.quests.list_assigned_items(
            status=",".join(status) if status else None,
            assignee_id=assignee_id,
            org_id=org_id,
            team_id=team_id,
            limit=limit,
            offset=offset,
        )

        def _item_line(item: QuestItem) -> str:
            parts = [markdown_id(item.id), f"status: {item.status}"]
            if item.quest_id:
                parts.append(f"quest_id: `{item.quest_id}`")
            body_bits = []
            for key in ("submission_assets", "eval_static_inputs"):
                value = getattr(item, key)
                if value:
                    body_bits.append(f"{key}: {json.dumps(value, default=str)}")
            evaluator = _evaluator_text(item)
            if evaluator:
                body_bits.append(evaluator)
            contributor_keys = _contributor_keys(item)
            if contributor_keys:
                body_bits.append(
                    f"contributor_keys: {json.dumps(contributor_keys, default=str)}"
                )
            return markdown_bullet(
                _item_description_text(item) or "(no description)",
                *parts,
                body=" · ".join(body_bits) if body_bits else None,
            )

        return render_markdown_list(
            list(page),
            line_fn=_item_line,
            pagination=page_pagination(page),
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
            parts = [markdown_id(i.id), f"status: {i.status}"]
            if i.reward_currency and i.reward_amount:
                amount = i.reward_amount / 100 if i.reward_currency == "usd" else i.reward_amount
                parts.append(
                    f"reward: {format_one_time_cost_summary(amount, i.reward_currency)}"
                )
            if getattr(i, "assignee_id", None):
                parts.append(f"assignee: `{i.assignee_id}`")
            if getattr(i, "waiting_on", None):
                parts.append(f"waiting_on: {i.waiting_on}")
            if getattr(i, "leaderboard_enabled", False):
                order = getattr(i, "leaderboard_order", None) or "desc"
                parts.append(f"leaderboard: {order}")
            body_bits = [_item_description_text(i) or "(no description)"]
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
            evaluator = _evaluator_text(i)
            if evaluator:
                body_bits.append(evaluator)
            contributor_keys = _contributor_keys(i)
            if contributor_keys:
                body_bits.append(
                    "contributor_keys: "
                    + json.dumps(contributor_keys, default=str)
                )
            return markdown_bullet(
                f"Item {i.sort_order}", *parts, body=" · ".join(body_bits)
            )

        return render_markdown_list(
            items,
            line_fn=_item_line,
            total=total,
            noun="quest items",
            empty_text="No quest items.",
            extras=[f"progress: {done}/{total} resolved"],
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_quest_items(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        items: Annotated[
            List[Union[str, QuestItemInput]],
            Field(
                description=(
                    "Items to add. Each element is either an extended markdown "
                    "description string or a full item object with any of: description, "
                    "assignee_id, "
                    "expected_asset_type, reward_currency ('btc'|'usd'), "
                    "reward_amount (sats for btc, cents for usd), "
                    "evaluator (routes run as steps, then score formulas), or "
                    "its one-route short form eval_route_id, eval_score_path, "
                    "eval_categories_path, eval_pass_min, eval_pass_max, "
                    "eval_static_inputs; leaderboard_enabled, leaderboard_order "
                    "('desc' higher wins, 'asc' lower wins), "
                    "submission_assets. submission_assets "
                    "must be a keyed record of declaration objects, each with "
                    "asset_type and optional required, primary, input_filter, "
                    "file_extensions, contains_file_extensions, or label. For an "
                    "item with an evaluator, you may overlay label on derived "
                    "keys; do not change keys or constraints."
                )
            ),
        ],
        ctx: Context,
    ) -> str:
        """Batch-add items to an existing quest.

        For non-eval items, `submission_assets` declares a keyed record of
        contributor inputs. To score entries automatically, give the item an
        `evaluator`: one or more routes run as steps (each with `route_id` and
        `inputs` wiring), optional `gates` and `categories`, a `score` formula
        and a `pass` band. For one route call, `eval_route_id` with optional
        `eval_static_inputs` is the short form. Either way the server derives
        contributor keys from the routes; you may overlay `label` on them.
        Try an evaluator before opening the quest with evaluate_quest_item.
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
        status: Annotated[Optional[QuestItemStatus], Field(description="New item status")] = None,
        description: Annotated[
            Optional[str],
            Field(description="Updated task description in extended markdown"),
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
        evaluator: Annotated[
            Optional[JsonObject],
            Field(
                description=(
                    "Replaces the item's evaluator with this whole definition; "
                    "pass {} to remove it. " + EVALUATOR_DESC
                )
            ),
        ] = None,
        eval_route_id: Annotated[
            Optional[str],
            Field(
                description=(
                    "Short form of evaluator for one route call: the route UUID "
                    "used to auto-evaluate entries. Ignored when evaluator is set."
                )
            ),
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
                    "for this item. Requires an evaluator when enabling."
                )
            ),
        ] = None,
        leaderboard_order: Annotated[
            Optional[Literal["desc", "asc"]], Field(description='"desc": higher score wins')
        ] = None,
        eval_trigger: Annotated[
            Optional[Literal["submit", "owner"]],
            Field(
                description=(
                    '"submit" evaluates every entry when it arrives; "owner" '
                    "waits for the quest's owner to start each evaluation "
                    "(evaluate_quest_entry)."
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
                    "an evaluator or eval_route_id, you may overlay label on the "
                    "derived keys; the server still owns keys and constraints "
                    "from the routes."
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
            Optional[Literal["btc", "usd"]], Field(description="Currency of the per-entry reward")
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
        rescore_entries: Annotated[
            bool,
            Field(
                description=(
                    "Re-run the item's evaluator on its existing entries after "
                    "this update, e.g. when the evaluator changed. Runs as you and "
                    "only updates scores; it never accepts, rejects or pays. The "
                    "result lists each started run_id."
                )
            ),
        ] = False,
    ) -> str:
        """Update an item's metadata, status, reward, or evaluator.

        `submission_assets` is for explicit non-eval declarations. With an
        `evaluator` (or its one-route short form, `eval_route_id`), the server
        owns contributor keys and derives them from the assets the routes
        take. For completions with provenance, use complete_quest_item instead.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        description_content = (
            content_from_markdown(ouro, description)
            if description is not None
            else None
        )
        changes: Dict[str, Any] = {}
        if evaluator is not None:
            # {} removes the evaluator: the API takes null for that
            changes["evaluator"] = evaluator or None
        updated = ouro.quests.update_item(
            quest_id,
            item_id,
            **changes,
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
                eval_trigger=eval_trigger,
                submission_assets=_dump_model_record(submission_assets),
                eval_static_inputs=_dump_model_record(eval_static_inputs),
                reward_currency=reward_currency,
                reward_amount=reward_amount,
            ),
        )
        summary = _item_summary(updated)
        if rescore_entries:
            summary["rescore"] = ouro.quests.rescore_item(quest_id, item_id)
        return dump_json(summary)

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

        For quest owners/admins only; other contributors use submit_quest_entry.
        The quest must be open; draft quests reject entry-producing actions until
        they are published.

        Provide `assets` when linking produced files or other inputs, and/or a
        substantive `description` markdown note.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        content = content_from_markdown(ouro, description) if description else None
        completion = ouro.quests.complete_item(
            quest_id,
            item_id,
            assets=assets,
            description=content,
        )
        return dump_json(completion.model_dump(mode="json"))

    @mcp.tool(annotations={"destructiveHint": True, "idempotentHint": True})
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
                    "First inspect list_quest_items (and the evaluator's routes "
                    "when present), then use the exact contributor_keys returned "
                    "for that item."
                )
            ),
        ] = None,
    ) -> str:
        """Submit an entry to a quest item.

        The quest must be open. Draft quests are not accepting submissions until
        the owner publishes them.

        Pass `item_id` and `assets` with one UUID per exact contributor key.
        Inspect `list_quest_items` first; for eval items, also inspect the
        evaluator's routes. The server derives these keys from the assets those
        routes take, so do not guess a generic file or artifact key. To see how
        a candidate scores before entering it, use evaluate_quest_item.

        Provide `description_markdown` with the contributor's explanation. For
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
            Optional[Literal["submitted", "accepted", "rejected"]], Field(description="Only entries in this state")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="Page size")] = 50,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """List contributor entries for a quest.

        A scored entry shows its evaluation run: each step's status and the
        action_id of its route run (open one with get_action), any failed
        gates, and the run's error.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        page = ouro.quests.list_entries(
            quest_id,
            status=status,
            limit=limit,
            offset=offset,
        )

        def _entry_line(entry: Entry) -> str:
            parts = [markdown_id(entry.id), f"status: {entry.status}"]
            if entry.item_id:
                parts.append(f"item_id: `{entry.item_id}`")
            if entry.user_id:
                parts.append(f"user_id: `{entry.user_id}`")
            if entry.eval_score is not None:
                parts.append(f"score: {entry.eval_score}")
            categories = _category_scores_part(entry.eval_category_scores)
            if categories:
                parts.append(categories)
            if entry.eval_status:
                parts.append(f"eval: {entry.eval_status}")
            body_bits = [
                f"assets: {entry.assets}" if entry.assets else None,
                _run_text(_item_field(entry, "eval_run")),
            ]
            return markdown_bullet(
                _item_description_text(entry) or entry.status,
                *parts,
                body=" · ".join(bit for bit in body_bits if bit) or None,
            )

        return render_markdown_list(
            list(page),
            line_fn=_entry_line,
            pagination=page_pagination(page),
            offset=offset,
            noun="quest entries",
            empty_text="No quest entries.",
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_quest_leaderboard(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Leaderboard-enabled quest item UUID")],
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=200, description="Page size")] = 50,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """List ranked scored submissions for a quest item leaderboard.

        Each row is one scored entry (not rolled up per user). Rejected entries
        are omitted. Ranking uses the item's leaderboard_order (desc = higher
        wins, asc = lower wins), then earliest submission. Subcategory scores
        from the evaluator appear on each row when present. run_id is the
        row's evaluation run; list_quest_entries shows its steps.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        page = ouro.quests.list_leaderboard(
            quest_id,
            item_id,
            limit=limit,
            offset=offset,
        )

        def _row_line(row: QuestLeaderboardRow) -> str:
            parts = [
                f"#{row.placement}",
                markdown_id(row.entry_id),
                f"score: {row.score}" if row.score is not None else None,
            ]
            categories = _category_scores_part(row.category_scores)
            if categories:
                parts.append(categories)
            if row.user and row.user.username:
                parts.append(f"@{row.user.username}")
            elif row.user:
                parts.append(f"user_id: `{row.user.user_id}`")
            if row.eval_status:
                parts.append(f"eval: {row.eval_status}")
            run_id = _item_field(row, "eval_run_id")
            if run_id:
                parts.append(f"run_id: `{run_id}`")
            return markdown_bullet(f"placement {row.placement}", *parts)

        extras: list[str] = []
        if page.item and page.item.leaderboard_order:
            extras.append(f"order: {page.item.leaderboard_order}")

        return render_markdown_list(
            list(page),
            line_fn=_row_line,
            pagination=page_pagination(page),
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
        status: Annotated[Literal["accepted", "rejected"], Field(description="Your decision")],
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

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def evaluate_quest_entry(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        entry_id: Annotated[str, Field(description="Quest entry UUID")],
        ctx: Context,
    ) -> str:
        """Run the item's evaluator on an entry, as the quest's owner (who pays).

        For items with eval_trigger="owner", whose entries wait as
        eval_status "pending", and to re-score an evaluated entry. An accepted
        entry's score is updated without paying it again. The entry's eval_run
        lists each step with the action_id of its route run. Steps that take
        longer leave the entry "running"; check it later with
        list_quest_entries.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        entry = ouro.quests.evaluate_entry(quest_id, entry_id)
        return dump_json(entry.model_dump(mode="json"))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def evaluate_quest_item(
        quest_id: Annotated[str, Field(description="Quest UUID")],
        item_id: Annotated[str, Field(description="Quest item UUID")],
        ctx: Context,
        assets: Annotated[
            Optional[Dict[str, str]],
            Field(
                description=(
                    "Assets to evaluate, keyed like an entry's: "
                    '{"<contributor_key>": "<asset_uuid>"}. Starts a new trial run.'
                )
            ),
        ] = None,
        run_id: Annotated[
            Optional[str],
            Field(
                description=(
                    "Read this evaluation run again instead of starting one: a "
                    "trial's, or a run_id from the leaderboard or a re-score."
                )
            ),
        ] = None,
    ) -> str:
        """Try an item's evaluator on assets without submitting an entry.

        A trial run: for a quest's author to check an evaluator, and for a
        contributor to see how a candidate scores first. It runs as you and you
        pay for the step runs; nothing is entered, accepted or paid out.

        Returns the run: its status, each step's status and action_id (open a
        step's route run with get_action), gates, categories, score, passed and
        error. Steps that answer inline are done when this returns; while the
        run is "running", call again with run_id (and no assets) to read it.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        if (assets is None) == (run_id is None):
            raise ValueError("Pass assets to start a trial run, or run_id to read one.")
        if run_id is not None:
            run = ouro.quests.get_evaluation(quest_id, run_id)
        else:
            run = ouro.quests.evaluate_item(quest_id, item_id, assets)
        return dump_json(_plain(run))
