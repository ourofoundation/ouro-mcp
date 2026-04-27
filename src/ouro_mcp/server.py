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
5. Pass org_id and team_id to create_post, create_dataset, or create_file.
   Pass org_id to create_team.

Omitting org_id/team_id defaults to the user's global organization and "All" team,
which is a low-visibility catch-all. Always prefer a specific team when possible.

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
- **Math**: $inline$ and $$display$$ LaTeX

**Conversations and messages**:
- Use list_conversations() to see conversations you belong to.
- Use get_conversation(conversation_id=...) to inspect conversation details and members.
- Use create_conversation(member_user_ids=...) to start a new conversation.
- Use send_message(conversation_id, text) and list_messages(conversation_id, ...) for chat.

**Route actions**:
- Use get_asset(route_id, detail="full") to inspect a route's schema before execution.
- For asset inputs, pass asset IDs via execute_route(input_assets={...}) keyed by
  the route's input asset names. Do not build file/dataset/post body objects by hand;
  Ouro resolves asset IDs into the service-facing request body.
- Use execute_route(..., dry_run=true) to validate parameters without running the route.
- execute_route returns an action_id and embed_markdown; use get_action(action_id) to inspect status/output.
- Use list_route_actions(route_id=...) to find previous executions and get ready-to-use action embeds.
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

# Register all tools and resources
from ouro_mcp.resources import register_all_resources  # noqa: E402
from ouro_mcp.tools import register_all_tools  # noqa: E402

register_all_tools(mcp)
register_all_resources(mcp)


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
