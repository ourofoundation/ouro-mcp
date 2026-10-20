"""Where file bytes come from: signed upload ids, or local paths when the server allows them."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from ouro_mcp.constants import (
    ENV_OURO_MCP_LOCAL_FILES,
    ENV_WORKSPACE_MOUNT,
    ENV_WORKSPACE_ROOT,
)

log = logging.getLogger(__name__)


def local_files_enabled() -> bool:
    raw = os.environ.get(ENV_OURO_MCP_LOCAL_FILES, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def source_names(path_param: str, *others: str) -> str:
    """The content sources a tool accepts on this server, for error messages.

    ``path_param`` is the local-path parameter, which the hosted server does
    not offer.
    """
    names = ([path_param] if local_files_enabled() else []) + list(others)
    if len(names) < 3:
        return " or ".join(names)
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def read_upload(ouro: Any, upload_id: str, suffixes: tuple[str, ...]) -> bytes:
    """Bytes uploaded through ``create_upload_url``, checked by file extension.

    The upload is kept: call ``discard_upload`` once the content has been
    used, so a failed create does not cost the caller a second upload.
    """
    suffix = Path(upload_id).suffix.lower()
    if suffix not in suffixes:
        raise ValueError(
            f"upload_id is for a '{suffix or 'no extension'}' file; expected {' or '.join(suffixes)}. "
            "Name the file accordingly in create_upload_url."
        )
    return ouro.files.read_upload(upload_id, discard=False)


def discard_upload(ouro: Any, upload_id: Optional[str]) -> None:
    """Delete an upload whose content has been used. A failure only leaves an orphan."""
    if not upload_id:
        return
    try:
        ouro.files.discard_upload(upload_id)
    except Exception:
        log.warning("Failed to discard upload %s", upload_id, exc_info=True)


def resolve_local_path(raw: str) -> Path:
    """Resolve a user-supplied file path, sandboxing to WORKSPACE_ROOT when set.

    The hosted HTTP server sets ``OURO_MCP_LOCAL_FILES=0``. Remote clients
    must send file contents inline; a path would otherwise read or write the
    machine that runs the server.

    When WORKSPACE_ROOT is set (typically to the calling agent's workspace
    directory) the resolved path MUST stay inside that root: relative paths
    are joined to it, absolute and ``~``-relative paths are accepted only
    when they already point inside it, and ``..`` traversal that escapes
    the root is rejected with ``PermissionError``.

    When WORKSPACE_MOUNT is also set (e.g. ``/workspace`` for a Docker
    sandbox), absolute paths under that mount are rewritten onto
    WORKSPACE_ROOT so container-style paths work from a host-side MCP
    process.

    When WORKSPACE_ROOT is not set (e.g. a desktop user running the MCP
    standalone) the path is returned as-is after ``~`` expansion and
    resolution, with no sandboxing.
    """
    if not local_files_enabled():
        raise PermissionError(
            "Local file paths are disabled on this MCP server. To send a file, upload it with "
            "create_upload_url and pass the upload_id; to fetch one, call download_asset "
            "without output_path for a link."
        )

    p = Path(raw).expanduser()
    workspace_env = os.environ.get(ENV_WORKSPACE_ROOT)

    if not workspace_env:
        return p.resolve()

    workspace = Path(workspace_env).expanduser().resolve()

    mount_env = (os.environ.get(ENV_WORKSPACE_MOUNT) or "").strip()
    if mount_env and p.is_absolute():
        mount = Path(mount_env)
        try:
            rel = p.relative_to(mount)
        except ValueError:
            rel = None
        if rel is not None:
            p = workspace / rel

    candidate = (workspace / p) if not p.is_absolute() else p
    resolved = candidate.resolve()

    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(
            f"Path '{raw}' escapes the agent workspace. "
            "Use a relative path or a path under the workspace root."
        ) from exc

    return resolved
