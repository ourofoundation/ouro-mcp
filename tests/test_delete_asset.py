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


class _FakeDeleter:
    def __init__(self, asset_type: str, name: str = "hello") -> None:
        self.asset_type = asset_type
        self.name = name
        self.delete_calls: list[dict] = []

    def delete(self, id: str, *, delete_children: bool = False, dry_run: bool = False) -> dict:
        self.delete_calls.append(
            {"id": id, "delete_children": delete_children, "dry_run": dry_run}
        )
        payload = {
            "id": id,
            "name": self.name,
            "asset_type": self.asset_type,
            "deleted_children": [],
        }
        if dry_run:
            payload["dry_run"] = True
        return payload


class _FakeAssets:
    def __init__(self, asset: SimpleNamespace) -> None:
        self._asset = asset

    def retrieve(self, id: str) -> SimpleNamespace:
        assert id == self._asset.id
        return self._asset


def _ctx(assets: _FakeAssets, **resources: object) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                ouro=SimpleNamespace(assets=assets, **resources)
            )
        )
    )


def _asset_tools() -> dict[str, object]:
    mcp = _CaptureMCP()
    register(mcp)
    return mcp.tools


def test_delete_asset_deletes_comments() -> None:
    comment = SimpleNamespace(id="comment-1", name="", asset_type="comment")
    posts = _FakeDeleter("comment")
    tools = _asset_tools()

    result = json.loads(
        tools["delete_asset"](
            id="comment-1", ctx=_ctx(_FakeAssets(comment), posts=posts)
        )
    )

    assert result == {
        "deleted": True,
        "id": "comment-1",
        "name": "hello",
        "asset_type": "comment",
        "deleted_children": [],
        "deleted_children_count": 0,
    }
    assert posts.delete_calls == [
        {"id": "comment-1", "delete_children": False, "dry_run": False}
    ]


def test_delete_asset_dry_run_previews_comments() -> None:
    comment = SimpleNamespace(id="comment-1", name="", asset_type="comment")
    posts = _FakeDeleter("comment")
    tools = _asset_tools()

    result = json.loads(
        tools["delete_asset"](
            id="comment-1",
            ctx=_ctx(_FakeAssets(comment), posts=posts),
            dry_run=True,
        )
    )

    assert result["deleted"] is False
    assert result["dry_run"] is True
    assert posts.delete_calls == [
        {"id": "comment-1", "delete_children": False, "dry_run": True}
    ]


def test_delete_asset_deletes_routes() -> None:
    route = SimpleNamespace(id="route-1", name="predict", asset_type="route")
    routes = _FakeDeleter("route", name="predict")
    tools = _asset_tools()

    result = json.loads(
        tools["delete_asset"](
            id="route-1", ctx=_ctx(_FakeAssets(route), routes=routes)
        )
    )

    assert result == {
        "deleted": True,
        "id": "route-1",
        "name": "predict",
        "asset_type": "route",
        "deleted_children": [],
        "deleted_children_count": 0,
    }
    assert routes.delete_calls == [
        {"id": "route-1", "delete_children": False, "dry_run": False}
    ]


def test_delete_asset_rejects_unsupported_types() -> None:
    asset = SimpleNamespace(id="other-1", name="x", asset_type="unknown")
    posts = _FakeDeleter("comment")
    tools = _asset_tools()

    result = json.loads(
        tools["delete_asset"](
            id="other-1", ctx=_ctx(_FakeAssets(asset), posts=posts)
        )
    )

    assert result["error"] == "unsupported_type"
    assert "unknown" in result["message"]
    assert posts.delete_calls == []
