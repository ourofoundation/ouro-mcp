"""Unified read, search, and delete tools — tools/assets.py"""

from __future__ import annotations

import logging
import shlex
from typing import Annotated, Any, Callable, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import Action, Comment
from ouro.utils.content import description_to_markdown
from pydantic import Field

from ouro_mcp.config import CommentPreviewConfig, get_comment_preview_config
from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.params import (
    ActionStatuses,
    AssetType,
    JsonObject,
    TeamRole,
    Visibility,
)
from ouro_mcp.rendering import (
    collapse_whitespace,
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
    render_markdown_sections,
    search_hit_line,
    truncate_response,
)
from ouro_mcp.routes import (
    route_caller_parameters,
    route_description_markdown,
    route_input_assets_summary,
    route_output_assets_summary,
    route_properties_summary,
    route_request_body_without_input_assets,
    route_response_charts_summary,
)
from ouro_mcp.summaries import (
    format_asset_properties,
    format_asset_summary,
    format_search_hit,
    short_description,
    slim_asset_tags,
    slim_connection_graph,
    slim_dataset_schema,
    strip_heavy_fields,
    user_summary,
)
from ouro_mcp.utils import (
    asset_resource,
    asset_type_of,
    run_parallel,
    optional_kwargs,
    present_kwargs,
)

log = logging.getLogger(__name__)


def download_command(download: dict[str, Any]) -> str:
    """The shell command that saves a download link's content to a file."""
    return (
        f"curl -sS -f -L -o {shlex.quote(download['file_name'])} "
        f"{shlex.quote(download['download_url'])}"
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_asset(
        id: Annotated[str, Field(description="UUID of any asset type")],
        ctx: Context,
        detail: Annotated[
            Literal["summary", "full"],
            Field(
                description=(
                    '"summary" (default) returns name, short description, visibility, '
                    "flat username/org_id/team_id, and engagement counts. "
                    '"full" also includes type-specific content '
                    "(post body, dataset schema/stats, file download URL, service routes, route execution schemas), "
                    "with a `↳ name (type · @owner · #team) — description` line under each embedded asset "
                    "so you don't need get_asset per embed, "
                    "plus a compact creation_action pointer (action_id/status/route_id), "
                    "connections, up to 20 per type (comment edges omitted — see comments preview / "
                    "list_comments; a post's own links and embeds are in its body), "
                    "`derived`: the versions, results and supporting assets that came from it, "
                    "tags, and a bounded comments/replies preview when present. "
                    "Both levels include `properties`: values Ouro computes (word_count, "
                    "column_statistics: column names in summary, per-column stats in full) "
                    "and values route runs calculated "
                    "(e.g. curie_temperature), each run's value with its route and action_id. "
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
        ouro = ctx.request_context.lifespan_context.ouro
        full = detail == "full"
        resource = asset_resource(ouro, id)
        # Reading the asset records a view, as ouro.assets.retrieve does; the
        # view, the read, and the counts and properties don't wait on each other
        asset, _, *parts = run_parallel(
            lambda: resource.retrieve(id),
            lambda: ouro.assets._mark_viewed(id),
            lambda: _counts_part(ouro, id),
            lambda: _properties_part(ouro, id, full=full),
        )
        result = strip_heavy_fields(_format_asset_detail(asset, ouro)) if full else format_asset_summary(asset)
        for part in parts:
            result.update(part)
        return dump_json(result)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def search_assets(
        ctx: Context,
        query: Annotated[str, Field(description="Search query or UUID for direct lookup")] = "",
        asset_type: Annotated[Optional[AssetType], Field(description="Only this asset type")] = None,
        scope: Annotated[
            Optional[Literal["personal", "org", "shared", "purchased", "global", "all"]],
            Field(description='"shared": assets others shared with you; "purchased": assets you bought'),
        ] = None,
        org_id: Annotated[Optional[str], Field(description="Organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Team UUID")] = None,
        user_id: Annotated[Optional[str], Field(description="Asset owner UUID")] = None,
        visibility: Annotated[Optional[Visibility], Field(description="Only this visibility")] = None,
        file_type: Annotated[
            Optional[Literal["image", "video", "audio", "pdf"]], Field(description="File category")
        ] = None,
        extension: Annotated[Optional[str], Field(description='File extension, e.g. "csv", "json", "png"')] = None,
        metadata_filters: Annotated[
            Optional[JsonObject], Field(description='Metadata key/value filters, e.g. {"key": "value"}')
        ] = None,
        sort: Annotated[
            Optional[Literal["relevant", "recent", "popular", "updated"]],
            Field(description='Default "relevant" with a query, "recent" without'),
        ] = None,
        time_window: Annotated[
            Optional[Literal["day", "week", "month", "all"]],
            Field(description='For sort="popular"; default "month"'),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Max results to return")] = 10,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
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

        # Invalid filters fail: unfiltered results would look like filtered ones
        merged_metadata: dict[str, Any] = dict(metadata_filters or {})
        if file_type:
            merged_metadata["file_type"] = file_type
        if extension:
            merged_metadata["extension"] = extension

        # Models often fill unused optionals with "" or "/null"; those UUID
        # filters 500 in Postgres ("invalid input syntax for type uuid").
        page = ouro.assets.search(
            query,
            limit=limit,
            offset=offset,
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

        return render_markdown_list(
            [format_search_hit(item) for item in page],
            line_fn=search_hit_line,
            pagination=page_pagination(page),
            offset=offset,
            noun="assets",
            empty_text="No assets found.",
        )

    @mcp.tool(
        annotations={"destructiveHint": True, "idempotentHint": True},
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
        (id, name, asset_type). Comments on the asset are always deleted with it
        and are not listed. Pass dry_run=true to preview first.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        result = ouro.assets.delete(id, delete_children=delete_children, dry_run=dry_run)
        payload = {
            "deleted": not dry_run,
            "id": str(result.id),
            "name": result.name,
            "asset_type": result.asset_type,
            "deleted_children": [
                child.model_dump(mode="json", exclude_none=True)
                for child in result.deleted_children
            ],
            "deleted_children_count": len(result.deleted_children),
        }
        if dry_run:
            payload["dry_run"] = True
        return dump_json(payload)

    @mcp.tool(
        annotations={"destructiveHint": True, "idempotentHint": True},
    )
    @handle_ouro_errors
    def share_asset(
        id: Annotated[str, Field(description="UUID of the asset to share")],
        user_id: Annotated[
            str, Field(description="UUID of the user to grant access to")
        ],
        ctx: Context,
        role: Annotated[TeamRole, Field(description="Access to grant")] = "read",
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

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def download_asset(
        id: Annotated[str, Field(description="UUID of the asset to download")],
        ctx: Context,
        output_path: Annotated[
            Optional[str],
            Field(
                description=(
                    "Local file path or existing directory where the asset should be saved. "
                    "Relative paths resolve against WORKSPACE_ROOT; when WORKSPACE_ROOT is set, "
                    "the path must stay inside it (no '..' traversal or outside-root absolutes)."
                )
            ),
        ] = None,
        asset_type: Annotated[
            Optional[Literal["file", "dataset", "post"]],
            Field(description="Optional override of the detected type"),
        ] = None,
        format: Annotated[
            Optional[Literal["markdown", "html"]],
            Field(description='Post format: "markdown" (default) or "html"'),
        ] = None,
    ) -> str:
        """Download an asset to your own machine.

        Files keep their original bytes, datasets download as CSV, and posts as
        markdown: the same extended markdown create_post and update_post take.

        With output_path the asset is saved there; a directory keeps the server-provided filename.
        Without it, the result is a link and the `command` that fetches it.
        Run the command in a shell: it saves the asset as `file_name` in the
        current directory and prints nothing on success. The link works for
        `expires_in` seconds and needs no other credentials, so treat it as
        a secret.

        Work on the downloaded file instead of paging the asset through chat:
        - bulk dataset analysis (scoring, filtering hundreds+ rows, local
          scripts): download the CSV and compute locally. Use `query_dataset`
          for peeks, small samples, and SQL top-N.
        - editing a long post: download the markdown, edit the file, upload it
          with create_upload_url and pass upload_id to update_post.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        format = format or "markdown"

        if output_path is not None:
            from ouro_mcp.uploads import (
                resolve_local_path,
            )

            download = ouro.assets.download(
                id,
                output_path=str(resolve_local_path(output_path)),
                format=format,
                **optional_kwargs(asset_type=asset_type),
            )
            return dump_json({"downloaded": True, **download.model_dump(mode="json")})

        download = ouro.assets.create_download_url(
            id, format=format, **optional_kwargs(asset_type=asset_type)
        )
        return dump_json(
            {
                "command": download_command(download),
                "file_name": download["file_name"],
                "asset_type": download["asset_type"],
                "content_type": download["content_type"],
                "download_url": download["download_url"],
                "expires_in": download["expires_in"],
            }
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_asset_connections(
        asset_id: Annotated[str, Field(description="Asset UUID")],
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

        Comment edges are omitted — use `list_comments` (or the comments
        preview on `get_asset(detail=\"full\")`). For datasets, outgoing
        `reference` edges (row-level file/action IDs) are also omitted —
        use the dataset schema / `query_dataset` instead. Incoming
        references and other connection types are still returned.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        omit_outgoing_refs = False
        try:
            omit_outgoing_refs = asset_type_of(ouro, asset_id) == "dataset"
        except Exception:
            log.debug("Failed to resolve asset type for connections on %s", asset_id, exc_info=True)
        connections = slim_connection_graph(
            ouro.assets.connections(asset_id),
            current_asset_id=asset_id,
            omit_outgoing_references=omit_outgoing_refs,
            omit_comments=True,
        )

        def _connection_line(row: dict[str, Any]) -> str:
            parts = [markdown_id(row["id"])]
            if row.get("action_id"):
                parts.append(f"action_id: `{row['action_id']}`")
            parts.append(row.get("created_at"))
            return markdown_bullet(
                str(row.get("name") or "(unnamed)"), *parts, kind=row["asset_type"]
            )

        return truncate_response(
            render_markdown_sections(
                connections,
                line_fn=_connection_line,
                empty_text="No connections.",
            )
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_asset_actions(
        asset_id: Annotated[str, Field(description="Asset UUID")],
        ctx: Context,
        role: Annotated[
            Literal["input", "output", "both"],
            Field(
                description=(
                    '"both" (default): created_by + as_input. '
                    '"input": only actions that used this asset as input. '
                    '"output": only the action that produced this asset.'
                )
            ),
        ] = "both",
        status: Annotated[
            Optional[ActionStatuses],
            Field(description='Statuses to keep among as_input runs, e.g. ["error", "timed-out"]'),
        ] = None,
        include_response: Annotated[
            bool,
            Field(
                description="Include each action response payload. Leave false for compact browsing."
            ),
        ] = False,
        limit: Annotated[int, Field(ge=1, le=200, description="Max as_input runs to return")] = 20,
        offset: Annotated[int, Field(ge=0, description="Pagination offset for as_input")] = 0,
    ) -> str:
        """List route actions linked to an asset.

        Prefer this over scraping posts for action IDs. Returns compact
        markdown. `created_by` is the action that produced the asset (if
        any). `as_input` is the list of executions that used the asset as
        an input — use this to find which routes ran on a file or dataset.
        Each row shows the run's inputs, outputs, run time and cost; pass
        include_response for its response too.
        """
        from ouro_mcp.tools.services import (
            ACTION_LINK_HINT,
            action_summary_line,
            format_action_summary,
        )

        ouro = ctx.request_context.lifespan_context.ouro
        bundle = ouro.assets.actions(
            asset_id,
            role=role,  # type: ignore[arg-type]
            include_response=include_response,
            limit=limit,
            offset=offset,
            **optional_kwargs(status=",".join(status) if status else None),
        )
        as_input = bundle.as_input

        sections: dict[str, list[Any]] = {}
        if role in {"output", "both"}:
            sections["created_by"] = (
                [format_action_summary(bundle.created_by, include_response=include_response)]
                if bundle.created_by is not None
                else []
            )
        if role in {"input", "both"}:
            sections["as_input"] = [
                format_action_summary(action, include_response=include_response)
                for action in as_input
            ]

        extras: list[str] = [ACTION_LINK_HINT] if any(sections.values()) else []
        if role in {"input", "both"} and bundle.has_more:
            extras.append(f"More as_input runs: call again with offset={offset + len(as_input)}.")

        return truncate_response(
            render_markdown_sections(
                sections,
                line_fn=action_summary_line,
                preamble="\n".join(extras),
                empty_text="No linked actions.",
            )
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_compatible_routes(
        asset_id: Annotated[str, Field(description="Asset UUID")],
        ctx: Context,
        query: Annotated[
            Optional[str],
            Field(
                description=(
                    "What you want to do with the asset, in plain words "
                    '(e.g. "relax the structure", "band gap"). Returns the best-matching '
                    "compatible routes first and ignores sort."
                )
            ),
        ] = None,
        sort: Annotated[
            Literal["popular", "recent", "updated"], Field(description='"popular": most used first')
        ] = "popular",
        limit: Annotated[int, Field(ge=1, le=200, description="Max routes to return")] = 10,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    ) -> str:
        """Find routes that can operate on this asset.

        Returns routes whose input type is compatible with the given asset,
        answering the question "what can I do with this asset?". Defaults to
        popularity order so the most-used routes appear first. Pass `query` to
        search those routes by intent instead of paging through the list.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        query = (query or "").strip() or None
        page = ouro.assets.compatible_routes(
            asset_id,
            query=query,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        results = [
            optional_kwargs(
                id=str(route.id),
                name=route.name,
                asset_type=route.asset_type,
                description=short_description(route.description),
            )
            for route in page
        ]

        def _route_line(row: dict[str, Any]) -> str:
            return markdown_bullet(
                str(row.get("name") or "(untitled)"),
                markdown_id(row["id"]),
                kind=row["asset_type"],
                body=row.get("description"),
            )

        return render_markdown_list(
            results,
            line_fn=_route_line,
            pagination=page_pagination(page),
            offset=offset,
            noun="compatible routes",
            empty_text="No compatible routes.",
        )


def _counts_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    """Engagement counts, best effort.

    Zero-valued counters are dropped — most assets have zero of at least one
    metric (brand-new assets often have zero of all four), and emitting a
    full `{views:0, comments:0, reactions:0, downloads:0}` block on every
    summary wastes ~60 chars per asset. Agents can treat a missing key as 0.
    """
    try:
        counts = ouro.assets.counts(asset_id)
    except Exception:
        log.debug("Failed to fetch counts for asset %s", asset_id, exc_info=True)
        return {}

    nonzero = {
        k: getattr(counts, k)
        for k in ("views", "comments", "reactions", "downloads")
        if getattr(counts, k)
    }
    return {"counts": nonzero} if nonzero else {}


def _compact_column_statistics(stats: Any, *, full: bool) -> Any:
    """Column statistics without the repeats.

    A summary read only names the columns: the per-column numbers are for
    analysis, so they come with `detail="full"`. Either way a `label`
    equal to the column name, and scan bookkeeping that says the whole table
    was read, are dropped.
    """
    if not isinstance(stats, dict) or not isinstance(stats.get("columns"), list):
        return stats
    columns = [c for c in stats["columns"] if isinstance(c, dict)]
    compact: dict[str, Any] = {"row_count": stats.get("row_count")}
    if stats.get("sampled"):
        compact["sampled"] = True
        compact["scanned_rows"] = stats.get("scanned_rows")
    if full:
        compact["columns"] = [
            {k: v for k, v in c.items() if not (k == "label" and v == c.get("name"))}
            for c in columns
        ]
    else:
        compact["columns"] = [c.get("name") for c in columns]
    return {k: v for k, v in compact.items() if v is not None}


def _properties_part(ouro: Any, asset_id: str, *, full: bool = False) -> dict[str, Any]:
    """The asset's properties (word count, column statistics, calculated
    values such as a Curie temperature), best effort."""
    try:
        properties = ouro.assets.properties(asset_id)
    except Exception:
        log.debug("Failed to fetch properties for asset %s", asset_id, exc_info=True)
        return {}
    formatted = format_asset_properties(properties)
    if formatted and "column_statistics" in formatted:
        formatted["column_statistics"] = _compact_column_statistics(
            formatted["column_statistics"], full=full
        )
    return {"properties": formatted} if formatted else {}


def _compact_creation_action(created_by: Action) -> dict[str, Any]:
    """Pointer-only producer action for get_asset(detail=full).

    Full action payloads (response, assets, metadata) belong on
    `list_asset_actions` / `get_action`, not inline on every full asset read.
    """
    pointer = {
        "action_id": str(created_by.id),
        "action_status": created_by.status,
    }
    if created_by.route_id:
        pointer["route_id"] = str(created_by.route_id)
    return pointer


# Connections of one type listed on a full read; get_asset_connections has the rest.
CONNECTIONS_PREVIEW_LIMIT = 20

# Versions, results and supporting assets listed on a full read; a family root
# can hold hundreds (every relaxed CIF and phonon run on one structure).
DERIVED_PREVIEW_LIMIT = 20


def _derived_entry(item: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": str(item.id),
        "name": getattr(item, "name", None),
        "asset_type": item.asset_type,
        "lineage_role": item.lineage_role,
    }
    run = getattr(item, "run", None)
    if run is not None:
        entry["action_id"] = str(run.action_id)
        if run.route and run.route.get("name"):
            entry["route_name"] = run.route["name"]
    return {key: value for key, value in entry.items() if value is not None}


def _creation_action_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        created_by = ouro.assets.actions(asset_id, role="output").created_by
        if created_by is not None:
            return {"creation_action": _compact_creation_action(created_by)}
    except Exception:
        log.debug("Failed to fetch creation action for %s", asset_id, exc_info=True)
    return {}


def _connections_part(ouro: Any, asset_id: str, *, asset_type: str, has_body: bool) -> dict[str, Any]:
    try:
        slimmed = _slim_connections(ouro, asset_id, asset_type=asset_type, has_body=has_body)
    except Exception:
        log.debug("Failed to fetch connections for %s", asset_id, exc_info=True)
        return {}
    part: dict[str, Any] = {}
    for edge_type, rows in slimmed.items():
        if len(rows) > CONNECTIONS_PREVIEW_LIMIT:
            slimmed[edge_type] = rows[:CONNECTIONS_PREVIEW_LIMIT]
            part["connections_has_more"] = True
    if slimmed:
        part = {"connections": slimmed, **part}
    return part


def _slim_connections(ouro: Any, asset_id: str, *, asset_type: str, has_body: bool) -> dict[str, list]:
    return slim_connection_graph(
        ouro.assets.connections(asset_id),
        current_asset_id=asset_id,
        # Dataset→row-ref edges duplicate IDs already in the table and
        # routinely number in the thousands; keep only incoming refs.
        omit_outgoing_references=asset_type == "dataset",
        # Comment stubs duplicate the bounded `comments` preview (and
        # `list_comments`); omit them so connections stay lineage-focused.
        omit_comments=True,
        # A post's own links and embeds are already in content_text
        omit_outgoing_types=("link", "reference") if has_body else (),
    )


def _derived_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        lineage = ouro.assets.lineage(asset_id)
        derived = [_derived_entry(item) for item in lineage.derived[:DERIVED_PREVIEW_LIMIT]]
    except Exception:
        log.debug("Failed to fetch lineage for %s", asset_id, exc_info=True)
        return {}
    if not derived:
        return {}
    part: dict[str, Any] = {"derived": derived}
    if lineage.truncated or len(lineage.derived) > DERIVED_PREVIEW_LIMIT:
        part["derived_has_more"] = True
    return part


def _tags_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        tags = slim_asset_tags(ouro.assets.tags(asset_id) or [])
    except Exception:
        log.debug("Failed to fetch tags for %s", asset_id, exc_info=True)
        return {}
    return {"tags": tags} if tags else {}


def _comment_text(comment: Comment, config: CommentPreviewConfig) -> str | None:
    text = description_to_markdown(comment.content or comment.text, embedded_assets=comment.assets)
    if not text or config.text_chars <= 0:
        return None
    return text[: config.text_chars]


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


def _comments_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    """Attach a small comment/reply preview to full asset details.

    This is intentionally bounded. It gives agents enough context to notice
    existing replies without turning `get_asset(detail="full")` into a full
    thread dump; callers can still use `list_comments` for complete threads.
    """
    comments_client = getattr(ouro, "comments", None)
    if not comments_client:
        return {}
    config = get_comment_preview_config()
    if config.comment_limit <= 0:
        return {}

    try:
        comments = list(comments_client.list_by_parent(asset_id) or [])
    except Exception:
        log.debug("Failed to fetch comments for %s", asset_id, exc_info=True)
        return {}

    if not comments:
        return {}

    preview: list[dict[str, Any]] = []
    for comment in comments[: config.comment_limit]:
        entry = _format_comment_preview(comment, config)
        comment_id = entry.get("id")
        # Skip the lookup when the comment says it has no replies
        known_replies = getattr(comment, "replies", None)
        if isinstance(known_replies, list):
            known_replies = len(known_replies)
        if comment_id and config.reply_limit > 0 and known_replies != 0:
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

    part: dict[str, Any] = {"comments": preview}
    if len(comments) > config.comment_limit:
        part["comments_has_more"] = True
    return part


# A service can hold dozens of routes; get_asset on a route has the full text.
SERVICE_ROUTE_DESCRIPTION_CHARS = 300


def _dataset_schema_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        return {"schema": slim_dataset_schema(ouro.datasets.schema(asset_id))}
    except Exception:
        log.debug("Failed to fetch schema for dataset %s", asset_id, exc_info=True)
        return {"schema": None}


def _dataset_stats_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        stats = ouro.datasets.stats(asset_id)
    except Exception:
        log.debug("Failed to fetch stats for dataset %s", asset_id, exc_info=True)
        return {"stats": None}
    return {"stats": {"row_count": stats.count, "is_estimate": stats.is_estimate, "size_bytes": stats.size_bytes}}


def _service_routes_part(ouro: Any, asset_id: str) -> dict[str, Any]:
    try:
        return {
            "routes": [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "description": collapse_whitespace(
                        route_description_markdown(r.route.description) if r.route else None,
                        max_length=SERVICE_ROUTE_DESCRIPTION_CHARS,
                    )
                    or None,
                }
                for r in ouro.services.read_routes(asset_id)
            ]
        }
    except Exception:
        log.debug("Failed to fetch routes for service %s", asset_id, exc_info=True)
        return {"routes": []}


def _format_asset_detail(asset: Any, ouro: Any) -> dict:
    """Build a type-appropriate detail response for any asset.

    What comes with the asset is formatted here; what needs another request
    (schema, routes, provenance, tags, comments) is fetched in parallel and
    merged in a fixed order.
    """
    base = format_asset_summary(asset)

    asset_id = str(asset.id)
    asset_type = asset.asset_type
    fetches: list[Callable[[], dict]] = []

    if asset_type == "dataset":
        fetches += [lambda: _dataset_schema_part(ouro, asset_id), lambda: _dataset_stats_part(ouro, asset_id)]
        if asset.preview:
            base["preview"] = asset.preview[:5]

    elif asset_type in {"post", "comment"}:
        base["content_text"] = description_to_markdown(asset.content, embedded_assets=asset.assets) or None

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
        fetches.append(lambda: _service_routes_part(ouro, asset_id))

    elif asset_type == "route":
        if asset.route:
            # The route's own description in full, in place of the summary's cut-down copy
            full_description = route_description_markdown(asset.route.description)
            if full_description:
                base["description"] = full_description
            parameters = route_caller_parameters(asset.route.parameters)
            if parameters:
                base["parameters"] = parameters
            base["request_body"] = route_request_body_without_input_assets(asset.route)
            base["input_assets"] = route_input_assets_summary(asset.route)
            base["output_assets"] = route_output_assets_summary(asset.route)
            base["fills_properties"] = route_properties_summary(asset.route)
            base["response_charts"] = route_response_charts_summary(asset.route)

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
                    "description": description_to_markdown(i.description) or "",
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

    has_body = bool(base.get("content_text"))
    fetches += [
        lambda: _creation_action_part(ouro, asset_id),
        lambda: _connections_part(ouro, asset_id, asset_type=asset_type, has_body=has_body),
        lambda: _derived_part(ouro, asset_id),
        lambda: _tags_part(ouro, asset_id),
        lambda: _comments_part(ouro, asset_id),
    ]
    for part in run_parallel(*fetches):
        base.update(part)
    return base
