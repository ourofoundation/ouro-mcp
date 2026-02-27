"""Tool registration for the Ouro MCP server."""

from mcp.server.fastmcp import FastMCP


def register_all_tools(mcp: FastMCP) -> None:
    """Import all tool modules so their @mcp.tool() decorators fire."""
    from ouro_mcp.tools import assets, datasets, files, money, organizations, posts, services, teams

    organizations.register(mcp)
    teams.register(mcp)
    assets.register(mcp)
    datasets.register(mcp)
    posts.register(mcp)
    files.register(mcp)
    services.register(mcp)
    money.register(mcp)
