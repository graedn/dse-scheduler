"""Tests for cogs/reschedule.py — RescheduleView buttons and send_thread_reschedule_notice."""
import pytest
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from database import Database

ET = ZoneInfo("America/New_York")

WEEK_MON_TS = int(datetime(2099, 6, 8, 19, 0, tzinfo=ET).timestamp())
WEEK_WED_TS = int(datetime(2099, 6, 10, 19, 0, tzinfo=ET).timestamp())


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _make_match(db, ts=WEEK_MON_TS):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", ts, ts - 100)
    db.mark_broadcast_accepted(mid)
    return mid


def _make_interaction(db, *, is_manager=True):
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.user.guild_permissions.administrator = is_manager
    interaction.user.roles = []
    interaction.client.db = db
    interaction.client.get_teamup = MagicMock(return_value=MagicMock())
    interaction.client.get_channel = MagicMock(return_value=AsyncMock())
    interaction.client.dispatch = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.message = AsyncMock()
    interaction.message.content = "⚠️ Match Time Changed"
    return interaction


# --- send_thread_reschedule_notice ---

async def test_send_thread_notice_posts_to_thread(db):
    from cogs.reschedule import send_thread_reschedule_notice

    mid = _make_match(db)
    db.conn.execute(
        "INSERT INTO thread_messages (match_id, thread_id, channel_id, created_at) "
        "VALUES (?, ?, ?, ?)", (mid, "111", "222", 0)
    )
    db.conn.commit()

    thread = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = thread

    await send_thread_reschedule_notice(bot, db, mid, "The time has changed.")
    thread.send.assert_called_once()
    assert "The time has changed." in thread.send.call_args[0][0]


async def test_send_thread_notice_skips_when_no_thread(db):
    from cogs.reschedule import send_thread_reschedule_notice
    mid = _make_match(db)
    bot = MagicMock()
    await send_thread_reschedule_notice(bot, db, mid, "message")
    bot.get_channel.assert_not_called()


async def test_send_thread_notice_includes_role_mentions(db):
    from cogs.reschedule import send_thread_reschedule_notice

    mid = _make_match(db)
    db.conn.execute(
        "INSERT INTO thread_messages "
        "(match_id, thread_id, channel_id, team1_role_id, team2_role_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, "111", "222", "role_aaa", "role_bbb", 0)
    )
    db.conn.commit()
    db.set_config("league_admin_role_id", "role_admin")

    thread = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = thread

    await send_thread_reschedule_notice(bot, db, mid, "Test message.")
    content = thread.send.call_args[0][0]
    assert "<@&role_admin>" in content
    assert "<@&role_aaa>" in content
    assert "<@&role_bbb>" in content


# --- _UpdateBroadcastButton ---

async def test_update_broadcast_button_updates_match_time(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)

    interaction = _make_interaction(db)
    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)

    update_btn = next(b for b in view.children if "Update" in b.label)
    await update_btn.callback(interaction)

    assert db.get_match(mid)["match_time"] == WEEK_WED_TS


async def test_update_broadcast_button_disables_view(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)

    interaction = _make_interaction(db)
    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)

    update_btn = next(b for b in view.children if "Update" in b.label)
    await update_btn.callback(interaction)

    assert all(b.disabled for b in view.children)


async def test_update_broadcast_denied_for_non_manager(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db)
    interaction = _make_interaction(db, is_manager=False)
    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)

    btn = next(b for b in view.children if "Update" in b.label)
    await btn.callback(interaction)

    interaction.response.send_message.assert_called_once()
    msg = (
        interaction.response.send_message.call_args[1].get("content", "")
        or (interaction.response.send_message.call_args[0][0]
            if interaction.response.send_message.call_args[0] else "")
    )
    assert "manager" in msg.lower()


# --- _InitiateSignUpButton ---

async def test_initiate_signup_button_updates_match_time(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)
    db.insert_broadcast_message(mid, "msg_old_111", "999")

    signup_ch = AsyncMock()
    signup_ch.fetch_message = AsyncMock(return_value=AsyncMock())
    new_msg = AsyncMock()
    new_msg.id = 12345
    signup_ch.send = AsyncMock(return_value=new_msg)

    interaction = _make_interaction(db)
    interaction.client.get_channel.return_value = signup_ch
    db.set_config("signup_channel_id", "999")

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Sign Up" in b.label)
    await btn.callback(interaction)

    assert db.get_match(mid)["match_time"] == WEEK_WED_TS


async def test_initiate_signup_button_resets_allocation(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)
    db.set_allocation_assignments(mid,
        {"producer": {"user_id": "u1", "display_name": "A", "username": "a"}},
        {}, None, None)
    db.insert_broadcast_message(mid, "msg_old_111", "999")

    signup_ch = AsyncMock()
    signup_ch.fetch_message = AsyncMock(return_value=AsyncMock())
    new_msg = AsyncMock()
    new_msg.id = 12345
    signup_ch.send = AsyncMock(return_value=new_msg)

    interaction = _make_interaction(db)
    interaction.client.get_channel.return_value = signup_ch
    db.set_config("signup_channel_id", "999")

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Sign Up" in b.label)
    await btn.callback(interaction)

    alloc = db.get_allocation(mid)
    assert alloc["role_assignments"] is None


# --- _CancelBroadcastButton ---

async def test_cancel_broadcast_button_deletes_teamup_event(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)
    db.update_match_teamup_id(mid, "evt_abc")

    teamup = MagicMock()
    interaction = _make_interaction(db)
    interaction.client.get_teamup.return_value = teamup
    db.set_config("broadcast_channel_id", "888")

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Cancel" in b.label)
    await btn.callback(interaction)

    teamup.delete_event.assert_called_once_with("evt_abc")


async def test_cancel_broadcast_denied_for_non_manager(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    interaction = _make_interaction(db, is_manager=False)

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Cancel" in b.label)
    await btn.callback(interaction)

    interaction.response.send_message.assert_called_once()
