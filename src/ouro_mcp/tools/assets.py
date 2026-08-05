"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro_mcp.config import CommentPreviewConfig, get_comment_preview_config
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    dump_json,
    format_asset_summary,
    format_search_hit,
    markdown_bullet,
    markdown_id,
    optional_kwargs,
    present_kwargs,
    render_markdown_list,
    render_markdown_sections,
    route_input_assets_summary,
    route_output_assets_summary,
    route_request_body_without_input_assets,
    search_hit_line,
    slim_asset_tags,
    slim_connection_graph,
    slim_dataset_schema,
    strip_heavy_fields,
    truncate_response,
    user_summary,
)
from pydantic import Field

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_asset(
        id: Annotated[str, Field(description="UUID of any asset type")],
        ctx: Context,
        detail: Annotated[
            str,
            Field(
                description=(
                    '"summary" (default) returns name, short description, visibility, '
                    "flat username/org_id/team_id, and engagement counts. "
                    '"full" also includes type-specific content '
                    "(post body, dataset schema/stats, file download URL, service routes, route execution schemas), "
                    "plus a compact creation_action pointer (action_id/status/route_id), "
                    "lineage connections (comment edges omitted — see comments preview / get_comments), "
                    "tags, and a bounded comments/replies preview when present. "
                    "Use list_asset_actions(role=\"output\") for the full producer action."
                )
            ),
        ] = "summary",
    ) -> str:
        """Get any asset by ID.

        Use detail="summary" (default) when you only need to identify an asset.
        Use detail="full" to read its content (e.g. post body, dataset schema).
        Both levels include engagement counts (views, comments, reactions, downloads).
        Full detail also includes a small comments preview when comments exist.
        """
        allowed_detail = {"summary", "full"}
        if detail not in allowed_detail:
            raise ValueError(f"Invalid detail={detail!r}. Must be one of: " f"{sorted(allowed_detail)}.")

        ouro = ctx.request_context.lifespan_context.ouro
        asset = ouro.assets.retrieve(id)
        if detail == "full":
            result = strip_heavy_fields(_format_asset_detail(asset, ouro))
        else:
            result = format_asset_summary(asset)
        _enrich_counts(result, ouro, id)
        return dump_json(result)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_assets(
        ctx: Context,
        query: Annotated[str, Field(description="Search query or UUID for direct lookup")] = "",
        asset_type: Annotated[
            Optional[str], Field(description='"dataset" | "post" | "file" | "service" | "route" | "quest"')
        ] = None,
        scope: Annotated[Optional[str], Field(description='"personal" | "org" | "global" | "all"')] = None,
        org_id: Annotated[Optional[str], Field(description="Organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Team UUID")] = None,
        user_id: Annotated[Optional[str], Field(description="Asset owner UUID")] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization" | "monetized"')
        ] = None,
        file_type: Annotated[
            Optional[str], Field(description='File category: "image" | "video" | "audio" | "pdf"')
        ] = None,
        extension: Annotated[Optional[str], Field(description='File extension, e.g. "csv", "json", "png"')] = None,
        metadata_filters: Annotated[
            Optional[Any],
            Field(description='Metadata key/value filters as JSON object or string, e.g. \'{"key": "value"}\''),
        ] = None,
        sort: Annotated[
            Optional[str],
            Field(
                description='"relevant" (default with query) | "recent" (default without query) | "popular" | "updated"'
            ),
        ] = None,
        time_window: Annotated[
            Optional[str],
            Field(description='For sort="popular": "day" | "week" | "month" (default) | "all"'),
        ] = None,
        limit: Annotated[int, Field(description="Max results to return")] = 10,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """Search or browse assets on Ouro. Supports chunk-level hybrid semantic + full-text search.

        Returns compact markdown discovery rows (id, asset_type, name, description,
        username, created_at). Query hits may also include a snippet for the
        matching passage. Call `get_asset` for full detail (content, schemas,
        download URLs, etc.).
        Without a query: returns recent assets by creation date.
        With a UUID as query: direct asset lookup.
        Use sort="popular" to find the most engaged assets (by views, reactions, comments, downloads, uses).
        Combine with time_window to scope popularity to a time period.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        merged_metadata: dict[str, Any] = {}
        if metadata_filters:
            if isinstance(metadata_filters, dict):
                merged_metadata.update(metadata_filters)
            elif isinstance(metadata_filters, str):
                try:
                    parsed = json.loads(metadata_filters)
                    if isinstance(parsed, dict):
                        merged_metadata.update(parsed)
                except (json.JSONDecodeError, TypeError):
                    log.warning("Ignoring invalid metadata_filters JSON: %s", metadata_filters)
        if file_type:
            merged_metadata["file_type"] = file_type
        if extension:
            merged_metadata["extension"] = extension

        # Models often fill unused optionals with ""; blank UUID filters 500
        # in Postgres ("invalid input syntax for type uuid: \"\"").
        response = ouro.assets.search(
            query,
            limit=limit,
            offset=offset,
            with_pagination=True,
            **present_kwargs(
                asset_type=asset_type,
                scope=scope,
                org_id=org_id,
                team_id=team_id,
                user_id=user_id,
                visibility=visibility,
                metadata_filters=merged_metadata or None,
                sort=sort,
                time_window=time_window,
            ),
        )

        assets = [format_search_hit(item) for item in response.get("data", [])]

        return render_markdown_list(
            assets,
            line_fn=search_hit_line,
            pagination=response.get("pagination") or {},
            offset=offset,
            noun="assets",
            empty_text="No assets found.",
        )

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    @handle_ouro_errors
    def delete_asset(
        id: Annotated[str, Field(description="UUID of the asset to delete")],
        ctx: Context,
        delete_children: Annotated[
            bool | None,
            Field(
                description=(
                    "Also delete child assets linked via parent_id "
                    "(e.g. service routes, post embedded assets). "
                    "Defaults to true for services, false otherwise."
                )
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(
                description=(
                    "If true, return the delete summary (including children "
                    "that would be removed) without deleting anything."
                )
            ),
        ] = False,
    ) -> str:
        """Delete an asset by ID. Auto-detects the asset type and routes to the appropriate delete method.

        Returns a summary of the deleted asset and any deleted children
        (id, name, asset_type). Pass dry_run=true to preview first.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        asset = ouro.assets.retrieve(id)
        asset_type = asset.asset_type
        name = asset.name

        if delete_children is None:
            effective_delete_children = asset_type == "service"
        else:
            effective_delete_children = delete_children

        delete_kwargs = {
            "delete_children": effective_delete_children,
            "dry_run": dry_run,
        }

        if asset_type == "dataset":
            result = ouro.datasets.delete(id, **delete_kwargs)
        elif asset_type == "post":
            result = ouro.posts.delete(id, **delete_kwargs)
        elif asset_type == "file":
            result = ouro.files.delete(id, **delete_kwargs)
        elif asset_type == "quest":
            result = ouro.quests.delete(id, **delete_kwargs)
        elif asset_type == "service":
            result = ouro.services.delete(id, **delete_kwargs)
        else:
            return dump_json(
                {
                    "error": "unsupported_type",
                    "message": f"Cannot delete asset of type '{asset_type}' via this tool.",
                }
            )

        deleted_children = (result or {}).get("deleted_children") or []
        payload = {
            "deleted": not dry_run,
            "id": (result or {}).get("id") or id,
            "name": (result or {}).get("name") or name,
            "asset_type": (result or {}).get("asset_type") or asset_type,
            "deleted_children": deleted_children,
            "deleted_children_count": len(deleted_children),
        }
        if dry_run:
            payload["dry_run"] = True
        return dump_json(payload)

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    @handle_ouro_errors
    def share_asset(
        id: Annotated[str, Field(description="UUID of the asset to share")],
        user_id: Annotated[
            str, Field(description="UUID of the user to grant access to")
        ],
        ctx: Context,
        role: Annotated[
            str,
            Field(description='"read" (default) | "write" | "admin"'),
        ] = "read",
    ) -> str:
        """Grant a user direct permission on an asset.

        Private assets are invisible to others until shared. Mentions, links,
        and embeds do not grant access — call this when someone needs to read
        or edit a private asset you own.
        """
        allowed_roles = {"read", "write", "admin"}
        if role not in allowed_roles:
            raise ValueError(
                f"Invalid role={role!r}. Must be one of: {sorted(allowed_roles)}."
            )

        ouro = ctx.request_context.lifespan_context.ouro
        ouro.assets.share(id, user_id, role=role)
        return dump_json({"id": id, "user_id": user_id, "role": role})

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def download_asset(
        id: Annotated[str, Field(description="UUID of the asset to download")],
        output_path: Annotated[
            str,
            Field(
                description=(
                    "Local file path or existing directory where the asset should be saved. "
                    "Relative paths resolve against WORKSPACE_ROOT; when WORKSPACE_ROOT is set, "
                    "the path must stay inside it (no '..' traversal or outside-root absolutes)."
                )
            ),
        ],
        ctx: Context,
        asset_type: Annotated[
            Optional[str],
            Field(description='Optional override: "file" | "dataset" | "post"'),
        ] = None,
    ) -> str:
        """Download an asset to the local filesystem.

        Files keep their original bytes, datasets download as CSV, and posts as HTML.
        If output_path is a directory, the server-provided filename is used.

        When WORKSPACE_ROOT is set (agent context), output_path is sandboxed
        to that workspace; paths that escape via '..' or absolute paths
        outside the workspace are rejected.
        """
        from ouro_mcp.utils import resolve_local_path

        ouro = ctx.request_context.lifespan_context.ouro
        resolved_path = str(resolve_local_path(output_path))
        result = ouro.assets.download(id, output_path=resolved_path, asset_type=asset_type)
        return dump_json(
            {
                "downloaded": True,
                **result,
            }
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_asset_connections(
        id: Annotated[str, Field(description="UUID of the asset")],
        ctx: Context,
    ) -> str:
        """Get the connection graph for an asset.

        Returns relationships like references, components, derivatives,
        and action inputs/outputs as compact markdown sections. Useful for
        understanding how assets relate to each other and navigating lineage.
        Each item is the connected asset summary with id, name, asset_type,
        and asset created_at when available. For action edges, action_id is
        included when the backend provides it so you can follow up with
        get_action or list_asset_actions.

        Comment edges are omitted — use ``get_comments`` (or the comments
        preview on ``get_asset(detail=\"full\")``). For datasets, outgoing
        ``reference`` edges (row-level file/action IDs) are also omitted —
        use the dataset schema / ``query_dataset`` instead. Incoming
        references and other connection types are still returned.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        omit_outgoing_refs = False
        try:
            omit_outgoing_refs = getattr(ouro.assets.retrieve(id), "asset_type", None) == "dataset"
        except Exception:
            log.debug("Failed to resolve asset type for connections on %s", id, exc_info=True)
        connections = slim_connection_graph(
            ouro.assets.connections(id),
            current_asset_id=id,
            omit_outgoing_references=omit_outgoing_refs,
            omit_comments=True,
        )
        if not isinstance(connections, dict):
            connections = {"connections": list(connections or [])}

        def _connection_line(row: Any) -> str:
            if not isinstance(row, dict):
                return markdown_bullet(str(row))
            name = row.get("name") or "(unnamed)"
            parts = [markdown_id(row.get("id"))]
            if row.get("action_id"):
                parts.append(f"action_id: `{row['action_id']}`")
            created = row.get("created_at")
            if created:
                parts.append(str(created))
            return markdown_bullet(str(name), *parts, kind=row.get("asset_type"))

        return truncate_response(
            render_markdown_sections(
                connections,
                line_fn=_connection_line,
                preamble=f"Connections for asset `{id}`",
                empty_text="No connections.",
            )
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_asset_actions(
        asset_id: Annotated[str, Field(description="Asset UUID")],
        ctx: Context,
        role: Annotated[
            str,
            Field(
                description=(
                    '"both" (default): created_by + as_input. '
                    '"input": only actions that used this asset as input. '
                    '"output": only the action that produced this asset.'
                )
            ),
        ] = "both",
        status: Annotated[
            Optional[str],
            Field(
                description=(
                    'Optional filter on as_input / input: "queued" | "in-progress" | '
                    '"success" | "error" | "timed-out"'
                )
            ),
        ] = None,
        include_response: Annotated[
            bool,
            Field(
                description="Include each action response payload. Leave false for compact browsing."
            ),
        ] = False,
        limit: Annotated[
            int,
            Field(description="Max as_input actions to return (1-200)"),
        ] = 20,
        offset: Annotated[int, Field(description="Pagination offset for as_input")] = 0,
    ) -> str:
        """List route actions linked to an asset.

        Prefer this over scraping posts for action IDs. Returns compact
        markdown. ``created_by`` is the action that produced the asset (if
        any). ``as_input`` is the list of executions that used the asset as
        an input — use this to find which routes ran on a file or dataset
        and to get embed/status/response.
        """
        from ouro_mcp.tools.services import (
            _format_action_summary,
            action_summary_line,
        )

        allowed_roles = {"input", "output", "both"}
        if role not in allowed_roles:
            raise ValueError(
                f"Invalid role={role!r}. Must be one of: {sorted(allowed_roles)}."
            )
        if limit <= 0 or limit > 200:
            raise ValueError("limit must be between 1 and 200.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")
        if status:
            allowed_status = {
                "queued",
                "in-progress",
                "success",
                "error",
                "timed-out",
            }
            if status not in allowed_status:
                raise ValueError(
                    f"Invalid status={status!r}. Must be one of: {sorted(allowed_status)}."
                )

        ouro = ctx.request_context.lifespan_context.ouro
        bundle = ouro.assets.actions(
            asset_id,
            role=role,  # type: ignore[arg-type]
            include_response=include_response,
            limit=limit,
            offset=offset,
            **optional_kwargs(status=status),
        )
        created_by = bundle.get("created_by")
        as_input = list(bundle.get("as_input") or [])
        pagination = bundle.get("pagination") or {}

        sections: dict[str, list[Any]] = {}
        if role in {"output", "both"}:
            sections["created_by"] = (
                [_format_action_summary(created_by, include_response=include_response)]
                if created_by is not None
                else []
            )
        if role in {"input", "both"}:
            sections["as_input"] = [
                _format_action_summary(action, include_response=include_response)
                for action in as_input
            ]

        extras: list[str] = [f"asset_id: `{asset_id}`", f"role: {role}"]
        if role in {"input", "both"}:
            has_more = bool(pagination.get("hasMore"))
            extras.append(f"as_input showing {len(as_input)} (offset={offset}, limit={limit})")
            if has_more:
                extras.append(
                    f"more as_input available — call again with offset={offset + len(as_input)}"
                )

        return truncate_response(
            render_markdown_sections(
                sections,
                line_fn=action_summary_line,
                preamble=" · ".join(extras),
                empty_text="No linked actions.",
            )
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_compatible_routes(
        id: Annotated[str, Field(description="UUID of the asset")],
        ctx: Context,
        sort: Annotated[
            str,
            Field(description='"popular" (default, most used first) | "recent" | "updated"'),
        ] = "popular",
        limit: Annotated[int, Field(description="Max routes to return (1-200)")] = 10,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
    ) -> str:
        """Find routes that can operate on this asset.

        Returns routes whose input type is compatible with the given asset,
        answering the question "what can I do with this asset?". Defaults to
        popularity order so the most-used routes appear first.
        """
        allowed_sort = {"popular", "recent", "updated"}
        if sort not in allowed_sort:
            raise ValueError(f"Invalid sort={sort!r}. Must be one of: {sorted(allowed_sort)}.")
        if limit <= 0 or limit > 200:
            raise ValueError("limit must be between 1 and 200.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")

        ouro = ctx.request_context.lifespan_context.ouro
        page = ouro.assets.compatible_routes(
            id,
            limit=limit,
            offset=offset,
            sort=sort,
            with_pagination=True,
        )
        routes = page.get("data") or []
        results = []
        for r in routes:
            entry: dict[str, Any] = {
                "id": str(r.get("id", "")),
                "name": r.get("name"),
                "asset_type": r.get("asset_type", "route"),
            }
            if r.get("description"):
                desc = r["description"]
                if isinstance(desc, dict):
                    entry["description"] = desc.get("text", "")[:200]
                else:
                    entry["description"] = str(desc)[:200]
            results.append(entry)

        def _route_line(row: dict[str, Any]) -> str:
            return markdown_bullet(
                str(row.get("name") or "(untitled)"),
                markdown_id(row.get("id")),
                kind=row.get("asset_type") or "route",
                body=row.get("description"),
            )

        return render_markdown_list(
            results,
            line_fn=_route_line,
            pagination=page.get("pagination") or {},
            offset=offset,
            noun="compatible routes",
            empty_text="No compatible routes.",
            extras=[f"asset_id: `{id}`", f"sort: {sort}"],
        )


def _enrich_counts(result: dict, ouro: Any, asset_id: str) -> None:
    """Best-effort merge of engagement counts into an asset result dict.

    Zero-valued counters are dropped — most assets have zero of at least one
    metric (brand-new assets often have zero of all four), and emitting a
    full `{views:0, comments:0, reactions:0, downloads:0}` block on every
    summary wastes ~60 chars per asset. Agents can treat a missing key as 0.
    """
    try:
        counts = ouro.assets.counts(asset_id)
    except Exception:
        log.debug("Failed to fetch counts for asset %s", asset_id, exc_info=True)
        return

    if not counts:
        return

    nonzero = {
        k: counts.get(k, 0)
        for k in ("views", "comments", "reactions", "downloads")
        if counts.get(k, 0)
    }
    if nonzero:
        result["counts"] = nonzero


def _compact_creation_action(created_by: Any) -> dict[str, Any] | None:
    """Pointer-only producer action for get_asset(detail=full).

    Full action payloads (response, assets, metadata) belong on
    ``list_asset_actions`` / ``get_action``, not inline on every full asset read.
    """
    if created_by is None:
        return None
    if hasattr(created_by, "model_dump"):
        raw = created_by.model_dump(mode="json")
    elif isinstance(created_by, dict):
        raw = created_by
    else:
        raw = {
            "id": getattr(created_by, "id", None),
            "status": getattr(created_by, "status", None),
            "route_id": getattr(created_by, "route_id", None),
        }
        route = getattr(created_by, "route", None)
        if route is not None and raw.get("route_id") is None:
            raw["route_id"] = getattr(route, "id", None) or (
                route.get("id") if isinstance(route, dict) else None
            )

    action_id = raw.get("id") or raw.get("action_id")
    if not action_id:
        return None
    pointer: dict[str, Any] = {"action_id": str(action_id)}
    status = raw.get("status") or raw.get("action_status")
    if status:
        pointer["action_status"] = status
    route_id = raw.get("route_id") or (raw.get("route") or {}).get("id")
    if route_id:
        pointer["route_id"] = str(route_id)
    return pointer


def _enrich_provenance(result: dict, ouro: Any, asset_id: str) -> None:
    """Best-effort merge of provenance, connections, and tags into an asset result dict."""
    try:
        bundle = ouro.assets.actions(asset_id, role="output")
        created_by = bundle.get("created_by") if isinstance(bundle, dict) else None
        pointer = _compact_creation_action(created_by)
        if pointer:
            result["creation_action"] = pointer
    except Exception:
        log.debug("Failed to fetch creation action for %s", asset_id, exc_info=True)

    try:
        connections = ouro.assets.connections(asset_id)
        slimmed = slim_connection_graph(
            connections,
            current_asset_id=asset_id,
            # Dataset→row-ref edges duplicate IDs already in the table and
            # routinely number in the thousands; keep only incoming refs.
            omit_outgoing_references=result.get("asset_type") == "dataset",
            # Comment stubs duplicate the bounded `comments` preview (and
            # `get_comments`); omit them so connections stay lineage-focused.
            omit_comments=True,
        )
        if slimmed:
            result["connections"] = slimmed
    except Exception:
        log.debug("Failed to fetch connections for %s", asset_id, exc_info=True)

    try:
        tags = slim_asset_tags(ouro.assets.tags(asset_id))
        if tags:
            result["tags"] = tags
    except Exception:
        log.debug("Failed to fetch tags for %s", asset_id, exc_info=True)


def _comment_text(comment: Any, config: CommentPreviewConfig) -> str | None:
    content = getattr(comment, "content", None)
    text = getattr(content, "text", None) if content else None
    if not text or config.text_chars <= 0:
        return None
    return str(text)[: config.text_chars]


def _format_comment_preview(comment: Any, config: CommentPreviewConfig) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": str(comment.id)}
    if getattr(comment, "created_at", None):
        entry["created_at"] = comment.created_at.isoformat()
    user = user_summary(comment)
    if user:
        entry["author"] = user["username"]
    text = _comment_text(comment, config)
    if text:
        entry["text"] = text
    return entry


def _enrich_comments_preview(result: dict, ouro: Any, asset_id: str) -> None:
    """Attach a small comment/reply preview to full asset details.

    This is intentionally bounded. It gives agents enough context to notice
    existing replies without turning `get_asset(detail="full")` into a full
    thread dump; callers can still use `get_comments` for complete threads.
    """
    comments_client = getattr(ouro, "comments", None)
    if not comments_client:
        return
    config = get_comment_preview_config()
    if config.comment_limit <= 0:
        return

    try:
        comments = list(comments_client.list_by_parent(asset_id) or [])
    except Exception:
        log.debug("Failed to fetch comments for %s", asset_id, exc_info=True)
        return

    if not comments:
        return

    preview: list[dict[str, Any]] = []
    for comment in comments[: config.comment_limit]:
        entry = _format_comment_preview(comment, config)
        comment_id = entry.get("id")
        if comment_id and config.reply_limit > 0:
            try:
                replies = list(comments_client.list_by_parent(comment_id) or [])
            except Exception:
                log.debug("Failed to fetch replies for comment %s", comment_id, exc_info=True)
                replies = []
            if replies:
                entry["replies"] = [
                    _format_comment_preview(reply, config)
                    for reply in replies[: config.reply_limit]
                ]
                if len(replies) > config.reply_limit:
                    entry["reply_has_more"] = True
        preview.append(entry)

    result["comments"] = preview
    if len(comments) > config.comment_limit:
        result["comments_has_more"] = True


def _format_asset_detail(asset: Any, ouro: Any) -> dict:
    """Build a type-appropriate detail response for any asset."""
    base = format_asset_summary(asset)

    asset_id = str(asset.id)
    asset_type = asset.asset_type

    if asset_type == "dataset":
        try:
            base["schema"] = slim_dataset_schema(ouro.datasets.schema(asset_id))
        except Exception:
            log.debug("Failed to fetch schema for dataset %s", asset.id, exc_info=True)
            base["schema"] = None
        try:
            stats = ouro.datasets.stats(asset_id)
            base["stats"] = stats
        except Exception:
            log.debug("Failed to fetch stats for dataset %s", asset.id, exc_info=True)
            base["stats"] = None
        if asset.preview:
            base["preview"] = asset.preview[:5]

    elif asset_type in {"post", "comment"}:
        if asset.content:
            base["content_text"] = asset.content.text
        else:
            base["content_text"] = None

    elif asset_type == "file":
        if asset.data:
            base["file_url"] = asset.data.url
        if asset.metadata:
            meta = asset.metadata
            if hasattr(meta, "size"):
                base["size"] = meta.size
            if hasattr(meta, "type"):
                base["mime_type"] = meta.type

    elif asset_type == "service":
        try:
            routes = ouro.services.read_routes(asset_id)
            base["routes"] = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "description": r.route.description if r.route else None,
                }
                for r in routes
            ]
        except Exception:
            log.debug("Failed to fetch routes for service %s", asset.id, exc_info=True)
            base["routes"] = []

    elif asset_type == "route":
        if asset.route:
            base["route_description"] = asset.route.description
            base["parameters"] = asset.route.parameters
            base["request_body"] = route_request_body_without_input_assets(asset.route)
            base["input_assets"] = route_input_assets_summary(asset.route)
            base["output_assets"] = route_output_assets_summary(asset.route)
            base["output_type"] = asset.route.output_type

    elif asset_type == "quest":
        if asset.quest:
            base["quest"] = {
                "type": asset.quest.type,
                "status": asset.quest.status,
            }
        if asset.items:
            base["items"] = [
                {
                    "id": str(i.id),
                    "description": i.description,
                    "status": i.status,
                    **({"notes": i.notes} if i.notes else {}),
                    **({"assignee_id": str(i.assignee_id)} if i.assignee_id else {}),
                }
                for i in asset.items
            ]
        if asset.progress:
            base["progress"] = {
                "total": asset.progress.total,
                "resolved": asset.progress.resolved,
                "remaining": asset.progress.remaining,
            }

    _enrich_provenance(base, ouro, asset_id)
    _enrich_comments_preview(base, ouro, asset_id)

    return base
