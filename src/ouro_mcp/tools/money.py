"""Money tools — balance, transactions, send, unlock assets (BTC & USD)."""

from __future__ import annotations

import json
from typing import Optional

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
        currency: str,
        ctx: Context,
    ) -> str:
        """Get wallet balance.

        Args:
            currency: "btc" (returns sats) or "usd" (returns cents).
        """
        ouro = ctx.request_context.lifespan_context.ouro
        result = ouro.money.get_balance(currency=currency)
        return json.dumps({"currency": currency.lower(), "balance": result})

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_transactions(
        currency: str,
        ctx: Context,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        type: Optional[str] = None,
    ) -> str:
        """Get transaction history.

        Args:
            currency: "btc" or "usd".
            limit: Max transactions to return (USD only).
            offset: Pagination offset (USD only).
            type: Filter by transaction type (USD only).
        """
        ouro = ctx.request_context.lifespan_context.ouro

        transactions = ouro.money.get_transactions(
            currency=currency,
            with_pagination=True,
            **optional_kwargs(limit=limit, offset=offset, type=type),
        )
        items = transactions.get("data", []) if isinstance(transactions, dict) else transactions
        pagination = transactions.get("pagination", {}) if isinstance(transactions, dict) else {}
        return json.dumps({
            "currency": currency.lower(),
            "results": items,
            "count": len(items) if isinstance(items, list) else None,
            "pagination": {
                "offset": pagination.get("offset", offset or 0),
                "limit": pagination.get("limit", limit or (len(items) if isinstance(items, list) else 0)),
                "hasMore": pagination.get("hasMore", False),
                "total": pagination.get("total"),
            },
        })

    @mcp.tool(annotations={"destructiveHint": True})
    @handle_ouro_errors
    def unlock_asset(
        asset_type: str,
        asset_id: str,
        currency: str,
        ctx: Context,
    ) -> str:
        """Unlock (purchase) a paid asset. Grants permanent read access after payment.

        Args:
            asset_type: The type of asset ("post", "file", "dataset", etc.).
            asset_id: The asset's UUID.
            currency: "btc" or "usd".
        """
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
        recipient_id: str,
        amount: int,
        currency: str,
        ctx: Context,
        message: Optional[str] = None,
    ) -> str:
        """Send money to another Ouro user.

        For BTC: sends sats. For USD: sends a tip in cents.

        Args:
            recipient_id: The recipient's user UUID.
            amount: Amount in sats (BTC) or cents (USD).
            currency: "btc" or "usd".
            message: Optional message (USD only).
        """
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
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        asset_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> str:
        """Get usage-based billing history (USD). Shows charges for pay-per-use route calls.

        Args:
            limit: Max records to return.
            offset: Pagination offset.
            asset_id: Filter by asset ID.
            role: "consumer" (your spending) or "creator" (your earnings).
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
