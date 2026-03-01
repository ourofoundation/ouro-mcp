from __future__ import annotations

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


def handle_ouro_errors(fn: Callable) -> Callable:
    """Decorator that catches ouro-py exceptions and returns agent-friendly error messages."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except NotFoundError as e:
            return json.dumps({"error": "not_found", "message": str(e)})
        except AuthenticationError:
            return json.dumps(
                {
                    "error": "authentication_failed",
                    "message": "Authentication failed. Check your OURO_API_KEY.",
                }
            )
        except PermissionDeniedError:
            return json.dumps(
                {
                    "error": "permission_denied",
                    "message": "No permission to access this resource.",
                }
            )
        except RateLimitError as e:
            retry_after = None
            if hasattr(e, "response"):
                retry_after = e.response.headers.get("retry-after")
            msg = "Rate limited."
            if retry_after:
                msg += f" Retry after {retry_after} seconds."
            return json.dumps({"error": "rate_limited", "message": msg})
        except BadRequestError as e:
            return json.dumps({"error": "bad_request", "message": str(e)})
        except InternalServerError:
            return json.dumps(
                {
                    "error": "server_error",
                    "message": "Ouro API error. Try again shortly.",
                }
            )
        except TimeoutError as e:
            return json.dumps({"error": "timeout", "message": str(e)})
        except Exception as e:
            log.exception("Unexpected error in MCP tool")
            return json.dumps({"error": "unexpected", "message": str(e)})

    return wrapper
