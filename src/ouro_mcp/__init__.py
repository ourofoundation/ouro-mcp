"""Ouro MCP Server — exposes Ouro platform capabilities to AI agents via the Model Context Protocol."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ouro-mcp")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"
