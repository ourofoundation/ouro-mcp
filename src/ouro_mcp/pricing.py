"""Prices: cost summaries for agents and the pricing arguments create/update tools share."""

from __future__ import annotations

import math
from typing import Any, Optional

from ouro_mcp.utils import (
    getv,
    optional_kwargs,
)


def _format_compact_number(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_usd_rate(value: Any) -> str:
    """Dollars, keeping sub-cent precision for per-second rates ($0.0005)."""
    amount = float(value)
    if 0 < amount < 0.01:
        return f"${amount:.8f}".rstrip("0")
    return f"${amount:.2f}"


def runtime_max_charge(unit_cost: Any, max_seconds: Any, currency: str) -> float:
    """Most one runtime-priced run can cost (dollars or sats), rounded up like the hold."""
    rate = float(unit_cost)
    seconds = float(max_seconds)
    if str(currency).lower() == "usd":
        return math.ceil(round(rate * 100 * seconds, 6)) / 100
    return float(math.ceil(round(rate * seconds, 6)))


def format_pay_per_use_cost_summary(
    unit_cost: Any,
    cost_unit: str,
    currency: str,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> str:
    """Human-readable pay-per-use cost summary for agent-facing tools."""
    currency_upper = str(currency).upper()
    currency_lower = str(currency).lower()
    if cost_accounting == "runtime":
        # Per second of runtime, capped per run; the cap is held up front
        if currency_lower == "usd":
            rate = _format_usd_rate(unit_cost)
        else:
            rate = f"{_format_compact_number(unit_cost)} sats"
        summary = f"{rate} per second of runtime"
        if max_billable_seconds:
            max_charge = runtime_max_charge(unit_cost, max_billable_seconds, currency_lower)
            max_label = (
                f"${max_charge:.2f}"
                if currency_lower == "usd"
                else f"{_format_compact_number(int(max_charge))} sats"
            )
            summary += (
                f", up to {max_label} per run (max {int(max_billable_seconds)}s; "
                "that much, or what you can afford, is held until the run finishes)"
            )
        return f"{summary} ({currency_upper})"
    if currency_lower == "usd":
        return f"{_format_usd_rate(unit_cost)} per {cost_unit} (USD)"
    if currency_lower == "btc":
        return f"{_format_compact_number(unit_cost)} sats per {cost_unit} (BTC)"
    return f"{_format_compact_number(unit_cost)} per {cost_unit} ({currency_upper})"


def format_one_time_cost_summary(price: Any, currency: str) -> str:
    """Human-readable one-time cost summary for agent-facing tools."""
    currency_upper = str(currency).upper()
    currency_lower = str(currency).lower()
    if currency_lower == "usd":
        return f"${price:.2f} (USD)"
    if currency_lower == "btc":
        return f"{_format_compact_number(price)} sats (BTC)"
    return f"{_format_compact_number(price)} {currency_upper}"


def _amount_label(amount: Any, currency: str, *, rate: bool = False) -> str:
    """One amount in its currency: "$0.05" / "50 sats" (rates keep sub-cent USD)."""
    if currency == "usd":
        return _format_usd_rate(amount) if rate else f"${float(amount):.2f}"
    return f"{_format_compact_number(amount)} sats"


def format_dual_cost_summary(
    prices: list[tuple[str, Any]],
    cost_unit: Optional[str] = None,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> str:
    """Cost of something sold in both currencies, e.g. "$0.05 or 50 sats per call".

    ``prices`` is (currency, amount) pairs, primary currency first. Without
    ``cost_unit`` the amounts are one-time prices.
    """
    if cost_unit is None:
        return " or ".join(_amount_label(amount, cur) for cur, amount in prices)
    rates = " or ".join(_amount_label(amount, cur, rate=True) for cur, amount in prices)
    if cost_accounting != "runtime":
        return f"{rates} per {cost_unit}"
    summary = f"{rates} per second of runtime"
    if max_billable_seconds:
        caps = " or ".join(
            _amount_label(
                runtime_max_charge(amount, max_billable_seconds, cur)
                if cur == "usd"
                else int(runtime_max_charge(amount, max_billable_seconds, cur)),
                cur,
            )
            for cur, amount in prices
        )
        summary += (
            f", up to {caps} per run (max {int(max_billable_seconds)}s; "
            "that much, or what you can afford, is held until the run finishes)"
        )
    return summary


def dual_price_fields(
    asset: Any,
    kind: str,
    cost_unit: Optional[str] = None,
    cost_accounting: Optional[str] = None,
    max_billable_seconds: Any = None,
) -> dict[str, Any]:
    """Fields for an asset sold in both currencies; empty when it's sold in one.

    ``kind`` is "price" (one-time unlock) or "unit_cost" (pay-per-use, with
    its ``cost_unit``). The primary currency (``price_currency``) is listed
    first: that is what's charged when the buyer doesn't pick.
    """
    amounts = {
        "usd": getv(asset, f"{kind}_usd"),
        "btc": getv(asset, f"{kind}_sats"),
    }
    if not all(amount is not None and float(amount) > 0 for amount in amounts.values()):
        return {}
    primary = str(getv(asset, "price_currency") or "usd").lower()
    primary = primary if primary in amounts else "usd"
    other = "btc" if primary == "usd" else "usd"
    summary = format_dual_cost_summary(
        [(primary, amounts[primary]), (other, amounts[other])],
        cost_unit,
        cost_accounting,
        max_billable_seconds,
    )
    return {
        f"{kind}_usd": amounts["usd"],
        f"{kind}_sats": amounts["btc"],
        "currencies": [primary, other],
        "cost_summary": (
            f"{summary}. Sold in both currencies: pass currency to pick, "
            f"otherwise {primary.upper()} is charged."
        ),
    }


def format_monetization_block(asset: Any) -> dict[str, Any]:
    """Build the monetization fields for an asset (free or paid).

    Returns an empty dict for free assets. For paid assets, returns the
    structured cost fields PLUS a human-readable `cost_summary` so an agent
    that ignores structured data still sees that the asset isn't free.

    Accepts either a typed Asset model or a dict (search results).
    """
    monetization = getv(asset, "monetization")
    if not monetization or monetization == "none":
        return {}

    block: dict[str, Any] = {"monetization": monetization}
    currency = getv(asset, "price_currency") or "usd"
    block["price_currency"] = currency

    if monetization == "pay-per-use":
        # `unit_cost` is dollars for USD and sats for BTC. Surface all four
        # fields so agents can compare costs without N+1 lookups.
        unit_cost = getv(asset, "unit_cost")
        cost_unit = getv(asset, "cost_unit") or "call"
        block["unit_cost"] = unit_cost
        block["cost_unit"] = cost_unit
        cost_accounting = getv(asset, "cost_accounting")
        max_billable_seconds = getv(asset, "max_billable_seconds")
        block["cost_accounting"] = cost_accounting
        if cost_accounting == "runtime":
            block["max_billable_seconds"] = max_billable_seconds
        if unit_cost is not None:
            block["cost_summary"] = format_pay_per_use_cost_summary(
                unit_cost,
                cost_unit,
                currency,
                cost_accounting,
                max_billable_seconds,
            )
        block.update(
            dual_price_fields(
                asset, "unit_cost", cost_unit, cost_accounting, max_billable_seconds
            )
        )
    else:
        # pay-to-unlock and any other one-time-price monetization.
        price = getv(asset, "price")
        block["price"] = price
        if price is not None:
            block["cost_summary"] = format_one_time_cost_summary(price, currency)
        block.update(dual_price_fields(asset, "price"))

    return {k: v for k, v in block.items() if v is not None}


PRICE_CURRENCY_DESC = (
    'Currency for the price: "usd" (price in dollars) or "btc" (price in sats). '
    "Defaults to usd. For an asset sold in both currencies, the one charged when "
    "the buyer doesn't pick."
)
PRICE_USD_DESC = (
    "One-time unlock price in dollars. Give with price_sats to sell in both "
    "currencies (the buyer picks); 0 stops selling in USD."
)
PRICE_SATS_DESC = (
    "One-time unlock price in whole sats. Give with price_usd to sell in both "
    "currencies (the buyer picks); 0 stops selling in Bitcoin."
)
UNIT_COST_USD_DESC = (
    "Price per call (or per second) in dollars. Give with unit_cost_sats to sell "
    "in both currencies (the caller picks); 0 stops selling in USD."
)
UNIT_COST_SATS_DESC = (
    "Price per call (or per second) in sats. Give with unit_cost_usd to sell in "
    "both currencies (the caller picks); 0 stops selling in Bitcoin."
)
UNLOCK_PRICE_DESC = (
    "One-time unlock price in price_currency units. A monetized asset needs this, "
    "or price_usd / price_sats."
)
UNIT_COST_DESC = (
    "Price per call (or per second with pricing=\"per_second\") in price_currency "
    "units. A monetized route needs this, or unit_cost_usd / unit_cost_sats."
)
ROUTE_PRICING_DESC = (
    '"per_call" charges unit_cost each run. "per_second" charges unit_cost per second '
    "the service runs (from dispatch to completion), capped at max_billable_seconds; "
    "each call holds the max (or what the caller can afford) and charges the seconds "
    "used; failed runs are free. The service gets the call's budget in the "
    "ouro-max-billable-seconds header. Suits long jobs of unknown length (e.g. GPU "
    "work on Modal)."
)
MAX_BILLABLE_SECONDS_DESC = (
    'Most seconds one run can be billed (1-86400). Required with pricing="per_second".'
)


def _pricing_kwargs(visibility: Optional[str], monetization: str, **prices: Any) -> dict:
    """Monetization fields implied by ``visibility``, plus any explicit prices.

    "monetized" applies ``monetization``; any other visibility makes the asset
    free again. Omitting visibility leaves the monetization model unchanged.
    """
    fields = optional_kwargs(**prices)
    if visibility == "monetized":
        fields["monetization"] = monetization
    elif visibility is not None:
        fields["monetization"] = "none"
    return fields


def _check_price_inputs(
    visibility: Optional[str],
    name: str,
    single: Optional[float],
    usd: Optional[float],
    sats: Optional[float],
) -> None:
    """A price is given one way: ``name`` + price_currency, or per currency."""
    if single is not None and (usd is not None or sats is not None):
        raise ValueError(
            f"Give either {name} with price_currency, or {name}_usd / {name}_sats, not both."
        )
    if visibility == "monetized" and not any(
        amount is not None and amount > 0 for amount in (single, usd, sats)
    ):
        raise ValueError(
            f'visibility "monetized" requires {name} (or {name}_usd / {name}_sats).'
        )


def unlock_pricing_kwargs(
    visibility: Optional[str],
    price: Optional[float],
    price_currency: Optional[str],
    price_usd: Optional[float] = None,
    price_sats: Optional[int] = None,
) -> dict:
    """SDK kwargs for a pay-to-unlock asset (post, file, dataset).

    ``price`` + ``price_currency`` sets a single price; ``price_usd`` and
    ``price_sats`` price the asset per currency (both = the buyer picks).
    """
    _check_price_inputs(visibility, "price", price, price_usd, price_sats)
    return _pricing_kwargs(
        visibility,
        "pay-to-unlock",
        price=price,
        price_currency=price_currency,
        price_usd=price_usd,
        price_sats=price_sats,
    )


def per_use_pricing_kwargs(
    visibility: Optional[str],
    unit_cost: Optional[float],
    price_currency: Optional[str],
    cost_unit: Optional[str],
    pricing: Optional[str] = None,
    max_billable_seconds: Optional[int] = None,
    default_pricing: Optional[str] = "per_call",
    unit_cost_usd: Optional[float] = None,
    unit_cost_sats: Optional[float] = None,
) -> dict:
    """SDK kwargs for a pay-per-use route, per call or per second of runtime.

    ``pricing`` picks the model; when omitted, a newly monetized route uses
    ``default_pricing``. Updates pass ``default_pricing=None`` so re-sending
    visibility doesn't reset a per-second route to per-call.
    """
    _check_price_inputs(visibility, "unit_cost", unit_cost, unit_cost_usd, unit_cost_sats)
    if pricing not in (None, "per_call", "per_second"):
        raise ValueError('pricing must be "per_call" or "per_second".')
    if pricing == "per_second" and max_billable_seconds is None:
        raise ValueError('pricing "per_second" requires max_billable_seconds.')
    fields = _pricing_kwargs(
        visibility,
        "pay-per-use",
        unit_cost=unit_cost,
        unit_cost_usd=unit_cost_usd,
        unit_cost_sats=unit_cost_sats,
        price_currency=price_currency,
        cost_unit=cost_unit,
    )
    model = pricing or (default_pricing if visibility == "monetized" else None)
    if model == "per_second":
        fields["cost_accounting"] = "runtime"
        fields["cost_unit"] = "seconds"
        fields["max_billable_seconds"] = max_billable_seconds
    elif model == "per_call":
        fields["cost_accounting"] = "fixed"
        fields.setdefault("cost_unit", "call")
    elif max_billable_seconds is not None:
        # Adjusting the cap of an existing per-second route
        fields["max_billable_seconds"] = max_billable_seconds
    return fields
