from __future__ import annotations

import unittest

from ouro_mcp.utils import (
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
