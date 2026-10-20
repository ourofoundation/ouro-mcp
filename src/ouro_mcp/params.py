"""Parameter types tools share, so the schema shows agents the allowed values.

Enumerations are ``Literal`` (the schema lists them). Structured arguments are
objects and arrays in the schema; a JSON-encoded string is still accepted,
since some clients send one. Status filters take a list or a comma-separated
string.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict
from typing_extensions import TypedDict

AssetType = Literal["post", "dataset", "file", "service", "route", "quest"]
Visibility = Literal["public", "private", "organization", "monetized"]
# Teams are public, or organization-only (internal)
TeamVisibility = Literal["public", "organization"]
TeamRole = Literal["read", "write", "admin"]
ActorTypePolicy = Literal["any", "verified_only", "agents_only"]
SourcePolicy = Literal["any", "web_only", "api_only"]
JoinPolicy = Literal["open", "request", "invite_only"]
ServiceVisibility = Literal["public", "private", "organization"]
# "inherit" follows the route's service
RouteVisibility = Literal["public", "private", "organization", "monetized", "inherit"]
ServiceAuthentication = Literal["None", "Ouro", "Personal Access Token", "OAuth 2.0"]
ActionStatus = Literal["queued", "in-progress", "success", "error", "timed-out"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ExecutionMode = Literal["sync", "async"]
QuestType = Literal["closable", "continuous"]
QuestStatus = Literal["draft", "open", "closed", "cancelled"]
QuestVisibility = Literal["public", "private", "organization"]
QuestItemStatus = Literal["pending", "in_progress", "done", "skipped"]
ResponseFormat = Literal["md", "json"]


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc
    return value


def _split_commas(value: Any) -> Any:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()] or None
    return value


JsonObject = Annotated[dict[str, Any], BeforeValidator(_decode_json)]
JsonArray = Annotated[list[Any], BeforeValidator(_decode_json)]
ActionStatuses = Annotated[list[ActionStatus], BeforeValidator(_split_commas)]
NotificationCategory = Literal["mentions", "comments", "references", "shares", "money", "actions"]
QuestItemStatuses = Annotated[list[Literal[QuestItemStatus, "all"]], BeforeValidator(_split_commas)]
NotificationCategories = Annotated[list[NotificationCategory], BeforeValidator(_split_commas)]


class Citation(TypedDict, total=False):
    """A related paper or other scholarly work."""

    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]

    doi: str
    title: str
    authors: list[str]
    year: int
    venue: str
    url: str
    bibtex: str


class AttributionFields(TypedDict, total=False):
    """Where an asset comes from."""

    # ouro-py's Attribution, minus doi: that one is minted for the asset itself
    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]

    originality: Literal["original", "derivative", "third-party"]
    github_url: str
    paper_url: str
    doi_url: str
    external_url: str
    citation: Citation
    # DataCite relation of this asset to the related work
    relation_type: Literal["IsSupplementTo", "IsDerivedFrom", "References", "IsVariantFormOf", "IsIdenticalTo"]


Attribution = Annotated[AttributionFields, BeforeValidator(_decode_json)]
ATTRIBUTION_DESC = (
    "Provenance: whether this is original work, and links to the repo, paper or DOI "
    'it comes from. Set it when wrapping or republishing someone else\'s work, e.g. '
    '{"originality": "third-party", "github_url": "...", "relation_type": "IsDerivedFrom"}'
)
