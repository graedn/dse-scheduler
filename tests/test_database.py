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


def test_get_matches_by_teamup_event_id(db):
    mid = db.insert_match("Premier", "Week 1", "Team A", "Team B", 1700000000, 1699990000)
    db.update_match_teamup_id(mid, "tu-event-xyz")
    results = db.get_matches_by_teamup_event_id("tu-event-xyz")
    assert len(results) == 1
    assert results[0]["id"] == mid

def test_get_matches_by_teamup_event_id_no_match(db):
    results = db.get_matches_by_teamup_event_id("nonexistent")
    assert results == []


# --- Reset ---

def test_reset_all_clears_everything(db):
    db.set_config("match_channel_id", "123")
    db.upsert_team("Alpha Squad")
    db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.reset_all()
    assert db.get_config("match_channel_id") is None
    assert db.get_team("Alpha Squad") is None
    assert db.get_all_teams() == []


# --- Broadcast messages ---

def test_insert_and_get_broadcast_message(db):
    mid = db.insert_match("Premier", "Week 1", "Team A", "Team B", 1700000000, 1699990000)
    db.insert_broadcast_message(mid, "msg-111", "ch-222")
    bcast = db.get_broadcast_message(mid)
    assert bcast is not None
    assert bcast["discord_message_id"] == "msg-111"
    assert bcast["channel_id"] == "ch-222"


def test_get_broadcast_message_none_when_missing(db):
    assert db.get_broadcast_message(9999) is None


def test_insert_broadcast_message_replace(db):
    mid = db.insert_match("Premier", "Week 1", "Team A", "Team B", 1700000000, 1699990000)
    db.insert_broadcast_message(mid, "msg-old", "ch-1")
    db.insert_broadcast_message(mid, "msg-new", "ch-1")
    assert db.get_broadcast_message(mid)["discord_message_id"] == "msg-new"


# --- Signups ---

def test_upsert_signup_new_returns_true(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    is_new = db.upsert_signup(mid, "msg-1", "pbp", "user-1", "user#1", "User One")
    assert is_new is True


def test_upsert_signup_duplicate_returns_false(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.upsert_signup(mid, "msg-1", "pbp", "user-1", "user#1", "User One")
    is_new = db.upsert_signup(mid, "msg-1", "pbp", "user-1", "user#1", "User One")
    assert is_new is False


def test_get_signups_for_match(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.upsert_signup(mid, "msg-1", "pbp", "user-1", "u1", "User One")
    db.upsert_signup(mid, "msg-1", "colour", "user-2", "u2", "User Two")
    sigs = db.get_signups_for_match(mid)
    assert len(sigs) == 2
    roles = {s["role"] for s in sigs}
    assert roles == {"pbp", "colour"}


def test_remove_signup(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.upsert_signup(mid, "msg-1", "pbp", "user-1", "u1", "User One")
    removed = db.remove_signup(mid, "pbp", "user-1")
    assert removed is True
    assert db.get_signups_for_match(mid) == []


def test_remove_signup_nonexistent_returns_false(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    assert db.remove_signup(mid, "pbp", "ghost-user") is False


# --- Managers ---

def test_add_and_is_manager(db):
    db.add_manager("uid-1", "user#1234", "Display Name", added_by="admin-uid")
    assert db.is_manager("uid-1") is True


def test_is_manager_false_for_unknown(db):
    assert db.is_manager("uid-nobody") is False


def test_remove_manager(db):
    db.add_manager("uid-1", "user#1", "Name", added_by="admin")
    removed = db.remove_manager("uid-1")
    assert removed is True
    assert db.is_manager("uid-1") is False


def test_remove_manager_not_found_returns_false(db):
    assert db.remove_manager("uid-nobody") is False


def test_get_all_managers_ordered_by_added_at(db):
    db.add_manager("uid-1", "u1", "Alice", added_by="admin")
    db.add_manager("uid-2", "u2", "Bob", added_by="admin")
    managers = db.get_all_managers()
    assert len(managers) == 2
    assert managers[0]["user_id"] == "uid-1"


# --- Signup deadline / past deadline ---

def test_set_signup_deadline(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.set_signup_deadline(mid, 1700000100)
    match = db.get_match(mid)
    assert match["signup_deadline"] == 1700000100


def test_get_matches_past_deadline(db):
    past = int(time.time()) - 3600
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.update_match_teamup_id(mid, "tu-event-1")
    db.set_signup_deadline(mid, past)
    results = db.get_matches_past_deadline()
    assert any(r["id"] == mid for r in results)


def test_get_matches_past_deadline_excludes_future(db):
    future = int(time.time()) + 3600
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.update_match_teamup_id(mid, "tu-event-1")
    db.set_signup_deadline(mid, future)
    assert db.get_matches_past_deadline() == []


def test_get_matches_past_deadline_excludes_already_allocated(db):
    past = int(time.time()) - 3600
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.update_match_teamup_id(mid, "tu-event-1")
    db.set_signup_deadline(mid, past)
    db.create_allocation(mid)
    assert db.get_matches_past_deadline() == []


# --- Talent broadcast counts ---

def test_talent_count_new_user_returns_zero(db):
    assert db.get_talent_count("uid-new") == 0


def test_increment_talent_broadcast(db):
    db.increment_talent_broadcast("uid-1", "user#1", "Alice")
    assert db.get_talent_count("uid-1") == 1
    db.increment_talent_broadcast("uid-1", "user#1", "Alice")
    assert db.get_talent_count("uid-1") == 2


def test_increment_talent_broadcast_upserts(db):
    db.increment_talent_broadcast("uid-1", "user#1", "Alice")
    db.increment_talent_broadcast("uid-1", "user#1_new", "Alice Updated")
    assert db.get_talent_count("uid-1") == 2


# --- Talent allocations ---

def test_create_and_get_allocation(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    alloc = db.get_allocation(mid)
    assert alloc is not None
    assert alloc["status"] == "pending"
    assert alloc["match_id"] == mid


def test_create_allocation_is_idempotent(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.create_allocation(mid)  # Should not raise
    assert db.get_allocation(mid)["status"] == "pending"


def test_set_allocation_assignments_sets_awaiting_confirm(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_assignments(
        mid,
        role_assignments={"producer": {"user_id": "uid-1", "username": "u1", "display_name": "Alice"}},
        confirmations={"uid-1": None},
        confirmation_message_id="conf-msg-1",
        confirmation_channel_id="ch-1",
    )
    alloc = db.get_allocation(mid)
    assert alloc["status"] == "awaiting_confirm"
    assert alloc["confirmation_message_id"] == "conf-msg-1"


def test_get_allocation_by_confirmation_message(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_assignments(mid, {}, {"uid-1": None}, "msg-999", "ch-1")
    alloc = db.get_allocation_by_confirmation_message("msg-999")
    assert alloc is not None
    assert alloc["match_id"] == mid


def test_set_and_get_confirmations(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_assignments(mid, {}, {"uid-1": None, "uid-2": None}, None, None)
    db.set_confirmation(mid, "uid-1", True)
    confs = db.get_confirmations(mid)
    assert confs["uid-1"] is True
    assert confs["uid-2"] is None


def test_all_confirmed_detection(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_assignments(mid, {}, {"uid-1": None, "uid-2": None}, None, None)
    db.set_confirmation(mid, "uid-1", True)
    db.set_confirmation(mid, "uid-2", True)
    assert all(v is True for v in db.get_confirmations(mid).values())


def test_reset_allocation_clears_assignments(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_assignments(mid, {"producer": {}}, {"uid-1": None}, "msg-1", "ch-1")
    db.reset_allocation(mid)
    alloc = db.get_allocation(mid)
    assert alloc["status"] == "pending"
    assert alloc["role_assignments"] is None
    assert alloc["confirmation_message_id"] is None


def test_set_allocation_status(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.create_allocation(mid)
    db.set_allocation_status(mid, "accepted")
    assert db.get_allocation(mid)["status"] == "accepted"


# --- Mark broadcast accepted ---

def test_mark_broadcast_accepted(db):
    mid = db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.mark_broadcast_accepted(mid)
    assert db.get_match(mid)["broadcast_accepted"] == 1


# --- reset_season ---

def test_reset_season_clears_matches_and_teams(db):
    db.upsert_team("Alpha Squad")
    db.insert_match("Premier", "Week 1", "A", "B", 1700000000, 1699990000)
    db.reset_season()
    assert db.get_all_teams() == []


def test_reset_season_preserves_config(db):
    db.set_config("broadcast_channel_id", "ch-123")
    db.reset_season()
    assert db.get_config("broadcast_channel_id") == "ch-123"


def test_reset_season_preserves_managers(db):
    db.add_manager("uid-1", "user#1", "Alice", added_by="admin")
    db.reset_season()
    assert db.is_manager("uid-1") is True


def test_reset_season_resets_autoincrement(db):
    mid = db.insert_match("Div1", "W1", "A", "B", 1700000000, 1699990000)
    assert mid == 1
    db.reset_season()
    mid2 = db.insert_match("Div1", "W1", "C", "D", 1700000001, 1699990001)
    assert mid2 == 1, "AUTOINCREMENT should reset to 1 after reset_season"


# --- user_settings ---

def test_get_user_timezone_returns_default(db):
    assert db.get_user_timezone("unknown-user") == "America/New_York"


def test_set_and_get_user_timezone(db):
    db.set_user_timezone("uid-1", "America/Los_Angeles")
    assert db.get_user_timezone("uid-1") == "America/Los_Angeles"


def test_set_user_timezone_upserts(db):
    db.set_user_timezone("uid-1", "UTC")
    db.set_user_timezone("uid-1", "Europe/London")
    assert db.get_user_timezone("uid-1") == "Europe/London"


# --- get_matches_past_calltime_last_call ---

def test_get_matches_past_calltime_last_call_returns_match(db):
    import time
    past_ts = int(time.time()) - 7200  # 2 hours ago — match already started
    mid = db.insert_match("Div1", "W1", "A", "B", past_ts, past_ts - 3600)
    db.update_match_teamup_id(mid, "evt-1")
    db.set_signup_deadline(mid, past_ts - 7200)
    db.create_allocation(mid)
    db.set_allocation_status(mid, "last_call")
    results = db.get_matches_past_calltime_last_call()
    assert any(r["id"] == mid for r in results)


def test_get_matches_past_calltime_last_call_ignores_accepted(db):
    import time
    past_ts = int(time.time()) - 7200
    mid = db.insert_match("Div1", "W1", "C", "D", past_ts, past_ts - 3600)
    db.update_match_teamup_id(mid, "evt-2")
    db.set_signup_deadline(mid, past_ts - 7200)
    db.create_allocation(mid)
    db.set_allocation_status(mid, "last_call")
    db.mark_broadcast_accepted(mid)
    results = db.get_matches_past_calltime_last_call()
    assert not any(r["id"] == mid for r in results)


def test_clear_broadcast_accepted(db):
    mid = db.insert_match("Div1", "W1", "A", "B", 1700000000, 1699990000)
    db.mark_broadcast_accepted(mid)
    assert db.get_match(mid)["broadcast_accepted"] == 1
    db.clear_broadcast_accepted(mid)
    assert db.get_match(mid)["broadcast_accepted"] == 0


def test_get_accepted_broadcast_matches(db):
    mid = db.insert_match("Div1", "W1", "A", "B", 1700000000, 1699990000)
    db.mark_broadcast_accepted(mid)
    db.insert_broadcast_message(mid, "msg-1", "ch-1")
    results = db.get_accepted_broadcast_matches()
    assert any(r["id"] == mid for r in results)


def test_get_accepted_broadcast_matches_excludes_unaccepted(db):
    mid = db.insert_match("Div1", "W1", "C", "D", 1700000001, 1699990001)
    db.insert_broadcast_message(mid, "msg-2", "ch-1")
    # broadcast_accepted = 0 (default)
    results = db.get_accepted_broadcast_matches()
    assert not any(r["id"] == mid for r in results)


def test_get_matches_past_calltime_last_call_ignores_future(db):
    import time
    future_ts = int(time.time()) + 7200  # match in 2 hours — call time not yet passed
    mid = db.insert_match("Div1", "W1", "E", "F", future_ts, future_ts - 3600)
    db.update_match_teamup_id(mid, "evt-3")
    db.set_signup_deadline(mid, future_ts - 7200)
    db.create_allocation(mid)
    db.set_allocation_status(mid, "last_call")
    results = db.get_matches_past_calltime_last_call()
    assert not any(r["id"] == mid for r in results)
