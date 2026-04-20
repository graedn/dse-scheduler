import pytest
from unittest.mock import MagicMock
from scheduler import (
    match_end_ts, is_fully_staffed,
    build_signup_message, build_approved_signup_message,
)

# Saturday 2024-04-20 timestamps in ET
TS_8PM      = 1713657600
TS_10PM_ET  = 1713664800  # Saturday 2024-04-20 22:00 ET = 02:00 UTC on 2024-04-21


# --- match_end_ts ---

def test_match_end_ts_normal_8pm():
    """8pm + 2h = 10pm ET, no capping needed."""
    end = match_end_ts(TS_8PM)
    assert end == TS_8PM + 7200


def test_match_end_ts_caps_22h_match():
    """22:00 ET + 2h would be 00:00 ET next day — should be capped at 23:59:59 ET same day."""
    end = match_end_ts(TS_10PM_ET)
    assert end < TS_10PM_ET + 7200   # capped, not the full 2h
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    start_day = datetime.fromtimestamp(TS_10PM_ET, tz=ET).date()
    end_day   = datetime.fromtimestamp(end, tz=ET).date()
    assert start_day == end_day


# --- is_fully_staffed ---

def _sig(role: str, user_id: str = "u1") -> dict:
    return {"role": role, "user_id": user_id, "username": "u", "display_name": "U",
            "signed_up_at": 0}


def _valid_crew():
    """Minimal valid crew: producer/observer shared (u1), pbp (u2), colour (u3)."""
    return [
        _sig("producer", "u1"),
        _sig("observer", "u1"),
        _sig("pbp",      "u2"),
        _sig("colour",   "u3"),
    ]


def test_is_fully_staffed_minimal_valid():
    assert is_fully_staffed(_valid_crew()) is True


def test_is_fully_staffed_four_distinct_users():
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u2"),
        _sig("pbp", "u3"), _sig("colour", "u4"),
    ]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_missing_one_role():
    sigs = [_sig("pbp", "u2"), _sig("colour", "u3"), _sig("observer", "u1")]  # no producer
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_empty():
    assert is_fully_staffed([]) is False


def test_is_fully_staffed_analyst_alone_not_enough():
    assert is_fully_staffed([_sig("analyst")]) is False


def test_is_fully_staffed_with_extra_analyst():
    sigs = _valid_crew() + [_sig("analyst", "u4")]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_pbp_same_as_colour_fails():
    """PBP and Colour must be completely disjoint sets."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u2"),
        _sig("pbp", "u3"), _sig("colour", "u3"),  # same person
    ]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_only_two_unique_users_fails():
    """Fewer than 3 unique users is not enough."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u1"),
        _sig("pbp",      "u2"), _sig("colour",   "u2"),  # only 2 unique, but pbp==colour fails first
    ]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_producer_observer_shared_ok():
    """Producer and Observer may be the same person as long as total unique >= 3."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u1"),
        _sig("pbp",      "u2"), _sig("colour",   "u3"),
    ]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_producer_cannot_be_pbp():
    """Producer cannot also be Play-by-Play."""
    sigs = [
        _sig("producer", "u1"), _sig("pbp", "u1"),  # same person
        _sig("observer", "u2"), _sig("colour", "u3"),
    ]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_observer_cannot_be_colour():
    """Observer cannot also be Colour Caster."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u2"),
        _sig("pbp", "u3"), _sig("colour", "u2"),  # observer is also colour
    ]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_multiple_pbp_ok():
    """Multiple people can sign up as PBP — is_fully_staffed passes as long as one exists."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u1"),
        _sig("pbp",      "u2"), _sig("pbp",      "u3"), _sig("pbp", "u5"),
        _sig("colour",   "u4"),
    ]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_multiple_colour_ok():
    """Multiple people can sign up as Colour."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u1"),
        _sig("pbp",      "u2"),
        _sig("colour",   "u3"), _sig("colour",   "u4"), _sig("colour", "u5"),
    ]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_pbp_and_colour_overlap_fails():
    """A person cannot be in both PBP and Colour even with others in each role."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u1"),
        _sig("pbp",      "u2"), _sig("pbp",      "u5"),
        _sig("colour",   "u3"), _sig("colour",   "u2"),  # u2 in both
    ]
    assert is_fully_staffed(sigs) is False


# --- build_signup_message format ---

def _match_with_deadline(ts: int, deadline: int = None) -> dict:
    return {
        "id": 1, "match_time": ts, "signup_deadline": deadline,
        "team_home": "Team Alpha", "team_away": "Team Beta",
        "division": "Division 1", "week": "Week 3",
    }


def test_signup_message_has_separator():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "━━━" in msg


def test_signup_message_title():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "📋 [Division 1] Team Alpha vs Team Beta" in msg


def test_signup_message_includes_deadline_when_set():
    msg = build_signup_message(_match_with_deadline(TS_8PM, deadline=1713650000), [])
    assert "Sign Up Deadline" in msg
    assert "<t:1713650000:R>" in msg


def test_signup_message_omits_deadline_when_none():
    msg = build_signup_message(_match_with_deadline(TS_8PM, deadline=None), [])
    assert "Sign Up Deadline" not in msg


def test_signup_message_has_call_time():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    call_time = TS_8PM - 1800
    assert "Call Time" in msg
    assert f"<t:{call_time}:F>" in msg


def test_signup_message_has_match_start():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "Match Start" in msg
    assert f"<t:{TS_8PM}:t>" in msg
    assert f"<t:{TS_8PM}:R>" in msg


def test_signup_message_has_manager_note():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "Managers" in msg
    assert "Force Schedule" in msg


def test_signup_message_last_call_header():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [], last_call=True)
    assert "LAST CALL" in msg


def test_signup_message_no_last_call_by_default():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "LAST CALL" not in msg


def test_signup_message_shows_signed_up_talent():
    sigs = [{"role": "pbp", "user_id": "u1", "username": "u1#1234",
             "display_name": "ZephyrCasts", "signed_up_at": 0}]
    msg = build_signup_message(_match_with_deadline(TS_8PM), sigs)
    assert "ZephyrCasts" in msg


def test_signup_message_includes_talent_role_mention():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [],
                               talent_role_mention="<@&123456>")
    assert "<@&123456>" in msg


def test_signup_message_no_talent_role_mention_by_default():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "<@&" not in msg


# --- build_approved_signup_message ---

def test_approved_message_has_separator_and_header():
    match = {"id": 1, "division": "Division 1", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "producer": {"user_id": "u1", "display_name": "Alice", "username": "a"},
        "pbp_1":    {"user_id": "u2", "display_name": "Bob",   "username": "b"},
        "colour_1": {"user_id": "u3", "display_name": "Carol", "username": "c"},
        "observer": {"user_id": "u1", "display_name": "Alice", "username": "a"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "━━━" in msg
    assert "✅" in msg
    assert "APPROVED" in msg


def test_approved_message_shows_allocated_talent():
    match = {"id": 1, "division": "Division 1", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "pbp_1":    {"user_id": "u2", "display_name": "Zephyr", "username": "z"},
        "colour_1": {"user_id": "u3", "display_name": "Raven",  "username": "r"},
        "producer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
        "observer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "Zephyr" in msg
    assert "Raven" in msg
    assert "Prod" in msg


def test_approved_message_shows_allocated_pbp():
    """Approved message shows the single allocated PBP (pbp_1 key only)."""
    match = {"id": 1, "division": "Premier", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "producer": {"user_id": "u1", "display_name": "Prod",  "username": "p"},
        "observer": {"user_id": "u1", "display_name": "Prod",  "username": "p"},
        "pbp_1":    {"user_id": "u2", "display_name": "PBP1",  "username": "pb1"},
        "colour_1": {"user_id": "u4", "display_name": "Col1",  "username": "c1"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "PBP1" in msg
    assert "Col1" in msg


def test_approved_message_omits_unassigned_roles():
    match = {"id": 1, "division": "Division 1", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "pbp_1":    {"user_id": "u2", "display_name": "Zephyr", "username": "z"},
        "colour_1": {"user_id": "u3", "display_name": "Raven",  "username": "r"},
        "producer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
        "observer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "Host" not in msg
    assert "Analyst" not in msg
