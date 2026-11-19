"""Quest authoring prompts."""

from __future__ import annotations

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field


def register(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="quest_authoring_guide",
        title="Quest Authoring Guide",
        description="Guide an agent through creating a clear, reviewable Ouro quest.",
    )
    def quest_authoring_guide(
        quest_goal: Annotated[
            Optional[str],
            Field(description="Optional plain-language goal or domain for the quest being drafted."),
        ] = None,
        reward_notes: Annotated[
            Optional[str],
            Field(description="Optional notes about currency, budget, per-entry reward, or payout policy."),
        ] = None,
        review_notes: Annotated[
            Optional[str],
            Field(description="Optional notes about reviewer expectations, acceptance criteria, or quality bar."),
        ] = None,
    ) -> str:
        """Guide an agent through creating a clear, reviewable Ouro quest."""

        context_lines = []
        if quest_goal:
            context_lines.append(f"- Quest goal: {quest_goal}")
        if reward_notes:
            context_lines.append(f"- Reward notes: {reward_notes}")
        if review_notes:
            context_lines.append(f"- Review notes: {review_notes}")

        context = (
            "\n".join(context_lines)
            if context_lines
            else "- No specific quest context was provided yet."
        )

        return f"""
Use this guide when drafting or publishing an Ouro quest.

Provided context:
{context}

## First, choose the right destination

Before creating any quest:

1. Call `list_organizations()` to see where the user can publish.
2. Call `list_teams(org_id=...)` for the target organization.
3. Choose the most specific relevant team and verify `agent_can_create` is true.
4. If the user did not specify a location and there is no obvious team, ask them to choose.
5. Pass both `org_id` and `team_id` to `create_quest`.

Do not omit `org_id` or `team_id`; the default catch-all location is low visibility.

## Decide whether to publish or draft

Prefer `status="draft"` when any of these are unclear:

- The reward amount, budget, or payout rules.
- The exact submission format.
- The acceptance criteria or reviewer.
- Whether the requester has the rights to solicit or publish the requested data.

Use `status="open"` only when the user has confirmed the scope and publishing location.

## Pick the quest type

- Use `type="continuous"` for standing calls where many contributors can submit many
  independent entries, such as data collection, benchmarks, examples, or bug reports.
- Use `type="closable"` for one-off work where each contributor should have at most
  one active submission for a specific item.

## Write a useful quest description

A good quest description should include:

- Goal: what outcome the requester wants and why it matters.
- Scope: what is in scope and out of scope.
- Submission requirements: asset type, files, metadata, units, schemas, or links required.
- Quality bar: what makes a submission acceptable, high-quality, or rejected.
- Review process: who reviews, expected timing if known, and what feedback submitters may receive.
- Rights and attribution: licensing, provenance, privacy, and confirmation that submitters can share the work.
- Reward policy: currency, amount, whether rewards are per accepted entry, and whether
  partial rewards or multiple winners are possible.

For data quests, ask for raw data when possible, structured metadata with units,
sample or record identifiers, and enough provenance to connect inputs to outputs.

## Make items concrete

Quest items are the trackable work units. Each item should describe exactly what
contributors submit and how it will be reviewed.

Use a plain markdown string for simple unpaid tasks, or a full item object when
reward / evaluation metadata matters. Do **not** JSON-stringify the whole item
into the description string — pass an object (or a plain prose string) instead:

```json
{{
  "description": "Submit a reusable dataset with raw files, metadata, units, sample IDs, and licensing notes.",
  "expected_asset_type": "dataset",
  "reward_currency": "btc",
  "reward_amount": 1000,
  "submission_assets": {{"dataset": {{"asset_type": "dataset", "required": true}}}}
}}
```

`reward_amount` is in the smallest currency unit: sats for BTC and cents for USD.
Confirm available budget before attaching rewards.

`submission_assets` is a keyed record: each key is a contributor input name and
each value is a declaration object with `asset_type` plus optional constraints
such as `required`, `primary`, `input_filter`, `file_extensions`,
`contains_file_extensions`, and `label` (the title shown on the contributor
form). Declare it directly for non-eval items.

For scored contests, give the item an `evaluator` and set
`leaderboard_enabled: true`. An evaluator runs one or more existing routes as
steps and turns their JSON responses into a score:

```json
{{
  "steps": {{
    "hull": {{"route_id": "<route uuid>", "inputs": {{"structure": "inputs.structure"}}}},
    "mae": {{"route_id": "<route uuid>", "inputs": {{"structure": "hull.relaxed"}}, "after": ["stable"]}}
  }},
  "gates": {{"stable": "hull.e_above_hull <= 0.075"}},
  "categories": {{"anisotropy": "min(mae.k1, 1)"}},
  "score": "100 * anisotropy",
  "pass": {{"min": 50}}
}}
```

Each step input is wired to `"inputs.<key>"` (an asset the entry submits),
`"<step>.<field>"` (an asset an earlier step returned) or a fixed
`{{"asset_id": ..., "asset_type": ...}}`. `gates` are pass/fail conditions: a
step waits for the gates in its `after`, and a failed gate skips every step not
yet started, so put cheap checks before expensive routes. `categories` are
named sub-scores shown on the leaderboard; ranking uses `score`. `pass` is the
inclusive score band an entry must land in. Formulas read step responses by
path (`hull.e_above_hull`). When one route already returns the score,
`eval_route_id` (with `eval_score_path`, `eval_categories_path`, pass bounds
and `eval_static_inputs`) is the short form.

Choose `leaderboard_order` `"desc"` (higher wins) or `"asc"` (lower wins). The
leaderboard is a view over stored entry scores — it does not run a second
ranking route. Continuous quests are the natural fit when the same contributor
may submit many scored entries.

Before opening the quest, try the evaluator with `evaluate_quest_item` on a
known asset: it runs the steps as you, without creating an entry, and returns
each step's status and route run, the gates and the score.

Benchmarks of routes (models, services): have a step take the submitted route
as an input (`asset_type: "route"` on the step route's input), and set
`eval_trigger: "owner"` so entries wait as `pending` until the quest's owner
runs each evaluation with `evaluate_quest_entry` (the owner's step routes call
the submitted route and the owner pays for it). `evaluate_quest_entry` also
re-scores an evaluated entry. A route can be entered once per item, so one
model can enter every track.

For an item with an evaluator, do not invent competing `submission_assets`
keys. The server derives contributor keys from the `inputs.<key>` assets the
steps read (for `eval_route_id`, the route's inputs minus those pinned in
`eval_static_inputs`). You may overlay `label` on those derived keys so the
contributor form shows a human title. Inspect each route before wiring it,
then inspect the created item with `list_quest_items` to see its effective
`submission_assets` and `contributor_keys`.

## Before calling create_quest

Check that you can answer these questions:

- Where will this quest be published?
- Should it be draft or open?
- Is the quest continuous or closable?
- What exactly should a contributor submit?
- What will reviewers accept or reject?
- Is any reward funded, denominated, and tied to an item?
- Are licensing, attribution, privacy, and data rights addressed?

If not, ask the user the missing question instead of publishing a vague quest.

## Contributor submissions and author review

Paid quest items (`reward_amount > 0`) use a privacy-preserving review model:

- Contributors submit a required description plus asset references.
- The backend computes **judge signals** (file size/type, dataset row counts, post
  structure stats, etc.) without exposing raw asset contents to the author.
- Private assets stay private until the author **accepts** the entry, which grants
  them read access via the existing permissions system.
- If an asset is already visible to the author at submit time (e.g. public), the
  API returns a warning — submission still succeeds.

When helping a contributor submit to a paid item, draft a clear description that
explains why the submission satisfies the quest without revealing private asset
contents, attach the private asset reference, and remind them that the author
judges from the description + signals until accept.

Before submitting any item, call `list_quest_items` and key `assets` with the
exact contributor keys shown there. For eval items, inspect the evaluator's
routes as well. Never assume a generic `file` or `artifact` key: eval
contributor keys are derived and owned by the server from the assets those
routes take. `evaluate_quest_item` shows how a candidate scores before it is
entered.
""".strip()
