from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any, Callable

from ouro import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

log = logging.getLogger(__name__)


def _format_ouro_error(e: Exception) -> str:
    """Convert an ouro-py exception to an agent-friendly JSON error string."""
    if isinstance(e, NotFoundError):
        return json.dumps({"error": "not_found", "message": str(e)})
    if isinstance(e, AuthenticationError):
        return json.dumps(
            {
                "error": "authentication_failed",
                "message": "Authentication failed. Check your OURO_API_KEY.",
            }
        )
    if isinstance(e, PermissionDeniedError):
        return json.dumps(
            {
                "error": "permission_denied",
                "message": "No permission to access this resource.",
            }
        )
    if isinstance(e, RateLimitError):
        retry_after = None
        if hasattr(e, "response"):
            retry_after = e.response.headers.get("retry-after")
        msg = "Rate limited."
        if retry_after:
            msg += f" Retry after {retry_after} seconds."
        return json.dumps({"error": "rate_limited", "message": msg})
    if isinstance(e, BadRequestError):
        return json.dumps({"error": "bad_request", "message": str(e)})
    if isinstance(e, InternalServerError):
        return json.dumps(
            {
                "error": "server_error",
                "message": "Ouro API error. Try again shortly.",
            }
        )
    if isinstance(e, TimeoutError):
        return json.dumps({"error": "timeout", "message": str(e)})
    log.exception("Unexpected error in MCP tool")
    return json.dumps({"error": "unexpected", "message": str(e)})


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
            TimeoutError,
            Exception,
        ) as e:
            return _format_ouro_error(e)

    return wrapper
