import pytest
from unittest.mock import MagicMock
from parser import (
    has_required_structure, parse_division, parse_week,
    parse_timestamp, resolve_team_name, parse_teams, parse_post, ParseError
)


VALID_POST = """Division: Premier
Week: 3
Team Alpha vs Team Beta
Time: <t:1713387600:F>"""


# --- Structure check ---

def test_has_required_structure_valid():
    assert has_required_structure(VALID_POST) is True


def test_has_required_structure_missing_division():
    post = "Week: 3\nTeam A vs Team B\nTime: <t:1713387600:F>"
    assert has_required_structure(post) is False


def test_has_required_structure_missing_week():
    post = "Division: Premier\nTeam A vs Team B\nTime: <t:1713387600:F>"
    assert has_required_structure(post) is False


def test_has_required_structure_missing_vs():
    post = "Division: Premier\nWeek: 3\nTime: <t:1713387600:F>"
    assert has_required_structure(post) is False


def test_has_required_structure_missing_time():
    post = "Division: Premier\nWeek: 3\nTeam A vs Team B"
    assert has_required_structure(post) is False


def test_has_required_structure_accepts_round():
    post = "Division: Premier\nRound: 2\nTeam A vs Team B\nTime: <t:1713387600:F>"
    assert has_required_structure(post) is True


# --- Division parsing ---

def test_parse_division_exact():
    assert parse_division("Division: Premier\nWeek: 1") == "Premier"


def test_parse_division_case_insensitive():
    assert parse_division("division: premier\nWeek: 1") == "Premier"


def test_parse_division_fuzzy_typo():
    # "Premire" is close enough to "Premier"
    assert parse_division("Division: Premire\nWeek: 1") == "Premier"


def test_parse_division_fuzzy_div1():
    assert parse_division("Division: Division1\nWeek: 1") == "Division 1"


def test_parse_division_too_far_raises():
    with pytest.raises(ParseError):
        parse_division("Division: Zzzzzz\nWeek: 1")


# --- Week / Round parsing ---

def test_parse_week_standard():
    assert parse_week("Division: Premier\nWeek: 3\nTeam A vs B\nTime: <t:123:F>") == "Week 3"


def test_parse_week_no_space():
    assert parse_week("Division: Premier\nWeek:5\nTeam A vs B\nTime: <t:123:F>") == "Week 5"


def test_parse_week_round_label():
    assert parse_week("Division: Premier\nRound: 2\nTeam A vs B\nTime: <t:123:F>") == "Round 2"


def test_parse_week_non_numeric_raises():
    with pytest.raises(ParseError):
        parse_week("Division: Premier\nWeek: abc\nTeam A vs B\nTime: <t:123:F>")


# --- Timestamp parsing ---

def test_parse_timestamp_standard():
    assert parse_timestamp("Time: <t:1713387600:F>") == 1713387600


def test_parse_timestamp_no_format_letter():
    assert parse_timestamp("Time: <t:1713387600>") == 1713387600


def test_parse_timestamp_malformed_raises():
    with pytest.raises(ParseError):
        parse_timestamp("Time: something else")


# --- Team name resolution ---

def make_db_with_teams(teams: dict) -> MagicMock:
    """teams = {canonical_name: [alias, ...]}"""
    import json
    db = MagicMock()
    team_list = [
        {"name": name, "aliases": json.dumps(aliases), "scheduled_count": 0, "broadcast_count": 0}
        for name, aliases in teams.items()
    ]
    db.get_all_teams.return_value = team_list
    db.get_team.side_effect = lambda n: next(
        (t for t in team_list if t["name"] == n), None
    )
    return db


def test_resolve_team_new_team_creates_titlecase():
    db = make_db_with_teams({})
    result = resolve_team_name("alpha squad", db)
    assert result == "Alpha Squad"
    db.upsert_team.assert_called_once_with("Alpha Squad")


def test_resolve_team_exact_match():
    db = make_db_with_teams({"Alpha Squad": []})
    result = resolve_team_name("Alpha Squad", db)
    assert result == "Alpha Squad"


def test_resolve_team_fuzzy_match():
    db = make_db_with_teams({"Alpha Squad": []})
    result = resolve_team_name("Aplha Squad", db)  # typo
    assert result == "Alpha Squad"
    db.add_team_alias.assert_called_once_with("Alpha Squad", "Aplha Squad")


def test_resolve_team_alias_match():
    db = make_db_with_teams({"Alpha Squad": ["alpha squad"]})
    result = resolve_team_name("alpha squad", db)
    assert result == "Alpha Squad"


# --- Full parse ---

def test_parse_post_valid():
    db = make_db_with_teams({})
    result = parse_post(VALID_POST, db)
    assert result.division == "Premier"
    assert result.week == "Week 3"
    assert result.match_time == 1713387600


def test_parse_post_invalid_division_raises():
    db = make_db_with_teams({})
    post = "Division: Zzzzzzz\nWeek: 3\nTeam A vs Team B\nTime: <t:1713387600:F>"
    with pytest.raises(ParseError):
        parse_post(post, db)


def test_parse_teams_same_team_raises():
    db = make_db_with_teams({})
    # Both sides resolve to the same team
    post = "Division: Premier\nWeek: 1\nAlpha Squad vs Alpha Squad\nTime: <t:123:F>"
    with pytest.raises(ParseError):
        parse_post(post, db)


# --- has_partial_structure ---

from parser import has_partial_structure

def test_has_partial_structure_division_and_week_no_timestamp():
    post = "Division: Premier\nWeek: 3\nTeam A vs Team B"
    assert has_partial_structure(post) is True


def test_has_partial_structure_false_when_has_required():
    # A fully valid post is NOT partial
    assert has_partial_structure(VALID_POST) is False


def test_has_partial_structure_false_when_only_division():
    post = "Division: Premier\nSome other text"
    assert has_partial_structure(post) is False


def test_has_partial_structure_false_when_only_week():
    post = "Week: 3\nTeam A vs Team B"
    assert has_partial_structure(post) is False


# --- _normalize_division ---

from parser import _normalize_division

def test_normalize_division_bare_number():
    assert _normalize_division("1") == "Division 1"


def test_normalize_division_div_prefix():
    assert _normalize_division("div 2") == "Division 2"


def test_normalize_division_div_dot():
    assert _normalize_division("div.3") == "Division 3"


def test_normalize_division_passthrough():
    assert _normalize_division("Premier") == "Premier"


# --- versus keyword ---

def test_has_required_structure_accepts_versus():
    post = "Division: Premier\nWeek: 3\nTeam A versus Team B\nTime: <t:1713387600:F>"
    assert has_required_structure(post) is True


def test_parse_teams_accepts_versus():
    db = make_db_with_teams({})
    post = "Division: Premier\nWeek: 1\nAlpha Squad versus Beta Squad\nTime: <t:123:F>"
    result = parse_post(post, db)
    assert "Alpha" in result.team_home or "Alpha" in result.team_away


# --- Timestamp format variants ---

def test_parse_timestamp_with_lowercase_r():
    assert parse_timestamp("Time: <t:1713387600:r>") == 1713387600


def test_parse_timestamp_with_uppercase_d():
    assert parse_timestamp("Time: <t:1713387600:D>") == 1713387600


def test_parse_timestamp_no_format_suffix():
    assert parse_timestamp("Time: <t:1713387600>") == 1713387600


# --- parse_division shorthand ---

def test_parse_division_bare_number_shorthand():
    assert parse_division("Division: 1") == "Division 1"


def test_parse_division_div_prefix_shorthand():
    assert parse_division("Division: div 2") == "Division 2"
