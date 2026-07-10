from __future__ import annotations

import json
from types import SimpleNamespace

from ouro_mcp.tools.teams import register


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeTeams:
    def __init__(self, team: dict) -> None:
        self.team = team
        self.retrieve_calls: list[dict] = []

    def retrieve(self, team_id: str, *, include_members: bool = False) -> dict:
        self.retrieve_calls.append(
            {"team_id": team_id, "include_members": include_members}
        )
        assert team_id == self.team["id"]
        return self.team

    def list(self, **_kwargs):
        raise AssertionError("list should not be called")


def _ctx(teams: _FakeTeams) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(ouro=SimpleNamespace(teams=teams))
        )
    )


def _sample_team() -> dict:
    return {
        "id": "team-1",
        "name": "matsci",
        "org_id": "00000000-0000-0000-0000-000000000000",
        "visibility": "public",
        "default_role": "write",
        "source_policy": "any",
        "actor_type_policy": "any",
        "organization": {"name": "all"},
        "memberCount": 2,
        "members": [
            {
                "user_id": "user-1",
                "role": "admin",
                "user": {"username": "alice"},
            },
            {
                "user_id": "user-2",
                "role": "write",
                "user": {"username": "bob"},
            },
        ],
    }


def test_get_teams_detail_omits_members_by_default():
    mcp = _CaptureMCP()
    register(mcp)
    get_teams = mcp.tools["get_teams"]
    teams = _FakeTeams(_sample_team())
    ctx = _ctx(teams)

    payload = json.loads(get_teams(ctx=ctx, id="team-1"))

    assert teams.retrieve_calls == [
        {"team_id": "team-1", "include_members": False}
    ]
    assert payload["member_count"] == 2
    assert "members" not in payload
    assert payload["url"] == "https://ouro.foundation/teams/matsci"


def test_get_teams_detail_includes_members_when_requested():
    mcp = _CaptureMCP()
    register(mcp)
    get_teams = mcp.tools["get_teams"]
    teams = _FakeTeams(_sample_team())
    ctx = _ctx(teams)

    payload = json.loads(get_teams(ctx=ctx, id="team-1", include_members=True))

    assert teams.retrieve_calls == [
        {"team_id": "team-1", "include_members": True}
    ]
    assert payload["member_count"] == 2
    assert payload["members"] == [
        {"user_id": "user-1", "role": "admin", "username": "alice"},
        {"user_id": "user-2", "role": "write", "username": "bob"},
    ]
