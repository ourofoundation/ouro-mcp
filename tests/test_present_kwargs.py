"""present_kwargs drops blank optional filter values."""

from ouro_mcp.utils import is_absent_optional, optional_kwargs, present_kwargs


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


def test_present_kwargs_drops_string_null_sentinels():
    assert present_kwargs(
        asset_type="/null",
        user_id="null",
        visibility="None",
        file_type="undefined",
        time_window=" /null ",
        sort="relevant",
        org_id="00000000-0000-0000-0000-000000000000",
    ) == {
        "sort": "relevant",
        "org_id": "00000000-0000-0000-0000-000000000000",
    }


def test_is_absent_optional_keeps_real_values():
    assert is_absent_optional(0) is False
    assert is_absent_optional(False) is False
    assert is_absent_optional("all") is False
    assert is_absent_optional("/null") is True
