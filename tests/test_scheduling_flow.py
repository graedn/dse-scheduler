"""Tests for the new proposal-based scheduling flow.

The old auto-scheduling (schedule_for_date, propose_change, apply_pending_change)
has been removed.  Scheduling is now done explicitly by managers via the weekly
proposal messages.  These tests cover the proposal_messages DB layer and the
match-logging flow in EventsCog.
"""
import pytest
import time
from database import Database

# Saturday 2099-04-20 timestamps (far future so they won't expire)
TS_8PM  = 4071974400   # approximate — exact value doesn't matter for these tests
DATE    = "2099-04-20"
WEEK_START = "2099-04-18"  # Monday of that week


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _insert_match(db, ts=TS_8PM, home="Team A", away="Team B", division="Premier"):
    return db.insert_match(
        division=division,
        week="Week 1",
        team_home=home,
        team_away=away,
        match_time=ts,
        posted_at=ts - 3600,
    )


# ---------------------------------------------------------------------------
# proposal_messages DB layer
# ---------------------------------------------------------------------------

def test_create_proposal_message(db):
    now = int(time.time())
    row_id = db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    assert row_id is not None

    row = db.get_proposal_message(DATE)
    assert row is not None
    assert row["date"] == DATE
    assert row["week_start"] == WEEK_START
    assert row["day_ts"] == TS_8PM
    assert row["status"] == "open"
    assert row["slot1_match_id"] is None
    assert row["slot2_match_id"] is None


def test_create_proposal_message_idempotent(db):
    """Creating a proposal message for the same date twice is a no-op (INSERT OR IGNORE)."""
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    db.create_proposal_message(DATE, TS_8PM + 999, WEEK_START)  # different day_ts, same date
    row = db.get_proposal_message(DATE)
    assert row["day_ts"] == TS_8PM  # first value kept


def test_get_proposal_message_missing_returns_none(db):
    assert db.get_proposal_message("2099-01-01") is None


def test_update_proposal_slots(db):
    mid1 = _insert_match(db, TS_8PM)
    mid2 = _insert_match(db, TS_8PM + 7200, "C", "D")
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)

    db.update_proposal_slots(DATE, mid1, mid2)
    row = db.get_proposal_message(DATE)
    assert row["slot1_match_id"] == mid1
    assert row["slot2_match_id"] == mid2


def test_update_proposal_slots_can_clear(db):
    mid1 = _insert_match(db)
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    db.update_proposal_slots(DATE, mid1, None)

    db.update_proposal_slots(DATE, None, None)
    row = db.get_proposal_message(DATE)
    assert row["slot1_match_id"] is None
    assert row["slot2_match_id"] is None


def test_set_proposal_status(db):
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)

    db.set_proposal_status(DATE, "blocked")
    assert db.get_proposal_message(DATE)["status"] == "blocked"

    db.set_proposal_status(DATE, "passed")
    assert db.get_proposal_message(DATE)["status"] == "passed"


def test_set_proposal_discord_message(db):
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    db.set_proposal_discord_message(DATE, "msg_111", "ch_222")

    row = db.get_proposal_message(DATE)
    assert row["discord_message_id"] == "msg_111"
    assert row["channel_id"] == "ch_222"


def test_get_proposal_messages_for_week(db):
    db.create_proposal_message("2099-04-21", TS_8PM,         WEEK_START)
    db.create_proposal_message("2099-04-22", TS_8PM + 86400, WEEK_START)
    db.create_proposal_message("2099-04-23", TS_8PM + 86400 * 2, "2099-04-25")  # different week

    rows = db.get_proposal_messages_for_week(WEEK_START)
    assert len(rows) == 2
    assert all(r["week_start"] == WEEK_START for r in rows)


def test_get_open_proposal_messages(db):
    db.create_proposal_message("2099-04-21", TS_8PM, WEEK_START)
    db.create_proposal_message("2099-04-22", TS_8PM, WEEK_START)
    db.set_proposal_status("2099-04-22", "blocked")

    open_rows = db.get_open_proposal_messages()
    assert len(open_rows) == 1
    assert open_rows[0]["date"] == "2099-04-21"


def test_get_blocked_proposal_messages(db):
    """get_blocked_proposal_messages returns only 'blocked' proposals."""
    db.create_proposal_message("2099-04-21", TS_8PM, WEEK_START)
    db.create_proposal_message("2099-04-22", TS_8PM, WEEK_START)
    db.create_proposal_message("2099-04-23", TS_8PM, WEEK_START)
    db.set_proposal_status("2099-04-22", "blocked")
    db.set_proposal_status("2099-04-23", "passed")

    blocked = db.get_blocked_proposal_messages()
    assert len(blocked) == 1
    assert blocked[0]["date"] == "2099-04-22"


# ---------------------------------------------------------------------------
# get_unscheduled_matches_for_date
# ---------------------------------------------------------------------------

def test_get_unscheduled_matches_for_date_returns_unscheduled(db):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    # Use a real ET midnight for 2099-04-20 so _et_day_range works
    day_start = int(datetime(2099, 4, 20, 0, 0, 0, tzinfo=ET).timestamp())
    day_8pm   = int(datetime(2099, 4, 20, 20, 0, 0, tzinfo=ET).timestamp())
    day_10pm  = int(datetime(2099, 4, 20, 22, 0, 0, tzinfo=ET).timestamp())

    mid1 = db.insert_match("Premier", "1", "A", "B", day_8pm, day_8pm - 3600)
    mid2 = db.insert_match("Premier", "1", "C", "D", day_10pm, day_10pm - 3600)
    db.update_match_teamup_id(mid1, "evt_abc")   # scheduled

    result = db.get_unscheduled_matches_for_date("2099-04-20")
    ids = [r["id"] for r in result]
    assert mid1 not in ids   # already scheduled
    assert mid2 in ids        # not yet scheduled


# ---------------------------------------------------------------------------
# reset_season clears proposal_messages
# ---------------------------------------------------------------------------

def test_reset_season_clears_proposal_messages(db):
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    assert db.get_proposal_message(DATE) is not None

    db.reset_season()
    assert db.get_proposal_message(DATE) is None


def test_reset_all_clears_proposal_messages(db):
    db.create_proposal_message(DATE, TS_8PM, WEEK_START)
    db.reset_all()
    assert db.get_proposal_message(DATE) is None
