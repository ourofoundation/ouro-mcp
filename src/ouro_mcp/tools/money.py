"""Money tools — balance, transactions, send, unlock assets (BTC & USD)."""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import dump_json, list_response, optional_kwargs


def register(mcp: FastMCP) -> None:
    # ------------------------------------------------------------------
    # Shared tools (currency-routed)
    # ------------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_balance(
        currency: Annotated[str, Field(description='"btc" (returns sats) or "usd" (returns cents)')],
        ctx: Context,
    ) -> str:
        """Read the authenticated user's wallet balance.

        Returns ``{"currency": "btc" | "usd", "balance": int}`` where the
        balance is always in the currency's smallest unit:

        - BTC: satoshis (1 BTC = 100_000_000 sats).
        - USD: cents (1 USD = 100 cents).

        Read-only; no side effects.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_balance(currency=currency)
        return dump_json({"currency": currency.lower(), "balance": result})

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_transactions(
        currency: Annotated[str, Field(description='"btc" or "usd"')],
        ctx: Context,
        limit: Annotated[Optional[int], Field(description="Max transactions to return (USD only)")] = None,
        offset: Annotated[Optional[int], Field(description="Pagination offset (USD only)")] = None,
        type: Annotated[Optional[str], Field(description="Filter by transaction type (USD only)")] = None,
    ) -> str:
        """List wallet transactions in reverse-chronological order.

        Returns the standard list envelope: ``{"results": [...], "total",
        "hasMore"}``. Amounts inside each transaction are in sats (BTC) or
        cents (USD).

        Pagination model:
        - USD supports offset-based paging via ``limit`` + ``offset`` and
          server-side ``type`` filtering. ``hasMore`` reflects the server's
          own signal.
        - BTC currently returns the full history in one call; ``limit`` /
          ``offset`` / ``type`` are ignored and ``hasMore`` is always
          ``false``.

        Read-only; no side effects.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        transactions = ouro.money.get_transactions(
            currency=currency,
            with_pagination=True,
            **optional_kwargs(limit=limit, offset=offset, type=type),
        )
        items = transactions.get("data", []) if isinstance(transactions, dict) else transactions
        pagination = transactions.get("pagination", {}) if isinstance(transactions, dict) else {}
        return dump_json(
            list_response(items, pagination=pagination, limit=limit)
        )

    @mcp.tool(annotations={"destructiveHint": True})
    @handle_ouro_errors
    def unlock_asset(
        asset_type: Annotated[str, Field(description='"post" | "file" | "dataset" | etc.')],
        asset_id: Annotated[str, Field(description="Asset UUID")],
        currency: Annotated[str, Field(description='"btc" or "usd"')],
        ctx: Context,
    ) -> str:
        """Purchase a paid asset, debiting the wallet in the chosen currency.

        **Side effect:** immediately charges the user's wallet (sats for
        BTC, cents for USD) at the asset's listed price and grants the
        caller permanent read access. Not reversible once the payment
        settles. Confirm the user actually intends to buy before calling.

        Make sure the currency matches one the asset is priced in —
        passing the wrong currency surfaces as a backend error.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.unlock_asset(
            asset_type=asset_type,
            asset_id=asset_id,
            currency=currency,
        )
        return dump_json({
            "success": True,
            "currency": currency.lower(),
            "asset_type": asset_type,
            "asset_id": asset_id,
            **result,
        })

    @mcp.tool(annotations={"destructiveHint": True})
    @handle_ouro_errors
    def send_money(
        recipient_id: Annotated[str, Field(description="Recipient user UUID")],
        amount: Annotated[int, Field(description="Amount in sats (BTC) or cents (USD)")],
        currency: Annotated[str, Field(description='"btc" or "usd"')],
        ctx: Context,
        message: Annotated[Optional[str], Field(description="Optional note (USD only)")] = None,
    ) -> str:
        """Transfer funds to another Ouro user. Destructive and typically non-reversible.

        **Side effect:** immediately debits the caller's wallet and
        credits the recipient's. Amounts are always in the currency's
        smallest unit — ``amount=500`` means 500 sats on BTC or $5.00
        (500 cents) on USD. Double-check the unit before calling.

        - BTC sends route through the Lightning "send-sats" endpoint; once
          the payment confirms it cannot be reversed by the SDK.
        - USD sends route through the Stripe "tip" endpoint and accept an
          optional ``message``; BTC ignores ``message``.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.send(
            recipient_id=recipient_id,
            amount=amount,
            currency=currency,
            message=message,
        )
        return dump_json({
            "success": True,
            "currency": currency.lower(),
            "recipient_id": recipient_id,
            "amount": amount,
            **result,
        })

    # ------------------------------------------------------------------
    # BTC-only tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_deposit_address(ctx: Context) -> str:
        """Return a Bitcoin L1 deposit address for funding the user's wallet.

        Returns ``{"deposit_address": "bc1..."}``. Funds sent on-chain to
        this address credit the user's BTC balance in sats once confirmed
        by the network. Read-only; the backend may return the same address
        across calls.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        address = ouro.money.get_deposit_address()
        return dump_json({"deposit_address": address})

    # ------------------------------------------------------------------
    # USD-only tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_usage_history(
        ctx: Context,
        limit: Annotated[Optional[int], Field(description="Max records to return")] = None,
        offset: Annotated[Optional[int], Field(description="Pagination offset")] = None,
        asset_id: Annotated[Optional[str], Field(description="Filter by asset UUID")] = None,
        role: Annotated[Optional[str], Field(description='"consumer" (spending) or "creator" (earnings)')] = None,
    ) -> str:
        """List pay-per-use route billing records (USD). Read-only.

        Each record captures a single metered route invocation: which
        asset was called, the resulting charge in cents, and when it was
        billed. Use ``role="consumer"`` to see what the user spent and
        ``role="creator"`` to see what they earned. Filter to a single
        asset with ``asset_id`` to audit one route.

        Pagination is offset-based via ``limit`` + ``offset`` and the
        response carries the server's ``pagination`` block through to the
        caller.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_usage_history(
            limit=limit,
            offset=offset,
            asset_id=asset_id,
            role=role,
            with_pagination=True,
        )
        if isinstance(result, dict):
            return dump_json({
                **result.get("data", {}),
                "pagination": result.get("pagination", {}),
            })
        return dump_json(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_pending_earnings(ctx: Context) -> str:
        """Read pending creator earnings from usage-based billing (USD, cents).

        Returns the backend's pending-earnings summary — revenue from
        routes and assets the user has published that buyers have called
        or purchased but which haven't been paid out to the user's
        Stripe-linked account yet. All amounts are in cents. Read-only.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_pending_earnings()
        return dump_json(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def add_funds(ctx: Context) -> str:
        """Return advisory instructions for topping up USD. Does not move money.

        USD top-ups require the Ouro web interface (Stripe-hosted flow).
        This tool is purely informational — it returns ``{"message": str}``
        with a link and brief instructions so the agent can hand the user
        off. No API call, no charge, no side effect.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        message = ouro.money.add_funds()
        return dump_json({"message": message})
