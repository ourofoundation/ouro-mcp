from __future__ import annotations

from ouro_mcp.prompts import register_all_prompts


class _CaptureMCP:
    def __init__(self) -> None:
        self.prompts: dict[str, object] = {}

    def prompt(self, name=None, **_kwargs):
        def decorator(fn):
            self.prompts[name or fn.__name__] = fn
            return fn

        return decorator


def test_registers_quest_authoring_guide_prompt() -> None:
    mcp = _CaptureMCP()

    register_all_prompts(mcp)

    assert "quest_authoring_guide" in mcp.prompts


def test_quest_authoring_guide_includes_context_and_guidance() -> None:
    mcp = _CaptureMCP()
    register_all_prompts(mcp)

    prompt = mcp.prompts["quest_authoring_guide"](
        quest_goal="collect 2D materials lab data",
        reward_notes="pay 1000 sats per accepted dataset",
        review_notes="accept only reusable data with units and provenance",
    )

    assert "collect 2D materials lab data" in prompt
    assert "pay 1000 sats per accepted dataset" in prompt
    assert "get_organizations()" in prompt
    assert "agent_can_create" in prompt
    assert 'type="continuous"' in prompt
    assert "reward_amount" in prompt
