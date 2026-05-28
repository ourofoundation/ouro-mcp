"""Prompt registration for the Ouro MCP server."""

from mcp.server.fastmcp import FastMCP


def register_all_prompts(mcp: FastMCP) -> None:
    """Import all prompt modules so their @mcp.prompt() decorators fire."""
    from ouro_mcp.prompts import quests

    quests.register(mcp)
