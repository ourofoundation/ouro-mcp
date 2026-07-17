"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.utils.content import description_to_markdown
from ouro_mcp.config import CommentPreviewConfig, get_comment_preview_config
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    asset_web_url,
    dump_json,
    format_asset_summary,
    format_monetization_block,
    list_response,
    optional_kwargs,
    org_summary,
    route_input_assets_summary,
    route_output_assets_summary,
    route_request_body_without_input_assets,
    slim_asset_tags,
    slim_connection_graph,
    strip_heavy_fields,
    team_summary,
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
                    '"summary" (default) returns name, description, metadata, and engagement counts. '
                    '"full" also includes type-specific content '
                    "(post body, dataset schema/stats, file download URL, service routes, route execution schemas), "
                    "plus provenance (creation action), connections grouped by type with the other asset "
                    "summarized as id, asset_type, name, and created_at when available, "
                    "tags, and a bounded comments/replies preview when present."
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

        Matching is fine-grained: posts, comments, dataset schemas, and routes are
        indexed as chunks, so results include a `snippet` (the matching passage) and
        `match_source` (summary/body/comment/schema/route) for precise citation.
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

        response = ouro.assets.search(
            query,
            limit=limit,
            offset=offset,
            with_pagination=True,
            **optional_kwargs(
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

        assets = []
        for item in response.get("data", []):
            row: dict[str, Any] = {
                "id": str(item.get("id", "")),
                "name": item.get("name"),
                "asset_type": item.get("asset_type"),
                "description": description_to_markdown(item.get("description"), max_length=200),
                "visibility": item.get("visibility"),
                "state": item.get("state"),
                "source": item.get("source"),
                "created_at": item.get("created_at"),
                "last_updated": item.get("last_updated"),
            }

            # Chunk-level search returns the matching passage and where it came
            # from (summary/body/comment/schema/route) so agents can quote the
            # exact evidence instead of refetching the whole asset.
            if item.get("snippet"):
                row["snippet"] = item["snippet"]
            if item.get("match_source"):
                row["match_source"] = item["match_source"]

            user = user_summary(item)
            if user:
                row["user"] = user

            org = org_summary(item)
            if org:
                row["organization"] = org

            team = team_summary(item)
            if team:
                row["team"] = team

            if item.get("parent_id"):
                row["parent_id"] = str(item["parent_id"])

            url = asset_web_url(item)
            if url:
                row["url"] = url

            # Surface monetization so agents can rank/filter without N+1
            # get_asset calls. Free assets contribute nothing.
            row.update(format_monetization_block(item))

            assets.append(row)

        return dump_json(
            list_response(
                assets,
                pagination=response.get("pagination") or {},
                limit=limit,
            )
        )

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    @handle_ouro_errors
    def delete_asset(
        id: Annotated[str, Field(description="UUID of the asset to delete")],
        ctx: Context,
    ) -> str:
        """Delete an asset by ID. Auto-detects the asset type and routes to the appropriate delete method."""
        ouro = ctx.request_context.lifespan_context.ouro

        asset = ouro.assets.retrieve(id)
        asset_type = asset.asset_type
        name = asset.name

        if asset_type == "dataset":
            ouro.datasets.delete(id)
        elif asset_type == "post":
            ouro.posts.delete(id)
        elif asset_type == "file":
            ouro.files.delete(id)
        elif asset_type == "quest":
            ouro.quests.delete(id)
        elif asset_type == "service":
            ouro.services.delete(id)
        else:
            return dump_json(
                {
                    "error": "unsupported_type",
                    "message": f"Cannot delete asset of type '{asset_type}' via this tool.",
                }
            )

        return dump_json(
            {
                "deleted": True,
                "id": id,
                "name": name,
                "asset_type": asset_type,
            }
        )

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
        and action inputs/outputs. Useful for understanding how assets
        relate to each other and navigating lineage. Connections are grouped
        by relationship type. Each item is the connected asset summary with
        ``id``, ``name``, ``asset_type``, and asset ``created_at`` when
        available. For ``action`` edges, ``action_id`` is included when the
        backend provides it so you can follow up with ``get_action`` or
        ``list_asset_actions``. Connection edge metadata and the current
        asset side are otherwise omitted to keep responses small.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        connections = ouro.assets.connections(id)
        connections = slim_connection_graph(connections, current_asset_id=id)
        return truncate_response(dump_json({"asset_id": id, "connections": connections}))

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

        Prefer this over scraping posts for action IDs. ``created_by`` is the
        action that produced the asset (if any). ``as_input`` is the list of
        executions that used the asset as an input — use this to find which
        routes ran on a file or dataset and to get embed/status/response.
        """
        from ouro_mcp.tools.services import _format_action_summary

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

        payload: dict[str, Any] = {
            "asset_id": asset_id,
            "role": role,
            "created_by": (
                _format_action_summary(created_by, include_response=include_response)
                if created_by is not None
                else None
            ),
            "as_input": [
                _format_action_summary(action, include_response=include_response)
                for action in as_input
            ],
        }
        if role in {"input", "both"}:
            payload["as_input_offset"] = offset
            payload["as_input_limit"] = limit
            payload["as_input_has_more"] = bool(pagination.get("hasMore"))
            payload["as_input_count"] = len(as_input)
        return truncate_response(dump_json(payload))

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
        response = list_response(
            results,
            pagination=page.get("pagination") or {},
            limit=limit,
            extra={"asset_id": id, "sort": sort},
        )
        response["compatible_routes"] = results
        return dump_json(response)


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


def _enrich_provenance(result: dict, ouro: Any, asset_id: str) -> None:
    """Best-effort merge of provenance, connections, and tags into an asset result dict."""
    try:
        bundle = ouro.assets.actions(asset_id, role="output")
        created_by = bundle.get("created_by") if isinstance(bundle, dict) else None
        if created_by is not None:
            raw = (
                created_by.model_dump(mode="json")
                if hasattr(created_by, "model_dump")
                else created_by
            )
            result["creation_action"] = strip_heavy_fields(raw)
    except Exception:
        log.debug("Failed to fetch creation action for %s", asset_id, exc_info=True)

    try:
        connections = ouro.assets.connections(asset_id)
        if connections:
            result["connections"] = slim_connection_graph(connections, current_asset_id=asset_id)
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
            schema = ouro.datasets.schema(asset_id)
            base["schema"] = schema
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
