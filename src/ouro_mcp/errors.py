from __future__ import annotations

import functools
import json
import logging
from typing import Any, Callable

from ouro._exceptions import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

log = logging.getLogger(__name__)

MAX_RESPONSE_SIZE = 50_000  # ~50KB JSON threshold


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


def truncate_response(data: str, context: str = "") -> str:
    """If a JSON response exceeds the size threshold, truncate and flag it."""
    if len(data) <= MAX_RESPONSE_SIZE:
        return data
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "rows" in parsed:
            # Progressively remove rows until under limit
            rows = parsed["rows"]
            while len(json.dumps(parsed)) > MAX_RESPONSE_SIZE and rows:
                rows.pop()
            parsed["returned"] = len(rows)
            parsed["truncated"] = True
            if context:
                parsed["note"] = f"Response truncated to fit context window. {context}"
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return data[:MAX_RESPONSE_SIZE] + "\n... [truncated]"


def format_asset_summary(asset: Any) -> dict:
    """Extract a consistent summary dict from any ouro-py asset model."""
    summary = {
        "id": str(asset.id),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "visibility": asset.visibility,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "last_updated": asset.last_updated.isoformat() if asset.last_updated else None,
    }
    if asset.description:
        desc = asset.description
        if isinstance(desc, dict):
            desc = desc.get("text", str(desc))
        summary["description"] = str(desc)[:500]
    if asset.user:
        summary["owner"] = asset.user.username
    return summary
