"""Tool registration for the Ouro MCP server."""

import functools
import inspect
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from ouro_mcp.errors import NON_RETRYABLE_TRANSPORT_TOOLS, ToolErrorText

_NULL = {"type": "null"}
# Keys whose values are name -> schema maps rather than schemas.
_SCHEMA_MAPS = ("properties", "$defs")
# Keys whose values are data, not schemas.
_LITERALS = ("default", "enum", "const", "examples")


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop JSON-schema noise that costs agents tokens without informing them.

    Pydantic titles every property and wraps each ``Optional`` parameter in
    ``anyOf: [T, null]`` with ``default: null``. An omitted optional parameter
    already means null, so both collapse to plain ``T``.
    """
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in _SCHEMA_MAPS:
            out[key] = {name: compact_schema(sub) for name, sub in value.items()}
        elif key in _LITERALS:
            out[key] = value
        elif isinstance(value, dict):
            out[key] = compact_schema(value)
        elif isinstance(value, list):
            out[key] = [compact_schema(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value

    variants = out.get("anyOf")
    if variants and _NULL in variants and out.get("default", None) is None:
        rest = [v for v in variants if v != _NULL]
        del out["anyOf"]
        out.pop("default", None)
        out = {**(rest[0] if len(rest) == 1 else {"anyOf": rest}), **out}
    return out


def _as_call_result(result: Any) -> Any:
    if isinstance(result, ToolErrorText):
        return CallToolResult(content=[TextContent(type="text", text=str(result))], is_error=True)
    return result


def report_errors(fn: Callable, is_async: bool) -> Callable:
    """Return a tool's JSON error payload with ``isError`` so clients see a failure."""
    if is_async:

        @functools.wraps(fn)
        async def async_wrapper(**kwargs: Any) -> Any:
            return _as_call_result(await fn(**kwargs))

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> Any:
        return _as_call_result(fn(**kwargs))

    return wrapper


def _hint(tool: Any, name: str) -> bool:
    """An annotation hint by its wire name (``readOnlyHint``), on either SDK."""
    hints = tool.annotations
    if isinstance(hints, dict):
        return bool(hints.get(name))
    snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in name)
    return bool(getattr(hints, name, None) or getattr(hints, snake, None))


def register_all_tools(mcp: FastMCP) -> None:
    """Import all tool modules so their @mcp.tool() decorators fire."""
    from ouro_mcp.tools import (
        assets,
        comments,
        conversations,
        datasets,
        files,
        money,
        notifications,
        organizations,
        posts,
        quests,
        services,
        teams,
        users,
    )

    organizations.register(mcp)
    teams.register(mcp)
    assets.register(mcp)
    users.register(mcp)
    datasets.register(mcp)
    posts.register(mcp)
    quests.register(mcp)
    comments.register(mcp)
    conversations.register(mcp)
    files.register(mcp)
    services.register(mcp)
    money.register(mcp)
    notifications.register(mcp)

    for tool in mcp._tool_manager.list_tools():
        tool.description = inspect.cleandoc(tool.description)
        tool.fn = report_errors(tool.fn, tool.is_async)
        if not _hint(tool, "readOnlyHint") and not _hint(tool, "idempotentHint"):
            NON_RETRYABLE_TRANSPORT_TOOLS.add(tool.name)
        # Reject unknown arguments; otherwise a misspelled or unsupported
        # filter is silently dropped and the agent trusts unfiltered results.
        arg_model = tool.fn_metadata.arg_model
        arg_model.model_config["extra"] = "forbid"
        arg_model.model_rebuild(force=True)
        tool.parameters = compact_schema(arg_model.model_json_schema(by_alias=True))
        # Tools return rendered text; a {"result": str} structured copy would
        # send every response twice.
        tool.fn_metadata.output_schema = None
