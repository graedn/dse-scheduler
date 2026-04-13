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
