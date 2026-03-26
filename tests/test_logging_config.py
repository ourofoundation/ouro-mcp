import os

import pytest

from ouro_mcp.constants import ENV_OURO_MCP_LOG_LEVEL
from ouro_mcp.logging_config import resolve_fastmcp_log_level


@pytest.fixture(autouse=True)
def _clear_log_level_env(monkeypatch):
    monkeypatch.delenv(ENV_OURO_MCP_LOG_LEVEL, raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)


def test_resolve_default_info():
    assert resolve_fastmcp_log_level() == "INFO"


def test_resolve_ouro_mcp_env_priority(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_LOG_LEVEL, "warning")
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "DEBUG")
    assert resolve_fastmcp_log_level() == "WARNING"


def test_resolve_fastmcp_fallback(monkeypatch):
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "error")
    assert resolve_fastmcp_log_level() == "ERROR"
