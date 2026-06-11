from __future__ import annotations

import os
from dataclasses import dataclass

from ouro_mcp.constants import (
    DEFAULT_COMMENT_PREVIEW_LIMIT,
    DEFAULT_COMMENT_TEXT_PREVIEW_CHARS,
    DEFAULT_REPLY_PREVIEW_LIMIT,
    ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT,
    ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS,
    ENV_OURO_MCP_REPLY_PREVIEW_LIMIT,
    MAX_COMMENT_PREVIEW_LIMIT,
    MAX_COMMENT_TEXT_PREVIEW_CHARS,
    MAX_REPLY_PREVIEW_LIMIT,
)


@dataclass(frozen=True)
class CommentPreviewConfig:
    comment_limit: int = DEFAULT_COMMENT_PREVIEW_LIMIT
    reply_limit: int = DEFAULT_REPLY_PREVIEW_LIMIT
    text_chars: int = DEFAULT_COMMENT_TEXT_PREVIEW_CHARS


def _env_int(name: str, default: int, *, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, min(value, maximum))


def get_comment_preview_config() -> CommentPreviewConfig:
    """Return env-configured limits for asset detail comment previews.

    MCP hosts can set these env vars when launching the server. Values are
    clamped so one aggressive client setting cannot explode response size.
    """
    return CommentPreviewConfig(
        comment_limit=_env_int(
            ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT,
            DEFAULT_COMMENT_PREVIEW_LIMIT,
            maximum=MAX_COMMENT_PREVIEW_LIMIT,
        ),
        reply_limit=_env_int(
            ENV_OURO_MCP_REPLY_PREVIEW_LIMIT,
            DEFAULT_REPLY_PREVIEW_LIMIT,
            maximum=MAX_REPLY_PREVIEW_LIMIT,
        ),
        text_chars=_env_int(
            ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS,
            DEFAULT_COMMENT_TEXT_PREVIEW_CHARS,
            maximum=MAX_COMMENT_TEXT_PREVIEW_CHARS,
        ),
    )
