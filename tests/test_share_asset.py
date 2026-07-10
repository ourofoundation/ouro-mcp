from __future__ import annotations

import json
from types import SimpleNamespace

from ouro_mcp.tools.assets import register


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeAssets:
    def __init__(self) -> None:
        self.share_calls: list[dict] = []

    def share(self, id: str, user_id: str, role: str = "read") -> None:
        self.share_calls.append({"id": id, "user_id": user_id, "role": role})


def _ctx(assets: _FakeAssets) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(ouro=SimpleNamespace(assets=assets))
        )
    )


def _asset_tools() -> dict[str, object]:
    mcp = _CaptureMCP()
    register(mcp)
    return mcp.tools


def test_share_asset_grants_permission() -> None:
    assets = _FakeAssets()
    tools = _asset_tools()

    result = json.loads(
        tools["share_asset"](
            id="asset-1",
            user_id="user-2",
            ctx=_ctx(assets),
            role="read",
        )
    )

    assert result == {"id": "asset-1", "user_id": "user-2", "role": "read"}
    assert assets.share_calls == [
        {"id": "asset-1", "user_id": "user-2", "role": "read"}
    ]


def test_share_asset_rejects_invalid_role() -> None:
    assets = _FakeAssets()
    tools = _asset_tools()

    result = json.loads(
        tools["share_asset"](
            id="asset-1",
            user_id="user-2",
            ctx=_ctx(assets),
            role="owner",
        )
    )

    assert result["error"] == "invalid_arguments"
    assert "role" in result["message"]
    assert assets.share_calls == []
