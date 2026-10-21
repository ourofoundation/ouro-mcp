"""Route contracts for agents: input and output assets, properties, charts and request bodies."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from ouro.utils.route_outputs import (
    request_body_json_schema,
    resolve_route_outputs,
    route_output_when_matches,
)

from ouro_mcp.utils import (
    getv,
    optional_kwargs,
)


def route_input_assets_summary(route: Any) -> dict[str, Any] | None:
    """Return the simple keyed asset-input contract an agent should use.

    Prefers ``input_assets`` (plural keyed declarations). Falls back to a
    single-entry summary synthesized from the legacy ``input_type`` column
    when the route hasn't been migrated yet. The result is keyed by the
    request body field name the route expects.
    """
    raw = getv(route, "input_assets") or {}
    result: dict[str, Any] = {}

    if isinstance(raw, dict):
        for name, config in raw.items():
            if hasattr(config, "model_dump"):
                config = config.model_dump(exclude_none=True)
            elif not isinstance(config, dict):
                config = {}
            result[name] = optional_kwargs(
                asset_type=config.get("asset_type"),
                primary=config.get("primary"),
                input_filter=config.get("input_filter"),
                file_extensions=config.get("file_extensions"),
                contains_file_extensions=config.get("contains_file_extensions"),
            )

    input_type = getv(route, "input_type")
    if input_type and not result:
        legacy_extensions = (
            getv(route, "input_file_extensions")
            or ([getv(route, "input_file_extension")] if getv(route, "input_file_extension") else None)
        )
        result[str(input_type)] = optional_kwargs(
            asset_type=input_type,
            primary=True,
            input_filter=getv(route, "input_filter"),
            file_extensions=legacy_extensions,
        )
    elif input_type and len(result) == 1:
        # Sparse keyed rows (e.g. `{file: {}}`) inherit the legacy primary
        # projection so agents still see the declared extension filter.
        sole_name, sole_config = next(iter(result.items()))
        sparse = not sole_config.get("asset_type") and not sole_config.get(
            "file_extensions"
        )
        if sparse or not sole_config.get("asset_type"):
            sole_config["asset_type"] = sole_config.get("asset_type") or input_type
        if not sole_config.get("file_extensions"):
            legacy_extensions = (
                getv(route, "input_file_extensions")
                or (
                    [getv(route, "input_file_extension")]
                    if getv(route, "input_file_extension")
                    else None
                )
            )
            if legacy_extensions:
                sole_config["file_extensions"] = legacy_extensions
        if not sole_config.get("input_filter"):
            legacy_filter = getv(route, "input_filter")
            if legacy_filter:
                sole_config["input_filter"] = legacy_filter
        if sparse and sole_config.get("primary") is None:
            sole_config["primary"] = True
        result[sole_name] = optional_kwargs(**sole_config)

    return result or None


def route_properties_summary(route: Any) -> list[dict[str, Any]] | None:
    """The asset properties a route's runs record (``x-ouro-properties``).

    Each entry names the property, the response fields its value (and
    uncertainty and conditions) come from, and the asset it is recorded on
    (``subject``; omitted = primary input).
    """
    raw = getv(route, "properties") or []
    result = []
    for declaration in raw:
        if hasattr(declaration, "model_dump"):
            declaration = declaration.model_dump(exclude_none=True)
        if not isinstance(declaration, dict) or not declaration.get("property"):
            continue
        result.append(
            optional_kwargs(
                property=declaration.get("property"),
                value=declaration.get("value"),
                unit=declaration.get("unit"),
                uncertainty=declaration.get("uncertainty"),
                method=declaration.get("method"),
                conditions=declaration.get("conditions"),
                subject=declaration.get("subject"),
                # Only recorded for runs whose request matches
                when=declaration.get("when"),
                # ...and whose response meets these
                requires=declaration.get("requires"),
            )
        )
    return result or None


def route_output_assets_for_body(
    route: Any, body: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The output contract with what a run sending ``body`` will create.

    Each output gains ``will_create`` (its ``when`` matches the body, or the
    schema defaults for fields left out) and, when it goes into another
    output that is also created, ``embedded: true``.
    """
    summary = route_output_assets_summary(route)
    if not summary:
        return summary
    resolved = {
        output["name"]: output
        for output in resolve_route_outputs(
            getv(route, "output_assets"), getv(route, "request_body"), body or {}
        )
    }
    for name, entry in summary.items():
        # An output only the legacy output_type declares has no `when`
        output = resolved.get(name)
        entry["will_create"] = output["active"] if output else True
        if output and output["embedded"]:
            entry["embedded"] = True
    return summary


def route_properties_for_body(
    route: Any, body: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    """The property declarations with ``will_record``: whether a run sending
    ``body`` matches the declaration's ``when``. A run records the value only
    when its response also meets ``requires``."""
    summary = route_properties_summary(route)
    if not summary:
        return summary
    request_body = getv(route, "request_body")
    for entry in summary:
        entry["will_record"] = route_output_when_matches(
            entry.get("when"), body or {}, request_body
        )
    return summary


def route_response_charts_summary(route: Any) -> list[dict[str, Any]] | None:
    """The charts Ouro draws on every run (``x-ouro-response-charts``): the
    response list each one plots, and the dataset output that opens on it."""
    result = []
    for chart in getv(route, "response_charts") or []:
        if hasattr(chart, "model_dump"):
            chart = chart.model_dump(exclude_none=True)
        if not isinstance(chart, dict) or not chart.get("data"):
            continue
        result.append(
            optional_kwargs(
                title=chart.get("title"), data=chart.get("data"), dataset=chart.get("dataset")
            )
        )
    return result or None


def route_output_assets_summary(route: Any) -> dict[str, Any] | None:
    """Return the keyed asset-output contract a route will produce.

    Mirrors :func:`route_input_assets_summary`. Prefers plural keyed
    ``output_assets`` and falls back to a single-entry summary synthesized
    from the legacy ``output_type`` and ``output_file_extension`` columns.
    The result tells the agent which response body fields will resolve to
    Ouro assets, and what each declared asset looks like.
    """
    raw = getv(route, "output_assets") or {}
    result: dict[str, Any] = {}

    if isinstance(raw, dict):
        for name, config in raw.items():
            if hasattr(config, "model_dump"):
                config = config.model_dump(exclude_none=True)
            elif not isinstance(config, dict):
                config = {}
            result[name] = optional_kwargs(
                asset_type=config.get("asset_type"),
                primary=config.get("primary"),
                file_extensions=config.get("file_extensions"),
                contains_file_extensions=config.get("contains_file_extensions"),
                # Only created when the request body matches, e.g.
                # {"include_dataset": true}
                when=config.get("when"),
                # May be absent depending on the run's results
                optional=config.get("optional"),
                # Delivered inside another output (a post) while that one is created
                embedded_in=config.get("embedded_in"),
                # What it is to the run's input: version / result / supporting / new
                role=config.get("role"),
            )

    output_type = getv(route, "output_type")
    if output_type and not result:
        legacy_extension = getv(route, "output_file_extension")
        result[str(output_type)] = optional_kwargs(
            asset_type=output_type,
            primary=True,
            file_extensions=[legacy_extension] if legacy_extension else None,
        )

    return result or None


def route_caller_parameters(parameters: Any) -> Any:
    """A route's parameters without the ``ouro-*`` headers Ouro sets on each run.

    Ouro fills in the route id, action id, user id, webhook and billing cap
    headers itself; listing them invites an agent to set them.
    """
    if not isinstance(parameters, list):
        return parameters
    return [
        p
        for p in parameters
        if not (isinstance(p, dict) and str(p.get("name", "")).lower().startswith("ouro-"))
    ] or None


# The leading "text" value, closed or (when the cut fell inside it) not
_RICH_TEXT_LEAD = re.compile(r'\{"text":"((?:[^"\\]|\\.)*)')


def route_description_markdown(description: str | None) -> str | None:
    """Render ``RouteData.description``, which stores RichText as a JSON string."""
    from ouro.utils.content import description_to_markdown

    if not description:
        return None
    try:
        parsed = json.loads(description, strict=False)  # stored text can hold raw newlines
    except ValueError:
        # Some stored RichText was cut off mid-document; its plain text comes first
        lead = _RICH_TEXT_LEAD.match(description)
        return json.loads(f'"{lead.group(1).rstrip(chr(92))}"', strict=False) if lead else description
    if not isinstance(parsed, dict):
        return description
    # An empty rich document can sit beside a plain "text" that has the words
    return description_to_markdown(parsed) or parsed.get("text") or None


def route_request_body_without_input_assets(route: Any) -> Any:
    """Hide Ouro-resolved asset object schemas from route execution metadata.

    Agents should pass IDs via the canonical keyed ``input_assets`` mapping.
    The backend expands those IDs into the service-facing body object.
    """
    request_body = getv(route, "request_body")
    if not isinstance(request_body, dict):
        return request_body

    cleaned = deepcopy(request_body)
    # The object variant of an anyOf/oneOf/allOf body, edited in place
    schema = request_body_json_schema(cleaned)
    if not isinstance(schema, dict):
        return cleaned

    handled_keys = set((route_input_assets_summary(route) or {}).keys())
    input_type = getv(route, "input_type")
    if input_type:
        handled_keys.add(str(input_type))

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key in handled_keys:
            properties.pop(key, None)

    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [key for key in required if key not in handled_keys]

    return cleaned
