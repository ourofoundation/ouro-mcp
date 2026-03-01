from __future__ import annotations

import json
from typing import Any

from ouro_mcp.constants import MAX_RESPONSE_SIZE


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
            parsed["count"] = len(rows)
            parsed["truncated"] = True
            if context:
                parsed["note"] = f"Response truncated to fit context window. {context}"
            return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    return data[:MAX_RESPONSE_SIZE] + "\n... [truncated]"


def format_asset_summary(asset: Any) -> dict:
    """Extract a consistent summary dict from any ouro-py asset model."""
    from ouro.utils.content import description_to_markdown

    summary = {
        "id": str(asset.id),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "visibility": asset.visibility,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "last_updated": asset.last_updated.isoformat() if asset.last_updated else None,
    }
    if asset.description:
        summary["description"] = description_to_markdown(asset.description, max_length=500)
    if asset.user:
        summary["owner"] = asset.user.username
    return summary


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


def content_from_markdown(ouro: Any, markdown: str) -> Any:
    """Create a Content object from markdown using the Ouro client."""
    content = ouro.posts.Content()
    content.from_markdown(markdown)
    return content


def file_result(file: Any) -> dict:
    """Build a standard result dict for a file asset, including data URL and metadata."""
    result = format_asset_summary(file)
    if file.data:
        result["url"] = file.data.url
    if file.metadata and hasattr(file.metadata, "type"):
        result["mime_type"] = file.metadata.type
    if file.metadata and hasattr(file.metadata, "size"):
        result["size"] = file.metadata.size
    return result
