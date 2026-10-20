from __future__ import annotations

import json
from types import SimpleNamespace

from ouro.models import DeleteResult

from ouro_mcp.tools.assets import register

COMMENT_ID = "019df875-7957-7888-888f-f8140ff62601"
ROUTE_ID = "019df875-7957-7888-888f-f8140ff62602"


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeAssets:
    """``ouro.assets.delete``: detects the type and deletes through its resource."""

    def __init__(self, asset_type: str, name: str = "hello") -> None:
        self.asset_type = asset_type
        self.name = name
        self.delete_calls: list[dict] = []

    def delete(self, id: str, *, delete_children=None, dry_run: bool = False) -> DeleteResult:
        if self.asset_type not in {"comment", "post", "route"}:
            raise ValueError(f"Cannot delete asset of type '{self.asset_type}' via assets.delete")
        self.delete_calls.append(
            {"id": id, "delete_children": delete_children, "dry_run": dry_run}
        )
        return DeleteResult(id=id, name=self.name, asset_type=self.asset_type, dry_run=dry_run)


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


def test_delete_asset_deletes_comments() -> None:
    assets = _FakeAssets("comment")

    result = json.loads(_asset_tools()["delete_asset"](id=COMMENT_ID, ctx=_ctx(assets)))

    assert result == {
        "deleted": True,
        "id": COMMENT_ID,
        "name": "hello",
        "asset_type": "comment",
        "deleted_children": [],
        "deleted_children_count": 0,
    }
    # Children default is the SDK's call (services cascade, others don't)
    assert assets.delete_calls == [
        {"id": COMMENT_ID, "delete_children": None, "dry_run": False}
    ]


def test_delete_asset_dry_run_previews_comments() -> None:
    assets = _FakeAssets("comment")

    result = json.loads(
        _asset_tools()["delete_asset"](id=COMMENT_ID, ctx=_ctx(assets), dry_run=True)
    )

    assert result["deleted"] is False
    assert result["dry_run"] is True
    assert assets.delete_calls == [
        {"id": COMMENT_ID, "delete_children": None, "dry_run": True}
    ]


def test_delete_asset_deletes_routes() -> None:
    assets = _FakeAssets("route", name="predict")

    result = json.loads(
        _asset_tools()["delete_asset"](id=ROUTE_ID, ctx=_ctx(assets), delete_children=True)
    )

    assert result == {
        "deleted": True,
        "id": ROUTE_ID,
        "name": "predict",
        "asset_type": "route",
        "deleted_children": [],
        "deleted_children_count": 0,
    }
    assert assets.delete_calls == [
        {"id": ROUTE_ID, "delete_children": True, "dry_run": False}
    ]


def test_delete_asset_rejects_unsupported_types() -> None:
    assets = _FakeAssets("unknown")

    result = json.loads(_asset_tools()["delete_asset"](id="other-1", ctx=_ctx(assets)))

    assert result["error"] == "invalid_arguments"
    assert "unknown" in result["message"]
    assert assets.delete_calls == []
