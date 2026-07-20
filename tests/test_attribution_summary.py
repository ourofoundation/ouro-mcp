"""Tests for MCP attribution summary reading assets.attribution."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from ouro_mcp.utils import _attribution_summary, format_asset_summary


class AttributionSummaryTests(unittest.TestCase):
    def test_reads_from_attribution_column(self) -> None:
        asset = SimpleNamespace(
            attribution={
                "originality": "third-party",
                "doi_url": "https://doi.org/10.1/x",
                "relation_type": "IsSupplementTo",
                "citation": {"title": "Paper", "doi": "10.1/x"},
            },
            metadata={"base_url": "https://api.example.com", "authentication": "None"},
        )
        summary = _attribution_summary(asset)
        self.assertEqual(summary["originality"], "third-party")
        self.assertEqual(summary["doi_url"], "https://doi.org/10.1/x")
        self.assertEqual(summary["relation_type"], "IsSupplementTo")
        self.assertEqual(summary["citation"]["title"], "Paper")
        self.assertNotIn("base_url", summary)

    def test_falls_back_to_legacy_metadata_keys(self) -> None:
        asset = SimpleNamespace(
            attribution=None,
            metadata={
                "base_url": "https://api.example.com",
                "originality": "original",
                "github_url": "https://github.com/org/repo",
            },
        )
        summary = _attribution_summary(asset)
        self.assertEqual(summary["originality"], "original")
        self.assertEqual(summary["github_url"], "https://github.com/org/repo")
        self.assertNotIn("base_url", summary)

    def test_format_asset_summary_includes_attribution(self) -> None:
        now = datetime.now(timezone.utc)
        asset = SimpleNamespace(
            id=uuid4(),
            name="demo",
            asset_type="service",
            visibility="public",
            created_at=now,
            last_updated=now,
            description=None,
            license_id="MIT",
            state=None,
            source=None,
            attribution={"originality": "original", "doi_url": "https://doi.org/10.1/x"},
            metadata={"base_url": "https://api.example.com"},
            user=None,
            organization=None,
            team=None,
            parent_id=None,
            url=None,
            slug=None,
        )
        # format_asset_summary expects more fields via helpers — stub user/org/team
        for missing in ("user", "organization", "team"):
            setattr(asset, missing, None)
        summary = format_asset_summary(asset)
        self.assertEqual(summary["attribution"]["doi_url"], "https://doi.org/10.1/x")
        self.assertEqual(summary["license_id"], "MIT")


if __name__ == "__main__":
    unittest.main()
