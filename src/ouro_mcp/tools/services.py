"""Route execution tool — the killer feature."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True, "openWorldHint": True},
    )
    @handle_ouro_errors
    def execute_route(
        name_or_id: Annotated[str, Field(description='Route UUID or "entity_name/route_name"')],
        ctx: Context,
        body: Annotated[Optional[dict], Field(description="Request body (for POST/PUT routes)")] = None,
        query: Annotated[Optional[dict], Field(description="Query parameters")] = None,
        params: Annotated[Optional[dict], Field(description="URL path parameters")] = None,
        dry_run: Annotated[bool, Field(description="Validate parameters without executing")] = False,
        timeout: Annotated[int, Field(description="Max seconds to wait for async routes")] = 120,
    ) -> str:
        """Execute an API route on Ouro. Use get_asset(route_id) first to see the route's parameter schema."""
        ouro = ctx.request_context.lifespan_context.ouro

        route = ouro.routes.retrieve(name_or_id)

        if dry_run:
            return json.dumps({
                "dry_run": True,
                "route_id": str(route.id),
                "name": route.name,
                "method": route.route.method if route.route else None,
                "path": route.route.path if route.route else None,
                "expected_parameters": route.route.parameters if route.route else None,
                "expected_request_body": route.route.request_body if route.route else None,
            })

        method = route.route.method.upper() if route.route else "UNKNOWN"

        start = time.time()

        try:
            result = ouro.routes.use(
                name_or_id,
                body=body,
                query=query,
                params=params,
                wait=True,
                poll_interval=5.0,
                poll_timeout=float(timeout),
            )
        except TimeoutError:
            return json.dumps({
                "status": "pending",
                "message": f"Route still executing after {timeout}s. Use get_asset with the action_id to check status later.",
                "route_id": str(route.id),
                "route_name": route.name,
            })

        duration = round(time.time() - start, 2)

        response: dict[str, Any] = {
            "status": "success",
            "route_id": str(route.id),
            "route_name": route.name,
            "method": method,
            "duration_seconds": duration,
            "data": _serialize_result(result),
        }

        return json.dumps(response, default=str)


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
