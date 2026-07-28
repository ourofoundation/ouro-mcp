from __future__ import annotations

import json
from types import SimpleNamespace

from ouro_mcp.tools.notifications import register


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeNotifications:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.list_calls: list[dict] = []
        self.fail_ids: set[str] = set()

    def read(self, nid: str):
        if nid in self.fail_ids:
            raise RuntimeError(f"boom:{nid}")
        self.read_calls.append(nid)
        return {
            "id": nid,
            "type": "comment",
            "viewed": True,
            "created_at": "2026-07-28T12:00:00Z",
            "source_user": {"username": "alice"},
            "content": {"text": "hi"},
        }

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "data": [
                {
                    "id": "n1",
                    "type": "mention",
                    "viewed": False,
                    "created_at": "2026-07-28T12:00:00Z",
                    "source_user": {"username": "alice"},
                    "content": {"text": "hey", "asset": {"id": "a1", "name": "P", "asset_type": "post"}},
                }
            ],
            "pagination": {"hasMore": False},
        }


def _ctx(notifications: _FakeNotifications) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                ouro=SimpleNamespace(notifications=notifications)
            )
        )
    )


def _tools() -> dict[str, object]:
    mcp = _CaptureMCP()
    register(mcp)
    return mcp.tools


def test_read_notification_single_id() -> None:
    notifications = _FakeNotifications()
    tools = _tools()
    result = json.loads(
        tools["read_notification"](ids="n1", ctx=_ctx(notifications))
    )
    assert result["read"] == 1
    assert result["read_ids"] == ["n1"]
    assert result["failed"] == []
    assert notifications.read_calls == ["n1"]


def test_read_notification_batch_dedupes() -> None:
    notifications = _FakeNotifications()
    tools = _tools()
    result = json.loads(
        tools["read_notification"](
            ids=["n1", "n2", "n1"], ctx=_ctx(notifications)
        )
    )
    assert result["read"] == 2
    assert result["read_ids"] == ["n1", "n2"]
    assert notifications.read_calls == ["n1", "n2"]


def test_read_notification_aggregates_failures() -> None:
    notifications = _FakeNotifications()
    notifications.fail_ids.add("bad")
    tools = _tools()
    result = json.loads(
        tools["read_notification"](
            ids=["ok", "bad", "also-ok"], ctx=_ctx(notifications)
        )
    )
    assert result["read"] == 2
    assert result["read_ids"] == ["ok", "also-ok"]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == "bad"
    assert notifications.read_calls == ["ok", "also-ok"]


def test_get_notifications_passes_category() -> None:
    notifications = _FakeNotifications()
    tools = _tools()
    result = json.loads(
        tools["get_notifications"](
            ctx=_ctx(notifications),
            category="mentions,comments,shares",
            unread_only=True,
            limit=10,
        )
    )
    assert notifications.list_calls == [
        {
            "offset": 0,
            "limit": 10,
            "org_id": None,
            "unread_only": True,
            "category": "mentions,comments,shares",
            "with_pagination": True,
        }
    ]
    assert result["results"][0]["id"] == "n1"
    assert result["results"][0]["type"] == "mention"
