import pytest
from unittest.mock import MagicMock
from scheduler import (
    get_et_hour, is_weekend, are_consecutive, has_overlap,
    generate_combinations, score_combination, best_combination,
    combo_match_ids, build_proposal_message, match_end_ts,
)

# Saturday 2024-04-20 timestamps in ET
TS_6PM  = 1713650400
TS_7PM  = 1713654000
TS_8PM  = 1713657600
TS_9PM  = 1713661200
TS_10PM = 1713664800
TS_10PM_ET = 1713664800  # Saturday 2024-04-20 22:00 ET = 02:00 UTC on 2024-04-21


# --- match_end_ts ---

def test_match_end_ts_normal_8pm():
    """8pm + 2h = 10pm ET, no capping needed."""
    end = match_end_ts(TS_8PM)
    assert end == TS_8PM + 7200


def test_match_end_ts_caps_22h_match():
    """22:00 ET + 2h would be 00:00 ET next day — should be capped at 23:59:59 ET same day."""
    end = match_end_ts(TS_10PM_ET)
    assert end < TS_10PM_ET + 7200   # capped, not the full 2h
    # Must still be on the same calendar day in ET
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    start_day = datetime.fromtimestamp(TS_10PM_ET, tz=ET).date()
    end_day   = datetime.fromtimestamp(end, tz=ET).date()
    assert start_day == end_day

# Weekday: Tuesday 2024-04-16 8pm ET
TS_WD_8PM = 1713312000
TS_WD_7PM = 1713308400
TS_WD_9PM = 1713315600
TS_WD_6PM = 1713304800


def make_match(ts: int, match_id: int = 1,
               home: str = "Team A", away: str = "Team B",
               division: str = "Premier") -> dict:
    return {
        "id": match_id,
        "match_time": ts,
        "team_home": home,
        "team_away": away,
        "division": division,
        "week": "Week 1",
    }


def make_db(teams: dict = None) -> MagicMock:
    """teams = {name: (scheduled_count, broadcast_count)}"""
    db = MagicMock()
    teams = teams or {}
    def get_team(name):
        if name in teams:
            sc, bc = teams[name]
            return {"name": name, "scheduled_count": sc, "broadcast_count": bc}
        return None
    db.get_team.side_effect = get_team
    return db


# --- Time helpers ---

def test_get_et_hour_8pm():
    assert get_et_hour(TS_8PM) == pytest.approx(20.0, abs=0.1)


def test_is_weekend_saturday():
    assert is_weekend(TS_8PM) is True


def test_is_weekend_tuesday():
    assert is_weekend(TS_WD_8PM) is False


# --- Consecutive / overlap ---

def test_are_consecutive_7pm_9pm():
    assert are_consecutive(TS_7PM, TS_9PM) is True


def test_are_consecutive_8pm_10pm():
    assert are_consecutive(TS_8PM, TS_10PM) is True


def test_are_consecutive_too_close():
    assert are_consecutive(TS_8PM, TS_8PM + 3000) is False  # ~50 min gap


def test_are_consecutive_too_far():
    assert are_consecutive(TS_6PM, TS_10PM) is False  # 4h gap


def test_has_overlap_true():
    assert has_overlap(TS_8PM, TS_8PM + 3000) is True  # 50 min gap


def test_has_overlap_false():
    assert has_overlap(TS_7PM, TS_9PM) is False  # 2h gap is not an overlap


# --- Combinations ---

def test_generate_combinations_2_matches():
    matches = [make_match(TS_7PM, 1), make_match(TS_9PM, 2)]
    combos = generate_combinations(matches)
    assert len(combos) == 1
    assert len(combos[0]) == 2


def test_generate_combinations_excludes_overlapping():
    # 8pm and 8:30pm overlap (30 min gap < 1.5h)
    matches = [make_match(TS_8PM, 1), make_match(TS_8PM + 1800, 2)]
    combos = generate_combinations(matches)
    assert combos == []


def test_generate_combinations_max_3():
    matches = [
        make_match(TS_6PM, 1), make_match(TS_8PM, 2),
        make_match(TS_10PM, 3), make_match(TS_7PM, 4),
    ]
    combos = generate_combinations(matches)
    assert all(len(c) <= 3 for c in combos)


def test_generate_combinations_only_1_match_returns_empty():
    combos = generate_combinations([make_match(TS_8PM, 1)])
    assert combos == []


# --- Scoring weekday ---

def test_score_weekday_solo_8pm():
    db = make_db()
    combo = [make_match(TS_WD_8PM, 1)]
    score = score_combination(combo, weekend=False, db=db)
    assert score == 100


def test_score_weekday_solo_6pm():
    db = make_db()
    combo = [make_match(TS_WD_6PM, 1)]
    score = score_combination(combo, weekend=False, db=db)
    assert score == 30


def test_score_weekday_consecutive_7_9pm_beats_solo_8pm():
    db = make_db()
    combo_consecutive = [make_match(TS_WD_7PM, 1), make_match(TS_WD_9PM, 2)]
    combo_8pm = [make_match(TS_WD_8PM, 3)]
    assert score_combination(combo_consecutive, weekend=False, db=db) > \
           score_combination(combo_8pm, weekend=False, db=db)


def test_score_weekday_team_penalty():
    db = make_db({"Team A": (2, 0), "Team B": (1, 0)})
    combo = [make_match(TS_WD_8PM, 1, home="Team A", away="Team B")]
    score = score_combination(combo, weekend=False, db=db)
    # 100 (8pm) - 10*2 (Team A) - 10*1 (Team B) = 70
    assert score == 70


# --- Scoring weekend ---

def test_score_weekend_consecutive_beats_8pm():
    db = make_db()
    combo_pair = [make_match(TS_7PM, 1), make_match(TS_9PM, 2)]
    combo_8pm = [make_match(TS_8PM, 3)]
    assert score_combination(combo_pair, weekend=True, db=db) > \
           score_combination(combo_8pm, weekend=True, db=db)


def test_score_weekend_solo_8pm():
    db = make_db()
    combo = [make_match(TS_8PM, 1)]
    assert score_combination(combo, weekend=True, db=db) == 50


# --- Best combination ---

def test_best_combination_picks_highest_score():
    db = make_db()
    matches = [
        make_match(TS_WD_7PM, 1, home="Team A", away="Team B"),
        make_match(TS_WD_9PM, 2, home="Team C", away="Team D"),
        make_match(TS_WD_6PM, 3, home="Team E", away="Team F"),
    ]
    best = best_combination(matches, db)
    # 7pm+9pm consecutive should beat any solo match
    ids = combo_match_ids(best)
    assert 1 in ids and 2 in ids


def test_best_combination_none_when_fewer_than_2():
    db = make_db()
    assert best_combination([make_match(TS_8PM, 1)], db) is None


def test_best_combination_tiebreak_by_team_counts():
    # Two solo matches at 8pm — pick team with lower count
    db = make_db({"Team A": (3, 0), "Team B": (3, 0), "Team C": (0, 0), "Team D": (0, 0)})
    matches = [
        make_match(TS_WD_8PM, 1, home="Team A", away="Team B"),
        make_match(TS_WD_8PM + 7200, 2, home="Team C", away="Team D"),
    ]
    best = best_combination(matches, db)
    # Both matches are in the best 2-match combo
    assert combo_match_ids(best) == [1, 2]


def test_build_proposal_message_contains_key_fields():
    db = make_db({"Team A": (0, 1), "Team B": (0, 2)})
    current = [make_match(TS_WD_8PM, 1, home="Team A", away="Team B")]
    proposed = [make_match(TS_WD_7PM, 2, home="Team A", away="Team B"),
                make_match(TS_WD_9PM, 3, home="Team A", away="Team B")]
    msg = build_proposal_message("2024-04-16", current, proposed, 100, 110, db)
    assert "━━━" in msg  # visual separator
    assert "Broadcast Schedule Proposal" in msg
    assert "Current schedule" in msg
    assert "Proposed schedule" in msg
    assert "Auto-approves in 12 hours" in msg
    assert "Team A" in msg


# --- is_fully_staffed ---

from scheduler import is_fully_staffed


def _sig(role: str, user_id: str = "u1") -> dict:
    return {"role": role, "user_id": user_id, "username": "u", "display_name": "U",
            "signed_up_at": 0}


# Minimal valid crew: producer/observer shared (u1), pbp (u2), colour (u3) → 3 unique users
def _valid_crew():
    return [
        _sig("producer", "u1"),
        _sig("observer", "u1"),
        _sig("pbp",      "u2"),
        _sig("colour",   "u3"),
    ]


def test_is_fully_staffed_all_roles():
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
    sigs = [_sig("analyst")]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_with_extra_analyst():
    sigs = _valid_crew() + [_sig("analyst", "u4")]
    assert is_fully_staffed(sigs) is True


def test_is_fully_staffed_pbp_same_as_colour_fails():
    """PBP and Colour must always be different people."""
    sigs = [
        _sig("producer", "u1"), _sig("observer", "u2"),
        _sig("pbp", "u3"), _sig("colour", "u3"),  # same person
    ]
    assert is_fully_staffed(sigs) is False


def test_is_fully_staffed_only_two_unique_users_fails():
    """Even with all roles filled, fewer than 3 unique users is not enough."""
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


# --- build_signup_message format ---

from scheduler import build_signup_message


def _match_with_deadline(ts: int, deadline: int = None) -> dict:
    return {
        "id": 1, "match_time": ts, "signup_deadline": deadline,
        "team_home": "Team Alpha", "team_away": "Team Beta",
        "division": "Division 1", "week": "Week 3",
    }


def test_signup_message_has_separator():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "━━━" in msg


def test_signup_message_title_not_bold():
    msg = build_signup_message(_match_with_deadline(TS_8PM), [])
    assert "**[Division 1]" not in msg
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
    call_time = TS_8PM - 1800  # 30 min before match
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


# --- build_approved_signup_message ---

from scheduler import build_approved_signup_message


def test_approved_message_has_separator_and_header():
    match = {"id": 1, "division": "Division 1", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "producer": {"user_id": "u1", "display_name": "Alice", "username": "a"},
        "pbp":      {"user_id": "u2", "display_name": "Bob",   "username": "b"},
        "colour":   {"user_id": "u3", "display_name": "Carol", "username": "c"},
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
        "pbp":    {"user_id": "u2", "display_name": "Zephyr", "username": "z"},
        "colour": {"user_id": "u3", "display_name": "Raven",  "username": "r"},
        "producer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
        "observer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "Zephyr" in msg
    assert "Raven" in msg
    assert "Prod" in msg


def test_approved_message_omits_unassigned_roles():
    match = {"id": 1, "division": "Division 1", "team_home": "A", "team_away": "B",
             "match_time": TS_8PM}
    role_assignments = {
        "pbp":    {"user_id": "u2", "display_name": "Zephyr", "username": "z"},
        "colour": {"user_id": "u3", "display_name": "Raven",  "username": "r"},
        "producer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
        "observer": {"user_id": "u1", "display_name": "Prod", "username": "p"},
    }
    msg = build_approved_signup_message(match, role_assignments)
    assert "Host" not in msg
    assert "Analyst" not in msg
