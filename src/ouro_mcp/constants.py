from __future__ import annotations

MAX_RESPONSE_SIZE = 50_000  # ~50KB JSON threshold

ENV_OURO_API_KEY = "OURO_API_KEY"
ENV_OURO_BASE_URL = "OURO_BASE_URL"
ENV_WORKSPACE_ROOT = "WORKSPACE_ROOT"

# Logging (read by clients via MCP server env, e.g. ouro-agents config.json)
ENV_OURO_MCP_LOG_LEVEL = "OURO_MCP_LOG_LEVEL"
# plain: one-line stderr logs (good when merged with a host process). rich: FastMCP default handler.
ENV_OURO_MCP_LOG_STYLE = "OURO_MCP_LOG_STYLE"

DEFAULT_HTTP_PORT = 8000
