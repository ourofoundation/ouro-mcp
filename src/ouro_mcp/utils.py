"""Small shared helpers: argument handling, asset type lookups, locations and team policies."""

from __future__ import annotations

import json
from typing import Any, Optional

_ASSET_RESOURCES = {
    "post": "posts",
    "comment": "comments",
    "file": "files",
    "dataset": "datasets",
    "service": "services",
    "route": "routes",
    "quest": "quests",
}


def asset_type_of(ouro: Any, id: str) -> Optional[str]:
    """An asset's type, looked up without recording a view.

    ``ouro.assets.retrieve`` records a view on every call, so a tool that only
    needs the type (or reads an asset the agent did not ask to open) would
    inflate the asset's view count.
    """
    assets = ouro.assets
    data = assets._handle_response(assets.client.get(f"/assets/{id}/type")) or {}
    return data.get("asset_type")


def retrieve_without_view(ouro: Any, id: str) -> Any:
    """Retrieve any asset through its typed resource, without recording a view."""
    asset_type = asset_type_of(ouro, id)
    resource = _ASSET_RESOURCES.get(asset_type or "")
    if resource is None:
        raise ValueError(f"Asset {id} has unsupported type {asset_type!r}.")
    return getattr(ouro, resource).retrieve(id)


def getv(obj: Any, key: str, default: Any = None) -> Any:
    """Get a value from a dict or object attribute, whichever applies."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def as_dict(value: Any) -> dict[str, Any]:
    """A model or dict as a JSON-ready dict; anything else as ``{}``."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


# Models fill unused optionals with blanks or string-nulls ("null", "/null")
# instead of omitting the key or sending JSON null. Filter params only — never
# a real org/team/user id, enum, or time_window.
_ABSENT_OPTIONAL_STRINGS = frozenset({"null", "none", "undefined", "/null"})


def is_absent_optional(value: Any) -> bool:
    """True when an optional filter should be treated as omitted."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or stripped.lower() in _ABSENT_OPTIONAL_STRINGS
    return False


def optional_kwargs(**kw: Any) -> dict:
    """Build a kwargs dict, dropping any keys whose value is None."""
    return {k: v for k, v in kw.items() if v is not None}


def resolve_location(
    ouro: Any,
    org_id: Optional[str],
    team_id: Optional[str] = None,
    *,
    need_team: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    """Where a new asset goes: the caller's choice, else the pinned organization.

    When the client is pinned (OURO_ORG_ID, or X-Ouro-Org over HTTP) the SDK
    fills in the organization and default team and refuses any other
    organization. Unpinned, the caller has to say where to publish.
    """
    pinned = getattr(ouro, "organization", None)
    if isinstance(pinned, str) and pinned:
        return org_id or pinned, team_id
    if not org_id or (need_team and not team_id):
        needed = "org_id and team_id are" if need_team else "org_id is"
        raise ValueError(
            f"{needed} required: this server is not pinned to an organization. "
            "Call list_organizations() and list_teams(org_id=...) to choose where to publish."
        )
    return org_id, team_id


def present_kwargs(**kw: Any) -> dict:
    """Like optional_kwargs, also dropping blank and string-null sentinels.

    Use for filter/search params where models often send ``""``, ``"null"``,
    or ``"/null"`` for unused optionals. Do **not** use for update fields
    that treat ``""`` as an explicit clear (e.g. quest waiting_*).
    """
    return {k: v for k, v in kw.items() if not is_absent_optional(v)}


def resolve_team_policy(team: Any, field: str, default: str = "any") -> str:
    """Return the effective policy for a team, falling back to the org's policy."""
    return getattr(team, field) or getattr(team.organization, field, None) or default


_JSON_KIND_NAMES = {dict: "a JSON object", list: "a JSON array"}


def json_arg(value: Any, name: str, *, kind: type | tuple[type, ...] = (dict, list)) -> Any:
    """A structured argument that may arrive JSON-encoded, or ``None`` when omitted.

    Some clients send objects and arrays as JSON strings. Anything that is
    not valid JSON of the expected ``kind`` raises :class:`ValueError`, which
    tools report as ``invalid_arguments``, so the agent corrects the call
    instead of running without the input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} is not valid JSON: {exc}.") from exc
    if not isinstance(value, kind):
        kinds = kind if isinstance(kind, tuple) else (kind,)
        expected = " or ".join(_JSON_KIND_NAMES.get(k, k.__name__) for k in kinds)
        raise ValueError(f"{name} must be {expected} (got {type(value).__name__}).")
    return value
