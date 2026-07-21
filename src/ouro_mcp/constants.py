from __future__ import annotations

MAX_RESPONSE_SIZE = 50_000  # ~50KB JSON threshold

ENV_OURO_API_KEY = "OURO_API_KEY"
ENV_OURO_BASE_URL = "OURO_BASE_URL"
ENV_OURO_FRONTEND_URL = "OURO_FRONTEND_URL"
ENV_OURO_MCP_TIMEZONE = "OURO_MCP_TIMEZONE"
ENV_WORKSPACE_ROOT = "WORKSPACE_ROOT"
# Optional container mount path (e.g. /workspace). When set alongside
# WORKSPACE_ROOT, absolute paths under this mount remap onto the host root.
ENV_WORKSPACE_MOUNT = "WORKSPACE_MOUNT"

# Public site origin for absolute asset/team links in tool responses.
DEFAULT_OURO_FRONTEND_URL = "https://ouro.foundation"
GLOBAL_ORG_ID = "00000000-0000-0000-0000-000000000000"

# Asset detail comment previews. These defaults keep `get_asset(detail="full")`
# useful for agents without turning it into a full discussion-thread dump.
ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT = "OURO_MCP_COMMENT_PREVIEW_LIMIT"
ENV_OURO_MCP_REPLY_PREVIEW_LIMIT = "OURO_MCP_REPLY_PREVIEW_LIMIT"
ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS = "OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS"
DEFAULT_COMMENT_PREVIEW_LIMIT = 3
DEFAULT_REPLY_PREVIEW_LIMIT = 2
DEFAULT_COMMENT_TEXT_PREVIEW_CHARS = 300
MAX_COMMENT_PREVIEW_LIMIT = 20
MAX_REPLY_PREVIEW_LIMIT = 10
MAX_COMMENT_TEXT_PREVIEW_CHARS = 2_000

# Logging (read by clients via MCP server env, e.g. ouro-agents config.json)
ENV_OURO_MCP_LOG_LEVEL = "OURO_MCP_LOG_LEVEL"
# plain: one-line stderr logs (good when merged with a host process). rich: FastMCP default handler.
ENV_OURO_MCP_LOG_STYLE = "OURO_MCP_LOG_STYLE"

DEFAULT_HTTP_PORT = 8000
