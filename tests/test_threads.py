"""Tests for cogs/threads.py — ready-check content builder.

Focuses on the team-status row logic: guild role lookup picks the first
responder per team and renders their status; falls back to 'Awaiting'
when no responder qualifies.
"""
import json
from unittest.mock import MagicMock

import pytest

from database import Database
from cogs.threads import _build_ready_check_content


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _insert_match(db, ts=1714600000):
    return db.insert_match(
        division="Premier", week="Week 1",
        team_home="Lightning", team_away="Storm",
        match_time=ts, posted_at=ts - 3600,
    )


def _setup_thread(db, match_id, team1_role="111", team2_role="222"):
    db.insert_thread_message(
        match_id, thread_id="t1", channel_id="c1",
        team1_role_id=team1_role, team2_role_id=team2_role,
    )


def _setup_allocation(db, match_id, producer_uid="100", observer_uid="200"):
    ra = {
        "producer": {"user_id": producer_uid, "username": f"u{producer_uid}",
                     "display_name": f"Prod{producer_uid}"},
        "observer": {"user_id": observer_uid, "username": f"u{observer_uid}",
                     "display_name": f"Obs{observer_uid}"},
        "pbp_1":    {"user_id": "x", "username": "ux", "display_name": "X"},
        "colour_1": {"user_id": "y", "username": "uy", "display_name": "Y"},
    }
    db.create_allocation(match_id)
    db.set_allocation_assignments(match_id, ra, {}, None, None)
    return ra


def _bot(db_instance):
    bot = MagicMock()
    bot.db = db_instance
    return bot


def _member_with_roles(role_ids):
    """Build a mock member with the given role IDs."""
    member = MagicMock()
    member.display_name = "Player1"
    member.roles = [MagicMock(id=int(rid)) for rid in role_ids]
    return member


def _guild_with_members(members_by_uid):
    """Build a mock guild whose get_member returns from a uid -> member map."""
    guild = MagicMock()
    guild.get_member = MagicMock(
        side_effect=lambda uid: members_by_uid.get(str(uid))
    )
    return guild


# ---------------------------------------------------------------------------
# Awaiting state — no responses yet
# ---------------------------------------------------------------------------

def test_team_rows_show_awaiting_when_no_responses(db):
    match_id = _insert_match(db)
    _setup_thread(db, match_id)
    _setup_allocation(db, match_id)
    match = db.get_match(match_id)

    content = _build_ready_check_content(match, {}, _bot(db),
                                          guild=_guild_with_members({}))

    assert "**Lightning** — ⏳ Awaiting team confirmation" in content
    assert "**Storm** — ⏳ Awaiting team confirmation" in content


def test_team_rows_show_awaiting_when_responder_not_on_team(db):
    """Producer click without team role does not satisfy team rows."""
    match_id = _insert_match(db)
    _setup_thread(db, match_id)
    _setup_allocation(db, match_id, producer_uid="100")
    db.set_thread_ready_check_response(match_id, "100", True)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    # producer has no team role
    guild = _guild_with_members({"100": _member_with_roles(["999"])})

    content = _build_ready_check_content(match, responses, _bot(db),
                                          guild=guild)
    assert "**Lightning** — ⏳ Awaiting team confirmation" in content
    assert "**Storm** — ⏳ Awaiting team confirmation" in content


# ---------------------------------------------------------------------------
# Ready / Not Ready states
# ---------------------------------------------------------------------------

def test_team1_responder_renders_ready_row(db):
    match_id = _insert_match(db)
    _setup_thread(db, match_id, team1_role="111", team2_role="222")
    _setup_allocation(db, match_id)
    db.set_thread_ready_check_response(match_id, "5001", True)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    member = _member_with_roles(["111"])
    member.display_name = "Captain1"
    guild = _guild_with_members({"5001": member})

    content = _build_ready_check_content(match, responses, _bot(db),
                                          guild=guild)
    assert "**Lightning** — <@5001> (Captain1): ✅ Ready" in content
    assert "**Storm** — ⏳ Awaiting team confirmation" in content


def test_team2_responder_renders_not_ready_row(db):
    match_id = _insert_match(db)
    _setup_thread(db, match_id)
    _setup_allocation(db, match_id)
    db.set_thread_ready_check_response(match_id, "6001", False)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    member = _member_with_roles(["222"])
    member.display_name = "Captain2"
    guild = _guild_with_members({"6001": member})

    content = _build_ready_check_content(match, responses, _bot(db),
                                          guild=guild)
    assert "**Storm** — <@6001> (Captain2): ❌ Not Ready" in content
    assert "**Lightning** — ⏳ Awaiting team confirmation" in content


def test_first_responder_per_team_wins_when_multiple_on_same_team(db):
    """Two team1 members click; the first one (insertion order) is shown."""
    match_id = _insert_match(db)
    _setup_thread(db, match_id, team1_role="111")
    _setup_allocation(db, match_id)
    db.set_thread_ready_check_response(match_id, "7001", True)
    db.set_thread_ready_check_response(match_id, "7002", True)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    m1 = _member_with_roles(["111"]); m1.display_name = "First"
    m2 = _member_with_roles(["111"]); m2.display_name = "Second"
    guild = _guild_with_members({"7001": m1, "7002": m2})

    content = _build_ready_check_content(match, responses, _bot(db),
                                          guild=guild)
    assert "<@7001> (First)" in content
    assert "<@7002>" not in content


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_team_row_omitted_when_role_id_missing(db):
    """thread_messages with team1_role_id=NULL should skip the team1 row."""
    match_id = _insert_match(db)
    db.insert_thread_message(
        match_id, thread_id="t1", channel_id="c1",
        team1_role_id=None, team2_role_id="222",
    )
    _setup_allocation(db, match_id)
    match = db.get_match(match_id)

    content = _build_ready_check_content(match, {}, _bot(db),
                                          guild=_guild_with_members({}))
    assert "**Lightning**" not in content   # team row for team1 omitted
    assert "**Storm** — ⏳ Awaiting team confirmation" in content


def test_no_guild_falls_back_to_awaiting(db):
    """Without a guild, team rows can't resolve members → all show Awaiting."""
    match_id = _insert_match(db)
    _setup_thread(db, match_id)
    _setup_allocation(db, match_id)
    db.set_thread_ready_check_response(match_id, "8001", True)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    content = _build_ready_check_content(match, responses, _bot(db), guild=None)
    assert "**Lightning** — ⏳ Awaiting team confirmation" in content
    assert "**Storm** — ⏳ Awaiting team confirmation" in content


def test_producer_observer_rows_still_render(db):
    """Existing producer/observer rendering isn't broken by the new team logic."""
    match_id = _insert_match(db)
    _setup_thread(db, match_id)
    _setup_allocation(db, match_id, producer_uid="100", observer_uid="200")
    db.set_thread_ready_check_response(match_id, "100", True)
    responses = db.get_thread_ready_check_responses(match_id)
    match = db.get_match(match_id)

    content = _build_ready_check_content(match, responses, _bot(db),
                                          guild=_guild_with_members({}))
    assert "<@100> (Prod100): ✅ Ready" in content
    assert "<@200> (Obs200): ⏳ No Response" in content
