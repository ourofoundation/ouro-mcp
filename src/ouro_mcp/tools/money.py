"""Money tools — balance, transactions, send, unlock assets (BTC & USD)."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP
from ouro.models import BitcoinTransaction, UsdBalance, UsdTransaction

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import (
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
)

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def register(mcp: FastMCP) -> None:
    # ------------------------------------------------------------------
    # Shared tools (currency-routed)
    # ------------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_balance(
        currency: Annotated[
            Literal["btc", "usd"],
            Field(description='"btc" (returns sats) or "usd" (returns cents)'),
        ],
        ctx: Context,
    ) -> str:
        """Read the authenticated user's wallet balance.

        Returns ``{"currency": "btc" | "usd", "balance": int, ...}`` where the
        balance is always in the currency's smallest unit:

        - BTC: satoshis (1 BTC = 100_000_000 sats).
        - USD: cents (1 USD = 100 cents).

        When the backend provides an ``available`` amount (balance minus soft
        escrow holds), it is included in the response.

        Read-only; no side effects.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        balance = ouro.money.get_balance(currency=currency)
        payload = {**balance.model_dump(mode="json"), "currency": currency}
        if isinstance(balance, UsdBalance):
            payload["available"] = balance.available_cents
        return dump_json(payload)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_transactions(
        currency: Annotated[
            Literal["btc", "usd"],
            Field(description='"btc" or "usd"'),
        ],
        ctx: Context,
        limit: Annotated[Optional[int], Field(description="Max transactions to return (USD only)")] = None,
        offset: Annotated[Optional[int], Field(description="Pagination offset (USD only)")] = None,
        type: Annotated[Optional[str], Field(description="Filter by transaction type (USD only)")] = None,
    ) -> str:
        """List wallet transactions in reverse-chronological order.

        Returns compact markdown. Amounts are in sats (BTC) or cents (USD).

        Pagination model:
        - USD supports offset-based paging via ``limit`` + ``offset`` and
          server-side ``type`` filtering. The header reflects whether more
          pages are available.
        - BTC currently returns the full history in one call; ``limit`` /
          ``offset`` / ``type`` are ignored.

        Read-only; no side effects.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if currency == "btc":
            transactions = ouro.money.get_transactions(currency="btc")
            pagination = None
        else:
            transactions = ouro.money.get_transactions(
                currency="usd", limit=limit, offset=offset, type=type
            )
            pagination = page_pagination(transactions)

        def _tx_line(tx: BitcoinTransaction | UsdTransaction) -> str:
            amount = tx.value if isinstance(tx, BitcoinTransaction) else tx.amount_cents
            parts = [markdown_id(tx.id), f"amount: {amount}", tx.status, tx.created_at]
            return markdown_bullet(tx.type, *parts)

        return render_markdown_list(
            list(transactions),
            line_fn=_tx_line,
            pagination=pagination,
            offset=offset or 0,
            noun="transactions",
            empty_text="No transactions.",
            extras=[f"currency: {currency}"],
        )

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "idempotentHint": False,
        }
    )
    @handle_ouro_errors
    def unlock_asset(
        asset_type: Annotated[str, Field(description='"post" | "file" | "dataset" | etc.')],
        asset_id: Annotated[
            str,
            Field(description="Asset UUID", pattern=UUID_PATTERN),
        ],
        currency: Annotated[
            Literal["btc", "usd"],
            Field(description='"btc" or "usd"'),
        ],
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
        purchase = ouro.money.unlock_asset(
            asset_type=asset_type,
            asset_id=asset_id,
            currency=currency,
        )
        return dump_json({
            "success": True,
            "currency": currency,
            "asset_type": asset_type,
            "asset_id": asset_id,
            **purchase.model_dump(mode="json"),
        })

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "idempotentHint": False,
        }
    )
    @handle_ouro_errors
    def send_money(
        recipient_id: Annotated[
            str,
            Field(description="Recipient user UUID", pattern=UUID_PATTERN),
        ],
        amount: Annotated[
            int,
            Field(gt=0, description="Amount in sats (BTC) or cents (USD)"),
        ],
        currency: Annotated[
            Literal["btc", "usd"],
            Field(description='"btc" or "usd"'),
        ],
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
        - USD sends move cents between Ouro balances (after the platform
          fee) and accept an optional ``message``; BTC ignores ``message``.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        transfer = ouro.money.send(
            recipient_id=recipient_id,
            amount=amount,
            currency=currency,
            message=message,
        )
        return dump_json({
            "success": True,
            "currency": currency,
            "recipient_id": recipient_id,
            "amount": amount,
            **transfer.model_dump(mode="json"),
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
        asset_id: Annotated[
            Optional[str],
            Field(description="Filter by asset UUID"),
        ] = None,
        role: Annotated[Optional[str], Field(description='"consumer" (spending) or "creator" (earnings)')] = None,
    ) -> str:
        """List pay-per-use route charges (USD). Read-only.

        Each record is one paid route call: which asset was called, the
        charge in cents (paid from the caller's Ouro balance when the call
        completed), and when. Use ``role="consumer"`` to see what the user spent and
        ``role="creator"`` to see what they earned. Filter to a single
        asset with ``asset_id`` to audit one route.

        Pagination is offset-based via ``limit`` + ``offset`` and the
        response carries the server's ``pagination`` block through to the
        caller.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        history = ouro.money.get_usage_history(
            limit=limit,
            offset=offset,
            asset_id=asset_id,
            role=role,
        )
        return dump_json({
            "records": [record.model_dump(mode="json") for record in history],
            "summary": history.summary.model_dump(mode="json"),
            "pagination": page_pagination(history),
        })

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_pending_earnings(ctx: Context) -> str:
        """Read creator route earnings (USD, cents). Read-only.

        Route revenue lands in the user's Ouro balance as each paid call
        completes (after the platform fee), so ``total_pending_cents`` is 0;
        ``total_paid_out_cents`` is everything earned, ``in_progress_cents``
        covers calls still running, and ``assets`` breaks it down per route.
        Earnings are spendable immediately and withdrawable after 7 days.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        return dump_json(ouro.money.get_pending_earnings().model_dump(mode="json"))

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
