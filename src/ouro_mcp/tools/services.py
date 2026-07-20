"""Route execution + action polling tools — the killer feature."""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    dump_json,
    format_asset_summary,
    format_one_time_cost_summary,
    format_pay_per_use_cost_summary,
    list_response,
    optional_kwargs,
    route_input_assets_summary,
    route_output_assets_summary,
    route_request_body_without_input_assets,
    truncate_response,
)

log = logging.getLogger(__name__)


def _parse_json_param(value: Any, name: str) -> Optional[dict]:
    """Coerce ``body`` / ``query`` / ``params`` into a dict or fail loudly.

    Returns ``None`` only when the caller explicitly omitted the parameter
    (``None`` / empty string). Any other non-object value raises
    :class:`ValueError`, which ``handle_ouro_errors`` surfaces as a
    structured ``{"error": "invalid_arguments", ...}`` payload so the agent
    can correct the call instead of silently running with missing input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"{name} is not valid JSON: {exc}. Pass a JSON object like "
                f'\'{{"key": "value"}}\' or omit the parameter.'
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{name} must be a JSON object (got {type(parsed).__name__}). "
                f'Example: \'{{"key": "value"}}\'.'
            )
        return parsed
    raise ValueError(
        f"{name} must be a JSON object or JSON-encoded string "
        f"(got {type(value).__name__})."
    )


def _parse_json_arg(value: Any, name: str) -> Optional[Any]:
    """Coerce a JSON object/array param into a Python value or fail loudly.

    Like :func:`_parse_json_param` but accepts both objects and arrays — used
    for route fields such as ``parameters`` (array) and ``request_body`` /
    ``input_assets`` (object). Returns ``None`` when omitted.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"{name} is not valid JSON: {exc}. Pass a JSON object/array or "
                "omit the parameter."
            ) from exc
    raise ValueError(
        f"{name} must be a JSON object/array or JSON-encoded string "
        f"(got {type(value).__name__})."
    )


def _route_action_embed(route_id: str, action_id: str) -> str:
    return (
        "```assetComponent\n"
        + json.dumps(
            {
                "id": route_id,
                "assetType": "route",
                "viewMode": "preview",
                "displayConfig": {"actionId": action_id},
            }
        )
        + "\n```"
    )


def _action_error_context(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    error = response.get("error") if isinstance(response.get("error"), dict) else response
    status = (
        response.get("statusCode")
        or error.get("statusCode")
        or error.get("status")
        or error.get("upstreamStatus")
    )
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    code = error.get("code")
    retryable = error.get("retryable")
    if retryable is None and status is not None:
        retryable = status in {408, 429, 500, 502, 503, 504}
    service_url = error.get("serviceUrl")
    error_type = error.get("type")
    is_external = (
        error_type == "external_service_error"
        or service_url is not None
        or (isinstance(code, str) and code.startswith("external_service"))
    )
    result: dict[str, Any] = {
        "error_type": "external_service_error" if is_external else "route_execution_failed",
        "retryable": retryable,
        "status_code": status,
        "code": code,
        "service_url": service_url,
    }
    return {k: v for k, v in result.items() if v is not None}


def _format_route_cost_preview(route: Any) -> Optional[dict[str, Any]]:
    """Build a `cost_preview` block for an upcoming route execution.

    Returns None for free routes. For monetized routes, surfaces the
    structured cost fields plus a human-readable `cost_summary` so the agent
    can confirm what the user is about to be charged before invoking the
    route for real.
    """
    monetization = getattr(route, "monetization", None)
    if not monetization or monetization == "none":
        return None
    currency = (getattr(route, "price_currency", None) or "usd").lower()
    preview: dict[str, Any] = {
        "monetization": monetization,
        "price_currency": currency,
        "warning": "This route will charge the caller when executed.",
    }
    if monetization == "pay-per-use":
        unit_cost = getattr(route, "unit_cost", None)
        cost_unit = getattr(route, "cost_unit", None) or "call"
        preview["unit_cost"] = unit_cost
        preview["cost_unit"] = cost_unit
        preview["cost_accounting"] = getattr(route, "cost_accounting", None)
        if unit_cost is not None:
            preview["cost_summary"] = format_pay_per_use_cost_summary(
                unit_cost,
                cost_unit,
                currency,
            )
    else:
        price = getattr(route, "price", None)
        preview["price"] = price
        if price is not None:
            preview["cost_summary"] = format_one_time_cost_summary(price, currency)
    return preview


def _format_action_cost(
    usage_record: Any = None,
    btc_charges: Any = None,
) -> Optional[dict[str, Any]]:
    """Build a `cost` block for an action.

    USD routes: data comes from the joined `usage_record` row (Stripe meter
    lifecycle: reserved → confirmed → invoiced).

    BTC routes: data comes from the joined `transactions` rows (Spark wallet
    transfer settled atomically per call). RLS filters to the rows visible
    to the caller — usually one of `route_usage` (buyer) or `route_revenue`
    (creator).

    Returns None for free routes or when RLS hides every billing row from
    the caller. Always emits a human-readable `cost_summary` so agents that
    ignore the structured fields still see what the run cost (or earned).
    """
    ur = _as_dict(usage_record)
    if ur:
        total_cents = ur.get("total_cents")
        status = ur.get("status")
        invoice_id = ur.get("stripe_invoice_id")
        cost: dict[str, Any] = {
            "currency": "usd",
            "total_cents": total_cents,
            "unit_cost_cents": ur.get("unit_cost_cents"),
            "quantity": ur.get("quantity"),
            "cost_unit": ur.get("cost_unit"),
            "status": status,  # "reserved" (in-flight) or "confirmed" (metered)
            "stripe_invoice_id": invoice_id,
        }
        if total_cents is not None:
            suffix = (
                " (in-progress)"
                if status == "reserved"
                else " (billed on invoice)"
                if invoice_id
                else " (pending invoice)"
            )
            cost["cost_summary"] = f"${total_cents / 100:.2f}" + suffix
        return {k: v for k, v in cost.items() if v is not None}

    charges = btc_charges or []
    if not isinstance(charges, list):
        charges = [charges]
    charges = [_as_dict(c) for c in charges if c is not None]
    if not charges:
        return None

    # Prefer the buyer's route_usage row (the actual cost). Fall back to
    # route_revenue for creator viewers who can only see their own row via RLS.
    usage = next(
        (c for c in charges if c.get("type") == "route_usage"),
        None,
    )
    revenue = next(
        (c for c in charges if c.get("type") == "route_revenue"),
        None,
    )
    primary = usage or revenue
    if not primary:
        return None

    raw_value = primary.get("value")
    sats = abs(int(raw_value)) if raw_value is not None else None
    cost = {
        "currency": "btc",
        "value_sats": sats,
        "status": primary.get("status"),
        "perspective": "buyer" if usage else "creator",
        "transfer_id": (primary.get("metadata") or {}).get("transfer_id"),
    }
    if sats is not None:
        suffix = " charged" if usage else " earned"
        cost["cost_summary"] = f"{sats:,} sats" + suffix
    return {k: v for k, v in cost.items() if v is not None}


def _format_action_result(
    action: Any,
    *,
    route_id: Optional[str] = None,
    route_name: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Build the tool response dict for a completed (or errored) Action."""
    result: dict[str, Any] = {
        "status": "success" if action.is_success else action.status,
        "action_id": str(action.id),
        "action_status": action.status,
    }
    if route_id:
        result["route_id"] = route_id
        result["embed_markdown"] = _route_action_embed(route_id, str(action.id))
    if route_name:
        result["route_name"] = route_name
    if duration_seconds is not None:
        result["duration_seconds"] = duration_seconds

    # For errored actions, surface the server response under `error` so the
    # agent can reason about the failure without losing the action context.
    if action.is_error:
        result["error"] = _serialize_result(action.response)
        result.update(_action_error_context(action.response))
    else:
        result["data"] = _serialize_result(action.response)

    # Surface inputs and outputs exclusively as the modern plural
    # `input_assets` / `output_assets` lists. Legacy actions with only the
    # singular `input_asset` / `output_asset` FK columns get synthesized
    # into a one-row list with `is_primary: True` so there's only one shape
    # for agents to learn.
    input_assets = _unified_action_assets(
        getattr(action, "input_assets", None),
        getattr(action, "input_asset", None),
    )
    if input_assets:
        result["input_assets"] = input_assets

    output_assets = _unified_action_assets(
        getattr(action, "output_assets", None),
        getattr(action, "output_asset", None),
    )
    if output_assets:
        result["output_assets"] = output_assets

    cost = _format_action_cost(
        usage_record=getattr(action, "usage_record", None),
        btc_charges=getattr(action, "btc_charges", None),
    )
    if cost:
        result["cost"] = cost

    return result


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _compact_asset(asset: Any) -> Optional[dict[str, Any]]:
    asset = _as_dict(asset)
    if not asset:
        return None
    result = {
        "id": str(asset.get("id", "")),
        "name": asset.get("name"),
        "asset_type": asset.get("asset_type"),
    }
    if asset.get("description"):
        result["description"] = asset.get("description")
    return {k: v for k, v in result.items() if v not in (None, "")}


def _compact_action_assets(rows: Any) -> Optional[list[dict[str, Any]]]:
    """Slim a list of action_assets rows for tool responses.

    Routes declare named input and output slots (e.g. a benchmarking route
    that produces `report` and `raw_results` files). The backend stores
    those in the `action_assets` join table, surfaced as `input_assets` /
    `output_assets` lists on the Action with the per-row shape::

        {"name": str, "is_primary": bool, "asset_id": uuid,
         "asset_type": str, "asset": {full asset record}}

    For the agent we only need the logical slot ``name``, the optional
    ``is_primary`` marker, and a compact view of the resolved asset.
    Returns ``None`` when the list is empty/missing so callers can omit the
    key entirely.
    """
    if not isinstance(rows, list) or not rows:
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        row = _as_dict(row)
        if not row:
            continue
        entry: dict[str, Any] = {}
        name = row.get("name")
        if name:
            entry["name"] = name
        # Only emit `is_primary` when True — the default (False) is implicit
        # and would just be noise on the many non-primary entries.
        if row.get("is_primary"):
            entry["is_primary"] = True
        # Prefer the resolved nested `asset` join; fall back to the FK
        # columns when the join wasn't selected so the agent still gets
        # `{id, asset_type}` to follow up on.
        asset = _compact_asset(row.get("asset"))
        if asset is None:
            asset = _compact_asset(
                {
                    "id": row.get("asset_id"),
                    "asset_type": row.get("asset_type"),
                }
            )
        if asset:
            entry["asset"] = asset
        if entry:
            out.append(entry)
    return out or None


def _unified_action_assets(
    plural_rows: Any,
    legacy_singular: Any,
) -> Optional[list[dict[str, Any]]]:
    """Return the slim plural list, synthesizing from the legacy singular when needed.

    The agent-facing tool response only carries the plural
    `input_assets` / `output_assets` shape — but the backend still keeps
    the pre-action_assets FK columns (`input_asset` / `output_asset`)
    populated for actions that predate the join table. To keep one shape
    for agents to learn, wrap that legacy single asset into a one-row
    plural list when no join rows exist. The synthesized entry has no
    `name` (legacy actions never had a logical slot name) and is marked
    `is_primary: True` so agents iterating the list can still find the
    canonical output.
    """
    plural = _compact_action_assets(plural_rows)
    if plural:
        return plural
    legacy = _compact_asset(legacy_singular)
    if legacy:
        return [{"is_primary": True, "asset": legacy}]
    return None


def _format_action_summary(
    action: Any,
    *,
    include_response: bool = False,
) -> dict[str, Any]:
    action = _as_dict(action)
    route_id = str(
        action.get("route_id") or (action.get("route") or {}).get("id") or ""
    )
    action_id = str(action.get("id", ""))
    result: dict[str, Any] = {
        "action_id": action_id,
        "action_status": action.get("status"),
        "route_id": route_id or None,
        "user_id": str(action.get("user_id", "")) if action.get("user_id") else None,
        "created_at": action.get("created_at"),
        "started_at": action.get("started_at"),
        "finished_at": action.get("finished_at"),
        "last_updated": action.get("last_updated"),
    }

    route = _compact_asset(action.get("route"))
    if route:
        result["route"] = route

    # Unified shape: only emit the plural `input_assets` / `output_assets`
    # lists. Legacy actions whose join rows haven't been backfilled fall
    # back to a synthesized one-row list from the FK columns so the agent
    # surface stays uniform. Per-row shape: `{name, is_primary?, asset:
    # {id, asset_type, name?, description?}}` — discriminate by `name`,
    # not by position.
    input_assets = _unified_action_assets(
        action.get("input_assets"), action.get("input_asset")
    )
    if input_assets:
        result["input_assets"] = input_assets

    output_assets = _unified_action_assets(
        action.get("output_assets"), action.get("output_asset")
    )
    if output_assets:
        result["output_assets"] = output_assets

    if action.get("metadata"):
        result["metadata"] = action.get("metadata")
    if include_response and action.get("response") is not None:
        result["response"] = _serialize_result(action.get("response"))
    cost = _format_action_cost(
        usage_record=action.get("usage_record"),
        btc_charges=action.get("btc_charges"),
    )
    if cost:
        result["cost"] = cost
    if route_id and action_id:
        result["embed_markdown"] = _route_action_embed(route_id, action_id)

    return {k: v for k, v in result.items() if v is not None}


def _format_log_entry(log_entry: Any) -> dict[str, Any]:
    log_entry = _as_dict(log_entry)
    result: dict[str, Any] = {
        "id": str(log_entry.get("id", "")),
        "level": log_entry.get("level"),
        "event_type": log_entry.get("event_type"),
        "message": log_entry.get("message"),
        "origin": log_entry.get("origin"),
        "source": log_entry.get("source"),
        "created_at": log_entry.get("created_at"),
    }
    asset = _compact_asset(log_entry.get("asset"))
    if asset:
        result["asset"] = asset
    user = log_entry.get("user")
    if isinstance(user, dict):
        result["user"] = {
            "user_id": str(user.get("user_id", "")),
            "username": user.get("username"),
        }
    if log_entry.get("metadata"):
        result["metadata"] = log_entry.get("metadata")
    return {k: v for k, v in result.items() if v is not None}


def _get_action_logs_payload(
    ouro: Any,
    action_id: str,
    *,
    level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    sort_order: str = "asc",
) -> dict[str, Any]:
    if limit <= 0 or limit > 500:
        raise ValueError("limit must be between 1 and 500.")
    if offset < 0:
        raise ValueError("offset must be non-negative.")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be 'asc' or 'desc'.")

    page = ouro.routes.get_action_logs(
        action_id,
        level=level,
        limit=limit,
        offset=offset,
        sort_order=sort_order,
        with_pagination=True,
    )
    logs = [_format_log_entry(item) for item in (page.get("data") or [])]
    return list_response(
        logs,
        pagination=page.get("pagination") or {},
        limit=limit,
        extra={"action_id": action_id, "sort_order": sort_order},
    )


_LICENSE_DESC = (
    'SPDX license id for services/routes: "MIT" | "Apache-2.0" | "GPL-3.0-only" | '
    '"AGPL-3.0-only" | "MPL-2.0" | "ARR". Defaults to MIT for new services.'
)
_ORIGINALITY_DESC = '"original" | "derivative" | "third-party"'
_RELATION_TYPE_DESC = (
    'DataCite relation of this asset to the related paper: '
    '"IsSupplementTo" | "IsDerivedFrom" | "References" | '
    '"IsVariantFormOf" | "IsIdenticalTo"'
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_service(
        name: Annotated[str, Field(description="Service name")],
        org_id: Annotated[str, Field(description="Organization UUID")],
        team_id: Annotated[str, Field(description="Team UUID")],
        base_url: Annotated[
            str,
            Field(description="Base URL of the upstream API, e.g. 'https://api.example.com'"),
        ],
        ctx: Context,
        authentication: Annotated[
            str,
            Field(
                description=(
                    'Upstream auth scheme: "None" | "Ouro" | '
                    '"Personal Access Token" | "OAuth 2.0"'
                )
            ),
        ] = "None",
        spec_url: Annotated[
            Optional[str],
            Field(
                description=(
                    "URL to an OpenAPI (Swagger) spec. When provided, the "
                    "service's routes are parsed and created automatically."
                )
            ),
        ] = None,
        spec_path: Annotated[
            Optional[str],
            Field(description="Storage path to an already-uploaded OpenAPI spec file."),
        ] = None,
        visibility: Annotated[
            str, Field(description='"public" | "private" | "organization"')
        ] = "public",
        description: Annotated[
            Optional[str], Field(description="Short description of the service")
        ] = None,
        version: Annotated[Optional[str], Field(description="Optional version string")] = None,
        auth_url: Annotated[
            Optional[str],
            Field(description="OAuth authorization URL (only for 'OAuth 2.0' auth)"),
        ] = None,
        license_id: Annotated[str, Field(description=_LICENSE_DESC)] = "MIT",
        originality: Annotated[
            Optional[str], Field(description=_ORIGINALITY_DESC)
        ] = None,
        github_url: Annotated[
            Optional[str], Field(description="Source repo URL (https://github.com/...)")
        ] = None,
        paper_url: Annotated[
            Optional[str], Field(description="Paper or preprint URL")
        ] = None,
        doi_url: Annotated[
            Optional[str], Field(description="DOI URL (https://doi.org/...)")
        ] = None,
        external_url: Annotated[
            Optional[str], Field(description="Other project/homepage URL")
        ] = None,
        relation_type: Annotated[
            Optional[str], Field(description=_RELATION_TYPE_DESC)
        ] = None,
    ) -> str:
        """Publish an external API as a service on Ouro.

        `base_url` must be unique across Ouro. Pass `spec_url` (or `spec_path`
        for an already-uploaded file) to register from an OpenAPI spec — routes
        are parsed and created automatically. Omit both to create a service
        with no routes yet, then add routes from the web UI.

        Set `license_id` and provenance (`originality`, `github_url`,
        `paper_url`, `doi_url`, `external_url`, `relation_type`) when wrapping
        third-party models. Provenance is stored on `attribution`, not service
        metadata.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        service = ouro.services.create(
            name=name,
            base_url=base_url,
            authentication=authentication,
            visibility=visibility,
            description=description,
            org_id=org_id,
            team_id=team_id,
            license_id=license_id,
            **optional_kwargs(
                spec_url=spec_url,
                spec_path=spec_path,
                version=version,
                auth_url=auth_url,
                originality=originality,
                github_url=github_url,
                paper_url=paper_url,
                doi_url=doi_url,
                external_url=external_url,
                relation_type=relation_type,
            ),
        )

        return dump_json(format_asset_summary(service))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_service(
        id: Annotated[str, Field(description="Service UUID")],
        ctx: Context,
        name: Annotated[Optional[str], Field(description="New name")] = None,
        base_url: Annotated[
            Optional[str], Field(description="New upstream base URL (must be unique)")
        ] = None,
        authentication: Annotated[
            Optional[str],
            Field(
                description=(
                    'Upstream auth scheme: "None" | "Ouro" | '
                    '"Personal Access Token" | "OAuth 2.0"'
                )
            ),
        ] = None,
        spec_url: Annotated[
            Optional[str],
            Field(description="URL to an OpenAPI spec; re-parses and syncs the service's routes."),
        ] = None,
        spec_path: Annotated[
            Optional[str],
            Field(description="Storage path to an uploaded OpenAPI spec; re-parses routes."),
        ] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization"')
        ] = None,
        description: Annotated[Optional[str], Field(description="New description")] = None,
        version: Annotated[Optional[str], Field(description="New version string")] = None,
        auth_url: Annotated[Optional[str], Field(description="OAuth authorization URL")] = None,
        org_id: Annotated[Optional[str], Field(description="Move to organization UUID")] = None,
        team_id: Annotated[Optional[str], Field(description="Move to team UUID")] = None,
        license_id: Annotated[
            Optional[str], Field(description=_LICENSE_DESC)
        ] = None,
        originality: Annotated[
            Optional[str], Field(description=_ORIGINALITY_DESC)
        ] = None,
        github_url: Annotated[
            Optional[str], Field(description="Source repo URL (https://github.com/...)")
        ] = None,
        paper_url: Annotated[
            Optional[str], Field(description="Paper or preprint URL")
        ] = None,
        doi_url: Annotated[
            Optional[str], Field(description="DOI URL (https://doi.org/...)")
        ] = None,
        external_url: Annotated[
            Optional[str], Field(description="Other project/homepage URL")
        ] = None,
        relation_type: Annotated[
            Optional[str], Field(description=_RELATION_TYPE_DESC)
        ] = None,
    ) -> str:
        """Update a service's metadata (name, base_url, auth, visibility, ...).

        Service config merges into `metadata`; provenance merges into
        `attribution`. Pass only what changes. Providing `spec_url` or
        `spec_path` re-parses the OpenAPI spec and syncs routes.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        service = ouro.services.update(
            id,
            **optional_kwargs(
                name=name,
                base_url=base_url,
                authentication=authentication,
                spec_url=spec_url,
                spec_path=spec_path,
                visibility=visibility,
                description=description,
                version=version,
                auth_url=auth_url,
                org_id=org_id,
                team_id=team_id,
                license_id=license_id,
                originality=originality,
                github_url=github_url,
                paper_url=paper_url,
                doi_url=doi_url,
                external_url=external_url,
                relation_type=relation_type,
            ),
        )

        return dump_json(format_asset_summary(service))

    @mcp.tool(annotations={"idempotentHint": False})
    @handle_ouro_errors
    def create_route(
        service_id: Annotated[str, Field(description="UUID of the service to add the route to")],
        method: Annotated[
            str, Field(description='HTTP method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"')
        ],
        path: Annotated[str, Field(description="Route path on the upstream API, e.g. '/predict'")],
        ctx: Context,
        name: Annotated[
            Optional[str],
            Field(description="Display name; defaults to '{method} {path}' when omitted"),
        ] = None,
        description: Annotated[Optional[str], Field(description="Route description")] = None,
        visibility: Annotated[
            Optional[str],
            Field(description='"public" | "private" | "organization"; defaults to the service\'s'),
        ] = None,
        parameters: Annotated[
            Optional[Any],
            Field(description="OpenAPI-style parameters as a JSON array (object or string)"),
        ] = None,
        request_body: Annotated[
            Optional[Any],
            Field(description="Request body schema as a JSON object (object or string)"),
        ] = None,
        input_assets: Annotated[
            Optional[Any],
            Field(
                description=(
                    "Keyed Ouro asset inputs as a JSON object, e.g. "
                    '\'{"structure": {"asset_type": "file"}}\''
                )
            ),
        ] = None,
        output_assets: Annotated[
            Optional[Any],
            Field(description="Keyed Ouro asset outputs as a JSON object"),
        ] = None,
        execution_mode: Annotated[
            str, Field(description='"sync" (default) or "async" for long-running upstreams')
        ] = "sync",
    ) -> str:
        """Add a route (a single API endpoint) to a service.

        `method` + `path` must be unique within the service. Use this after
        `create_service` to expose individual endpoints, or when a service was
        created without an OpenAPI spec. `org_id`, `team_id`, and `visibility`
        default to the parent service's values.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        route = ouro.routes.create(
            service_id,
            method=method,
            path=path,
            execution_mode=execution_mode,
            **optional_kwargs(
                name=name,
                description=description,
                visibility=visibility,
                parameters=_parse_json_arg(parameters, "parameters"),
                request_body=_parse_json_arg(request_body, "request_body"),
                input_assets=_parse_json_arg(input_assets, "input_assets"),
                output_assets=_parse_json_arg(output_assets, "output_assets"),
            ),
        )

        return dump_json(format_asset_summary(route))

    @mcp.tool(annotations={"idempotentHint": True})
    @handle_ouro_errors
    def update_route(
        id: Annotated[str, Field(description='Route UUID or "entity_name/route_name"')],
        ctx: Context,
        method: Annotated[Optional[str], Field(description="New HTTP method")] = None,
        path: Annotated[Optional[str], Field(description="New route path")] = None,
        name: Annotated[Optional[str], Field(description="New display name")] = None,
        description: Annotated[Optional[str], Field(description="New description")] = None,
        visibility: Annotated[
            Optional[str], Field(description='"public" | "private" | "organization"')
        ] = None,
        parameters: Annotated[
            Optional[Any], Field(description="Parameters as a JSON array (object or string)")
        ] = None,
        request_body: Annotated[
            Optional[Any], Field(description="Request body schema as a JSON object (object or string)")
        ] = None,
        input_assets: Annotated[
            Optional[Any], Field(description="Keyed Ouro asset inputs as a JSON object")
        ] = None,
        output_assets: Annotated[
            Optional[Any], Field(description="Keyed Ouro asset outputs as a JSON object")
        ] = None,
        execution_mode: Annotated[
            Optional[str], Field(description='"sync" or "async"')
        ] = None,
    ) -> str:
        """Update a route's method, path, schema, or metadata.

        Only the fields you pass are changed; the route's name is preserved
        when `name` is omitted.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        route = ouro.routes.update(
            id,
            **optional_kwargs(
                method=method,
                path=path,
                name=name,
                description=description,
                visibility=visibility,
                execution_mode=execution_mode,
                parameters=_parse_json_arg(parameters, "parameters"),
                request_body=_parse_json_arg(request_body, "request_body"),
                input_assets=_parse_json_arg(input_assets, "input_assets"),
                output_assets=_parse_json_arg(output_assets, "output_assets"),
            ),
        )

        return dump_json(format_asset_summary(route))

    @mcp.tool(
        annotations={"destructiveHint": True, "openWorldHint": True},
    )
    @handle_ouro_errors
    def execute_route(
        name_or_id: Annotated[
            str,
            Field(description='Route UUID or "entity_name/route_name"'),
        ],
        ctx: Context,
        body: Annotated[
            Optional[Any],
            Field(
                description=(
                    "Route request body as JSON object or string, e.g. "
                    '\'{"key": "value"}\''
                )
            ),
        ] = None,
        query: Annotated[
            Optional[Any],
            Field(description='Query parameters as JSON object or string, e.g. \'{"page": 1}\''),
        ] = None,
        params: Annotated[
            Optional[Any],
            Field(description='Route path parameters as JSON object or string, e.g. \'{"id": "abc"}\''),
        ] = None,
        input_assets: Annotated[
            Optional[Any],
            Field(
                description=(
                    "Keyed Ouro asset inputs as JSON object or string. Values should be "
                    'asset IDs, e.g. \'{"structure": "file-id"}\'.'
                )
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(description="Validate parameters without executing"),
        ] = False,
        wait: Annotated[
            bool,
            Field(
                description=(
                    "If true (default), block until the action reaches a terminal "
                    "state. If false, return the action_id immediately so you can "
                    "check back later via get_action(action_id). Pass false for "
                    "long-running async routes you don't want to block on."
                )
            ),
        ] = True,
        timeout: Annotated[
            int,
            Field(
                description=(
                    "Max seconds to wait for async routes to complete before returning 'pending'. "
                    "Bump for long-running ML/simulation routes. Ignored when wait=false."
                )
            ),
        ] = 300,
    ) -> str:
        """Execute a platform route on Ouro. Use get_asset(route_id) first to see the route's execution schema.

        Billing: monetized routes WILL charge the caller per call. Check the
        route's `monetization` and `cost_summary` fields (via get_asset) before
        executing on a user's behalf, or use `dry_run=true` to get a
        `cost_preview` block in the response. Once the route runs, the
        returned `cost` block reports the actual charge.

        Returns the completed action — data on success, error details on
        failure — plus `action_id`, `action_status`, and a `cost` block for
        monetized pay-per-use routes. Resolved inputs and produced assets
        come back as `input_assets` / `output_assets`: lists of
        `{name, is_primary?, asset: {id, asset_type, name?, description?}}`
        entries, one per named slot the route declared. Discriminate by
        `name` (the slot name from the route's input/output schema), not
        by position; `is_primary: true` marks the canonical entry for
        legacy single-output routes. If the route doesn't complete within
        `timeout`, returns `{status: "pending", action_id}`; call
        `get_action(action_id)` later to check on it. Embed the route with
        `displayConfig.actionId` to render the action inline in Ouro
        markdown.

        Async routes: routes whose `execution_mode == "async"` (or those with a
        large `p95_completion_ms`) typically take a long time. For those, pass
        `wait=false` to get the `action_id` back immediately and check on it
        later via `get_action(action_id)` rather than blocking the agent loop.

        For asset inputs, pass IDs with `input_assets` keyed by route body parameter
        name. Do not construct file/dataset/post body objects by hand; Ouro resolves
        those IDs into the service-facing request body.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        route = ouro.routes.retrieve(name_or_id)
        execution_mode = (
            getattr(route.route, "execution_mode", None) if route.route else None
        ) or "sync"
        metrics = getattr(route, "metrics", None)
        p95_completion_ms = (
            getattr(metrics, "p95_completion_ms", None) if metrics else None
        )
        avg_completion_ms = (
            getattr(metrics, "avg_completion_ms", None) if metrics else None
        )

        body_dict = _parse_json_param(body, "body")
        query_dict = _parse_json_param(query, "query")
        params_dict = _parse_json_param(params, "params")
        input_assets_dict = _parse_json_param(input_assets, "input_assets")

        if dry_run:
            dry_run_payload: dict[str, Any] = {
                "dry_run": True,
                "route_id": str(route.id),
                "name": route.name,
                "execution_mode": execution_mode,
                "p95_completion_ms": p95_completion_ms,
                "avg_completion_ms": avg_completion_ms,
                "expected_parameters": route.route.parameters if route.route else None,
                "expected_request_body": (
                    route_request_body_without_input_assets(route.route)
                    if route.route
                    else None
                ),
                "expected_input_assets": (
                    route_input_assets_summary(route.route) if route.route else None
                ),
                "expected_output_assets": (
                    route_output_assets_summary(route.route) if route.route else None
                ),
            }
            cost_preview = _format_route_cost_preview(route)
            if cost_preview:
                dry_run_payload["cost_preview"] = cost_preview
            return dump_json(dry_run_payload)

        start = time.time()

        try:
            action = ouro.routes.execute(
                name_or_id,
                body=body_dict,
                query=query_dict,
                params=params_dict,
                input_assets=input_assets_dict,
                wait=wait,
                # Pass timeout only when we're actually waiting; otherwise let
                # the SDK skip polling entirely.
                poll_interval=5.0 if wait else None,
                poll_timeout=float(timeout) if wait else None,
            )
        except TimeoutError as exc:
            action_id = getattr(exc, "action_id", None)
            result = {
                "status": "pending",
                "action_id": action_id,
                "route_id": str(route.id),
                "route_name": route.name,
                "execution_mode": execution_mode,
                "p95_completion_ms": p95_completion_ms,
                "message": (
                    f"Route still executing after {timeout}s. "
                    "Call `get_action(action_id)` to check status later, "
                    "or retry with a larger `timeout=`."
                ),
            }
            if action_id:
                result["embed_markdown"] = _route_action_embed(str(route.id), action_id)
            return dump_json(result)

        duration = round(time.time() - start, 2)

        # When wait=false we get an in-progress action back; surface it as a
        # 'pending' result so the agent knows to call get_action later.
        if not wait and action.is_pending:
            result = {
                "status": "pending",
                "action_id": str(action.id),
                "action_status": action.status,
                "route_id": str(route.id),
                "route_name": route.name,
                "execution_mode": execution_mode,
                "p95_completion_ms": p95_completion_ms,
                "message": (
                    "Route accepted; check progress with get_action(action_id)."
                ),
                "embed_markdown": _route_action_embed(str(route.id), str(action.id)),
            }
            return dump_json(result)

        # The sync execution envelope can contain a freshly synthesized action
        # before joined fields like `usage_record` are reloaded. Refresh once so
        # monetized route calls can include the same cost block as get_action().
        try:
            action = ouro.routes.retrieve_action(str(action.id))
        except Exception:
            log.debug(
                "Failed to refresh action %s after route execution",
                getattr(action, "id", None),
                exc_info=True,
            )

        result = _format_action_result(
            action,
            route_id=str(route.id),
            route_name=route.name,
            duration_seconds=duration,
        )
        result["execution_mode"] = execution_mode
        if p95_completion_ms is not None:
            result["p95_completion_ms"] = p95_completion_ms
        return dump_json(result)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_action(
        action_id: Annotated[
            str,
            Field(description="Action UUID returned by execute_route"),
        ],
        ctx: Context,
        wait: Annotated[
            bool,
            Field(
                description=(
                    "If true, poll until the action completes "
                    "(up to `timeout` seconds) before returning."
                )
            ),
        ] = False,
        timeout: Annotated[
            int,
            Field(description="Max seconds to wait when `wait=true`."),
        ] = 300,
        include_logs: Annotated[
            bool,
            Field(description="Include recent action logs in the result"),
        ] = False,
        log_limit: Annotated[
            int,
            Field(description="Max logs to include when include_logs=true"),
        ] = 50,
    ) -> str:
        """Check the status of a route action (execute_route result).

        Use this after `execute_route` returns `{status: "pending", action_id}`,
        or to inspect a past action you want to reference / embed. Set
        `wait=true` to block until the action reaches a terminal state.

        Returns action status, route ID, data/error, output asset info, and embed_markdown.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if wait:
            try:
                action = ouro.routes.poll_action(
                    action_id,
                    poll_interval=5.0,
                    timeout=float(timeout),
                    raise_on_error=False,
                )
            except TimeoutError:
                # Fall through to a snapshot read so the caller at least sees
                # current status + logs context.
                action = ouro.routes.retrieve_action(action_id)
                snapshot = _format_action_result(action, route_id=str(action.route_id))
                snapshot["status"] = "pending"
                snapshot["message"] = (
                    f"Action still in progress after {timeout}s. "
                    "Call `get_action` again later."
                )
                if include_logs:
                    snapshot["logs"] = _get_action_logs_payload(
                        ouro, action_id, limit=log_limit
                    )["results"]
                return dump_json(snapshot)
        else:
            action = ouro.routes.retrieve_action(action_id)

        result = _format_action_result(action, route_id=str(action.route_id))
        if include_logs:
            result["logs"] = _get_action_logs_payload(
                ouro, action_id, limit=log_limit
            )["results"]
        return dump_json(result)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def list_route_actions(
        route_id: Annotated[
            str,
            Field(description="Route UUID whose executions/actions should be listed"),
        ],
        ctx: Context,
        include_other_users: Annotated[
            bool,
            Field(
                description=(
                    "If false, list only your actions. If true, include visible actions "
                    "from all users."
                )
            ),
        ] = False,
        limit: Annotated[int, Field(description="Max actions to return (1-200)")] = 20,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
        status: Annotated[
            Optional[str],
            Field(
                description=(
                    'Optional client-side filter: "queued" | "in-progress" | '
                    '"success" | "error" | "timed-out"'
                )
            ),
        ] = None,
        include_response: Annotated[
            bool,
            Field(description="Include each action response payload. Leave false for compact browsing."),
        ] = False,
    ) -> str:
        """List previous executions for a route.

        Use this when you need to find prior runs to reference or embed. Each
        result includes `embed_markdown`, a ready-to-use route preview block
        pinned to that action's logs/output.
        """
        if limit <= 0 or limit > 200:
            raise ValueError("limit must be between 1 and 200.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")

        ouro = ctx.request_context.lifespan_context.ouro
        route = ouro.routes.retrieve(route_id)
        service_id = str(route.parent_id) if route.parent_id else None
        if not service_id:
            raise ValueError("Route has no parent service; cannot list actions.")

        page = ouro.routes.list_actions(
            str(route.id),
            include_other_users=include_other_users,
            limit=limit,
            offset=offset,
            with_pagination=True,
        )
        actions = page.get("data") or []
        if status:
            allowed = {"queued", "in-progress", "success", "error", "timed-out"}
            if status not in allowed:
                raise ValueError(
                    f"Invalid status={status!r}. Must be one of: {sorted(allowed)}."
                )
            actions = [
                action
                for action in actions
                if _as_dict(action).get("status") == status
            ]

        results = [
            _format_action_summary(action, include_response=include_response)
            for action in actions
        ]
        payload = list_response(
            results,
            pagination=page.get("pagination") or {},
            limit=limit,
            extra={
                "route": {
                    "id": str(route.id),
                    "name": route.name,
                },
                "note": "Use embed_markdown to embed an action preview in Ouro markdown.",
            },
        )
        return truncate_response(dump_json(payload))

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    @handle_ouro_errors
    def get_action_logs(
        action_id: Annotated[str, Field(description="Action UUID")],
        ctx: Context,
        level: Annotated[
            Optional[str],
            Field(description='Optional log level filter, e.g. "info", "warn", or "error"'),
        ] = None,
        limit: Annotated[int, Field(description="Max logs to return (1-500)")] = 100,
        offset: Annotated[int, Field(description="Pagination offset")] = 0,
        sort_order: Annotated[
            str,
            Field(description='"asc" for oldest-first (default) or "desc" for newest-first'),
        ] = "asc",
    ) -> str:
        """Read logs for a route action."""
        ouro = ctx.request_context.lifespan_context.ouro
        return truncate_response(
            dump_json(
                _get_action_logs_payload(
                    ouro,
                    action_id,
                    level=level,
                    limit=limit,
                    offset=offset,
                    sort_order=sort_order,
                )
            )
        )


def _serialize_result(result: Any) -> Any:
    """Ensure route results are JSON-serializable."""
    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, dict):
        return {k: _serialize_result(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [_serialize_result(item) for item in result]
    return str(result)
