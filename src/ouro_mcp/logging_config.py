"""Centralized logging behavior for ouro-mcp (stdio-friendly, env-controlled)."""

from __future__ import annotations

import logging
import os
import sys
from io import TextIOBase
from typing import Literal, TextIO

from ouro_mcp.constants import ENV_OURO_MCP_LOG_LEVEL, ENV_OURO_MCP_LOG_STYLE

FastMCPLogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_LEVEL_NAMES: tuple[FastMCPLogLevel, ...] = (
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
)

_RESET = "\033[0m"
_DIM = "\033[2m"
# levelname
_C_DEBUG = "\033[36m"
_C_INFO = "\033[32m"
_C_WARNING = "\033[33m"
_C_ERROR = "\033[31m"
_C_CRITICAL = "\033[1;31m"
# [tag]
_C_TAG = "\033[94m"


def want_color_for_stream(stream: TextIO | None = None) -> bool:
    """Respects https://no-color.org/ and common force-color env vars."""
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("FORCE_COLOR", "").strip() or os.environ.get("CLICOLOR_FORCE", "").strip():
        return True
    mode = os.environ.get("OURO_LOG_COLOR", "").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    s = stream if stream is not None else sys.stderr
    try:
        return s.isatty()
    except Exception:
        return False


class TaggedColoredFormatter(logging.Formatter):
    """One-line stderr format with optional ANSI colors (TTY + NO_COLOR aware)."""

    def __init__(
        self,
        tag: str,
        *,
        datefmt: str | None = None,
        use_colors: bool | None = None,
        stream: TextIO | None = None,
    ):
        super().__init__(datefmt=datefmt)
        self.tag = tag
        self._use_colors = use_colors
        self._stream = stream

    def _colors(self) -> bool:
        if self._use_colors is not None:
            return self._use_colors
        return want_color_for_stream(self._stream or sys.stderr)

    @staticmethod
    def _paint_level(levelno: int, name: str, colors: bool) -> str:
        if not colors:
            return name
        if levelno <= logging.DEBUG:
            return f"{_C_DEBUG}{name}{_RESET}"
        if levelno == logging.INFO:
            return f"{_C_INFO}{name}{_RESET}"
        if levelno == logging.WARNING:
            return f"{_C_WARNING}{name}{_RESET}"
        if levelno == logging.ERROR:
            return f"{_C_ERROR}{name}{_RESET}"
        if levelno >= logging.CRITICAL:
            return f"{_C_CRITICAL}{name}{_RESET}"
        return name

    def format(self, record: logging.LogRecord) -> str:
        colors = self._colors()
        ts = self.formatTime(record, self.datefmt)
        if colors:
            ts = f"{_DIM}{ts}{_RESET}"
        levelname = self._paint_level(record.levelno, record.levelname, colors)
        tag = f"[{self.tag}]"
        if colors:
            tag = f"{_C_TAG}{tag}{_RESET}"
        body = f"{ts} {levelname} {tag} {record.name}: {record.getMessage()}"
        if record.exc_info:
            body += "\n" + self.formatException(record.exc_info)
        return body


def resolve_fastmcp_log_level() -> FastMCPLogLevel:
    """Level for FastMCP root logging.

    ``OURO_MCP_LOG_LEVEL`` is preferred for host-controlled MCP subprocesses.
    ``FASTMCP_LOG_LEVEL`` is honored for compatibility with FastMCP's env convention.
    """
    for key in (ENV_OURO_MCP_LOG_LEVEL, "FASTMCP_LOG_LEVEL"):
        raw = os.environ.get(key, "").strip().upper()
        if raw in _LEVEL_NAMES:
            return raw  # type: ignore[return-value]
    return "INFO"


def _log_style() -> str:
    return os.environ.get(ENV_OURO_MCP_LOG_STYLE, "plain").strip().lower()


def apply_ouro_mcp_logging(fastmcp_level: FastMCPLogLevel) -> None:
    """Run immediately after :class:`mcp.server.fastmcp.FastMCP` is constructed.

    FastMCP already called ``configure_logging``; we optionally replace the root
    handler and always tune noisy library loggers for embedded stdio use.
    """
    root = logging.getLogger()
    level = getattr(logging, fastmcp_level)

    if _log_style() == "plain":
        root.handlers.clear()
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            TaggedColoredFormatter(
                tag="ouro-mcp",
                datefmt="%Y-%m-%d %H:%M:%S",
                stream=sys.stderr,
            )
        )
        root.addHandler(handler)

    root.setLevel(level)

    debug = fastmcp_level == "DEBUG"
    low = logging.getLogger("mcp.server.lowlevel.server")
    low.setLevel(logging.DEBUG if debug else logging.WARNING)

    logging.getLogger("uvicorn").setLevel(logging.WARNING if not debug else logging.DEBUG)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING if not debug else logging.DEBUG)
