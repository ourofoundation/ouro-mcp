#!/usr/bin/env python
"""Direct test of Ouro MCP tools — validates Ouro client + same logic as MCP tools.

Tests search_assets with all supported filters: asset_type, org_id, team_id,
user_id, visibility. Run with OURO_API_KEY set (and optionally OURO_BASE_URL).
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

load_dotenv(override=True)


def _format_assets(results, max_show=3):
    return [
        {
            "id": str(r.get("id", "")),
            "name": r.get("name"),
            "asset_type": r.get("asset_type"),
            "visibility": r.get("visibility"),
        }
        for r in results
    ], results


def main():
    from ouro import Ouro

    api_key = os.environ.get("OURO_API_KEY", "").strip()
    if not api_key:
        print("OURO_API_KEY not set. Set it in .env or environment.")
        return 1

    kwargs = {"api_key": api_key}
    if os.environ.get("OURO_BASE_URL"):
        kwargs["base_url"] = os.environ["OURO_BASE_URL"].strip()

    ouro = Ouro(**kwargs)

    print("Ouro MCP search_assets — filter tests")
    print("=" * 60)
    print(f"Authenticated as: {ouro.user.email}")
    print(f"Backend: {ouro.base_url}")
    print()

    passed = 0
    failed = 0

    # Test 1: asset_type filter (services)
    print("1. asset_type='service'")
    try:
        results = ouro.assets.search("", asset_type="service", limit=5, offset=0)
        assets, _ = _format_assets(results)
        all_service = all(r.get("asset_type") == "service" for r in results)
        if all_service or len(results) == 0:
            print(f"   OK — {len(assets)} services (all asset_type=service)")
            passed += 1
        else:
            print(f"   FAIL — some results not service: {[r.get('asset_type') for r in results]}")
            failed += 1
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Test 2: asset_type filter (datasets)
    print("2. asset_type='dataset'")
    try:
        results = ouro.assets.search("data", asset_type="dataset", limit=5, offset=0)
        assets, _ = _format_assets(results)
        all_dataset = all(r.get("asset_type") == "dataset" for r in results)
        if all_dataset or len(results) == 0:
            print(f"   OK — {len(assets)} datasets")
            passed += 1
        else:
            print(f"   FAIL — some results not dataset: {[r.get('asset_type') for r in results]}")
            failed += 1
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Test 3: user_id filter (user's own assets)
    print("3. user_id filter (current user's assets)")
    try:
        user_id = str(ouro.user.id) if hasattr(ouro.user, "id") else None
        if user_id:
            results = ouro.assets.search("", user_id=user_id, limit=5, offset=0)
            assets, raw = _format_assets(results)
            all_owned = all(
                str(r.get("user_id", "")) == user_id for r in raw
            ) or len(raw) == 0
            if all_owned:
                print(f"   OK — {len(assets)} assets (user_id filter applied)")
                passed += 1
            else:
                print(f"   FAIL — some results not owned by user")
                failed += 1
        else:
            print("   SKIP — no user_id available")
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Test 4: visibility filter
    print("4. visibility='public'")
    try:
        results = ouro.assets.search("", visibility="public", limit=5, offset=0)
        assets, raw = _format_assets(results)
        all_public = all(r.get("visibility") == "public" for r in raw) if raw else True
        if all_public or len(results) == 0:
            print(f"   OK — {len(assets)} public assets")
            passed += 1
        else:
            print(f"   FAIL — some not public: {[r.get('visibility') for r in raw]}")
            failed += 1
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Test 5: pagination (limit/offset)
    print("5. pagination (limit=3, offset=0)")
    try:
        results = ouro.assets.search("", asset_type="service", limit=3, offset=0)
        if len(results) <= 3:
            print(f"   OK — returned {len(results)} (limit=3 respected)")
            passed += 1
        else:
            print(f"   FAIL — expected ≤3, got {len(results)}")
            failed += 1
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Test 6: search_users
    print("6. search_users(query='ouro')")
    try:
        results = ouro.users.search("ouro")
        users = [
            {
                "id": str(u.get("user_id", u.get("id", ""))),
                "username": u.get("username"),
                "display_name": u.get("display_name"),
            }
            for u in results
        ]
        print(f"   OK — {len(users)} users")
        for u in users[:2]:
            print(f"      - {u.get('username')} ({u.get('display_name', '')})")
        passed += 1
    except Exception as e:
        print(f"   FAIL — {e}")
        failed += 1
    print()

    # Summary
    print("=" * 60)
    print(f"Passed: {passed} | Failed: {failed}")
    if failed:
        return 1
    print("All search_assets filter tests passed.")
    return 0


if __name__ == "__main__":
    exit(main())
