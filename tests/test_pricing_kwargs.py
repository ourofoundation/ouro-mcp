from __future__ import annotations

import unittest

from ouro_mcp.utils import (
    format_monetization_block,
    format_pay_per_use_cost_summary,
    per_use_pricing_kwargs,
    unlock_pricing_kwargs,
)


class TestUnlockPricingKwargs(unittest.TestCase):
    def test_monetized_sets_pay_to_unlock(self) -> None:
        self.assertEqual(
            unlock_pricing_kwargs("monetized", 0.5, "usd"),
            {"monetization": "pay-to-unlock", "price": 0.5, "price_currency": "usd"},
        )

    def test_monetized_requires_price(self) -> None:
        with self.assertRaises(ValueError):
            unlock_pricing_kwargs("monetized", None, "btc")

    def test_other_visibility_makes_free(self) -> None:
        self.assertEqual(unlock_pricing_kwargs("public", None, None), {"monetization": "none"})

    def test_price_change_alone_leaves_monetization(self) -> None:
        self.assertEqual(unlock_pricing_kwargs(None, 200, None), {"price": 200})

    def test_nothing_passed(self) -> None:
        self.assertEqual(unlock_pricing_kwargs(None, None, None), {})


class TestPerUsePricingKwargs(unittest.TestCase):
    def test_monetized_sets_fixed_pay_per_use(self) -> None:
        self.assertEqual(
            per_use_pricing_kwargs("monetized", 10, "btc", None),
            {
                "monetization": "pay-per-use",
                "cost_accounting": "fixed",
                "cost_unit": "call",
                "unit_cost": 10,
                "price_currency": "btc",
            },
        )

    def test_keeps_custom_cost_unit(self) -> None:
        kwargs = per_use_pricing_kwargs("monetized", 0.05, "usd", "image")
        self.assertEqual(kwargs["cost_unit"], "image")

    def test_monetized_requires_unit_cost(self) -> None:
        with self.assertRaises(ValueError):
            per_use_pricing_kwargs("monetized", None, "usd", None)

    def test_inherit_makes_free(self) -> None:
        self.assertEqual(per_use_pricing_kwargs("inherit", None, None, None), {"monetization": "none"})

    def test_cost_change_alone_leaves_monetization(self) -> None:
        self.assertEqual(per_use_pricing_kwargs(None, 0.1, None, None), {"unit_cost": 0.1})

    def test_per_second_sets_runtime_pricing(self) -> None:
        self.assertEqual(
            per_use_pricing_kwargs("monetized", 0.0005, "usd", None, "per_second", 3600),
            {
                "monetization": "pay-per-use",
                "cost_accounting": "runtime",
                "cost_unit": "seconds",
                "max_billable_seconds": 3600,
                "unit_cost": 0.0005,
                "price_currency": "usd",
            },
        )

    def test_per_second_requires_max(self) -> None:
        with self.assertRaises(ValueError):
            per_use_pricing_kwargs("monetized", 0.0005, "usd", None, "per_second", None)

    def test_update_without_pricing_keeps_model(self) -> None:
        self.assertEqual(
            per_use_pricing_kwargs("monetized", 0.001, None, None, default_pricing=None),
            {"monetization": "pay-per-use", "unit_cost": 0.001},
        )

    def test_cap_change_alone(self) -> None:
        self.assertEqual(
            per_use_pricing_kwargs(None, None, None, None, max_billable_seconds=600),
            {"max_billable_seconds": 600},
        )


class TestRuntimeCostSummary(unittest.TestCase):
    def test_usd_runtime_summary_keeps_sub_cent_rate(self) -> None:
        summary = format_pay_per_use_cost_summary(0.0005, "seconds", "usd", "runtime", 3600)
        self.assertIn("$0.0005 per second", summary)
        self.assertIn("up to $1.80 per run", summary)

    def test_usd_per_unit_summary_keeps_sub_cent_rate(self) -> None:
        self.assertEqual(
            format_pay_per_use_cost_summary(0.001, "word", "usd"),
            "$0.001 per word (USD)",
        )
        self.assertEqual(
            format_pay_per_use_cost_summary(0.25, "call", "usd"),
            "$0.25 per call (USD)",
        )

    def test_btc_runtime_summary(self) -> None:
        summary = format_pay_per_use_cost_summary(2, "seconds", "btc", "runtime", 600)
        self.assertIn("2 sats per second", summary)
        self.assertIn("up to 1200 sats per run", summary)


if __name__ == "__main__":
    unittest.main()


class TestDualPricingKwargs(unittest.TestCase):
    def test_unlock_price_per_currency(self) -> None:
        self.assertEqual(
            unlock_pricing_kwargs("monetized", None, None, 0.5, 500),
            {"monetization": "pay-to-unlock", "price_usd": 0.5, "price_sats": 500},
        )

    def test_one_currency_is_enough_to_monetize(self) -> None:
        self.assertEqual(
            unlock_pricing_kwargs("monetized", None, None, price_sats=500),
            {"monetization": "pay-to-unlock", "price_sats": 500},
        )

    def test_zero_stops_selling_in_a_currency(self) -> None:
        self.assertEqual(unlock_pricing_kwargs(None, None, None, price_usd=0), {"price_usd": 0})

    def test_single_and_per_currency_prices_dont_mix(self) -> None:
        with self.assertRaises(ValueError):
            unlock_pricing_kwargs("monetized", 0.5, "usd", price_sats=500)
        with self.assertRaises(ValueError):
            per_use_pricing_kwargs("monetized", 0.1, "usd", None, unit_cost_sats=100)

    def test_monetized_needs_a_positive_price(self) -> None:
        with self.assertRaises(ValueError):
            unlock_pricing_kwargs("monetized", None, None, price_usd=0)

    def test_route_price_per_currency(self) -> None:
        self.assertEqual(
            per_use_pricing_kwargs(
                "monetized", None, "btc", None, unit_cost_usd=0.05, unit_cost_sats=50
            ),
            {
                "monetization": "pay-per-use",
                "unit_cost_usd": 0.05,
                "unit_cost_sats": 50,
                "price_currency": "btc",
                "cost_accounting": "fixed",
                "cost_unit": "call",
            },
        )


class TestDualMonetizationBlock(unittest.TestCase):
    def test_route_sold_in_both_lists_each_price_primary_first(self) -> None:
        block = format_monetization_block(
            {
                "monetization": "pay-per-use",
                "price_currency": "btc",
                "unit_cost": 50,
                "unit_cost_usd": 0.05,
                "unit_cost_sats": 50,
                "cost_unit": "call",
                "cost_accounting": "fixed",
            }
        )
        self.assertEqual(block["currencies"], ["btc", "usd"])
        self.assertEqual(block["unit_cost_usd"], 0.05)
        self.assertEqual(block["unit_cost_sats"], 50)
        self.assertEqual(
            block["cost_summary"],
            "50 sats or $0.05 per call. Sold in both currencies: pass currency "
            "to pick, otherwise BTC is charged.",
        )

    def test_post_sold_in_both(self) -> None:
        block = format_monetization_block(
            {
                "monetization": "pay-to-unlock",
                "price_currency": "usd",
                "price": 0.5,
                "price_usd": 0.5,
                "price_sats": 400,
            }
        )
        self.assertEqual(block["currencies"], ["usd", "btc"])
        self.assertEqual(
            block["cost_summary"],
            "$0.50 or 400 sats. Sold in both currencies: pass currency to pick, "
            "otherwise USD is charged.",
        )

    def test_per_second_route_sold_in_both_states_the_hold_once(self) -> None:
        block = format_monetization_block(
            {
                "monetization": "pay-per-use",
                "price_currency": "btc",
                "unit_cost": 2,
                "unit_cost_usd": 0.0005,
                "unit_cost_sats": 2,
                "cost_unit": "seconds",
                "cost_accounting": "runtime",
                "max_billable_seconds": 60,
            }
        )
        self.assertEqual(
            block["cost_summary"],
            "2 sats or $0.0005 per second of runtime, up to 120 sats or $0.03 "
            "per run (max 60s; that much, or what you can afford, is held until "
            "the run finishes). Sold in both currencies: pass currency to pick, "
            "otherwise BTC is charged.",
        )

    def test_single_currency_asset_is_unchanged(self) -> None:
        block = format_monetization_block(
            {
                "monetization": "pay-to-unlock",
                "price_currency": "usd",
                "price": 0.5,
                "price_usd": 0.5,
                "price_sats": None,
            }
        )
        self.assertEqual(
            block,
            {
                "monetization": "pay-to-unlock",
                "price_currency": "usd",
                "price": 0.5,
                "cost_summary": "$0.50 (USD)",
            },
        )
