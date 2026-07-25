from __future__ import annotations

import asyncio

from ouro_mcp.server import mcp


ATTRIBUTION_TOOLS = (
    "create_post",
    "update_post",
    "create_dataset",
    "update_dataset",
    "create_file",
    "update_file",
    "create_quest",
    "update_quest",
    "create_service",
    "update_service",
    "create_route",
    "update_route",
    "write_comment",
    "create_conversation",
)


def test_asset_write_tools_expose_top_level_license_and_attribution() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for name in ATTRIBUTION_TOOLS:
        properties = tools[name].inputSchema["properties"]
        assert "license_id" in properties, name
        assert "attribution" in properties, name
