import pytest
import time
import json
from database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


# --- Config ---

def test_config_set_and_get(db):
    db.set_config("foo", "bar")
    assert db.get_config("foo") == "bar"


def test_config_get_missing_returns_none(db):
    assert db.get_config("nonexistent") is None


def test_config_delete(db):
    db.set_config("foo", "bar")
    db.delete_config("foo")
    assert db.get_config("foo") is None


# --- Matches ---

def test_insert_and_get_match(db):
    match_id = db.insert_match("Premier", "Week 1", "Team A", "Team B", 1700000000, 1699990000)
    match = db.get_match(match_id)
    assert match["team_home"] == "Team A"
    assert match["division"] == "Premier"
    assert match["broadcast_done"] == 0


def test_get_matches_for_date(db):
    # 2024-04-20 8pm ET = 2024-04-21 00:00 UTC = 1713657600
    ts = 1713657600  # Saturday 2024-04-20 8:00pm ET
    db.insert_match("Premier", "Week 1", "Team A", "Team B", ts, ts - 100)
    matches = db.get_matches_for_date("2024-04-20")
    assert len(matches) == 1
    assert matches[0]["team_home"] == "Team A"


def test_get_scheduled_matches_for_date_excludes_unscheduled(db):
    ts = 1713657600
    mid = db.insert_match("Premier", "Week 1", "Team A", "Team B", ts, ts - 100)
    # Not yet on calendar
    assert db.get_scheduled_matches_for_date("2024-04-20") == []
    # Now put it on the calendar
    db.update_match_teamup_id(mid, "tu-event-123")
    scheduled = db.get_scheduled_matches_for_date("2024-04-20")
    assert len(scheduled) == 1


def test_mark_broadcast_done(db):
    mid = db.insert_match("Premier", "Week 1", "Team A", "Team B", 1700000000, 1699990000)
    db.mark_broadcast_done(mid)
    match = db.get_match(mid)
    assert match["broadcast_done"] == 1


# --- Teams ---

def test_upsert_team_creates_new(db):
    db.upsert_team("Alpha Squad")
    team = db.get_team("Alpha Squad")
    assert team is not None
    assert team["scheduled_count"] == 0
    assert team["broadcast_count"] == 0


def test_upsert_team_is_idempotent(db):
    db.upsert_team("Alpha Squad")
    db.upsert_team("Alpha Squad")
    assert db.get_team("Alpha Squad")["scheduled_count"] == 0


def test_increment_scheduled_count(db):
    db.upsert_team("Alpha Squad")
    db.increment_scheduled_count("Alpha Squad")
    db.increment_scheduled_count("Alpha Squad")
    assert db.get_team("Alpha Squad")["scheduled_count"] == 2


def test_increment_broadcast_count(db):
    db.upsert_team("Alpha Squad")
    db.increment_broadcast_count("Alpha Squad")
    assert db.get_team("Alpha Squad")["broadcast_count"] == 1


def test_decrement_scheduled_count(db):
    db.upsert_team("Alpha Squad")
    db.increment_scheduled_count("Alpha Squad")
    db.increment_scheduled_count("Alpha Squad")
    db.decrement_scheduled_count("Alpha Squad")
    assert db.get_team("Alpha Squad")["scheduled_count"] == 1


def test_decrement_scheduled_count_floor_at_zero(db):
    db.upsert_team("Alpha Squad")
    db.decrement_scheduled_count("Alpha Squad")  # Already 0
    assert db.get_team("Alpha Squad")["scheduled_count"] == 0


def test_add_team_alias(db):
    db.upsert_team("Alpha Squad")
    db.add_team_alias("Alpha Squad", "alpha squad")
    team = db.get_team("Alpha Squad")
    aliases = json.loads(team["aliases"])
    assert "alpha squad" in aliases


def test_add_team_alias_no_duplicates(db):
    db.upsert_team("Alpha Squad")
    db.add_team_alias("Alpha Squad", "alpha squad")
    db.add_team_alias("Alpha Squad", "alpha squad")
    aliases = json.loads(db.get_team("Alpha Squad")["aliases"])
    assert aliases.count("alpha squad") == 1


# --- Pending Changes ---

def test_insert_and_get_pending_change(db):
    cid = db.insert_pending_change("Test proposal", ["tu-1"], [1, 2], "discord-msg-999")
    change = db.get_pending_change(cid)
    assert change["description"] == "Test proposal"
    assert change["approved"] is None
    assert change["discord_message_id"] == "discord-msg-999"


def test_get_pending_change_by_message(db):
    db.insert_pending_change("Proposal", [], [1], "msg-abc")
    change = db.get_pending_change_by_message("msg-abc")
    assert change is not None
    assert change["description"] == "Proposal"


def test_resolve_pending_change_approved(db):
    cid = db.insert_pending_change("Proposal", [], [1], "msg-abc")
    db.resolve_pending_change(cid, approved=True)
    change = db.get_pending_change(cid)
    assert change["approved"] == 1


def test_resolve_pending_change_rejected(db):
    cid = db.insert_pending_change("Proposal", [], [1], "msg-abc")
    db.resolve_pending_change(cid, approved=False)
    change = db.get_pending_change(cid)
    assert change["approved"] == 0


def test_get_expired_pending_changes(db):
    # Insert a change with auto_approve_at in the past by manipulating DB directly
    now = int(time.time())
    db.conn.execute(
        "INSERT INTO pending_changes (proposed_at, auto_approve_at, description, old_event_ids, new_match_ids) "
        "VALUES (?, ?, ?, ?, ?)",
        (now - 50000, now - 100, "Old proposal", "[]", "[1]")
    )
    db.conn.commit()
    expired = db.get_expired_pending_changes()
    assert len(expired) == 1


# --- Blocked Days ---

def test_insert_and_get_blocked_day(db):
    db.insert_blocked_day("2024-05-01", "Major event", "tu-block-1")
    blocked = db.get_blocked_day("2024-05-01")
    assert blocked["reason"] == "Major event"
    assert blocked["teamup_event_id"] == "tu-block-1"


def test_delete_blocked_day(db):
    db.insert_blocked_day("2024-05-01", None, None)
    db.delete_blocked_day("2024-05-01")
    assert db.get_blocked_day("2024-05-01") is None


def test_get_all_blocked_days_ordered(db):
    db.insert_blocked_day("2024-05-03", None, None)
    db.insert_blocked_day("2024-05-01", None, None)
    days = db.get_all_blocked_days()
    assert days[0]["date"] == "2024-05-01"
    assert days[1]["date"] == "2024-05-03"


# --- Reset ---

def test_reset_all_clears_everything(db):
    db.set_config("match_channel_id", "123")
    db.upsert_team("Alpha Squad")
    db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.reset_all()
    assert db.get_config("match_channel_id") is None
    assert db.get_team("Alpha Squad") is None
    assert db.get_all_teams() == []
