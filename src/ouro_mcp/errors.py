from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any, Callable

from ouro import (
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

try:
    from ouro import RouteExecutionError
except ImportError:  # ouro-py < 0.5.3
    class RouteExecutionError(Exception):  # type: ignore[no-redef]
        """Fallback stub when running against older ouro-py."""

        action_id: str | None = None
        status: str | None = None
        response: Any = None
        message: str = ""
        retryable: bool | None = None

try:
    from ouro import ExternalServiceError
except ImportError:  # ouro-py < external service typed errors
    class ExternalServiceError(RouteExecutionError):  # type: ignore[no-redef]
        status_code: int | None = None
        code: str | None = None

try:
    from ouro import APIConnectionError, APITimeoutError
except ImportError:  # ouro-py < 0.5.4 (pre-transport-error mapping)
    class APIConnectionError(Exception):  # type: ignore[no-redef]
        """Fallback stub for older ouro-py installs."""

        request: Any = None

    class APITimeoutError(APIConnectionError):  # type: ignore[no-redef]
        """Fallback stub for older ouro-py installs."""

log = logging.getLogger(__name__)

# Money-moving tools: timeouts/connection errors must not be auto-retried by
# agents (a late success after retry could double-spend).
_NON_RETRYABLE_TRANSPORT_TOOLS = frozenset({"send_money", "unlock_asset"})


def _request_url(e: Exception) -> str | None:
    """Best-effort extraction of the attempted URL from an APIConnectionError."""
    request = getattr(e, "request", None)
    if request is None:
        return None
    url = getattr(request, "url", None)
    return str(url) if url else None


def _server_error_object(e: APIStatusError) -> dict[str, Any] | None:
    """Return the nested ``error`` object from an APIStatusError body, if any."""
    body = getattr(e, "body", None)
    if not isinstance(body, dict):
        return None
    error_obj = body.get("error")
    return error_obj if isinstance(error_obj, dict) else None


def _server_detail(e: APIStatusError) -> str | None:
    """Extract a sanitized server-side explanation from an APIStatusError.

    Prefers the server's ``message`` field if present (that's the human
    string we surface in the backend), falling back to common alternates
    and finally the exception's own message.
    """
    error_obj = _server_error_object(e)
    if error_obj is not None:
        for key in ("message", "detail", "reason"):
            value = error_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        error_obj_raw = body.get("error")
        if isinstance(error_obj_raw, str) and error_obj_raw.strip():
            return error_obj_raw.strip()
        for key in ("message", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    message = getattr(e, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _attach_sql_diagnostics(
    payload: dict[str, Any], e: APIStatusError, *, message: str
) -> dict[str, Any]:
    """Preserve backend SQL ``code``/``hint``/``details`` on query errors."""
    error_obj = _server_error_object(e) or {}
    for key in ("code", "hint", "details"):
        value = error_obj.get(key)
        if value is not None and value != "":
            payload[key] = value

    # Agents often paste mixed-case schema names without quotes; Postgres
    # folds those to lowercase and reports "column does not exist".
    if "column" in message.lower() and "does not exist" in message.lower():
        payload["error"] = "invalid_dataset_query"
        payload["retryable"] = False
        guidance = (
            "Re-read the dataset schema and use the exact lowercase "
            "snake_case column names unquoted. Mixed-case names are "
            "normalized on write."
        )
        existing = payload.get("hint")
        if isinstance(existing, str) and existing.strip():
            if "snake_case" not in existing and "schema" not in existing.lower():
                payload["hint"] = f"{existing.strip()} {guidance}"
        else:
            payload["hint"] = guidance
    return payload


def _status_code(e: Exception) -> int | None:
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(e, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _retryable_for_status(status: int | None) -> bool | None:
    if status is None:
        return None
    return status in {408, 429, 500, 502, 503, 504}


def _base_error_payload(error: str, message: str, *, status: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": error, "message": message}
    if status is not None:
        payload["status"] = status
        retryable = _retryable_for_status(status)
        if retryable is not None:
            payload["retryable"] = retryable
    return payload


def _format_ouro_error(e: Exception, *, tool_name: str | None = None) -> str:
    """Convert an ouro-py exception to an agent-friendly JSON error string."""
    raw = str(e)
    raw_lower = raw.lower()
    force_non_retryable_transport = tool_name in _NON_RETRYABLE_TRANSPORT_TOOLS

    # Known server-side failures that agents should handle without retries.
    if "json object requested, multiple (or no) rows returned" in raw_lower or "thread depth" in raw_lower:
        return json.dumps(
            {
                "error": "nested_reply_failed",
                "message": "Nested reply failed. Do not retry repeatedly; reply under the top-level comment in the thread and mention the target user as inline code, e.g. `@username`.",
                "retryable": False,
            }
        )
    if "unique_user_to_team_role" in raw_lower:
        return json.dumps(
            {
                "error": "already_team_member",
                "message": "User is already a member of this team.",
                "retryable": False,
            }
        )

    if isinstance(e, NotFoundError):
        return json.dumps(_base_error_payload("not_found", _server_detail(e) or raw, status=404))
    if isinstance(e, AuthenticationError):
        return json.dumps(
            {
                "error": "authentication_failed",
                "message": "Authentication failed. Check your OURO_API_KEY.",
                "status": 401,
                "retryable": False,
            }
        )
    if isinstance(e, PermissionDeniedError):
        detail = _server_detail(e)
        return json.dumps(
            {
                "error": "permission_denied",
                "message": detail or "No permission to access this resource.",
                "status": 403,
                "retryable": False,
            }
        )
    if isinstance(e, RateLimitError):
        retry_after = None
        if hasattr(e, "response"):
            retry_after = e.response.headers.get("retry-after")
        msg = _server_detail(e) or "Rate limited."
        if retry_after:
            msg += f" Retry after {retry_after} seconds."
        payload = _base_error_payload("rate_limited", msg, status=429)
        if retry_after:
            payload["retry_after_seconds"] = retry_after
        return json.dumps(payload)
    if isinstance(e, BadRequestError):
        message = _server_detail(e) or raw
        payload = _base_error_payload("bad_request", message, status=400)
        payload["retryable"] = False
        return json.dumps(_attach_sql_diagnostics(payload, e, message=message))
    if isinstance(e, InternalServerError):
        detail = _server_detail(e) or "Ouro API error. Try again shortly."
        payload = _base_error_payload(
            "server_error",
            detail,
            status=_status_code(e) or 500,
        )
        # Dataset SQL mistakes should be 400 after the backend mapping; if a
        # column-missing error still arrives as 500, make it actionable.
        if "column" in detail.lower() and "does not exist" in detail.lower():
            payload = _attach_sql_diagnostics(payload, e, message=detail)
        return json.dumps(payload)
    if isinstance(e, ExternalServiceError):
        payload = _base_error_payload(
            "external_service_error",
            e.message,
            status=getattr(e, "status_code", None),
        )
        payload["action_id"] = e.action_id
        payload["action_status"] = e.status
        payload["response"] = e.response
        payload["code"] = getattr(e, "code", None)
        if getattr(e, "retryable", None) is not None:
            payload["retryable"] = e.retryable
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)
    if isinstance(e, RouteExecutionError):
        payload: dict[str, Any] = {
            "error": "route_execution_failed",
            "message": e.message,
        }
        if getattr(e, "response", None) is not None:
            payload["response"] = e.response
        if e.action_id:
            payload["action_id"] = e.action_id
        if e.status:
            payload["action_status"] = e.status
        if getattr(e, "retryable", None) is not None:
            payload["retryable"] = e.retryable
        return json.dumps(payload, default=str)
    # APITimeoutError is a subclass of APIConnectionError, so check it first.
    if isinstance(e, APITimeoutError):
        payload = {
            "error": "timeout",
            "message": raw or "Request to Ouro API timed out.",
            "retryable": False if force_non_retryable_transport else True,
        }
        url = _request_url(e)
        if url:
            payload["url"] = url
        return json.dumps(payload)
    if isinstance(e, APIConnectionError):
        payload = {
            "error": "connection_failed",
            "message": raw or "Failed to connect to Ouro API.",
            "retryable": False if force_non_retryable_transport else True,
        }
        url = _request_url(e)
        if url:
            payload["url"] = url
        return json.dumps(payload)
    if isinstance(e, TimeoutError):
        return json.dumps(
            {
                "error": "timeout",
                "message": raw,
                "retryable": False if force_non_retryable_transport else True,
            }
        )
    if isinstance(e, ValueError):
        return json.dumps({"error": "invalid_arguments", "message": raw, "retryable": False})
    if isinstance(e, PermissionError):
        return json.dumps(
            {
                "error": "workspace_path_denied",
                "message": raw or "Path is outside the agent workspace.",
                "retryable": False,
            }
        )
    log.exception("Unexpected error in MCP tool")
    return json.dumps({"error": "unexpected", "message": raw})


def handle_ouro_errors(fn: Callable) -> Callable:
    """Decorator that catches ouro-py exceptions and returns agent-friendly error messages.

    Works for both sync and async tool functions.
    """
    tool_name = getattr(fn, "__name__", None)

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (
                NotFoundError,
                AuthenticationError,
                PermissionDeniedError,
                RateLimitError,
                BadRequestError,
                InternalServerError,
                RouteExecutionError,
                APITimeoutError,
                APIConnectionError,
                TimeoutError,
                Exception,
            ) as e:
                return _format_ouro_error(e, tool_name=tool_name)

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except (
            NotFoundError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
            BadRequestError,
            InternalServerError,
            RouteExecutionError,
            APITimeoutError,
            APIConnectionError,
            TimeoutError,
            Exception,
        ) as e:
            return _format_ouro_error(e, tool_name=tool_name)

    return wrapper
