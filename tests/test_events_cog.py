"""Tests for EventsCog._scan_match_history() (M7).

Mocks channel.history (async generator) and schedule_for_date so no
real Discord or TeamUp calls are made.  Uses a real in-memory Database.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from database import Database
from cogs.events import EventsCog

# A future timestamp (2099-01-01 20:00 UTC — well past 'now' in any test run)
FUTURE_TS  = int(datetime(2099, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp())
FUTURE_DATE = "2099-01-01"
PAST_TS    = int(datetime(2000, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp())


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


async def _async_gen(*messages):
    """Async generator that yields messages — simulates channel.history()."""
    for m in messages:
        yield m


def _make_match_channel(*messages):
    """A channel mock whose .history() yields the given messages."""
    ch = MagicMock()
    ch.history.return_value = _async_gen(*messages)
    return ch


def _make_log_channel():
    """A channel mock that supports await send()."""
    return AsyncMock()


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _make_cog(db, match_channel, log_channel=None):
    """Build an EventsCog with mocked bot channels."""
    bot = MagicMock()

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

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    mock_sched.assert_not_called()


# ---------------------------------------------------------------------------
# Past matches are skipped
# ---------------------------------------------------------------------------

async def test_scan_skips_past_matches(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(ts=PAST_TS)))
    cog = _make_cog(db, match_ch, log_ch)

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    mock_sched.assert_not_called()


# ---------------------------------------------------------------------------
# New future match is inserted and scheduled
# ---------------------------------------------------------------------------

async def test_scan_inserts_new_match_and_schedules(db):
    log_ch = _make_log_channel()
    match_ch = _make_match_channel(_make_message(_valid_post(ts=FUTURE_TS)))
    cog = _make_cog(db, match_ch, log_ch)

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    matches = db.get_matches_for_date(FUTURE_DATE)
    assert len(matches) == 1
    assert matches[0]["team_home"] == "Alpha"
    assert matches[0]["team_away"] == "Beta"
    mock_sched.assert_called_once()
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

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 1   # no duplicate
    mock_sched.assert_not_called()
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

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    assert db.get_matches_for_date(FUTURE_DATE) == []
    mock_sched.assert_not_called()


# ---------------------------------------------------------------------------
# schedule_for_date called once per unique date
# ---------------------------------------------------------------------------

async def test_scan_schedules_per_date_not_per_match(db):
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

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock) as mock_sched:
        await cog._scan_match_history()

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 2
    # Two matches, same date — schedule_for_date called only once
    assert mock_sched.call_count == 1


# ---------------------------------------------------------------------------
# No log channel: scan still runs silently
# ---------------------------------------------------------------------------

async def test_scan_works_without_log_channel(db):
    match_ch = _make_match_channel(_make_message(_valid_post(ts=FUTURE_TS)))
    cog = _make_cog(db, match_ch, log_channel=None)  # no log channel

    with patch("cogs.events.schedule_for_date", new_callable=AsyncMock):
        await cog._scan_match_history()  # must not raise

    assert len(db.get_matches_for_date(FUTURE_DATE)) == 1
