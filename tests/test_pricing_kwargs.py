from __future__ import annotations

import unittest

from ouro_mcp.utils import per_use_pricing_kwargs, unlock_pricing_kwargs


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


if __name__ == "__main__":
    unittest.main()
