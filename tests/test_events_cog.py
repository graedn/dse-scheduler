"""Tests for EventsCog._scan_match_history() and on_message().

Uses a real in-memory Database. The new flow does NOT auto-schedule;
it just logs matches and dispatches 'match_logged' events.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
from database import Database
from cogs.events import EventsCog, _week_bounds

# A future timestamp (2099-01-01 20:00 UTC — well past 'now' in any test run)
FUTURE_TS   = int(datetime(2099, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp())
FUTURE_DATE = "2099-01-01"
PAST_TS     = int(datetime(2000, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp())

ET_TZ = ZoneInfo("America/New_York")

# Week of 2099-06-08 (Monday) through 2099-06-14 (Sunday)
WEEK_MON_TS = int(datetime(2099, 6, 8, 19, 0, tzinfo=ET_TZ).timestamp())
WEEK_WED_TS = int(datetime(2099, 6, 10, 19, 0, tzinfo=ET_TZ).timestamp())
NEXT_MON_TS = int(datetime(2099, 6, 15, 19, 0, tzinfo=ET_TZ).timestamp())


def _make_message(content: str, is_bot=False):
    msg = MagicMock()
    msg.author.bot = is_bot
    msg.content = content
    msg.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return msg


def _valid_post(ts: int = FUTURE_TS) -> str:
    return (
        f"Division: Premier\n"
        f"Week: 1\n"
        f"Alpha vs Beta\n"
        f"Time: <t:{ts}:F>"
    )


def _valid_post_for_time(ts: int) -> str:
    return (
        f"Division: Premier\n"
        f"Week: 1\n"
        f"Alpha vs Beta\n"
        f"Time: <t:{ts}:F>"
    )


async def _async_gen(*messages):
    """Async generator that yields messages — simulates channel.history()."""
    for m in messages:
        yield m


def _make_match_channel(*messages):
    ch = MagicMock()
    ch.history.return_value = _async_gen(*messages)
    return ch


def _make_log_channel():
    return AsyncMock()


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _make_cog(db, match_channel, log_channel=None):
    """Build an EventsCog with mocked bot channels."""
    bot = MagicMock()
    bot.dispatch = MagicMock()  # track dispatch calls

    def _get_channel(ch_id):
        if str(ch_id) == "123":
            return match_channel
        if str(ch_id) == "456":
            return log_channel
        return None

    bot.get_channel.side_effect = _get_channel
    db.set_config("match_channel_id", "123")
    if log_channel is not None:
        db.set_config("log_channel_id", "456")
    return EventsCog(bot, db, get_teamup=lambda: None)


# ---------------------------------------------------------------------------
# Bot messages are skipped
# ---------------------------------------------------------------------------

async def test_scan_skips_bot_messages(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(), is_bot=True))
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    cog.bot.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Past matches are skipped
# ---------------------------------------------------------------------------

async def test_scan_skips_past_matches(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(ts=PAST_TS)))
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    cog.bot.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# New future match is inserted and dispatched
# ---------------------------------------------------------------------------

async def test_scan_inserts_new_match_and_dispatches(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(ts=FUTURE_TS)))
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    matches = db.get_matches_for_date(FUTURE_DATE)
    assert len(matches) == 1
    assert matches[0]["team_home"] == "Alpha"
    assert matches[0]["team_away"] == "Beta"

    # Dispatches match_logged for the date
    cog.bot.dispatch.assert_called_once_with("match_logged", FUTURE_DATE)

    # Log channel notified with count
    log_ch.send.assert_called_once()
    assert "1" in log_ch.send.call_args[0][0]


# ---------------------------------------------------------------------------
# Duplicate matches are not re-inserted
# ---------------------------------------------------------------------------

async def test_scan_skips_already_known_match(db):
    db.insert_match("Premier", "1", "Alpha", "Beta", FUTURE_TS, FUTURE_TS - 3600)

    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(ts=FUTURE_TS)))
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 1   # no duplicate
    cog.bot.dispatch.assert_not_called()
    assert "no new" in log_ch.send.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Messages without required structure are ignored
# ---------------------------------------------------------------------------

async def test_scan_ignores_non_match_messages(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(
        _make_message("Hey everyone, good game tonight!"),
        _make_message("This is not a match post."),
    )
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    cog.bot.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch called once per unique date (not once per match)
# ---------------------------------------------------------------------------

async def test_scan_dispatches_per_date_not_per_match(db):
    ts1 = FUTURE_TS
    ts2 = FUTURE_TS + 7200  # 2h later on the same calendar day
    post1 = f"Division: Premier\nWeek: 1\nAlpha vs Beta\nTime: <t:{ts1}:F>"
    post2 = f"Division: Division 1\nWeek: 1\nGamma vs Delta\nTime: <t:{ts2}:F>"

    log_ch = _make_log_channel()
    match_ch = _make_match_channel(
        _make_message(post1),
        _make_message(post2),
    )
    cog = _make_cog(db, match_ch, log_ch)

    await cog._scan_match_history()

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 2
    # Two matches, same date — dispatch called only once for the date
    assert cog.bot.dispatch.call_count == 1
    cog.bot.dispatch.assert_called_once_with("match_logged", FUTURE_DATE)


# ---------------------------------------------------------------------------
# No log channel: scan still runs silently
# ---------------------------------------------------------------------------

async def test_scan_works_without_log_channel(db):
    match_ch = _make_match_channel(_make_message(_valid_post(ts=FUTURE_TS)))
    cog = _make_cog(db, match_ch, log_channel=None)

    await cog._scan_match_history()  # must not raise

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 1


# ---------------------------------------------------------------------------
# on_message: new match is inserted and dispatched
# ---------------------------------------------------------------------------

async def test_on_message_inserts_new_match(db):
    log_ch = _make_log_channel()
    match_ch = MagicMock()
    cog = _make_cog(db, match_ch, log_ch)

    msg = _make_message(_valid_post(ts=FUTURE_TS))
    msg.author.bot = False
    msg.channel.id = 123  # matches match_channel_id

    await cog.on_message(msg)

    matches = db.get_matches_for_date(FUTURE_DATE)
    assert len(matches) == 1
    cog.bot.dispatch.assert_called_once_with("match_logged", FUTURE_DATE)


async def test_on_message_skips_duplicate(db):
    db.insert_match("Premier", "1", "Alpha", "Beta", FUTURE_TS, FUTURE_TS - 3600)

    log_ch = _make_log_channel()
    match_ch = MagicMock()
    cog = _make_cog(db, match_ch, log_ch)

    msg = _make_message(_valid_post(ts=FUTURE_TS))
    msg.author.bot = False
    msg.channel.id = 123

    await cog.on_message(msg)

    # No new match inserted, no dispatch
    assert len(db.get_matches_for_date(FUTURE_DATE)) == 1
    cog.bot.dispatch.assert_not_called()


async def test_on_message_ignores_wrong_channel(db):
    log_ch = _make_log_channel()
    match_ch = MagicMock()
    cog = _make_cog(db, match_ch, log_ch)

    msg = _make_message(_valid_post(ts=FUTURE_TS))
    msg.author.bot = False
    msg.channel.id = 999  # wrong channel

    await cog.on_message(msg)

    assert db.get_matches_for_date(FUTURE_DATE) == []
    cog.bot.dispatch.assert_not_called()


# --- _week_bounds ---

def test_week_bounds_monday_is_start():
    start, end = _week_bounds(WEEK_MON_TS)
    mon_midnight = datetime(2099, 6, 8, 0, 0, 0, tzinfo=ET_TZ)
    assert start == int(mon_midnight.timestamp())


def test_week_bounds_sunday_is_end():
    start, end = _week_bounds(WEEK_MON_TS)
    sun_end = datetime(2099, 6, 14, 23, 59, 59, tzinfo=ET_TZ)
    assert end == int(sun_end.timestamp())


def test_week_bounds_same_for_all_days_in_week():
    start_mon, end_mon = _week_bounds(WEEK_MON_TS)
    start_wed, end_wed = _week_bounds(WEEK_WED_TS)
    assert start_mon == start_wed
    assert end_mon == end_wed


def test_week_bounds_differs_for_next_week():
    start_this, _ = _week_bounds(WEEK_MON_TS)
    start_next, _ = _week_bounds(NEXT_MON_TS)
    assert start_this != start_next


# --- on_message: reschedule detection ---

async def test_on_message_reschedule_detected_deletes_old_match(db):
    """When a new post appears for the same matchup in the same week with a different time,
    the old match is removed and the new one is inserted."""
    cog = _make_cog(db, _make_match_channel(), AsyncMock())

    # Pre-insert old match on Monday
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    # New message arrives with Wednesday timestamp (same week)
    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123

    await cog.on_message(msg)

    # Old match gone
    assert db.get_match(old_id) is None
    # New match exists
    matches = db.get_matches_for_date("2099-06-10")
    assert len(matches) == 1
    assert matches[0]["match_time"] == WEEK_WED_TS


async def test_on_message_reschedule_dispatches_match_logged_for_new_date(db):
    cog = _make_cog(db, _make_match_channel(), AsyncMock())
    db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    dispatched_dates = [call.args[1] for call in cog.bot.dispatch.call_args_list
                        if call.args[0] == "match_logged"]
    assert "2099-06-10" in dispatched_dates  # new date dispatched


async def test_on_message_reschedule_dispatches_old_date_when_different(db):
    cog = _make_cog(db, _make_match_channel(), AsyncMock())
    db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    dispatched_dates = [call.args[1] for call in cog.bot.dispatch.call_args_list
                        if call.args[0] == "match_logged"]
    assert "2099-06-08" in dispatched_dates  # old date also dispatched


async def test_on_message_same_time_is_duplicate_not_reschedule(db):
    """A new post with the exact same time as an existing match is still a silent duplicate."""
    cog = _make_cog(db, _make_match_channel(), AsyncMock())
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    msg = _make_message(_valid_post_for_time(WEEK_MON_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    assert db.get_match(old_id) is not None  # original untouched
    assert len(db.get_matches_for_date("2099-06-08")) == 1  # no duplicate


async def test_on_message_different_week_is_new_match(db):
    """When a new post appears for the same matchup (Alpha vs Beta) but in a different week,
    it is treated as a new match (not a reschedule). The old match should remain and a new
    one should be inserted."""
    cog = _make_cog(db, _make_match_channel(), AsyncMock())

    # Pre-insert old match in week of 2099-06-08 (Monday)
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    # New message arrives for the same teams but in the next week (2099-06-15, Monday)
    msg = _make_message(_valid_post_for_time(NEXT_MON_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123

    await cog.on_message(msg)

    # Old match should still exist (not rescheduled/deleted)
    assert db.get_match(old_id) is not None
    old_match = db.get_match(old_id)
    assert old_match["match_time"] == WEEK_MON_TS

    # New match should be inserted in the next week
    new_matches = db.get_matches_for_date("2099-06-15")
    assert len(new_matches) == 1
    assert new_matches[0]["match_time"] == NEXT_MON_TS
    assert new_matches[0]["team_home"] == "Alpha"
    assert new_matches[0]["team_away"] == "Beta"
