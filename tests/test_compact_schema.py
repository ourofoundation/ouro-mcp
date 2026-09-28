"""Advertised tool schemas drop pydantic noise but keep meaning."""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import pytest
from pydantic import BaseModel, Field, ValidationError

from ouro_mcp.tools import compact_schema


class Item(BaseModel):
    title: Annotated[str, Field(description="Item title")]
    note: Optional[str] = None


class Args(BaseModel):
    title: Annotated[str, Field(description="A parameter literally named title")]
    limit: Optional[int] = None
    item: Optional[Item] = None
    mode: Optional[str] = "fast"
    tags: list[str] = Field(default_factory=list)


def test_strips_titles_but_keeps_properties_named_title() -> None:
    schema = compact_schema(Args.model_json_schema())
    assert "title" not in schema
    assert schema["properties"]["title"] == {
        "description": "A parameter literally named title",
        "type": "string",
    }
    assert "title" in schema["$defs"]["Item"]["properties"]
    assert "title" not in schema["$defs"]["Item"]
    assert schema["required"] == ["title"]


def test_collapses_nullable_optionals() -> None:
    props = compact_schema(Args.model_json_schema())["properties"]
    assert props["limit"] == {"type": "integer"}
    assert props["item"] == {"$ref": "#/$defs/Item"}


def test_keeps_null_when_default_is_not_null() -> None:
    props = compact_schema(Args.model_json_schema())["properties"]
    assert props["mode"]["default"] == "fast"
    assert {"type": "null"} in props["mode"]["anyOf"]


def test_registered_tools_are_compact_and_unstructured() -> None:
    from ouro_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert tools
    for tool in tools:
        assert "title" not in tool.input_schema
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema is None


def test_unknown_tool_arguments_are_rejected() -> None:
    from ouro_mcp.server import mcp

    arg_model = mcp._tool_manager.get_tool("get_comments").fn_metadata.arg_model
    with pytest.raises(ValidationError, match="page_size"):
        arg_model.model_validate({"parent_id": "x", "page_size": 3})
