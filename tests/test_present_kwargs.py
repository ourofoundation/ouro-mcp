"""present_kwargs drops blank optional filter values."""

from ouro_mcp.utils import optional_kwargs, present_kwargs


def test_optional_kwargs_keeps_empty_string():
    # Update tools rely on "" meaning "clear this field".
    assert optional_kwargs(waiting_on="", status=None) == {"waiting_on": ""}


def test_present_kwargs_drops_blank_and_none():
    assert present_kwargs(
        org_id="",
        team_id="   ",
        user_id=None,
        visibility="public",
        query="energy gate",
    ) == {"visibility": "public", "query": "energy gate"}
