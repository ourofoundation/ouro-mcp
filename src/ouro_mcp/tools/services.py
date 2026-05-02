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
    list_response,
    route_input_assets_summary,
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

    if action.output_asset:
        output_asset_id = action.output_asset.get("id")
        output_asset_type = action.output_asset.get("asset_type")
        if output_asset_id:
            result["output_asset_id"] = output_asset_id
        if output_asset_type:
            result["output_asset_type"] = output_asset_type

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
    input_asset = _compact_asset(action.get("input_asset"))
    if input_asset:
        result["input_asset"] = input_asset
    output_asset = _compact_asset(action.get("output_asset"))
    if output_asset:
        result["output_asset"] = output_asset
        result["output_asset_id"] = output_asset.get("id")
        result["output_asset_type"] = output_asset.get("asset_type")

    if action.get("metadata"):
        result["metadata"] = action.get("metadata")
    if include_response and action.get("response") is not None:
        result["response"] = _serialize_result(action.get("response"))
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


def register(mcp: FastMCP) -> None:
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
                    "Keyed Ouro asset inputs as JSON object or string. Values can be "
                    'asset IDs or objects, e.g. \'{"structure": "file-id"}\'.'
                )
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(description="Validate parameters without executing"),
        ] = False,
        timeout: Annotated[
            int,
            Field(
                description=(
                    "Max seconds to wait for async routes to complete before returning 'pending'. "
                    "Bump for long-running ML/simulation routes."
                )
            ),
        ] = 300,
    ) -> str:
        """Execute a platform route on Ouro. Use get_asset(route_id) first to see the route's execution schema.

        Returns the completed action — data on success, error details on failure —
        plus `action_id`, `action_status`, and any `output_asset_id/type`. If the
        route doesn't complete within `timeout`, returns `{status: "pending", action_id}`;
        call `get_action(action_id)` later to check on it. Embed the route with
        `displayConfig.actionId` to render the action inline in Ouro markdown.

        For asset inputs, pass IDs with `input_assets` keyed by route body parameter
        name. Do not construct file/dataset/post body objects by hand; Ouro resolves
        those IDs into the service-facing request body.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        route = ouro.routes.retrieve(name_or_id)

        body_dict = _parse_json_param(body, "body")
        query_dict = _parse_json_param(query, "query")
        params_dict = _parse_json_param(params, "params")
        input_assets_dict = _parse_json_param(input_assets, "input_assets")

        if dry_run:
            return dump_json({
                "dry_run": True,
                "route_id": str(route.id),
                "name": route.name,
                "expected_parameters": route.route.parameters if route.route else None,
                "expected_request_body": (
                    route_request_body_without_input_assets(route.route)
                    if route.route
                    else None
                ),
                "expected_input_assets": (
                    route_input_assets_summary(route.route) if route.route else None
                ),
            })

        start = time.time()

        try:
            action = ouro.routes.execute(
                name_or_id,
                body=body_dict,
                query=query_dict,
                params=params_dict,
                input_assets=input_assets_dict,
                poll_interval=5.0,
                poll_timeout=float(timeout),
            )
        except TimeoutError as exc:
            action_id = getattr(exc, "action_id", None)
            result = {
                "status": "pending",
                "action_id": action_id,
                "route_id": str(route.id),
                "route_name": route.name,
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
        result = _format_action_result(
            action,
            route_id=str(route.id),
            route_name=route.name,
            duration_seconds=duration,
        )
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
