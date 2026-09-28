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
from ouro_mcp.constants import (
    DEFAULT_HTTP_PORT,
    DEFAULT_OURO_MCP_AUTH_ISSUER,
    ENV_OURO_API_KEY,
    ENV_OURO_BASE_URL,
    ENV_OURO_MCP_AUTH_ISSUER,
    ENV_OURO_MCP_LOCAL_FILES,
    ENV_OURO_MCP_RESOURCE_URL,
    OURO_MCP_OAUTH_SCOPE,
)
from ouro_mcp.http_auth import (
    ApiKeyHeaderShim,
    ApiKeyMiddleware,
    OuroTokenVerifier,
    RequestScopedOuro,
    bind_stdio_client,
    http_mode,
    set_http_mode,
)
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
    """Initialize the Ouro client once at startup and share it across all tools.

    HTTP mode does not authenticate at startup. Each request carries the
    caller's personal access token, and tools resolve a client from that token.
    """
    if http_mode():
        log.info("HTTP mode: tool calls use the API key on each request")
        yield OuroContext(ouro=RequestScopedOuro())
        return

    log.info("Initializing Ouro client...")

    api_key = os.environ.get(ENV_OURO_API_KEY, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{ENV_OURO_API_KEY} environment variable is required but not set. "
            "Get your API key from https://ouro.foundation/settings/api-keys."
        )

    kwargs = {"api_key": api_key, "client": f"ouro-mcp/{__version__}"}
    if os.environ.get(ENV_OURO_BASE_URL):
        kwargs["base_url"] = os.environ[ENV_OURO_BASE_URL].strip()

    ouro = Ouro(**kwargs)
    log.info(f"Authenticated as {ouro.user.email}")
    log.info(f"Backend: {ouro.base_url}")
    log.info(f"Client: {ouro._ouro_client} ({ouro._user_agent})")
    bind_stdio_client(ouro)
    try:
        yield OuroContext(ouro=ouro)
    finally:
        bind_stdio_client(None)


INSTRUCTIONS = """
Ouro is a platform for creating, sharing, and discovering data assets: posts, datasets, files, services (with routes), and quests.

**Where assets live**: every asset belongs to one organization and one team (a channel) in it.
Before creating anything, call get_organizations() and get_teams(org_id=...), and skip teams
marked `agent_can_create: false`. If the user hasn't said where to publish, ask. Pass org_id and
team_id to create_* tools; omitting them publishes to the low-visibility global "All" team.

**Reading responses**: list/search tools return compact markdown — a header with counts, then one
bullet per item with its id in backticks after `id:`. Copy ids verbatim into follow-up calls. When
the header says more are available, page with `offset` (list_messages pages with `before`).
Single-entity tools (get_asset, create_*, update_*, get_action, ...) and errors return JSON.

**Access**: private assets are invisible to others until share_asset(id, user_id, role="read").
Mentions, links, and embeds do not grant access, and @mentions on assets a user cannot see do not
notify them.

**Provenance**: create/update tools take top-level `license_id` and `attribution`
(`originality`: "original" | "derivative" | "third-party", plus optional github_url, paper_url,
doi_url, external_url, relation_type). Keep it out of `metadata`, and confirm the license permits
redistribution before publishing third-party work.

**Extended markdown** (posts, comments, quest descriptions and items):
- Mention users with @username.
- Link assets inline with `[label](post:<uuid>)` — likewise file:, dataset:, route:, service:,
  quest:, or asset: when the type is unknown. Link a route run with `[label](action:<uuid>)`.
  Never invent URL paths.
- Embed an asset as a block:
  ```assetComponent
  {"id": "<uuid>", "assetType": "post"|"file"|"dataset"|"route"|"service", "viewMode": "preview"|"card", "displayConfig": {"visualizationId": "<uuid>", "actionId": "<uuid>"}}
  ```
  displayConfig is optional: visualizationId picks a saved dataset view, actionId shows a route
  run receipt. Prefer viewMode "preview" for files and datasets. Route-action tools return
  ready-made `link_markdown` / `embed_markdown` to paste.
- LaTeX: \\(inline\\) and \\[display\\].

**Common workflows**:
- Datasets: read the schema (get_asset detail="full"), then query_dataset for samples, filters,
  and aggregates. For bulk analysis, download_asset (CSV) and compute locally instead of paging
  rows into chat.
- Services: search_assets(asset_type="service") → get_asset(service_id) →
  get_asset(route_id, detail="full") for the input schema → execute_route (dry_run=true to
  validate) → get_action for status and outputs.
- Quests: inspect with list_quest_items and use each item's exact contributor_keys in
  submit_quest_entry. Draft quests accept no entries until update_quest(status="open").
""".strip()

_mcp_log_level = resolve_fastmcp_log_level()

mcp = FastMCP(
    "Ouro",
    instructions=INSTRUCTIONS,
    lifespan=app_lifespan,
    log_level=_mcp_log_level,
    middleware=[ApiKeyMiddleware()],
)

from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})

apply_ouro_mcp_logging(_mcp_log_level)

# Register all tools, resources, and prompts
from ouro_mcp.prompts import register_all_prompts  # noqa: E402
from ouro_mcp.resources import register_all_resources  # noqa: E402
from ouro_mcp.tools import register_all_tools  # noqa: E402

register_all_tools(mcp)
register_all_resources(mcp)
register_all_prompts(mcp)


def enable_http_auth(server: FastMCP, public_host: str) -> None:
    """Require a verified bearer token on the MCP endpoint and advertise OAuth.

    Unauthenticated requests get a 401 whose ``WWW-Authenticate`` header points
    at ``/.well-known/oauth-protected-resource/mcp``, which names Supabase Auth
    as the authorization server. That is the discovery chain MCP clients follow.
    """
    from mcp.server.auth.settings import AuthSettings

    issuer = os.environ.get(ENV_OURO_MCP_AUTH_ISSUER, "").strip() or DEFAULT_OURO_MCP_AUTH_ISSUER
    resource = os.environ.get(ENV_OURO_MCP_RESOURCE_URL, "").strip() or f"https://{public_host}/mcp"
    # MCPServer only takes these in its constructor, and HTTP mode is not
    # known until main() runs; streamable_http_app() reads both at build time.
    server.settings.auth = AuthSettings(
        issuer_url=issuer,
        resource_server_url=resource,
        required_scopes=[OURO_MCP_OAUTH_SCOPE],
    )
    server._token_verifier = OuroTokenVerifier()
    log.info("OAuth enabled: resource=%s issuer=%s", resource, issuer)


def main():
    parser = argparse.ArgumentParser(description="Ouro MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for HTTP transports (default: 127.0.0.1)",
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
        return

    # One shared OURO_API_KEY would make every HTTP caller that user.
    # Drop it so a local .env cannot become the server identity.
    set_http_mode(True)
    os.environ[ENV_OURO_MCP_LOCAL_FILES] = "0"
    os.environ.pop(ENV_OURO_API_KEY, None)

    from mcp.server.transport_security import TransportSecuritySettings

    public_host = os.environ.get("OURO_MCP_PUBLIC_HOST", "mcp.ouro.foundation").strip()
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[
            f"https://{public_host}",
            f"http://{public_host}",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "HTTP transport bound to %s. Terminate TLS at a reverse proxy; "
            "callers send API keys as bearer tokens.",
            args.host,
        )
    enable_http_auth(mcp, public_host)
    log.info(
        "Starting ouro-mcp transport=%s on %s:%s (per-request credentials, local files disabled)",
        args.transport,
        args.host,
        args.port,
    )
    if args.transport == "streamable-http":
        app = mcp.streamable_http_app(
            json_response=True,
            stateless_http=True,
            transport_security=transport_security,
            host=args.host,
        )
    else:
        app = mcp.sse_app(transport_security=transport_security, host=args.host)

    import uvicorn

    uvicorn.run(
        ApiKeyHeaderShim(app),
        host=args.host,
        port=args.port,
        log_level=mcp.settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
