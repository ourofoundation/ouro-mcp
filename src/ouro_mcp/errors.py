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
        service_url: str | None = None
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


def _request_url(e: Exception) -> str | None:
    """Best-effort extraction of the attempted URL from an APIConnectionError."""
    request = getattr(e, "request", None)
    if request is None:
        return None
    url = getattr(request, "url", None)
    return str(url) if url else None


def _server_detail(e: APIStatusError) -> str | None:
    """Extract a sanitized server-side explanation from an APIStatusError.

    Prefers the server's ``message`` field if present (that's the human
    string we surface in the backend), falling back to common alternates
    and finally the exception's own message.
    """
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            for key in ("message", "detail", "reason"):
                value = error_obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        elif isinstance(error_obj, str) and error_obj.strip():
            return error_obj.strip()
        for key in ("message", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    message = getattr(e, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


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


def _format_ouro_error(e: Exception) -> str:
    """Convert an ouro-py exception to an agent-friendly JSON error string."""
    raw = str(e)
    raw_lower = raw.lower()

    # Known server-side failures that agents should handle without retries.
    if "json object requested, multiple (or no) rows returned" in raw_lower or "thread depth" in raw_lower:
        return json.dumps(
            {
                "error": "nested_reply_failed",
                "message": "Nested reply failed. Do not retry repeatedly; post a root-level follow-up and mention the target user as @username.",
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
        return json.dumps(
            _base_error_payload("bad_request", _server_detail(e) or raw, status=400)
        )
    if isinstance(e, InternalServerError):
        detail = _server_detail(e)
        return json.dumps(
            _base_error_payload(
                "server_error",
                detail or "Ouro API error. Try again shortly.",
                status=_status_code(e) or 500,
            )
        )
    if isinstance(e, ExternalServiceError):
        payload = _base_error_payload(
            "external_service_error",
            e.message,
            status=getattr(e, "status_code", None),
        )
        payload["action_id"] = e.action_id
        payload["action_status"] = e.status
        payload["response"] = e.response
        payload["service_url"] = getattr(e, "service_url", None)
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
            "retryable": True,
        }
        url = _request_url(e)
        if url:
            payload["url"] = url
        return json.dumps(payload)
    if isinstance(e, APIConnectionError):
        payload = {
            "error": "connection_failed",
            "message": raw or "Failed to connect to Ouro API.",
            "retryable": True,
        }
        url = _request_url(e)
        if url:
            payload["url"] = url
        return json.dumps(payload)
    if isinstance(e, TimeoutError):
        return json.dumps({"error": "timeout", "message": raw, "retryable": True})
    if isinstance(e, ValueError):
        return json.dumps({"error": "invalid_arguments", "message": raw, "retryable": False})
    log.exception("Unexpected error in MCP tool")
    return json.dumps({"error": "unexpected", "message": raw})


def handle_ouro_errors(fn: Callable) -> Callable:
    """Decorator that catches ouro-py exceptions and returns agent-friendly error messages.

    Works for both sync and async tool functions.
    """
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
                return _format_ouro_error(e)

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
            return _format_ouro_error(e)

    return wrapper
