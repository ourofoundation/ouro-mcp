from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from ouro_mcp.constants import ENV_OURO_FRONTEND_URL, GLOBAL_ORG_ID
from ouro_mcp.tools.teams import _team_summary
from ouro_mcp.utils import (
    absolute_web_url,
    asset_web_url,
    format_asset_summary,
    frontend_origin,
    team_web_url,
)


class TestFrontendOrigin(unittest.TestCase):
    def test_defaults_to_ouro_foundation(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            self.assertEqual(frontend_origin(), "https://ouro.foundation")
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous

    def test_strips_trailing_slash(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ[ENV_OURO_FRONTEND_URL] = "https://example.test/"
        try:
            self.assertEqual(frontend_origin(), "https://example.test")
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous


class TestAbsoluteWebUrl(unittest.TestCase):
    def test_joins_path_to_origin(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            self.assertEqual(
                absolute_web_url("/posts/hermes/example"),
                "https://ouro.foundation/posts/hermes/example",
            )
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous

    def test_passes_through_absolute_url(self) -> None:
        self.assertEqual(
            absolute_web_url("https://ouro.foundation/teams/physics"),
            "https://ouro.foundation/teams/physics",
        )


class TestAssetWebUrl(unittest.TestCase):
    def test_prefers_backend_url(self) -> None:
        asset = SimpleNamespace(
            url="https://ouro.foundation/posts/hermes/example",
            slug="/posts/hermes/other",
        )
        self.assertEqual(
            asset_web_url(asset),
            "https://ouro.foundation/posts/hermes/example",
        )

    def test_falls_back_to_slug(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            self.assertEqual(
                asset_web_url({"slug": "/posts/hermes/example"}),
                "https://ouro.foundation/posts/hermes/example",
            )
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous

    def test_missing_returns_none(self) -> None:
        self.assertIsNone(asset_web_url({"id": "x"}))


class TestTeamWebUrl(unittest.TestCase):
    def test_global_org_team(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            self.assertEqual(
                team_web_url(name="permanent-magnets", org_id=GLOBAL_ORG_ID),
                "https://ouro.foundation/teams/permanent-magnets",
            )
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous

    def test_org_scoped_team(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            self.assertEqual(
                team_web_url(
                    name="research",
                    org_id="org-1",
                    org_name="acme",
                ),
                "https://ouro.foundation/acme/teams/research",
            )
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous

    def test_org_scoped_without_name_returns_none(self) -> None:
        self.assertIsNone(
            team_web_url(name="research", org_id="org-1", org_name=None)
        )


class TestFormatAssetSummaryUrl(unittest.TestCase):
    def test_includes_full_url(self) -> None:
        now = datetime.now(UTC)
        asset = SimpleNamespace(
            id="019f292d-75a6-7fb5-8cb0-008378f8a7ce",
            name="Audit",
            asset_type="post",
            visibility="public",
            created_at=now,
            last_updated=now,
            description=None,
            state=None,
            source=None,
            parent_id=None,
            user=None,
            organization=None,
            team=None,
            url="https://ouro.foundation/posts/hermes/what-machine-learning-gets-wrong-about-materials-a-cross-domain-failure-audit",
            slug="/posts/hermes/what-machine-learning-gets-wrong-about-materials-a-cross-domain-failure-audit",
        )
        summary = format_asset_summary(asset)
        self.assertEqual(
            summary["url"],
            "https://ouro.foundation/posts/hermes/what-machine-learning-gets-wrong-about-materials-a-cross-domain-failure-audit",
        )


class TestTeamSummaryUrl(unittest.TestCase):
    def test_includes_full_url_for_global_team(self) -> None:
        previous = os.environ.get(ENV_OURO_FRONTEND_URL)
        os.environ.pop(ENV_OURO_FRONTEND_URL, None)
        try:
            summary = _team_summary(
                {
                    "id": "team-1",
                    "name": "permanent-magnets",
                    "org_id": GLOBAL_ORG_ID,
                    "visibility": "public",
                    "default_role": "write",
                    "source_policy": "any",
                    "actor_type_policy": "any",
                    "organization": {"name": "all"},
                }
            )
            self.assertEqual(
                summary["url"],
                "https://ouro.foundation/teams/permanent-magnets",
            )
        finally:
            if previous is None:
                os.environ.pop(ENV_OURO_FRONTEND_URL, None)
            else:
                os.environ[ENV_OURO_FRONTEND_URL] = previous


if __name__ == "__main__":
    unittest.main()
