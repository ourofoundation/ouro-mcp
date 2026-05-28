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

1. Call `get_organizations()` to see where the user can publish.
2. Call `get_teams(org_id=...)` for the target organization.
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

Use plain string items for simple unpaid tasks. Use full item objects when reward or evaluation metadata matters:

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
""".strip()
