from __future__ import annotations

import json
from types import SimpleNamespace

from ouro_mcp.tools.quests import register


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeModel(SimpleNamespace):
    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.__dict__)


class _FakeContent:
    def __init__(self) -> None:
        self.text = ""

    def from_markdown(self, markdown: str) -> None:
        self.text = markdown


class _FakeQuests:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_items(self, quest_id: str, items):
        self.calls.append(
            {"method": "create_items", "quest_id": quest_id, "items": items}
        )
        return [
            _FakeModel(
                id=f"item-{idx}",
                description=(
                    item if isinstance(item, str) else item.get("description")
                ),
                status="pending",
                sort_order=idx,
                expected_asset_type=(
                    None
                    if isinstance(item, str)
                    else item.get("expected_asset_type")
                ),
                reward_currency=(
                    "btc"
                    if isinstance(item, str)
                    else item.get("reward_currency", "btc")
                ),
                reward_amount=(
                    0
                    if isinstance(item, str)
                    else int(item.get("reward_amount", 0))
                ),
                reward_xp=(
                    None if isinstance(item, str) else item.get("reward_xp")
                ),
                eval_route_id=(
                    None if isinstance(item, str) else item.get("eval_route_id")
                ),
                eval_score_path=(
                    None if isinstance(item, str) else item.get("eval_score_path")
                ),
                eval_pass_min=(
                    None if isinstance(item, str) else item.get("eval_pass_min")
                ),
                eval_pass_max=(
                    None if isinstance(item, str) else item.get("eval_pass_max")
                ),
                eval_input_key=(
                    None if isinstance(item, str) else item.get("eval_input_key")
                ),
            )
            for idx, item in enumerate(items)
        ]

    def list_assigned_items(self, **kwargs):
        self.calls.append({"method": "list_assigned_items", **kwargs})
        return {
            "data": [
                {
                    "id": "item-1",
                    "quest_id": "quest-1",
                    "description": "Assigned task",
                    "status": "pending",
                }
            ],
            "pagination": {"hasMore": False},
        }

    def update_item(self, quest_id: str, item_id: str, **kwargs):
        self.calls.append(
            {
                "method": "update_item",
                "quest_id": quest_id,
                "item_id": item_id,
                **kwargs,
            }
        )
        return _FakeModel(
            id=item_id,
            description="updated",
            status=kwargs.get("status", "pending"),
            sort_order=0,
            reward_currency=kwargs.get("reward_currency", "btc"),
            reward_amount=kwargs.get("reward_amount", 0),
        )

    def create_entry(self, quest_id: str, **kwargs):
        self.calls.append({"method": "create_entry", "quest_id": quest_id, **kwargs})
        return _FakeModel(id="entry-1", status="submitted")

    def list_entries(self, quest_id: str, **kwargs):
        self.calls.append({"method": "list_entries", "quest_id": quest_id, **kwargs})
        return {
            "data": [_FakeModel(id="entry-1", status="accepted")],
            "pagination": {"hasMore": False, "limit": kwargs["limit"]},
        }

    def review_entry(self, quest_id: str, entry_id: str, **kwargs):
        self.calls.append(
            {
                "method": "review_entry",
                "quest_id": quest_id,
                "entry_id": entry_id,
                **kwargs,
            }
        )
        return _FakeModel(id=entry_id, status=kwargs["status"])


def _ctx(quests: _FakeQuests) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                ouro=SimpleNamespace(
                    quests=quests,
                    posts=SimpleNamespace(Content=_FakeContent),
                )
            )
        )
    )


def _quest_tools() -> dict[str, object]:
    mcp = _CaptureMCP()
    register(mcp)
    return mcp.tools


def test_list_assigned_quest_items_calls_sdk() -> None:
    quests = _FakeQuests()
    result = json.loads(
        _quest_tools()["list_assigned_quest_items"](
            _ctx(quests),
            status="pending,in_progress",
            assignee_id="user-1",
            org_id="org-1",
            team_id="team-1",
            limit=5,
            offset=10,
        )
    )

    assert result["data"][0]["description"] == "Assigned task"
    assert quests.calls == [
        {
            "method": "list_assigned_items",
            "status": "pending,in_progress",
            "assignee_id": "user-1",
            "org_id": "org-1",
            "team_id": "team-1",
            "limit": 5,
            "offset": 10,
            "with_pagination": True,
        }
    ]


def test_submit_quest_entry_passes_keyed_assets() -> None:
    quests = _FakeQuests()
    json.loads(
        _quest_tools()["submit_quest_entry"](
            "quest-1",
            _ctx(quests),
            item_id="item-1",
            description_markdown="Submission notes",
            assets={"file": "asset-1"},
        )
    )

    assert quests.calls == [
        {
            "method": "create_entry",
            "quest_id": "quest-1",
            "item_id": "item-1",
            "assets": {"file": "asset-1"},
            "description": quests.calls[0]["description"],
        }
    ]
    assert quests.calls[0]["description"].text == "Submission notes"


def test_submit_quest_entry_calls_sdk() -> None:
    quests = _FakeQuests()
    result = json.loads(
        _quest_tools()["submit_quest_entry"](
            "quest-1",
            _ctx(quests),
            item_id="item-1",
            description_markdown="Dataset submission notes",
            assets={"dataset": "asset-1"},
        )
    )

    assert result == {"id": "entry-1", "status": "submitted"}
    assert quests.calls == [
        {
            "method": "create_entry",
            "quest_id": "quest-1",
            "item_id": "item-1",
            "assets": {"dataset": "asset-1"},
            "description": quests.calls[0]["description"],
        }
    ]
    assert quests.calls[0]["description"].text == "Dataset submission notes"


def test_list_quest_entries_returns_list_envelope() -> None:
    quests = _FakeQuests()

    def list_entries(quest_id: str, **kwargs):
        quests.calls.append(
            {"method": "list_entries", "quest_id": quest_id, **kwargs}
        )
        return {
            "data": [
                _FakeModel(
                    id="entry-1",
                    status="accepted",
                    assets={"file": {"asset_id": "asset-1", "asset_type": "file"}},
                    embedded_assets=[],
                    users=[],
                )
            ],
            "pagination": {"hasMore": False, "limit": kwargs["limit"]},
        }

    quests.list_entries = list_entries
    result = json.loads(
        _quest_tools()["list_quest_entries"](
            "quest-1",
            _ctx(quests),
            status="accepted",
            limit=10,
            offset=20,
        )
    )

    assert result["results"][0]["assets"] == {
        "file": {"asset_id": "asset-1", "asset_type": "file"}
    }
    assert result["results"][0]["embedded_assets"] == []
    assert result["hasMore"] is False
    assert quests.calls == [
        {
            "method": "list_entries",
            "quest_id": "quest-1",
            "status": "accepted",
            "limit": 10,
            "offset": 20,
            "with_pagination": True,
        }
    ]


def test_create_quest_items_accepts_strings_and_dicts() -> None:
    quests = _FakeQuests()
    payload = [
        "plain description task",
        {
            "description": "Paid eval task",
            "reward_currency": "btc",
            "reward_amount": 1500,
            "reward_xp": 30,
            "eval_route_id": "route-1",
            "eval_score_path": "$.eval.score",
            "eval_pass_min": 0.7,
            "eval_pass_max": 1.0,
            "eval_input_key": "submission",
            "expected_asset_type": "dataset",
        },
    ]
    result = json.loads(
        _quest_tools()["create_quest_items"](
            "quest-1",
            payload,
            _ctx(quests),
        )
    )

    assert len(quests.calls) == 1
    assert quests.calls[0]["method"] == "create_items"
    assert quests.calls[0]["items"] == payload

    assert isinstance(result, list)
    assert result[0]["description"] == "plain description task"
    assert result[0]["reward_amount"] == 0
    assert result[1]["reward_currency"] == "btc"
    assert result[1]["reward_amount"] == 1500
    assert result[1]["eval_route_id"] == "route-1"
    assert result[1]["eval_pass_min"] == 0.7
    assert result[1]["expected_asset_type"] == "dataset"


def test_update_quest_item_propagates_reward_and_eval_fields() -> None:
    quests = _FakeQuests()
    json.loads(
        _quest_tools()["update_quest_item"](
            "quest-1",
            "item-1",
            _ctx(quests),
            description="patched",
            reward_currency="usd",
            reward_amount=2500,
            reward_xp=15,
            eval_route_id="route-2",
        eval_pass_min=0.5,
        eval_pass_max=1.0,
    )
)

    assert quests.calls == [
        {
            "method": "update_item",
            "quest_id": "quest-1",
            "item_id": "item-1",
            "description": "patched",
            "reward_currency": "usd",
            "reward_amount": 2500,
            "reward_xp": 15,
            "eval_route_id": "route-2",
            "eval_pass_min": 0.5,
            "eval_pass_max": 1.0,
        }
    ]


def test_review_quest_entry_calls_sdk() -> None:
    quests = _FakeQuests()
    result = json.loads(
        _quest_tools()["review_quest_entry"](
            "quest-1",
            "entry-1",
            "accepted",
            _ctx(quests),
        )
    )

    assert result == {"id": "entry-1", "status": "accepted"}
    assert quests.calls == [
        {
            "method": "review_entry",
            "quest_id": "quest-1",
            "entry_id": "entry-1",
            "status": "accepted",
            "review": None,
        }
    ]
