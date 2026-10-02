"""Where create_* tools publish: the caller's choice, else the pinned organization."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ouro_mcp.utils import resolve_location


class TestResolveLocation(unittest.TestCase):
    def test_unpinned_requires_org_and_team(self) -> None:
        ouro = SimpleNamespace(organization=None)
        with self.assertRaises(ValueError):
            resolve_location(ouro, None, None)
        with self.assertRaises(ValueError):
            resolve_location(ouro, "org", None)
        self.assertEqual(resolve_location(ouro, "org", "team"), ("org", "team"))

    def test_unpinned_team_optional_when_not_needed(self) -> None:
        ouro = SimpleNamespace(organization=None)
        self.assertEqual(resolve_location(ouro, "org", need_team=False), ("org", None))
        with self.assertRaises(ValueError):
            resolve_location(ouro, None, need_team=False)

    def test_pinned_fills_org_and_leaves_team_to_the_sdk(self) -> None:
        ouro = SimpleNamespace(organization="pinned-org")
        self.assertEqual(resolve_location(ouro, None, None), ("pinned-org", None))
        self.assertEqual(resolve_location(ouro, None, "team"), ("pinned-org", "team"))

    def test_pinned_passes_explicit_org_through_for_the_sdk_to_check(self) -> None:
        ouro = SimpleNamespace(organization="pinned-org")
        self.assertEqual(resolve_location(ouro, "other", "team"), ("other", "team"))


if __name__ == "__main__":
    unittest.main()
