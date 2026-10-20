"""Money tools — balance, transactions, send, unlock assets (BTC & USD)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from ouro.models import BitcoinTransaction, UsdTransaction
from pydantic import Field

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.rendering import (
    dump_json,
    markdown_bullet,
    markdown_id,
    page_pagination,
    render_markdown_list,
)
from ouro_mcp.utils import asset_type_of

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def _signed_amount(tx: BitcoinTransaction | UsdTransaction) -> str:
    """``+$0.82`` / ``−$5.00`` or ``+1,200 sats``: the API signs amounts (negative = paid)."""
    if isinstance(tx, BitcoinTransaction):
        return f"{'−' if tx.value < 0 else '+'}{abs(tx.value):,} sats"
    cents = tx.amount_cents
    return f"{'−' if cents < 0 else '+'}${abs(cents) / 100:,.2f}"


def _tx_label(tx: BitcoinTransaction | UsdTransaction) -> str:
    """The transaction type in words, as the wallet page names it."""
    amount = tx.value if isinstance(tx, BitcoinTransaction) else tx.amount_cents
    if tx.type == "route_usage":
        return "Route usage" if amount < 0 else "Route earnings"
    return _TX_LABELS.get(tx.type) or tx.type.replace("_", " ").capitalize()


_TX_LABELS = {
    "topup": "Added funds",
    "invoice_payout": "Usage payout",
    "invoice_payout_reversal": "Usage payout reversed",
    "balance_migrated": "Balance moved to the USD ledger",
}


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

        Amounts are in the currency's smallest unit:

        - USD: cents, in `*_cents` fields. `available_cents` is what you
          can spend now (balance minus holds for running jobs);
          `credits_cents` can only be spent on Ouro; `withdrawable_cents`
          is the part of `earnings_cents` you can cash out now.
        - BTC: satoshis (1 BTC = 100_000_000 sats), in `balance` and
          `available`.

        Read-only; no side effects.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        balance = ouro.money.get_balance(currency=currency)
        payload = {**balance.model_dump(mode="json", exclude_none=True), "currency": currency}
        if not payload.get("requirements"):
            payload.pop("requirements", None)
        return dump_json(payload)

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def list_transactions(
        currency: Annotated[
            Literal["btc", "usd"],
            Field(description='"btc" or "usd"'),
        ],
        ctx: Context,
        limit: Annotated[Optional[int], Field(ge=1, le=200, description="Max transactions to return (USD only)")] = None,
        offset: Annotated[Optional[int], Field(ge=0, description="Pagination offset (USD only)")] = None,
        type: Annotated[Optional[str], Field(description="Filter by transaction type (USD only)")] = None,
    ) -> str:
        """List wallet transactions in reverse-chronological order.

        Returns compact markdown. Amounts are signed: + is money in, − is
        money out (USD in dollars, BTC in sats).

        Pagination model:
        - USD supports offset-based paging via `limit` + `offset` and
          server-side `type` filtering. The header reflects whether more
          pages are available.
        - BTC currently returns the full history in one call; `limit` /
          `offset` / `type` are ignored.

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
            metadata = tx.metadata or {}
            parts = [
                _signed_amount(tx),
                markdown_id(tx.id),
                None if tx.status == "completed" else tx.status,
                tx.created_at,
            ]
            asset_id = tx.asset_id or metadata.get("asset_id")
            body = None
            if asset_id:
                name = metadata.get("asset_name")
                body = f"{name} `{asset_id}`" if name else f"asset `{asset_id}`"
            return markdown_bullet(_tx_label(tx), *parts, body=body)

        return render_markdown_list(
            list(transactions),
            line_fn=_tx_line,
            pagination=pagination,
            offset=offset or 0,
            noun="transactions",
            empty_text="No transactions.",
        )

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "idempotentHint": False,
        }
    )
    @handle_ouro_errors
    def unlock_asset(
        id: Annotated[str, Field(description="Asset UUID", pattern=UUID_PATTERN)],
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

        An asset can be sold in both currencies, each at its own price
        (`price_usd` / `price_sats`; `currencies` lists them on get_asset).
        Pass one it is sold in; any other surfaces as a backend error.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        asset_type = asset_type_of(ouro, id)
        purchase = ouro.money.unlock_asset(
            asset_type=asset_type,
            asset_id=id,
            currency=currency,
        )
        return dump_json({
            "success": True,
            "currency": currency,
            "asset_type": asset_type,
            "id": id,
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
        smallest unit — `amount=500` means 500 sats on BTC or $5.00
        (500 cents) on USD. Double-check the unit before calling.

        - BTC sends route through the Lightning "send-sats" endpoint; once
          the payment confirms it cannot be reversed by the SDK.
        - USD sends move cents between Ouro balances (after the platform
          fee) and accept an optional `message`; BTC ignores `message`.
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

        Returns `{"deposit_address": "bc1..."}`. Funds sent on-chain to
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
    def list_usage(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=200, description="Max records to return")] = 20,
        offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
        asset_id: Annotated[Optional[str], Field(description="Only charges for this route")] = None,
        role: Annotated[
            Optional[Literal["consumer", "creator"]],
            Field(description='"consumer": what you spent; "creator": what you earned'),
        ] = None,
    ) -> str:
        """List pay-per-use route charges (USD), newest first. Read-only.

        Each row is one paid run: the route, the charge, and the run's
        action_id. Filter to one route with `asset_id` to audit it.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        history = ouro.money.get_usage_history(
            limit=limit,
            offset=offset,
            asset_id=asset_id,
            role=role,
        )

        def _usage_line(record: Any) -> str:
            asset = record.asset or {}
            quantity = f"{record.quantity:g} {record.cost_unit}" if record.cost_unit else None
            return markdown_bullet(
                asset.get("name") or "route",
                f"${record.total_cents / 100:,.2f}",
                quantity,
                markdown_id(record.id),
                f"action_id: `{record.action_id}`" if record.action_id else None,
                None if record.status in (None, "confirmed") else record.status,
                record.created_at,
            )

        total = history.summary.total_cents
        return render_markdown_list(
            list(history),
            line_fn=_usage_line,
            pagination=page_pagination(history),
            offset=offset,
            noun="charges",
            empty_text="No charges.",
            extras=[f"total: ${total / 100:,.2f} over {history.summary.record_count} charges"],
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_pending_earnings(ctx: Context) -> str:
        """Read creator route earnings (USD, cents). Read-only.

        Route revenue lands in the user's Ouro balance as each paid call
        completes (after the platform fee), so `total_pending_cents` is 0;
        `total_paid_out_cents` is everything earned, `in_progress_cents`
        covers calls still running, and `assets` breaks it down per route.
        Earnings are spendable immediately and withdrawable after 7 days.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        return dump_json(ouro.money.get_pending_earnings().model_dump(mode="json"))

    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def add_funds(ctx: Context) -> str:
        """Return advisory instructions for topping up USD. Does not move money.

        USD top-ups require the Ouro web interface (Stripe-hosted flow).
        This tool is purely informational — it returns `{"message": str}`
        with a link and brief instructions so the agent can hand the user
        off. No API call, no charge, no side effect.
        """
        ouro = ctx.request_context.lifespan_context.ouro
        message = ouro.money.add_funds()
        return dump_json({"message": message})
