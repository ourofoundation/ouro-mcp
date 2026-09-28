from __future__ import annotations

import json
from types import SimpleNamespace

from ouro.models import Notification, Page

from ouro_mcp.tools.notifications import register

NOTIFICATION_ID = "019df875-7957-7888-888f-f8140ff62801"
ALICE_ID = "019df875-7957-7888-888f-f8140ff62802"
ASSET_ID = "019df875-7957-7888-888f-f8140ff62803"


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

    def list(self, **kwargs) -> Page[Notification]:
        self.list_calls.append(kwargs)
        return Page[Notification].model_validate(
            {
                "data": [
                    {
                        "id": NOTIFICATION_ID,
                        "type": "mention",
                        "viewed": False,
                        "created_at": "2026-07-28T12:00:00Z",
                        "source_user": {"user_id": ALICE_ID, "username": "alice"},
                        "content": {"text": "hey"},
                        "asset": {"id": ASSET_ID, "name": "P", "asset_type": "post"},
                    }
                ],
                "hasMore": False,
            }
        )


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
    result = tools["get_notifications"](
        ctx=_ctx(notifications),
        category="mentions,comments,shares",
        unread_only=True,
        limit=10,
    )
    assert notifications.list_calls == [
        {
            "offset": 0,
            "limit": 10,
            "org_id": None,
            "unread_only": True,
            "category": "mentions,comments,shares",
        }
    ]
    assert f"id: `{NOTIFICATION_ID}`" in result
    assert "mention" in result
    assert "@alice" in result
