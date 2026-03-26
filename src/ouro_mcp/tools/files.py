"""File tools — create and update."""

from __future__ import annotations

import json
from base64 import b64decode
from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import file_result, optional_kwargs, resolve_local_path


def _resolve_file_input(
    *,
    file_path: Optional[str] = None,
    file_content_base64: Optional[str] = None,
    file_content_text: Optional[str] = None,
    file_name: Optional[str] = None,
) -> dict[str, Any]:
    """Return SDK kwargs for the file upload source.

    Exactly one of ``file_path``, ``file_content_base64``, or
    ``file_content_text`` must be provided.  When using inline content,
    ``file_name`` (with extension) is required for MIME-type detection.

    Returns a dict that can be spread into ``ouro.files.create()`` /
    ``ouro.files.update()`` (keys: ``file_path`` *or*
    ``file_content`` + ``file_name``).
    """
    sources = [
        ("file_path", file_path is not None),
        ("file_content_base64", file_content_base64 is not None),
        ("file_content_text", file_content_text is not None),
    ]
    selected = [name for name, is_set in sources if is_set]

    if len(selected) > 1:
        raise ValueError(
            f"Provide only one of file_path, file_content_base64, or "
            f"file_content_text (got: {', '.join(selected)})."
        )

    if not selected:
        return {}

    if file_path is not None:
        return {"file_path": str(resolve_local_path(file_path))}

    if not file_name:
        raise ValueError(
            "file_name (with extension, e.g. 'data.cif') is required "
            "when using file_content_base64 or file_content_text."
        )

    if file_content_base64 is not None:
        content = b64decode(file_content_base64)
    else:
        content = file_content_text.encode("utf-8")

    return {"file_content": content, "file_name": file_name}


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_file(
        name: Annotated[str, Field(description="File asset name")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        team_id: Annotated[str, Field(description="Team UUID")],
        ctx: Context,
        file_path: Annotated[
            Optional[str],
            Field(description="Absolute local filesystem path to upload"),
        ] = None,
        file_content_base64: Annotated[
            Optional[str],
            Field(description="Base64-encoded file bytes (for binary files)"),
        ] = None,
        file_content_text: Annotated[
            Optional[str],
            Field(description="Plain-text file content (for text files like CIF, JSON, CSV)"),
        ] = None,
        file_name: Annotated[
            Optional[str],
            Field(
                description=(
                    "Original filename with extension, e.g. 'structure.cif'. "
                    "Required when using file_content_base64 or file_content_text."
                )
            ),
        ] = None,
        visibility: Annotated[str, Field(description='"public" | "private" | "organization"')] = "private",
        description: Annotated[Optional[str], Field(description="File description")] = None,
    ) -> str:
        """Upload a file as an asset on Ouro.

        Provide the file via **one** of:
        - file_path — a local filesystem path (works when the MCP server
          can access the file directly).
        - file_content_base64 — base64-encoded bytes (for binary files
          like images or PDFs from a remote/sandboxed client).
        - file_content_text — plain-text content (for text files like
          CIF, JSON, CSV, etc.).

        When using file_content_base64 or file_content_text, also pass
        file_name with the original filename and extension so MIME type
        can be detected.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        file_kwargs = _resolve_file_input(
            file_path=file_path,
            file_content_base64=file_content_base64,
            file_content_text=file_content_text,
            file_name=file_name,
        )

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            description=description,
            org_id=org_id,
            team_id=team_id,
            **file_kwargs,
        )

        return json.dumps(file_result(file))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_file(
        id: Annotated[str, Field(description="File asset UUID")],
        ctx: Context,
        file_path: Annotated[
            Optional[str],
            Field(description="Local path to replacement file"),
        ] = None,
        file_content_base64: Annotated[
            Optional[str],
            Field(description="Base64-encoded replacement file bytes"),
        ] = None,
        file_content_text: Annotated[
            Optional[str],
            Field(description="Plain-text replacement file content"),
        ] = None,
        file_name: Annotated[
            Optional[str],
            Field(
                description=(
                    "Original filename with extension. Required when "
                    "using file_content_base64 or file_content_text."
                )
            ),
        ] = None,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        description: Annotated[Optional[str], Field(description="New description")] = None,
        visibility: Annotated[Optional[str], Field(description='"public" | "private" | "organization"')] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
    ) -> str:
        """Update a file's content or metadata.

        To replace the file data, provide one of file_path,
        file_content_base64, or file_content_text (see create_file for
        details).  Pass name, description, or visibility to update
        metadata only.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        file_kwargs = _resolve_file_input(
            file_path=file_path,
            file_content_base64=file_content_base64,
            file_content_text=file_content_text,
            file_name=file_name,
        )

        file = ouro.files.update(
            id,
            **file_kwargs,
            **optional_kwargs(
                name=name,
                description=description,
                visibility=visibility,
                org_id=org_id,
                team_id=team_id,
            ),
        )

        return json.dumps(file_result(file))
