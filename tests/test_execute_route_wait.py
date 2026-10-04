from __future__ import annotations

import json
from types import SimpleNamespace

from ouro.models import Action, Page

from ouro_mcp.tools.services import register

ROUTE_ID = "019df875-7957-7888-888f-f8140ff62602"
ACTION_ID = "019df875-7957-7888-888f-f8140ff62603"
USER_ID = "019df875-7957-7888-888f-f8140ff62604"


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _action(status: str, **overrides) -> Action:
    return Action(
        id=ACTION_ID, route_id=ROUTE_ID, user_id=USER_ID, status=status, **overrides
    )


class _FakeRoutes:
    def __init__(self, polled: Action | None = None, page: list[Action] | None = None):
        self.polled = polled
        self.page = page or []
        self.execute_calls: list[dict] = []
        self.poll_calls: list[dict] = []
        self.list_calls: list[dict] = []

    def retrieve(self, route_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=ROUTE_ID,
            name="Relax",
            route=SimpleNamespace(execution_mode="sync"),
            metrics=None,
        )

    def execute(self, route_id: str, **kwargs) -> Action:
        self.execute_calls.append(kwargs)
        return _action("in-progress")

    def poll_action(self, action_id: str, **kwargs) -> Action:
        self.poll_calls.append(kwargs)
        if self.polled is None:
            raise TimeoutError("still running")
        return self.polled

    def retrieve_action(self, action_id: str) -> Action:
        return self.polled

    def list_my_actions(self, **kwargs) -> Page[Action]:
        self.list_calls.append(kwargs)
        return Page[Action](data=self.page, has_more=False)


def _call(tool: str, routes: _FakeRoutes, **kwargs) -> str:
    mcp = _CaptureMCP()
    register(mcp)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(ouro=SimpleNamespace(routes=routes))
        )
    )
    return mcp.tools[tool](ctx=ctx, **kwargs)


def test_execute_route_takes_handle_first_and_returns_result() -> None:
    routes = _FakeRoutes(polled=_action("success", response={"ok": True}))

    result = json.loads(_call("execute_route", routes, route_id=ROUTE_ID))

    # Never a held-open request, even on a sync route
    assert routes.execute_calls[0]["wait"] is False
    assert routes.poll_calls[0]["timeout"] == 45.0
    assert result["status"] == "success"
    assert result["finished"] is True
    assert result["data"] == {"ok": True}


def test_get_action_wait_timeout_reports_still_running() -> None:
    class _StillRunning(_FakeRoutes):
        def retrieve_action(self, action_id: str) -> Action:
            return _action("in-progress")

    result = json.loads(
        _call("get_action", _StillRunning(), action_id=ACTION_ID, wait=True, timeout=1)
    )

    assert result["status"] == "in-progress"
    assert result["finished"] is False
    assert "STILL RUNNING" in result["message"]


def test_get_action_timed_out_status_is_a_final_failure() -> None:
    routes = _FakeRoutes(polled=_action("timed-out"))

    result = json.loads(_call("get_action", routes, action_id=ACTION_ID))

    assert result["status"] == "timed-out"
    assert result["finished"] is True
    assert "did not succeed" in result["note"]


def test_execute_route_timeout_keeps_action_id() -> None:
    routes = _FakeRoutes(polled=None)

    result = json.loads(
        _call("execute_route", routes, route_id=ROUTE_ID, timeout=1)
    )

    assert result["status"] == "in-progress"
    assert result["finished"] is False
    assert "STILL RUNNING" in result["message"]
    assert result["action_id"] == ACTION_ID
    assert f"ouro action wait {ACTION_ID}" in result["message"]


def test_execute_route_no_wait_skips_polling() -> None:
    routes = _FakeRoutes(polled=None)

    result = json.loads(
        _call("execute_route", routes, route_id=ROUTE_ID, wait=False)
    )

    assert routes.poll_calls == []
    assert result["status"] == "in-progress"
    assert result["finished"] is False
    assert result["action_id"] == ACTION_ID


def test_list_my_actions_passes_status_filter() -> None:
    routes = _FakeRoutes(
        page=[_action("in-progress", route={"id": ROUTE_ID, "name": "Relax"})]
    )

    text = _call("list_my_actions", routes, status="queued, in-progress")

    assert routes.list_calls[0]["status"] == ["queued", "in-progress"]
    assert ACTION_ID in text
    assert "in-progress" in text
    assert "assetComponent" not in text


def test_list_my_actions_rejects_unknown_status() -> None:
    result = json.loads(_call("list_my_actions", _FakeRoutes(), status="running"))

    assert "error" in result


ORG_ID = "00000000-0000-0000-0000-0000000000aa"
TEAM_ID = "00000000-0000-0000-0000-0000000000bb"


def _call_with_ouro(ouro: SimpleNamespace, **kwargs) -> str:
    mcp = _CaptureMCP()
    register(mcp)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(ouro=ouro))
    )
    return mcp.tools["execute_route"](ctx=ctx, **kwargs)


def test_execute_route_runs_in_the_named_organization() -> None:
    routes = _FakeRoutes(polled=_action("success"))

    _call("execute_route", routes, route_id=ROUTE_ID, org_id=ORG_ID, team_id=TEAM_ID)

    assert routes.execute_calls[0]["org_id"] == ORG_ID
    assert routes.execute_calls[0]["team_id"] == TEAM_ID


def test_execute_route_without_an_organization_stays_personal() -> None:
    routes = _FakeRoutes(polled=_action("success"))

    _call("execute_route", routes, route_id=ROUTE_ID)

    assert "org_id" not in routes.execute_calls[0]
    assert "team_id" not in routes.execute_calls[0]


def test_execute_route_takes_the_organization_from_the_team() -> None:
    routes = _FakeRoutes(polled=_action("success"))
    teams = SimpleNamespace(retrieve=lambda team_id: {"id": team_id, "org_id": ORG_ID})

    _call_with_ouro(
        SimpleNamespace(routes=routes, teams=teams, organization=None),
        route_id=ROUTE_ID,
        team_id=TEAM_ID,
    )

    assert routes.execute_calls[0]["org_id"] == ORG_ID


def test_execute_route_pinned_leaves_the_organization_to_the_client() -> None:
    routes = _FakeRoutes(polled=_action("success"))

    _call_with_ouro(
        SimpleNamespace(routes=routes, organization=ORG_ID),
        route_id=ROUTE_ID,
        team_id=TEAM_ID,
    )

    assert "org_id" not in routes.execute_calls[0]
    assert routes.execute_calls[0]["team_id"] == TEAM_ID
