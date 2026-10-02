"""Post tools — create and update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    resolve_location,
    PRICE_CURRENCY_DESC,
    PRICE_SATS_DESC,
    PRICE_USD_DESC,
    UNLOCK_PRICE_DESC,
    content_from_markdown,
    dump_json,
    format_asset_summary,
    discard_upload,
    optional_kwargs,
    read_upload,
    resolve_local_path,
    source_names,
    unlock_pricing_kwargs,
)
from pydantic import Field


_MARKDOWN_SUFFIXES = (".md", ".markdown")


def _post_sources() -> str:
    return source_names("content_path", "content_markdown", "upload_id")


def _resolve_post_markdown(
    content_markdown: Optional[str],
    content_path: Optional[str],
    upload_id: Optional[str] = None,
    ouro: Any = None,
) -> Optional[str]:
    # Some clients send "" for optional fields they meant to leave unset; treat blanks as absent.
    if content_markdown is not None and not content_markdown.strip():
        content_markdown = None
    if content_path is not None and not content_path.strip():
        content_path = None
    if upload_id is not None and not upload_id.strip():
        upload_id = None

    provided = [
        ("content_markdown", content_markdown is not None),
        ("content_path", content_path is not None),
        ("upload_id", upload_id is not None),
    ]
    selected = [name for name, is_set in provided if is_set]
    if len(selected) > 1:
        raise ValueError(f"Provide only one of {_post_sources()} (got: {', '.join(selected)}).")

    if upload_id is not None:
        return read_upload(ouro, upload_id, _MARKDOWN_SUFFIXES).decode("utf-8")

    if content_path is None:
        return content_markdown

    path = resolve_local_path(content_path)
    if not path.exists():
        raise ValueError(f"content_path not found: {content_path} (resolved to {path})")
    if not path.is_file():
        raise ValueError(f"content_path must point to a file: {content_path} (resolved to {path})")
    if path.suffix.lower() not in _MARKDOWN_SUFFIXES:
        raise ValueError("content_path must be a .md or .markdown file.")

    return path.read_text(encoding="utf-8")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_post(
        name: Annotated[str, Field(description="Post title")],
        ctx: Context,
        org_id: Annotated[
            Optional[str],
            Field(description="Organization UUID. Omit when the server is pinned to an organization"),
        ] = None,
        team_id: Annotated[
            Optional[str],
            Field(description="Team UUID. Omit to use the pinned organization's default team"),
        ] = None,
        content_markdown: Annotated[
            Optional[str],
            Field(description="Extended markdown body (syntax in the tool description)"),
        ] = None,
        content_path: Annotated[Optional[str], Field(description="Local .md/.markdown file path")] = None,
        upload_id: Annotated[
            Optional[str],
            Field(description="upload_id from create_upload_url for a .md file holding the body"),
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
        description: Annotated[Optional[str], Field(description="Short description/subtitle")] = None,
        license_id: Annotated[Optional[str], Field(description="Asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Top-level provenance object; separate from asset metadata"),
        ] = None,
    ) -> str:
        """Create a new post on Ouro from extended markdown. Provide content_markdown, content_path, or upload_id.

        For a long post, keep the markdown in a local file, upload it with
        create_upload_url and pass upload_id, so the body is not written out again.

        Extended markdown is standard markdown plus:
        - Mentions: @username
        - LaTeX: \\(inline\\) and \\[display\\]
        - Inline links: [label](post:|file:|dataset:|route:|service:|quest:<uuid>), [label](action:<uuid>)
          for route runs, or [label](asset:<uuid>) when the type is unknown. Do not invent URL paths.
        - Block embeds:
        ```assetComponent
        {"id":"<uuid>","assetType":"post"|"file"|"dataset"|"route"|"service","viewMode":"preview"|"card","displayConfig":{"visualizationId":"<uuid>|null","actionId":"<uuid>|null"}}
        ```
        - Images: an image is a file asset. Create it with create_file, then put
          ![alt](file:<uuid>) on a line of its own to show it. An external image URL becomes a link.
        displayConfig is optional. For datasets, set visualizationId to render a specific saved view.
        For routes, set actionId to show a compact action receipt (status, timing, output).
        Prefer paste embed_markdown / link_markdown from route-action tools when referencing a run.
        @mentions on private or organization-only posts do not notify the mentioned
        user unless they can already discover the post — share it first if you want a response.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        org_id, team_id = resolve_location(ouro, org_id, team_id)

        markdown = _resolve_post_markdown(
            content_markdown=content_markdown,
            content_path=content_path,
            upload_id=upload_id,
            ouro=ouro,
        )
        if markdown is None:
            raise ValueError(f"No post body provided. Pass one of: {_post_sources()}.")

        content = content_from_markdown(ouro, markdown)

        post = ouro.posts.create(
            content=content,
            name=name,
            visibility=visibility,
            description=description,
            org_id=org_id,
            team_id=team_id,
            license_id=license_id,
            attribution=attribution,
            **unlock_pricing_kwargs(
                visibility, price, price_currency, price_usd, price_sats
            ),
        )

        discard_upload(ouro, upload_id)
        return dump_json(format_asset_summary(post))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_post(
        id: Annotated[str, Field(description="Post UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New title")] = None,
        content_markdown: Annotated[
            Optional[str],
            Field(description="Replacement body in extended markdown (same syntax as create_post)"),
        ] = None,
        content_path: Annotated[
            Optional[str],
            Field(description="Local .md/.markdown file with replacement body"),
        ] = None,
        upload_id: Annotated[
            Optional[str],
            Field(description="upload_id from create_upload_url for a .md file with the replacement body"),
        ] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization" | "monetized"')
        ] = None,
        price: Annotated[Optional[float], Field(description=UNLOCK_PRICE_DESC)] = None,
        price_currency: Annotated[
            Optional[Literal["usd", "btc"]], Field(description=PRICE_CURRENCY_DESC)
        ] = None,
        price_usd: Annotated[Optional[float], Field(description=PRICE_USD_DESC)] = None,
        price_sats: Annotated[Optional[int], Field(description=PRICE_SATS_DESC)] = None,
        description: Annotated[Optional[str], Field(description="New description/subtitle")] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
        license_id: Annotated[Optional[str], Field(description="New asset license identifier")] = None,
        attribution: Annotated[
            Optional[dict[str, Any]],
            Field(description="Updated top-level provenance object"),
        ] = None,
    ) -> str:
        """Update a post's content or metadata. Pass content_markdown, content_path, or upload_id to replace the body.

        The body uses the same extended markdown as create_post. To edit a
        long post, change the local markdown file, upload it with
        create_upload_url and pass upload_id.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        markdown = _resolve_post_markdown(
            content_markdown=content_markdown,
            content_path=content_path,
            upload_id=upload_id,
            ouro=ouro,
        )
        content = content_from_markdown(ouro, markdown) if markdown is not None else None

        post = ouro.posts.update(
            id,
            content=content,
            **optional_kwargs(
                name=name,
                visibility=visibility,
                description=description,
                org_id=org_id,
                team_id=team_id,
                license_id=license_id,
                attribution=attribution,
            ),
            **unlock_pricing_kwargs(
                visibility, price, price_currency, price_usd, price_sats
            ),
        )

        discard_upload(ouro, upload_id)
        return dump_json(format_asset_summary(post))
