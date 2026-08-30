from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP
from ouro_mcp.tools.quests import (
    EvalStaticInput,
    SubmissionAssetDeclaration,
    register,
)


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

        def _desc(item):
            raw = item if isinstance(item, str) else item.get("description")
            if hasattr(raw, "text") and not isinstance(raw, (str, dict)):
                return raw.text
            return raw

        return [
            _FakeModel(
                id=f"item-{idx}",
                description=_desc(item),
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
                eval_route_id=(
                    None if isinstance(item, str) else item.get("eval_route_id")
                ),
                eval_score_path=(
                    None if isinstance(item, str) else item.get("eval_score_path")
                ),
                eval_categories_path=(
                    None
                    if isinstance(item, str)
                    else item.get("eval_categories_path")
                ),
                eval_pass_min=(
                    None if isinstance(item, str) else item.get("eval_pass_min")
                ),
                eval_pass_max=(
                    None if isinstance(item, str) else item.get("eval_pass_max")
                ),
                leaderboard_enabled=(
                    False
                    if isinstance(item, str)
                    else bool(item.get("leaderboard_enabled"))
                ),
                leaderboard_order=(
                    None if isinstance(item, str) else item.get("leaderboard_order")
                ),
                eval_input_key=(
                    None if isinstance(item, str) else item.get("eval_input_key")
                ),
                submission_assets=(
                    None if isinstance(item, str) else item.get("submission_assets")
                ),
                eval_static_inputs=(
                    None if isinstance(item, str) else item.get("eval_static_inputs")
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
                    "description": {
                        "text": "Assigned task",
                        "json": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Assigned task"}
                                    ],
                                }
                            ],
                        },
                    },
                    "status": "pending",
                }
            ],
            "pagination": {"hasMore": False},
        }

    def list_items(self, quest_id: str):
        self.calls.append({"method": "list_items", "quest_id": quest_id})
        return [
            _FakeModel(
                id="item-1",
                description="Evaluate a structure",
                status="pending",
                sort_order=1,
                reward_currency="btc",
                reward_amount=0,
                assignee_id=None,
                waiting_on=None,
                waiting_until=None,
                child_quest_id=None,
                notes=None,
                leaderboard_enabled=False,
                submission_assets={
                    "structure": {
                        "asset_type": "file",
                        "required": True,
                        "file_extensions": ["cif"],
                    }
                },
                eval_static_inputs={
                    "reference": {
                        "asset_id": "asset-reference",
                        "asset_type": "dataset",
                    }
                },
                contributor_keys=[{"key": "structure", "required": True}],
            )
        ]

    def update_item(self, quest_id: str, item_id: str, **kwargs):
        self.calls.append(
            {
                "method": "update_item",
                "quest_id": quest_id,
                "item_id": item_id,
                **kwargs,
            }
        )
        description = kwargs.get("description", "updated")
        if hasattr(description, "text") and not isinstance(description, (str, dict)):
            description = description.text
        return _FakeModel(
            id=item_id,
            description=description,
            status=kwargs.get("status", "pending"),
            sort_order=0,
            reward_currency=kwargs.get("reward_currency", "btc"),
            reward_amount=kwargs.get("reward_amount", 0),
            leaderboard_enabled=kwargs.get("leaderboard_enabled"),
            leaderboard_order=kwargs.get("leaderboard_order"),
        )

    def create_entry(self, quest_id: str, **kwargs):
        self.calls.append({"method": "create_entry", "quest_id": quest_id, **kwargs})
        return _FakeModel(id="entry-1", status="submitted")

    def list_entries(self, quest_id: str, **kwargs):
        self.calls.append({"method": "list_entries", "quest_id": quest_id, **kwargs})
        return {
            "data": [
                _FakeModel(
                    id="entry-1",
                    status="accepted",
                    eval_score=0.91,
                    eval_status="passed",
                    eval_action_id="action-1",
                )
            ],
            "pagination": {"hasMore": False, "limit": kwargs["limit"]},
        }

    def list_leaderboard(self, quest_id: str, item_id: str, **kwargs):
        self.calls.append(
            {
                "method": "list_leaderboard",
                "quest_id": quest_id,
                "item_id": item_id,
                **kwargs,
            }
        )
        return {
            "data": [
                _FakeModel(
                    placement=1,
                    entry_id="entry-1",
                    score=0.91,
                    status="accepted",
                    eval_status="passed",
                    eval_action_id="action-1",
                    category_scores={"accuracy": 0.95, "completeness": 0.8},
                    user={"username": "ada"},
                )
            ],
            "pagination": {"hasMore": False, "limit": kwargs["limit"]},
            "item": {"id": item_id, "leaderboard_order": "desc"},
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
    result = _quest_tools()["list_assigned_quest_items"](
        _ctx(quests),
        status="pending,in_progress",
        assignee_id="user-1",
        org_id="org-1",
        team_id="team-1",
        limit=5,
        offset=10,
    )

    assert "Assigned task" in result
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


def test_list_quest_items_exposes_effective_submission_shape() -> None:
    quests = _FakeQuests()

    result = _quest_tools()["list_quest_items"]("quest-1", _ctx(quests))

    assert '"structure"' in result
    assert '"file_extensions": ["cif"]' in result
    assert "eval_static_inputs" in result
    assert "asset-reference" in result
    assert "contributor_keys" in result
    assert quests.calls == [{"method": "list_items", "quest_id": "quest-1"}]


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


def test_list_quest_entries_returns_markdown() -> None:
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
                    description="submission",
                    assets={"file": {"asset_id": "asset-1", "asset_type": "file"}},
                    embedded_assets=[],
                    users=[],
                )
            ],
            "pagination": {"hasMore": False, "limit": kwargs["limit"]},
        }

    quests.list_entries = list_entries
    result = _quest_tools()["list_quest_entries"](
        "quest-1",
        _ctx(quests),
        status="accepted",
        limit=10,
        offset=20,
    )

    assert "quest_id: `quest-1`" in result
    assert "id: `entry-1`" in result
    assert "status: accepted" in result
    assert "asset-1" in result
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
            "eval_route_id": "route-1",
            "eval_score_path": "$.eval.score",
            "eval_categories_path": "$.eval.categories",
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
    sent = quests.calls[0]["items"]
    assert len(sent) == 2
    assert sent[0]["description"].text == "plain description task"
    assert sent[1]["description"].text == "Paid eval task"
    assert sent[1]["reward_amount"] == 1500

    assert isinstance(result, list)
    assert result[0]["description"] == "plain description task"
    assert result[0]["reward_amount"] == 0
    assert result[1]["reward_currency"] == "btc"
    assert result[1]["reward_amount"] == 1500
    assert result[1]["eval_route_id"] == "route-1"
    assert result[1]["eval_categories_path"] == "$.eval.categories"
    assert result[1]["eval_pass_min"] == 0.7
    assert result[1]["expected_asset_type"] == "dataset"


def test_create_quest_items_summary_exposes_submission_shape() -> None:
    quests = _FakeQuests()
    result = json.loads(
        _quest_tools()["create_quest_items"](
            "quest-1",
            [
                {
                    "description": "Submit a structure",
                    "submission_assets": {
                        "structure": {
                            "asset_type": "file",
                            "required": True,
                            "file_extensions": ["cif"],
                        }
                    },
                    "eval_static_inputs": {
                        "reference": {
                            "asset_id": "asset-reference",
                            "asset_type": "dataset",
                        }
                    },
                }
            ],
            _ctx(quests),
        )
    )

    assert result[0]["submission_assets"]["structure"]["asset_type"] == "file"
    assert result[0]["eval_static_inputs"]["reference"]["asset_id"] == "asset-reference"
    assert result[0]["contributor_keys"] == [
        {"key": "structure", "required": True}
    ]


def test_quest_write_tools_expose_concrete_submission_asset_schemas() -> None:
    mcp = FastMCP("quest-schema-test")
    register(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    update_schema = tools["update_quest_item"].inputSchema
    submission_property = update_schema["properties"]["submission_assets"]
    declaration_ref = submission_property["anyOf"][0]["additionalProperties"]["$ref"]
    declaration_name = declaration_ref.rsplit("/", 1)[-1]
    declaration = update_schema["$defs"][declaration_name]

    assert declaration["required"] == ["asset_type"]
    assert set(declaration["properties"]) >= {
        "asset_type",
        "required",
        "primary",
        "input_filter",
        "file_extensions",
        "contains_file_extensions",
    }
    assert "server" in submission_property["description"]
    assert "eval_route_id" in submission_property["description"]

    for tool_name in ("create_quest", "create_quest_items"):
        schema = tools[tool_name].inputSchema
        item_model = schema["$defs"]["QuestItemInput"]
        item_submission = item_model["properties"]["submission_assets"]
        assert item_submission["anyOf"][0]["additionalProperties"]["$ref"].endswith(
            "/SubmissionAssetDeclaration"
        )
        assert "server" in item_submission["description"]
        assert "eval_route_id" in item_submission["description"]


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
            eval_route_id="route-2",
            eval_categories_path="$.metrics",
            eval_pass_min=0.5,
            eval_pass_max=1.0,
            leaderboard_enabled=True,
            leaderboard_order="asc",
        )
    )

    assert len(quests.calls) == 1
    call = quests.calls[0]
    assert call["method"] == "update_item"
    assert call["quest_id"] == "quest-1"
    assert call["item_id"] == "item-1"
    assert call["description"].text == "patched"
    assert call["reward_currency"] == "usd"
    assert call["reward_amount"] == 2500
    assert call["eval_route_id"] == "route-2"
    assert call["eval_categories_path"] == "$.metrics"
    assert call["eval_pass_min"] == 0.5
    assert call["eval_pass_max"] == 1.0
    assert call["leaderboard_enabled"] is True
    assert call["leaderboard_order"] == "asc"


def test_update_quest_item_serializes_typed_submission_config() -> None:
    quests = _FakeQuests()
    _quest_tools()["update_quest_item"](
        "quest-1",
        "item-1",
        _ctx(quests),
        submission_assets={
            "structure": SubmissionAssetDeclaration(
                asset_type="file",
                required=True,
                file_extensions=["cif"],
            )
        },
        eval_static_inputs={
            "reference": EvalStaticInput(
                asset_id="asset-reference",
                asset_type="dataset",
            )
        },
    )

    call = quests.calls[0]
    assert call["submission_assets"] == {
        "structure": {
            "asset_type": "file",
            "required": True,
            "file_extensions": ["cif"],
        }
    }
    assert call["eval_static_inputs"] == {
        "reference": {
            "asset_id": "asset-reference",
            "asset_type": "dataset",
        }
    }


def test_update_quest_item_passes_waiting_fields() -> None:
    quests = _FakeQuests()
    result = json.loads(
        _quest_tools()["update_quest_item"](
            "quest-1",
            "item-1",
            _ctx(quests),
            waiting_on="reply from authors",
            waiting_until="2026-07-07T14:00:00Z",
            waiting_check_every="1d",
        )
    )

    assert quests.calls == [
        {
            "method": "update_item",
            "quest_id": "quest-1",
            "item_id": "item-1",
            "waiting_on": "reply from authors",
            "waiting_until": "2026-07-07T14:00:00Z",
            "waiting_check_every": "1d",
        }
    ]
    # Empty strings are preserved so callers can clear the fields.
    quests.calls.clear()
    _quest_tools()["update_quest_item"](
        "quest-1",
        "item-1",
        _ctx(quests),
        waiting_on="",
        waiting_until="",
    )
    assert quests.calls[0]["waiting_on"] == ""
    assert quests.calls[0]["waiting_until"] == ""


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


def test_list_quest_leaderboard_renders_ranked_rows() -> None:
    quests = _FakeQuests()
    result = _quest_tools()["list_quest_leaderboard"](
        "quest-1",
        "item-1",
        _ctx(quests),
    )

    assert quests.calls == [
        {
            "method": "list_leaderboard",
            "quest_id": "quest-1",
            "item_id": "item-1",
            "limit": 50,
            "offset": 0,
            "with_pagination": True,
        }
    ]
    assert "score: 0.91" in result
    assert "accuracy=0.95" in result
    assert "@ada" in result
    assert "item_id: `item-1`" in result
