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
from ouro import Ouro

from ouro_mcp import __version__
from ouro_mcp.constants import (
    DEFAULT_HTTP_PORT,
    ENV_OURO_API_KEY,
    ENV_OURO_BASE_URL,
    ENV_OURO_DATABASE_ANON_KEY,
    ENV_OURO_DATABASE_URL,
)

load_dotenv(find_dotenv(usecwd=True), override=True)

log = logging.getLogger(__name__)


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
            "Get your API key from https://ouro.foundation/settings/api."
        )

    kwargs = {"api_key": api_key}
    if os.environ.get(ENV_OURO_BASE_URL):
        kwargs["base_url"] = os.environ[ENV_OURO_BASE_URL].strip()
    if os.environ.get(ENV_OURO_DATABASE_URL):
        kwargs["database_url"] = os.environ[ENV_OURO_DATABASE_URL].strip()
    if os.environ.get(ENV_OURO_DATABASE_ANON_KEY):
        kwargs["database_anon_key"] = os.environ[ENV_OURO_DATABASE_ANON_KEY].strip()

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

**Writing Ouro posts** — use extended markdown in create_post and update_post:
- **Mention users**: `{@username}` — call search_users(query=...) first to find usernames
- **Embed assets**: ```assetComponent
  {"id": "<uuid>", "assetType": "post"|"file"|"dataset"|"route"|"service", "viewMode": "preview"|"card"}
  ``` — use search_assets() or get_asset() for IDs; prefer viewMode "preview" for files/datasets
- **Standard markdown**: headings, **bold**, *italic*, lists, code blocks, tables, links
- **Math**: \\(inline\\) and \\[display\\] LaTeX
""".strip()

mcp = FastMCP(
    "Ouro",
    instructions=INSTRUCTIONS,
    lifespan=app_lifespan,
    json_response=True,
)

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
