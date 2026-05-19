"""Tests for weekly_proposals.py — proposal content builder and mark_passed_proposals."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from database import Database

ET = ZoneInfo("America/New_York")

# Use a fixed future date (Tuesday 2099-06-10) so tests don't depend on 'today'
DATE_STR   = "2099-06-10"
DATE_TS    = int(datetime(2099, 6, 10, 12, 0, tzinfo=timezone.utc).timestamp())
MATCH_TS   = int(datetime(2099, 6, 10, 19, 0, tzinfo=timezone.utc).timestamp())  # 7pm UTC
MATCH_TS2  = int(datetime(2099, 6, 10, 22, 0, tzinfo=timezone.utc).timestamp())  # 10pm UTC

# A past date for mark_passed_proposals tests
PAST_DATE  = "2000-01-05"
PAST_TS    = int(datetime(2000, 1, 5, 0, 0, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


# ---------------------------------------------------------------------------
# build_proposal_day_content
# ---------------------------------------------------------------------------

def test_build_proposal_day_no_matches_or_proposal(db):
    """No proposal row and no matches → 'No matches logged' in content."""
    from cogs.weekly_proposals import build_proposal_day_content
    content = build_proposal_day_content(DATE_STR, db)
    assert "No matches logged" in content


def test_build_proposal_day_with_unscheduled_match(db):
    """A logged match not in a slot appears in Logged Matches section."""
    from cogs.weekly_proposals import build_proposal_day_content
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.insert_match("Premier", "1", "Alpha", "Beta", MATCH_TS, MATCH_TS - 3600)
    content = build_proposal_day_content(DATE_STR, db)
    assert "Alpha" in content
    assert "No matches selected" in content   # Current Schedule is empty


def test_build_proposal_day_with_scheduled_match(db):
    """A match assigned to slot1 appears in Current Schedule, not Logged Matches."""
    from cogs.weekly_proposals import build_proposal_day_content
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", MATCH_TS, MATCH_TS - 3600)
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.update_proposal_slots(DATE_STR, mid, None)
    content = build_proposal_day_content(DATE_STR, db)
    # Match appears in Current Schedule section
    lines = content.splitlines()
    sched_idx = next(i for i, l in enumerate(lines) if "Current Schedule" in l)
    logged_idx = next(i for i, l in enumerate(lines) if "Logged Matches" in l)
    sched_block = "\n".join(lines[sched_idx:logged_idx])
    assert "Alpha" in sched_block
    # Logged Matches section should say all assigned
    logged_block = "\n".join(lines[logged_idx:])
    assert "All matches assigned" in logged_block


def test_build_proposal_day_blocked(db):
    """A blocked proposal day shows NO STREAM in Current Schedule."""
    from cogs.weekly_proposals import build_proposal_day_content
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.set_proposal_status(DATE_STR, "blocked")
    content = build_proposal_day_content(DATE_STR, db)
    assert "NO STREAM" in content


def test_build_proposal_day_passed_shows_closed_notice(db):
    """A passed proposal shows a closed notice at the bottom."""
    from cogs.weekly_proposals import build_proposal_day_content
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.set_proposal_status(DATE_STR, "passed")
    content = build_proposal_day_content(DATE_STR, db)
    assert "passed" in content.lower() or "closed" in content.lower()


def test_build_proposal_day_two_scheduled_matches(db):
    """Both slot1 and slot2 appear in Current Schedule."""
    from cogs.weekly_proposals import build_proposal_day_content
    mid1 = db.insert_match("Premier", "1", "Alpha", "Beta", MATCH_TS, MATCH_TS - 3600)
    mid2 = db.insert_match("Division 1", "1", "Gamma", "Delta", MATCH_TS2, MATCH_TS2 - 3600)
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.update_proposal_slots(DATE_STR, mid1, mid2)
    content = build_proposal_day_content(DATE_STR, db)
    assert "Alpha" in content
    assert "Gamma" in content


# ---------------------------------------------------------------------------
# get_priority_label
# ---------------------------------------------------------------------------

def _ts(year, month, day, hour, minute, tz=ET):
    return int(datetime(year, month, day, hour, minute, tzinfo=tz).timestamp())

# 2099-06-11 = Thursday, 2099-06-12 = Friday (verified)

# Thursday slots
def test_priority_label_thu_7pm_star():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 11, 19, 0)) == "⭐ "


def test_priority_label_thu_930pm_star():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 11, 21, 30)) == "⭐ "


def test_priority_label_thu_8pm_moon():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 11, 20, 0)) == "🌑 "


def test_priority_label_thu_1030pm_moon():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 11, 22, 30)) == "🌑 "


# Friday slots
def test_priority_label_fri_7pm_star():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 12, 19, 0)) == "⭐ "


def test_priority_label_fri_8pm_moon():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 12, 20, 0)) == "🌑 "


# Non-priority times on Thu/Fri
def test_priority_label_thu_6pm_no_label():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 11, 18, 0)) == ""


def test_priority_label_fri_9pm_no_label():
    from cogs.weekly_proposals import get_priority_label
    assert get_priority_label(_ts(2099, 6, 12, 21, 0)) == ""


# Wrong day — same times get no label
def test_priority_label_wed_7pm_no_label():
    from cogs.weekly_proposals import get_priority_label
    # 2099-06-10 is a Wednesday
    assert get_priority_label(_ts(2099, 6, 10, 19, 0)) == ""


def test_priority_label_sat_8pm_no_label():
    from cogs.weekly_proposals import get_priority_label
    # 2099-06-13 is a Saturday
    assert get_priority_label(_ts(2099, 6, 13, 20, 0)) == ""


def test_priority_label_in_proposal_content(db):
    """Priority emoji appears in both Current Schedule and Logged Matches sections."""
    from cogs.weekly_proposals import build_proposal_day_content
    # 2099-06-11 is Thursday
    thu = "2099-06-11"
    thu_ts = int(datetime(2099, 6, 11, 0, 0, tzinfo=ET).timestamp())
    match_ts  = _ts(2099, 6, 11, 19, 0)  # 7pm ET Thursday → ⭐
    match_ts2 = _ts(2099, 6, 11, 20, 0)  # 8pm ET Thursday → 🌑

    mid1 = db.insert_match("Premier", "1", "Alpha", "Beta", match_ts, match_ts - 3600)
    mid2 = db.insert_match("Division 1", "1", "Gamma", "Delta", match_ts2, match_ts2 - 3600)
    db.create_proposal_message(thu, thu_ts, "2099-06-11")
    db.update_proposal_slots(thu, mid1, None)

    content = build_proposal_day_content(thu, db)
    # mid1 in Current Schedule → ⭐
    assert "⭐" in content
    # mid2 in Logged Matches → 🌑
    assert "🌑" in content


def test_priority_label_absent_on_non_priority_day(db):
    """No priority emoji when the date is not Thursday or Friday."""
    from cogs.weekly_proposals import build_proposal_day_content
    # DATE_STR (2099-06-10) is a Wednesday
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.insert_match("Premier", "1", "Alpha", "Beta", MATCH_TS, MATCH_TS - 3600)
    content = build_proposal_day_content(DATE_STR, db)
    assert "⭐" not in content
    assert "🌑" not in content


def test_match_option_label_no_time_non_priority():
    """Dropdown label excludes match time for non-priority matches."""
    from cogs.weekly_proposals import _match_option_label
    match = {
        "match_time": _ts(2099, 6, 10, 19, 0),  # Wednesday 7pm — no priority
        "division": "Premier",
        "team_home": "Alpha",
        "team_away": "Beta",
    }
    label = _match_option_label(match)
    assert "ET" not in label
    assert "19:00" not in label
    assert "Alpha" in label


def test_match_option_label_has_emoji_priority():
    """Dropdown label has ⭐ for Thursday 7pm."""
    from cogs.weekly_proposals import _match_option_label
    match = {
        "match_time": _ts(2099, 6, 11, 19, 0),  # Thursday 7pm ET
        "division": "Premier",
        "team_home": "Alpha",
        "team_away": "Beta",
    }
    label = _match_option_label(match)
    assert label.startswith("⭐")


# ---------------------------------------------------------------------------
# recover_missed_weekly_proposals (startup catch-up)
# ---------------------------------------------------------------------------

async def test_recover_missed_weekly_proposals_creates_when_week_empty(db):
    """Bot offline at the Sunday transition → current week has no proposal
    rows → startup recovery creates them (today through this Sunday)."""
    from cogs.weekly_proposals import recover_missed_weekly_proposals

    today = datetime.now(ET).date()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    assert db.get_proposal_messages_for_week(week_start) == []  # nothing yet

    db.set_config("proposal_channel_id", "555")
    channel = AsyncMock()
    channel.id = 555
    channel.send = AsyncMock(return_value=MagicMock(id=999))
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await recover_missed_weekly_proposals(bot, db)

    rows = db.get_proposal_messages_for_week(week_start)
    assert rows  # current week's proposals were created
    assert channel.send.await_count >= 1


async def test_recover_missed_weekly_proposals_noop_when_week_present(db, monkeypatch):
    """Normal restart → current week already has proposal rows → recovery is
    a no-op and does NOT re-run create_weekly_proposals."""
    import cogs.weekly_proposals as wp
    from cogs.weekly_proposals import recover_missed_weekly_proposals

    today = datetime.now(ET).date()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    day_ts = int(datetime(today.year, today.month, today.day, tzinfo=ET).timestamp())
    db.create_proposal_message(today.isoformat(), day_ts, week_start)
    assert db.get_proposal_messages_for_week(week_start)  # row exists

    called = AsyncMock()
    monkeypatch.setattr(wp, "create_weekly_proposals", called)
    bot = MagicMock()

    await recover_missed_weekly_proposals(bot, db)

    called.assert_not_awaited()


# ---------------------------------------------------------------------------
# mark_passed_proposals
# ---------------------------------------------------------------------------

async def test_mark_passed_proposals_marks_past(db):
    """Proposals with dates before today are marked 'passed'."""
    from cogs.weekly_proposals import mark_passed_proposals
    db.create_proposal_message(PAST_DATE, PAST_TS, "2000-01-01")
    # Set a discord_message_id so _refresh tries to edit (we'll mock that away)
    db.set_proposal_discord_message(PAST_DATE, "9999", "8888")

    bot = MagicMock()
    ch = AsyncMock()
    ch.fetch_message.side_effect = Exception("not found")
    bot.get_channel.return_value = ch

    await mark_passed_proposals(db, bot)

    proposal = db.get_proposal_message(PAST_DATE)
    assert proposal["status"] == "passed"


async def test_mark_passed_proposals_skips_future(db):
    """Proposals with future dates remain 'open'."""
    from cogs.weekly_proposals import mark_passed_proposals
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")

    bot = MagicMock()
    bot.get_channel.return_value = AsyncMock()

    await mark_passed_proposals(db, bot)

    proposal = db.get_proposal_message(DATE_STR)
    assert proposal["status"] == "open"


async def test_mark_passed_proposals_skips_already_passed(db):
    """Proposals already 'passed' are not in get_open_proposal_messages."""
    from cogs.weekly_proposals import mark_passed_proposals
    db.create_proposal_message(PAST_DATE, PAST_TS, "2000-01-01")
    db.set_proposal_status(PAST_DATE, "passed")

    bot = MagicMock()
    bot.get_channel.return_value = AsyncMock()

    await mark_passed_proposals(db, bot)  # must not raise
    # Status should still be "passed" (untouched)
    assert db.get_proposal_message(PAST_DATE)["status"] == "passed"


# ---------------------------------------------------------------------------
# _UnblockDayButton
# ---------------------------------------------------------------------------

def _unblock_interaction(db, teamup=None):
    """Build a mock interaction for _UnblockDayButton tests (admin user)."""
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.user.guild_permissions.administrator = True
    interaction.client.db = db
    interaction.client.get_teamup.return_value = teamup
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.message = AsyncMock()
    return interaction


async def test_unblock_day_reverts_status_to_open(db):
    """_UnblockDayButton sets proposal status back to 'open' and removes the block."""
    from cogs.weekly_proposals import _UnblockDayButton
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.set_proposal_status(DATE_STR, "blocked")
    db.insert_blocked_day(DATE_STR, reason="test", teamup_event_id=None)

    button = _UnblockDayButton(DATE_STR)
    await button.callback(_unblock_interaction(db))

    assert db.get_proposal_message(DATE_STR)["status"] == "open"
    assert db.get_blocked_day(DATE_STR) is None


async def test_unblock_day_deletes_teamup_event(db):
    """_UnblockDayButton calls teamup.delete_event when a TeamUp event ID is stored."""
    from cogs.weekly_proposals import _UnblockDayButton
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.set_proposal_status(DATE_STR, "blocked")
    db.insert_blocked_day(DATE_STR, reason="test", teamup_event_id="evt_block_123")

    teamup = MagicMock()
    button = _UnblockDayButton(DATE_STR)
    await button.callback(_unblock_interaction(db, teamup=teamup))

    teamup.delete_event.assert_called_once_with("evt_block_123")
