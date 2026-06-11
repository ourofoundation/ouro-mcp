from __future__ import annotations

from ouro_mcp.config import get_comment_preview_config
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


def test_comment_preview_config_defaults(monkeypatch):
    monkeypatch.delenv(ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT, raising=False)
    monkeypatch.delenv(ENV_OURO_MCP_REPLY_PREVIEW_LIMIT, raising=False)
    monkeypatch.delenv(ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS, raising=False)

    config = get_comment_preview_config()

    assert config.comment_limit == DEFAULT_COMMENT_PREVIEW_LIMIT
    assert config.reply_limit == DEFAULT_REPLY_PREVIEW_LIMIT
    assert config.text_chars == DEFAULT_COMMENT_TEXT_PREVIEW_CHARS


def test_comment_preview_config_reads_env(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT, "5")
    monkeypatch.setenv(ENV_OURO_MCP_REPLY_PREVIEW_LIMIT, "4")
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS, "120")

    config = get_comment_preview_config()

    assert config.comment_limit == 5
    assert config.reply_limit == 4
    assert config.text_chars == 120


def test_comment_preview_config_clamps_aggressive_env(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT, "999")
    monkeypatch.setenv(ENV_OURO_MCP_REPLY_PREVIEW_LIMIT, "999")
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS, "999999")

    config = get_comment_preview_config()

    assert config.comment_limit == MAX_COMMENT_PREVIEW_LIMIT
    assert config.reply_limit == MAX_REPLY_PREVIEW_LIMIT
    assert config.text_chars == MAX_COMMENT_TEXT_PREVIEW_CHARS


def test_comment_preview_config_allows_zero_to_disable(monkeypatch):
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_PREVIEW_LIMIT, "0")
    monkeypatch.setenv(ENV_OURO_MCP_REPLY_PREVIEW_LIMIT, "0")
    monkeypatch.setenv(ENV_OURO_MCP_COMMENT_TEXT_PREVIEW_CHARS, "0")

    config = get_comment_preview_config()

    assert config.comment_limit == 0
    assert config.reply_limit == 0
    assert config.text_chars == 0
