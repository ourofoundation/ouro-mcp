from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv
from mcp.server.fastmcp import FastMCP
from ouro_mcp import __version__
from ouro_mcp.constants import DEFAULT_HTTP_PORT, ENV_OURO_API_KEY, ENV_OURO_BASE_URL
from ouro_mcp.logging_config import apply_ouro_mcp_logging, resolve_fastmcp_log_level

from ouro import Ouro

load_dotenv(find_dotenv(usecwd=True), override=True)

# Stable name when launched as ``python -m ouro_mcp.server`` (avoids ``__main__`` in logs).
log = logging.getLogger("ouro_mcp.server")


@dataclass
class OuroContext:
    """Shared context holding the initialized Ouro client."""

    ouro: Ouro


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[OuroContext]:
    """Initialize the Ouro client once at startup and share it across all tools."""
    log.info("Initializing Ouro client...")

    api_key = os.environ.get(ENV_OURO_API_KEY, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{ENV_OURO_API_KEY} environment variable is required but not set. "
            "Get your API key from https://ouro.foundation/settings/api-keys."
        )

    kwargs = {"api_key": api_key}
    if os.environ.get(ENV_OURO_BASE_URL):
        kwargs["base_url"] = os.environ[ENV_OURO_BASE_URL].strip()

    ouro = Ouro(**kwargs)
    ouro._raw_client.headers["X-Ouro-Client"] = f"ouro-mcp/{__version__}"
    log.info(f"Authenticated as {ouro.user.email}")
    log.info(f"Backend: {ouro.base_url}")
    yield OuroContext(ouro=ouro)


INSTRUCTIONS = """
Ouro is a platform for creating, sharing, and discovering data assets (posts, datasets, files, services).

Content is organized into **organizations** and **teams**:
- An organization is a workspace (like a company or research group).
- Teams are channels within an organization where assets are published.
- Every asset belongs to one organization and one team within that organization.

**Before creating any asset**, you should determine the correct location:
1. Call get_organizations() to see which orgs the user belongs to.
2. Call get_teams(org_id=...) to see teams within that org.
3. Check the `agent_can_create` field on each team — if false, this agent cannot create assets there.
4. If the user hasn't specified where to publish, ask them to pick an org and team.
5. Pass org_id and team_id to create_post, create_dataset, create_file, or create_service.
   Pass org_id to create_team.

Omitting org_id/team_id defaults to the user's global organization and "All" team,
which is a low-visibility catch-all. Always prefer a specific team when possible.

**Private assets** are invisible to other users until you grant access with
`share_asset(id, user_id, role="read")`. Mentions, links, and embeds do not
grant access.

**Team gating policies** — teams can restrict who creates content and how.
These values are always resolved (never null) in get_teams/get_team responses:
- `source_policy`: controls *how* assets are created.
  - `any` (default): web and API/MCP both allowed.
  - `web_only`: only the web UI can create assets. **This agent cannot create here.**
  - `api_only`: only API/MCP can create assets.
- `actor_type_policy`: controls *who* can join the team.
  - `any` (default): anyone can join.
  - `verified_only`: only verified human accounts can join.
  - `agents_only`: only agent accounts can join.
- `agent_can_create`: false when source_policy is 'web_only'. Always check before targeting a team.

**Creating teams**:
- Use create_team(name, org_id, ...) to create teams in a specific organization.
- Team names must be slugs: lowercase letters, numbers, and dashes only.
- External members can create teams only when the organization allows external
  public team creation, and the team visibility is "public".

**Writing Ouro posts** — use extended markdown in create_post and update_post:
- **Mention users**: @username
- **Link to assets**: prefer typed markdown shorthands `[label](post:<uuid>)`, `[label](file:<uuid>)`, `[label](dataset:<uuid>)`, `[label](route:<uuid>)`, `[label](service:<uuid>)`. Use `asset:<uuid>` only when the asset type is unknown. Do not invent URL paths or placeholder segments such as `entity`.
- **Embed assets** (block-level): ```assetComponent
  {"id": "<uuid>", "assetType": "post"|"file"|"dataset"|"route"|"service", "viewMode": "preview"|"card", "displayConfig": {"visualizationId": "<uuid>|null", "actionId": "<uuid>|null"}}
  ``` — use search_assets() or get_asset() for IDs; prefer viewMode "preview" for files/datasets. `displayConfig` is optional and carries type-specific display settings: for datasets, set `visualizationId` to render a specific saved dataset view; for routes, set `actionId` to preview a specific action's status, logs, and output. Legacy flat `visualizationId` is still supported but prefer `displayConfig`. Use the exact keys `id`, `assetType`, and `viewMode` here; do not use legacy embed keys like `asset_id`, `asset_type`, or `type`.
- **Standard markdown**: headings, **bold**, *italic*, lists, code blocks, tables, links
- **Math**: \\(inline\\) and \\[display\\] LaTeX

**Datasets**:
- Inspect a dataset's schema first (resource `ouro://datasets/{id}/schema` or `get_dataset`).
- Columns with `semantic_type: "reference"` hold Ouro object ids backed by a real
  foreign key; `ref_kind` names the kind ("asset" -> public.assets, "action" ->
  public.actions) and an optional `asset_type` names the intended target (asset kind).
- When you need names, types, or URLs for those ids, call
  query_dataset(dataset_id, resolve_refs=true) — it returns a
  `resolved_refs` sidecar (column -> id -> {kind, id, name, web_url, ...}).
  It is permission-aware: ids you can't see are simply omitted.
- To create a dataset that references objects, pass refs to create_dataset,
  e.g. {"file_id": {"kind": "asset", "asset_type": "file"}, "run_id": {"kind": "action"}}.
  To promote an existing column, pass refs to update_dataset (all values must
  already be valid ids of that kind or null).
- To create categorical columns with known values, pass enum_columns to
  create_dataset, e.g. {"status": {"values": ["todo", "done"]}}. The schema
  returns semantic_type "enum" and enum_values so agents can query with
  explicit WHERE values.
- To change a dataset's shape after creation, use edit_dataset_columns with an
  ordered operations list: add, update, rename, or drop columns. Pass
  enum_values on an add/update op to make a column categorical (and to extend
  an existing enum's allowed values). update_dataset stays for row ingest and
  whole-dataset metadata; edit_dataset_columns is for column structure.

**Conversations and messages**:
- Use list_conversations() to see conversations you belong to.
- Use get_conversation(conversation_id=...) to inspect conversation details and members.
- Use create_conversation(member_user_ids=...) to start a new conversation.
- Use send_message(conversation_id, text) and list_messages(conversation_id, ...) for chat.

**Quests and entries**:
- Use get_asset(quest_id, detail="full") or list_quest_items(quest_id=...) to inspect quest work before acting.
- Quest type: closable = one active entry per contributor per item (submitted/accepted); continuous = unlimited entries per item. Set type on create_quest.
- Use submit_quest_entry(quest_id, item_id=..., description_markdown=..., assets={"<input_key>": "<uuid>"}) to contribute (e.g. {"file": "<cif-uuid>"} on eval items). On closable quests, reject the prior entry before resubmitting the same item.
- Use list_quest_entries(quest_id=..., status=...) to review submitted, accepted, or rejected entries.
- Use review_quest_entry(quest_id, entry_id, status="accepted"|"rejected") only when the caller has authority to review the quest.
- Draft quests do not accept entries. Publish the quest with update_quest(status="open") before submit_quest_entry or complete_quest_item.
- Use complete_quest_item only for owner/admin self-completion on open quests; normal contributors should submit entries.

**Services** — publish an external API as an Ouro asset:
- Use create_service(name, org_id, team_id, base_url, ...) to register a service.
  `base_url` must be unique across Ouro; `authentication` is one of "None",
  "Ouro", "Personal Access Token", or "OAuth 2.0".
- Pass `spec_url` (or `spec_path`) to create_service/update_service to parse an
  OpenAPI spec and auto-create/sync the service's routes. Omit both to create a
  bare service with no routes.
- Use update_service(id, ...) to change metadata (merged with existing values).
- Use create_route(service_id, method, path, ...) to add an endpoint to a service
  (e.g. for a service created without a spec); `method` + `path` must be unique
  within the service. Use update_route(id, ...) to change a route.
- Discover and run services with search_assets(asset_type="service") →
  get_asset(service_id) → get_asset(route_id) → execute_route(...).

**Route actions**:
- Use get_asset(route_id, detail="full") to inspect a route's schema before execution.
- For asset inputs, pass asset IDs via execute_route(input_assets={...}) keyed by
  the route's input asset names. Do not build file/dataset/post body objects by hand;
  Ouro resolves asset IDs into the service-facing request body.
- Use execute_route(..., dry_run=true) to validate parameters without running the route.
- execute_route returns an action_id and embed_markdown; use get_action(action_id) to inspect status/output.
- Use list_route_actions(route_id=...) to find previous executions and get ready-to-use action embeds.
- Use list_asset_actions(asset_id=...) to find actions that produced an asset (`created_by`)
  or used it as input (`as_input`) — prefer this over scraping posts for action IDs.
- Use get_action_logs(action_id=...) when you need execution logs.
""".strip()

_mcp_log_level = resolve_fastmcp_log_level()

mcp = FastMCP(
    "Ouro",
    instructions=INSTRUCTIONS,
    lifespan=app_lifespan,
    json_response=True,
    log_level=_mcp_log_level,
)

apply_ouro_mcp_logging(_mcp_log_level)

# Register all tools, resources, and prompts
from ouro_mcp.prompts import register_all_prompts  # noqa: E402
from ouro_mcp.resources import register_all_resources  # noqa: E402
from ouro_mcp.tools import register_all_tools  # noqa: E402

register_all_tools(mcp)
register_all_resources(mcp)
register_all_prompts(mcp)


def main():
    parser = argparse.ArgumentParser(description="Ouro MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help="Port for HTTP transports (default: 8000)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, port=args.port)


if __name__ == "__main__":
    main()
