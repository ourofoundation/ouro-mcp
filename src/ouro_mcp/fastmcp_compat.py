"""Expose ``mcp.server.fastmcp`` on MCP Python SDK 2.

SDK 2 renamed ``FastMCP`` to ``MCPServer`` and moved it to
``mcp.server.mcpserver``. The rest of this package still imports the old
path. Tests that install their own stub before importing ``ouro_mcp`` are
left alone.
"""

from __future__ import annotations

import sys
import types


def install_fastmcp_compat() -> None:
    if "mcp.server.fastmcp" in sys.modules:
        return
    from mcp.server.mcpserver import Context, MCPServer

    module = types.ModuleType("mcp.server.fastmcp")
    module.Context = Context
    module.FastMCP = MCPServer
    module.__doc__ = "Compatibility alias for mcp.server.mcpserver."
    sys.modules["mcp.server.fastmcp"] = module
    import mcp.server as server_pkg

    server_pkg.fastmcp = module
