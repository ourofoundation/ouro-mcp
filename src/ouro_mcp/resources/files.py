"""File resource — metadata, URL, and MIME info."""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, file_result


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        "ouro://files/{id}",
        name="File",
        description="File asset detail: metadata, `file_url` (download), MIME type, and size.",
        mime_type="application/json",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def get_file(id: str, ctx: Context) -> str:
        ouro = ctx.request_context.lifespan_context.ouro
        file = ouro.files.retrieve(id)
        return dump_json(file_result(file))
