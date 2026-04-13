import pytest
from unittest.mock import MagicMock
from scheduler import (
    get_et_hour, is_weekend, are_consecutive, has_overlap,
    generate_combinations, score_combination, best_combination,
    combo_match_ids, build_proposal_message,
)

# Saturday 2024-04-20 timestamps in ET
TS_6PM  = 1713650400
TS_7PM  = 1713654000
TS_8PM  = 1713657600
TS_9PM  = 1713661200
TS_10PM = 1713664800

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
    assert "Broadcast Schedule Proposal" in msg
    assert "Current schedule" in msg
    assert "Proposed schedule" in msg
    assert "Auto-approves in 12 hours" in msg
    assert "Team A" in msg
