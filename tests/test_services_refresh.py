from __future__ import annotations

import asyncio

from ouro_mcp.server import mcp


def test_update_service_exposes_remote_spec_refresh_flag() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    refresh_spec = tools["update_service"].inputSchema["properties"]["refresh_spec"]

    assert refresh_spec["type"] == "boolean"
    assert refresh_spec["default"] is False
