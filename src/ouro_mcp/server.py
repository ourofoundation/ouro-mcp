from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from ouro import Ouro

load_dotenv(override=True)

log = logging.getLogger(__name__)


@dataclass
class OuroContext:
    """Shared context holding the initialized Ouro client."""

    ouro: Ouro


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[OuroContext]:
    """Initialize the Ouro client once at startup and share it across all tools."""
    log.info("Initializing Ouro client...")

    api_key = os.environ.get("OURO_API_KEY", "").strip()
    kwargs = {"api_key": api_key}
    if os.environ.get("OURO_BASE_URL"):
        kwargs["base_url"] = os.environ["OURO_BASE_URL"].strip()
    if os.environ.get("OURO_DATABASE_URL"):
        kwargs["database_url"] = os.environ["OURO_DATABASE_URL"].strip()
    if os.environ.get("OURO_DATABASE_ANON_KEY"):
        kwargs["database_anon_key"] = os.environ["OURO_DATABASE_ANON_KEY"].strip()

    ouro = Ouro(**kwargs)
    log.info(f"Authenticated as {ouro.user.email}")
    log.info(f"Backend: {ouro.base_url}")
    yield OuroContext(ouro=ouro)


mcp = FastMCP(
    "Ouro",
    lifespan=app_lifespan,
    json_response=True,
)

# Register all tools
from ouro_mcp.tools import register_all_tools  # noqa: E402

register_all_tools(mcp)


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
        default=8000,
        help="Port for HTTP transports (default: 8000)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, port=args.port)


if __name__ == "__main__":
    main()
