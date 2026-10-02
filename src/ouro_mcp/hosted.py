"""Tool surface for the hosted HTTP server.

On the hosted server a filesystem path means the server's disk, not the
caller's, so ``resolve_local_path`` rejects every one. Advertising those
parameters anyway makes an agent try them and fail. HTTP mode removes them, so
the schema says what the server can do. Each tool has another way in or out:
an upload_id for content, and a download link where it would write a file.

Tools register at import time, before ``main()`` knows the transport, so this
edits the registered tools instead of the tool definitions.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# Tool -> the parameter that takes a local path.
LOCAL_PATH_PARAMS: dict[str, str] = {
    "create_file": "file_path",
    "update_file": "file_path",
    "create_dataset": "data_path",
    "update_dataset": "data_path",
    "create_post": "content_path",
    "update_post": "content_path",
    "download_asset": "output_path",
}

# Description text that names a removed parameter -> its hosted wording. Every
# "old" string must be present: a docstring edit that breaks one fails loudly
# here and in tests instead of leaving a stale mention.
_DESCRIPTION_EDITS: dict[str, tuple[tuple[str, str], ...]] = {
    "create_file": (
        ("- file_path — relative paths are resolved from WORKSPACE_ROOT.\n", ""),
        (" (e.g. remote clients)", ""),
    ),
    "update_file": (
        (
            "provide one of file_path,\nfile_content_base64, file_content_text, or upload_id",
            "provide one of\nfile_content_base64, file_content_text, or upload_id",
        ),
    ),
    "download_asset": (
        (
            "With output_path the asset is saved there; a directory keeps the server-provided filename.\n"
            "Without it, the result is a link",
            "The result is a link",
        ),
    ),
    "create_dataset": (
        (
            "Provide data, data_path, or upload_id (one required).",
            "Provide data or upload_id (one required).",
        ),
    ),
    "update_dataset": (
        (
            "Pass data, data_path, or upload_id for row ingest",
            "Pass data or upload_id for row ingest",
        ),
    ),
    "create_post": (
        (
            "Provide content_markdown, content_path, or upload_id.",
            "Provide content_markdown or upload_id.",
        ),
    ),
    "update_post": (
        (
            "Pass content_markdown, content_path, or upload_id to replace the body.",
            "Pass content_markdown or upload_id to replace the body.",
        ),
    ),
}


def apply_hosted_tool_surface(server: FastMCP) -> None:
    """Remove local-path parameters from the tools registered on ``server``."""
    tools = server._tool_manager._tools
    for name, param in LOCAL_PATH_PARAMS.items():
        tool = tools[name]
        # The argument model still accepts the parameter; a caller that sends
        # it anyway reaches resolve_local_path and gets workspace_path_denied.
        del tool.parameters["properties"][param]
        description = tool.description
        for old, new in _DESCRIPTION_EDITS[name]:
            if old not in description:
                raise RuntimeError(f"{name}: hosted description edit no longer matches: {old!r}")
            description = description.replace(old, new)
        tool.description = description
