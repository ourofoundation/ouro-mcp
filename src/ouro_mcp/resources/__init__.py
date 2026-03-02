"""Resource registration for the Ouro MCP server."""

from mcp.server.fastmcp import FastMCP


def register_all_resources(mcp: FastMCP) -> None:
    """Import all resource modules so their @mcp.resource() decorators fire."""
    from ouro_mcp.resources import datasets, files, notifications, posts, profile

    profile.register(mcp)
    datasets.register(mcp)
    files.register(mcp)
    posts.register(mcp)
    notifications.register(mcp)
