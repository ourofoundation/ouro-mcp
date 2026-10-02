"""File tools — create and update."""

from __future__ import annotations

import json
import shlex
from base64 import b64decode
from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    resolve_location,
    PRICE_CURRENCY_DESC,
    PRICE_SATS_DESC,
    PRICE_USD_DESC,
    UNLOCK_PRICE_DESC,
    dump_json,
    file_result,
    optional_kwargs,
    local_files_enabled,
    resolve_local_path,
    source_names,
    unlock_pricing_kwargs,
)
from pydantic import Field


def _source_names() -> str:
    return source_names("file_path", "file_content_base64", "file_content_text", "upload_id")


def _resolve_file_input(
    *,
    file_path: Optional[str] = None,
    file_content_base64: Optional[str] = None,
    file_content_text: Optional[str] = None,
    file_name: Optional[str] = None,
    upload_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return SDK kwargs for the file upload source.

    Exactly one of ``file_path``, ``file_content_base64``,
    ``file_content_text``, or ``upload_id`` must be provided.  When using
    inline content, ``file_name`` (with extension) is required for MIME-type
    detection.

    Returns a dict that can be spread into ``ouro.files.create()`` /
    ``ouro.files.update()`` (keys: ``file_path``, ``upload_id``, *or*
    ``file_content`` + ``file_name``).
    """
    sources = [
        ("file_path", file_path is not None),
        ("file_content_base64", file_content_base64 is not None),
        ("file_content_text", file_content_text is not None),
        ("upload_id", upload_id is not None),
    ]
    selected = [name for name, is_set in sources if is_set]

    if len(selected) > 1:
        raise ValueError(
            f"Provide only one of {_source_names()} (got: {', '.join(selected)})."
        )

    if not selected:
        return {}

    if upload_id is not None:
        return optional_kwargs(upload_id=upload_id, file_name=file_name)

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


def upload_command(upload: dict[str, Any], file_name: str) -> str:
    """The shell command that sends a local file to a signed upload URL."""
    headers = " ".join(
        f"-H {shlex.quote(f'{key}: {value}')}" for key, value in upload["headers"].items()
    )
    return (
        f"curl -sS -f -o /dev/null -X {upload['method']} {headers} "
        f"--data-binary @{shlex.quote(file_name)} {shlex.quote(upload['upload_url'])}"
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_upload_url(
        file_name: Annotated[
            str,
            Field(description="Filename with extension, e.g. 'plot.png'; sets the content type"),
        ],
        ctx: Context,
    ) -> str:
        """Get a signed URL to upload a local file to, then use it by its upload_id.

        Use this for anything on your own machine that is binary or long, so
        you do not have to write its contents out again:
        - any file, as a file asset: create_file / update_file
        - a post's markdown: create_post / update_post
        - dataset rows (.csv, .json, .jsonl, .parquet): create_dataset / update_dataset
        - an OpenAPI spec: create_service / update_service (``spec_upload_id``)

        1. Call this tool with the file's name.
        2. Run the returned ``command`` in a shell, from the file's directory
           (or change the path after ``@``). It prints nothing on success.
        3. Pass ``upload_id`` to one of the tools above. Each upload is used once.

        The URL works for ``expires_in`` seconds and needs no other credentials,
        so treat it as a secret.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        upload = ouro.files.create_upload_url(file_name)
        return dump_json(
            {
                "upload_id": upload["upload_id"],
                "command": upload_command(upload, file_name),
                "upload_url": upload["upload_url"],
                "method": upload["method"],
                "headers": upload["headers"],
                "expires_in": upload["expires_in"],
            }
        )

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_file(
        name: Annotated[str, Field(description="File asset name")],
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Organization UUID. Omit when the server is pinned to an organization"),
        ] = None,
        team_id: Annotated[
            Optional[str],
            Field(description="Team UUID. Omit to use the pinned organization's default team"),
        ] = None,
        file_path: Annotated[
            Optional[str],
            Field(
                description=(
                    "Path to the file on disk. Prefer paths relative to WORKSPACE_ROOT. "
                    "Absolute paths under WORKSPACE_MOUNT (Docker) are remapped onto "
                    "WORKSPACE_ROOT; other absolute paths must already be inside the workspace."
                )
            ),
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
        upload_id: Annotated[
            Optional[str],
            Field(description="upload_id from create_upload_url, once the file has been uploaded to its upload_url"),
        ] = None,
        visibility: Annotated[
            Optional[str],
            Field(
                description='"public" | "private" | "organization" | "monetized". '
                "Omit to follow the team: public in a public team, organization in an internal one"
            ),
        ] = None,
        price: Annotated[Optional[float], Field(description=UNLOCK_PRICE_DESC)] = None,
        price_currency: Annotated[
            Optional[Literal["usd", "btc"]], Field(description=PRICE_CURRENCY_DESC)
        ] = None,
        price_usd: Annotated[Optional[float], Field(description=PRICE_USD_DESC)] = None,
        price_sats: Annotated[Optional[int], Field(description=PRICE_SATS_DESC)] = None,
        description: Annotated[Optional[str], Field(description="File description")] = None,
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Top-level provenance object; separate from file metadata"),
        ] = None,
    ) -> str:
        """Upload a file as an asset on Ouro.

        Provide the file via **one** of:
        - file_path — relative paths are resolved from WORKSPACE_ROOT.
        - file_content_base64 — inline bytes (e.g. remote clients).
        - file_content_text — inline text (CIF, JSON, CSV, etc.).
        - upload_id — bytes already uploaded with create_upload_url. Use it
          for images, PDFs, archives and anything large.

        When using file_content_base64 or file_content_text, also pass
        file_name with the original filename and extension so MIME type
        can be detected.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        org_id, team_id = resolve_location(ouro, org_id, team_id)

        file_kwargs = _resolve_file_input(
            file_path=file_path,
            file_content_base64=file_content_base64,
            file_content_text=file_content_text,
            file_name=file_name,
            upload_id=upload_id,
        )

        if not file_kwargs:
            message = f"create_file requires exactly one of: {_source_names()}."
            if local_files_enabled():
                message += (
                    " For file_path: use the same path you used with run_python write_file"
                    " (relative paths resolve against WORKSPACE_ROOT)."
                )
            raise ValueError(
                message + " For inline content, file_name (e.g. 'structure.cif') is required."
            )

        file = ouro.files.create(
            name=name,
            visibility=visibility,
            description=description,
            org_id=org_id,
            team_id=team_id,
            license_id=license_id,
            attribution=attribution,
            **file_kwargs,
            **unlock_pricing_kwargs(
                visibility, price, price_currency, price_usd, price_sats
            ),
        )

        return dump_json(file_result(file))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_file(
        id: Annotated[str, Field(description="File asset UUID")],
        ctx: Context,
        file_path: Annotated[
            Optional[str],
            Field(
                description=(
                    "Path to replacement file; relative paths resolve against WORKSPACE_ROOT " "(same as create_file)."
                )
            ),
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
                    "Original filename with extension. Required when " "using file_content_base64 or file_content_text."
                )
            ),
        ] = None,
        upload_id: Annotated[
            Optional[str],
            Field(description="upload_id from create_upload_url holding the replacement bytes"),
        ] = None,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        description: Annotated[Optional[str], Field(description="New description")] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization" | "monetized"')
        ] = None,
        price: Annotated[Optional[float], Field(description=UNLOCK_PRICE_DESC)] = None,
        price_currency: Annotated[
            Optional[Literal["usd", "btc"]], Field(description=PRICE_CURRENCY_DESC)
        ] = None,
        price_usd: Annotated[Optional[float], Field(description=PRICE_USD_DESC)] = None,
        price_sats: Annotated[Optional[int], Field(description=PRICE_SATS_DESC)] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
        license_id: Annotated[Optional[str], Field(description="New asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Updated top-level provenance object"),
        ] = None,
    ) -> str:
        """Update a file's content or metadata.

        To replace the file data, provide one of file_path,
        file_content_base64, file_content_text, or upload_id (see
        create_file for details).  Pass name, description, visibility, or
        pricing to update metadata only.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        file_kwargs = _resolve_file_input(
            file_path=file_path,
            file_content_base64=file_content_base64,
            file_content_text=file_content_text,
            file_name=file_name,
            upload_id=upload_id,
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
                license_id=license_id,
                attribution=attribution,
            ),
            **unlock_pricing_kwargs(
                visibility, price, price_currency, price_usd, price_sats
            ),
        )

        return dump_json(file_result(file))
