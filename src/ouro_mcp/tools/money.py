"""Money tools — balance, transactions, send, unlock assets (BTC & USD)."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import optional_kwargs


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
        """Get wallet balance."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_balance(currency=currency)
        return json.dumps({"currency": currency.lower(), "balance": result})

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_transactions(
        currency: Annotated[str, Field(description='"btc" or "usd"')],
        ctx: Context,
        limit: Annotated[Optional[int], Field(description="Max transactions to return (USD only)")] = None,
        offset: Annotated[Optional[int], Field(description="Pagination offset (USD only)")] = None,
        type: Annotated[Optional[str], Field(description="Filter by transaction type (USD only)")] = None,
    ) -> str:
        """Get transaction history."""
        ouro = ctx.request_context.lifespan_context.ouro

        transactions = ouro.money.get_transactions(
            currency=currency,
            with_pagination=True,
            **optional_kwargs(limit=limit, offset=offset, type=type),
        )
        items = transactions.get("data", []) if isinstance(transactions, dict) else transactions
        pagination = transactions.get("pagination", {}) if isinstance(transactions, dict) else {}
        return json.dumps({
            "results": items,
            "total": pagination.get("total"),
            "hasMore": pagination.get("hasMore", False),
        })

    @mcp.tool(annotations={"destructiveHint": True})
    @handle_ouro_errors
    def unlock_asset(
        asset_type: Annotated[str, Field(description='"post" | "file" | "dataset" | etc.')],
        asset_id: Annotated[str, Field(description="Asset UUID")],
        currency: Annotated[str, Field(description='"btc" or "usd"')],
        ctx: Context,
    ) -> str:
        """Unlock (purchase) a paid asset. Grants permanent read access after payment."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.unlock_asset(
            asset_type=asset_type,
            asset_id=asset_id,
            currency=currency,
        )
        return json.dumps({
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
        """Send money to another Ouro user. BTC sends sats, USD sends cents."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.send(
            recipient_id=recipient_id,
            amount=amount,
            currency=currency,
            message=message,
        )
        return json.dumps({
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
        """Get a Bitcoin L1 deposit address to receive BTC into your Ouro wallet."""
        ouro = ctx.request_context.lifespan_context.ouro
        address = ouro.money.get_deposit_address()
        return json.dumps({"deposit_address": address})

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
        """Get usage-based billing history (USD). Shows charges for pay-per-use route calls."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_usage_history(
            limit=limit,
            offset=offset,
            asset_id=asset_id,
            role=role,
            with_pagination=True,
        )
        if isinstance(result, dict):
            return json.dumps({
                **result.get("data", {}),
                "pagination": result.get("pagination", {}),
            })
        return json.dumps(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_pending_earnings(ctx: Context) -> str:
        """Get pending creator earnings (USD). Shows revenue from assets others have used or purchased."""
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_pending_earnings()
        return json.dumps(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def add_funds(ctx: Context) -> str:
        """Get instructions for adding USD funds to your wallet.

        USD top-ups require the Ouro web interface — this tool provides the link.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        message = ouro.money.add_funds()
        return json.dumps({"message": message})
