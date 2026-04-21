# Match Reschedule Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when a team reschedules a match (via message edit or a new post in the same week) and handle it gracefully across four match states: just logged, in a proposal slot, has sign-ups, or fully confirmed.

**Architecture:** Detection runs in `EventsCog` on both `on_message` and `on_raw_message_edit`. A shared `_handle_reschedule` method dispatches to the right behaviour based on match state. Confirmed-match rescheduling uses a new `RescheduleView` in `cogs/reschedule.py` that posts three action buttons to the log channel and sends a thread notice after each choice.

**Tech Stack:** Python 3.12, discord.py 2.x, SQLite via `database.py`, `zoneinfo.ZoneInfo("America/New_York")`, `pytest` with in-memory DB.

---

## File Map

| File | Change |
|---|---|
| `database.py` | Add 4 methods: `get_match_by_teams_in_week`, `delete_match_cascade`, `clear_match_from_proposal_slots`, `update_match_time` |
| `cogs/events.py` | Add `_week_bounds` module helper, `_handle_reschedule` method, `on_raw_message_edit` listener; update `on_message` to call reschedule check before inserting |
| `cogs/reschedule.py` | New — `RescheduleView` + `_UpdateBroadcastButton`, `_InitiateSignUpButton`, `_CancelBroadcastButton`; `send_thread_reschedule_notice` helper |
| `tests/test_database.py` | Tests for the 4 new DB methods |
| `tests/test_events_cog.py` | Tests for reschedule detection in `on_message` and `on_raw_message_edit` |
| `tests/test_reschedule.py` | New — tests for `RescheduleView` button callbacks |

---

## Task 1: Four new database methods

**Files:**
- Modify: `database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing tests for all four methods**

Add to `tests/test_database.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Monday 2099-06-08 19:00 ET and Wednesday 2099-06-10 19:00 ET — same week
WEEK_MON_TS = int(datetime(2099, 6, 8, 19, 0, tzinfo=ET).timestamp())
WEEK_WED_TS = int(datetime(2099, 6, 10, 19, 0, tzinfo=ET).timestamp())
# Monday 2099-06-15 19:00 ET — next week
NEXT_WEEK_TS = int(datetime(2099, 6, 15, 19, 0, tzinfo=ET).timestamp())

# Week bounds: 2099-06-08 00:00 ET (Monday) to 2099-06-14 23:59:59 ET (Sunday)
WEEK_START = int(datetime(2099, 6, 8, 0, 0, 0, tzinfo=ET).timestamp())
WEEK_END   = int(datetime(2099, 6, 14, 23, 59, 59, tzinfo=ET).timestamp())


# --- get_match_by_teams_in_week ---

def test_get_match_by_teams_in_week_finds_same_week(db):
    db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    result = db.get_match_by_teams_in_week("Alpha", "Beta", WEEK_START, WEEK_END)
    assert result is not None
    assert result["match_time"] == WEEK_MON_TS


def test_get_match_by_teams_in_week_returns_none_for_different_week(db):
    db.insert_match("Premier", "1", "Alpha", "Beta", NEXT_WEEK_TS, NEXT_WEEK_TS - 100)
    result = db.get_match_by_teams_in_week("Alpha", "Beta", WEEK_START, WEEK_END)
    assert result is None


def test_get_match_by_teams_in_week_returns_none_when_no_match(db):
    result = db.get_match_by_teams_in_week("Alpha", "Beta", WEEK_START, WEEK_END)
    assert result is None


def test_get_match_by_teams_in_week_different_teams_not_found(db):
    db.insert_match("Premier", "1", "Gamma", "Delta", WEEK_MON_TS, WEEK_MON_TS - 100)
    result = db.get_match_by_teams_in_week("Alpha", "Beta", WEEK_START, WEEK_END)
    assert result is None


# --- delete_match_cascade ---

def test_delete_match_cascade_removes_match(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.delete_match_cascade(mid)
    assert db.get_match(mid) is None


def test_delete_match_cascade_removes_signups(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.upsert_signup(mid, str(mid), "pbp", "u1", "user1", "User One")
    db.delete_match_cascade(mid)
    assert db.get_signups_for_match(mid) == []


def test_delete_match_cascade_removes_broadcast_message(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.insert_broadcast_message(mid, "msg123", "ch456")
    db.delete_match_cascade(mid)
    assert db.get_broadcast_message(mid) is None


def test_delete_match_cascade_removes_allocation(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.create_allocation(mid)
    db.delete_match_cascade(mid)
    assert db.get_allocation(mid) is None


# --- clear_match_from_proposal_slots ---

def test_clear_match_from_proposal_slots_clears_slot1(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.create_proposal_message("2099-06-08", WEEK_START, "2099-06-08")
    db.update_proposal_slots("2099-06-08", mid, None)
    db.clear_match_from_proposal_slots(mid)
    prop = db.get_proposal_message("2099-06-08")
    assert prop["slot1_match_id"] is None


def test_clear_match_from_proposal_slots_clears_slot2(db):
    mid1 = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    mid2 = db.insert_match("Division 1", "1", "Gamma", "Delta", WEEK_WED_TS, WEEK_WED_TS - 100)
    db.create_proposal_message("2099-06-08", WEEK_START, "2099-06-08")
    db.update_proposal_slots("2099-06-08", mid1, mid2)
    db.clear_match_from_proposal_slots(mid2)
    prop = db.get_proposal_message("2099-06-08")
    assert prop["slot1_match_id"] == mid1
    assert prop["slot2_match_id"] is None


def test_clear_match_from_proposal_slots_noop_when_not_assigned(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.create_proposal_message("2099-06-08", WEEK_START, "2099-06-08")
    db.clear_match_from_proposal_slots(mid)  # should not raise
    prop = db.get_proposal_message("2099-06-08")
    assert prop["slot1_match_id"] is None


# --- update_match_time ---

def test_update_match_time_changes_timestamp(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.update_match_time(mid, WEEK_WED_TS)
    assert db.get_match(mid)["match_time"] == WEEK_WED_TS


def test_update_match_time_other_fields_unchanged(db):
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.update_match_time(mid, WEEK_WED_TS)
    match = db.get_match(mid)
    assert match["team_home"] == "Alpha"
    assert match["division"] == "Premier"
```

- [ ] **Step 2: Run to confirm they all fail**

```
python -m pytest tests/test_database.py -k "teams_in_week or delete_match_cascade or clear_match_from or update_match_time" -v
```

Expected: all FAIL with `AttributeError: 'Database' object has no attribute ...`

- [ ] **Step 3: Add the four methods to `database.py`**

Add after `match_exists` (around line 281):

```python
def get_match_by_teams_in_week(self, team_home: str, team_away: str,
                                week_start_ts: int, week_end_ts: int) -> Optional[dict]:
    """Return the first match for team_home vs team_away in the given Mon–Sun ET window."""
    row = self.conn.execute(
        "SELECT * FROM matches WHERE team_home = ? AND team_away = ? "
        "AND match_time >= ? AND match_time <= ? LIMIT 1",
        (team_home, team_away, week_start_ts, week_end_ts)
    ).fetchone()
    return dict(row) if row else None

def delete_match_cascade(self, match_id: int) -> None:
    """Delete a match and all dependent rows (signups, broadcast message, allocation, thread)."""
    self.conn.execute("DELETE FROM broadcast_signups WHERE match_id = ?", (match_id,))
    self.conn.execute("DELETE FROM broadcast_messages WHERE match_id = ?", (match_id,))
    self.conn.execute("DELETE FROM talent_allocations WHERE match_id = ?", (match_id,))
    self.conn.execute("DELETE FROM thread_messages WHERE match_id = ?", (match_id,))
    self.conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    self.conn.commit()

def clear_match_from_proposal_slots(self, match_id: int) -> None:
    """Null out any proposal slot references to this match."""
    self.conn.execute(
        "UPDATE proposal_messages SET slot1_match_id = NULL WHERE slot1_match_id = ?",
        (match_id,)
    )
    self.conn.execute(
        "UPDATE proposal_messages SET slot2_match_id = NULL WHERE slot2_match_id = ?",
        (match_id,)
    )
    self.conn.commit()

def update_match_time(self, match_id: int, new_match_time: int) -> None:
    """Update a match's timestamp in-place (used when a confirmed broadcast is rescheduled)."""
    self.conn.execute(
        "UPDATE matches SET match_time = ? WHERE id = ?",
        (new_match_time, match_id)
    )
    self.conn.commit()
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_database.py -k "teams_in_week or delete_match_cascade or clear_match_from or update_match_time" -v
```

Expected: all PASS

- [ ] **Step 5: Run full suite to check for regressions**

```
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add DB methods for match reschedule — get_match_by_teams_in_week, delete_match_cascade, clear_match_from_proposal_slots, update_match_time"
```

---

## Task 2: `_week_bounds` helper + reschedule detection in `on_message`

**Files:**
- Modify: `cogs/events.py`
- Test: `tests/test_events_cog.py`

- [ ] **Step 1: Write failing tests for `_week_bounds` and the detection path in `on_message`**

Add to `tests/test_events_cog.py` (after existing imports, add `from zoneinfo import ZoneInfo`):

```python
from zoneinfo import ZoneInfo
from cogs.events import _week_bounds

ET = ZoneInfo("America/New_York")

# Week of 2099-06-08 (Monday) through 2099-06-14 (Sunday)
WEEK_MON_TS = int(datetime(2099, 6, 8, 19, 0, tzinfo=ET).timestamp())
WEEK_WED_TS = int(datetime(2099, 6, 10, 19, 0, tzinfo=ET).timestamp())
NEXT_MON_TS = int(datetime(2099, 6, 15, 19, 0, tzinfo=ET).timestamp())


# --- _week_bounds ---

def test_week_bounds_monday_is_start():
    start, end = _week_bounds(WEEK_MON_TS)
    mon_midnight = datetime(2099, 6, 8, 0, 0, 0, tzinfo=ET)
    assert start == int(mon_midnight.timestamp())


def test_week_bounds_sunday_is_end():
    start, end = _week_bounds(WEEK_MON_TS)
    sun_end = datetime(2099, 6, 14, 23, 59, 59, tzinfo=ET)
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

def _valid_post_for_time(ts: int) -> str:
    return (
        f"Division: Premier\n"
        f"Week: 1\n"
        f"Alpha vs Beta\n"
        f"Time: <t:{ts}:F>"
    )


async def test_on_message_reschedule_detected_deletes_old_match(db):
    """When a new post appears for the same matchup in the same week with a different time,
    the old match is removed and the new one is inserted."""
    cog = _make_cog(db, _make_match_channel(), AsyncMock())

    # Pre-insert old match on Monday
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    # New message arrives with Wednesday timestamp (same week)
    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel.id = 123
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
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_events_cog.py -k "week_bounds or reschedule" -v
```

Expected: FAIL (`ImportError: cannot import name '_week_bounds'`)

- [ ] **Step 3: Add `_week_bounds` to `cogs/events.py` and update `on_message`**

Add at the top of `cogs/events.py`, after existing imports:

```python
from datetime import timedelta
```

Add module-level helper after the `ET` and `log` definitions:

```python
def _week_bounds(ts: int) -> tuple[int, int]:
    """Return (week_start_ts, week_end_ts) for the Mon–Sun ET week containing ts."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday = (monday + timedelta(days=6)).replace(hour=23, minute=59, second=59)
    return int(monday.timestamp()), int(sunday.timestamp())
```

Replace the duplicate-check block in `on_message` (currently lines ~116–126):

```python
        week_start, week_end = _week_bounds(parsed.match_time)
        old_match = self.db.get_match_by_teams_in_week(
            parsed.team_home, parsed.team_away, week_start, week_end
        )
        if old_match:
            if old_match["match_time"] == parsed.match_time:
                return  # True duplicate — silently ignore
            await self._handle_reschedule(old_match, parsed)
            return

        self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=parsed.match_time,
            posted_at=int(message.created_at.timestamp()),
        )

        match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")

        log_ch = self._get_log_channel()
        if log_ch:
            try:
                await log_ch.send(
                    f"📋 Match logged for **{match_date}**: "
                    f"[{parsed.division}] {parsed.team_home} vs {parsed.team_away} "
                    f"— <t:{parsed.match_time}:F>"
                )
            except Exception:
                log.exception("Failed to send match log message")

        self.bot.dispatch("match_logged", match_date)
```

Add a stub for `_handle_reschedule` (full implementation in Task 3):

```python
    async def _handle_reschedule(self, old_match: dict, parsed) -> None:
        """Dispatch to correct handling based on old match state."""
        old_ts = old_match["match_time"]
        new_ts = parsed.match_time
        old_date = datetime.fromtimestamp(old_ts, tz=ET).strftime("%Y-%m-%d")
        new_date = datetime.fromtimestamp(new_ts, tz=ET).strftime("%Y-%m-%d")

        # Delete old, insert new — full state logic added in Task 3
        self.db.clear_match_from_proposal_slots(old_match["id"])
        self.db.delete_match_cascade(old_match["id"])
        self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=new_ts,
            posted_at=int(datetime.now(tz=ET).timestamp()),
        )
        self.bot.dispatch("match_logged", new_date)
        if old_date != new_date:
            self.bot.dispatch("match_logged", old_date)
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_events_cog.py -k "week_bounds or reschedule" -v
```

Expected: all PASS

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add cogs/events.py tests/test_events_cog.py
git commit -m "feat: reschedule detection in on_message — week-bounds check replaces simple duplicate guard"
```

---

## Task 3: `on_raw_message_edit` listener

**Files:**
- Modify: `cogs/events.py`
- Test: `tests/test_events_cog.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_events_cog.py`:

```python
import discord as discord_lib


def _make_raw_edit_payload(message_id: int, channel_id: int):
    payload = MagicMock(spec=discord_lib.RawMessageUpdateEvent)
    payload.message_id = message_id
    payload.channel_id = channel_id
    return payload


async def test_on_raw_message_edit_reschedule_detected(db):
    """An edited match post with a new time triggers reschedule handling."""
    log_ch = AsyncMock()
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    edited_msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    edited_msg.author.bot = False

    ch = AsyncMock()
    ch.fetch_message = AsyncMock(return_value=edited_msg)

    bot = MagicMock()
    bot.dispatch = MagicMock()

    def _get_channel(ch_id):
        if str(ch_id) == "123":
            return ch
        if str(ch_id) == "456":
            return log_ch
        return None

    bot.get_channel.side_effect = _get_channel
    db.set_config("match_channel_id", "123")
    db.set_config("log_channel_id", "456")
    cog = EventsCog(bot, db, get_teamup=lambda: None)

    payload = _make_raw_edit_payload(message_id=999, channel_id=123)
    await cog.on_raw_message_edit(payload)

    assert db.get_match(old_id) is None
    assert len(db.get_matches_for_date("2099-06-10")) == 1


async def test_on_raw_message_edit_ignores_non_match_channel(db):
    """Edits in a different channel are ignored."""
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.set_config("match_channel_id", "123")
    cog = _make_cog(db, _make_match_channel())

    payload = _make_raw_edit_payload(message_id=999, channel_id=999)  # wrong channel
    await cog.on_raw_message_edit(payload)

    assert db.get_match(old_id) is not None  # unchanged


async def test_on_raw_message_edit_ignores_bot_messages(db):
    """Bot-authored edits are ignored."""
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    bot_msg = _make_message(_valid_post_for_time(WEEK_WED_TS), is_bot=True)
    ch = AsyncMock()
    ch.fetch_message = AsyncMock(return_value=bot_msg)

    bot = MagicMock()
    bot.dispatch = MagicMock()
    bot.get_channel.return_value = ch
    db.set_config("match_channel_id", "123")
    cog = EventsCog(bot, db, get_teamup=lambda: None)

    payload = _make_raw_edit_payload(message_id=999, channel_id=123)
    await cog.on_raw_message_edit(payload)

    assert db.get_match(old_id) is not None  # unchanged


async def test_on_raw_message_edit_same_time_is_noop(db):
    """An edit that doesn't change the time is silently ignored."""
    old_id = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    edited_msg = _make_message(_valid_post_for_time(WEEK_MON_TS))  # same time
    ch = AsyncMock()
    ch.fetch_message = AsyncMock(return_value=edited_msg)

    bot = MagicMock()
    bot.dispatch = MagicMock()
    bot.get_channel.return_value = ch
    db.set_config("match_channel_id", "123")
    cog = EventsCog(bot, db, get_teamup=lambda: None)

    payload = _make_raw_edit_payload(message_id=999, channel_id=123)
    await cog.on_raw_message_edit(payload)

    assert db.get_match(old_id) is not None  # unchanged
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_events_cog.py -k "raw_message_edit" -v
```

Expected: FAIL (`AttributeError: 'EventsCog' object has no attribute 'on_raw_message_edit'`)

- [ ] **Step 3: Add `on_raw_message_edit` to `cogs/events.py`**

Add after `on_message`:

```python
    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        match_ch_id = self.db.get_config("match_channel_id")
        if not match_ch_id or str(payload.channel_id) != match_ch_id:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        if message.author.bot:
            return
        if not has_required_structure(message.content):
            return
        try:
            parsed = parse_post(message.content, self.db)
        except ParseError:
            return
        now_ts = int(datetime.now(tz=ET).timestamp())
        if parsed.match_time <= now_ts:
            return

        week_start, week_end = _week_bounds(parsed.match_time)
        old_match = self.db.get_match_by_teams_in_week(
            parsed.team_home, parsed.team_away, week_start, week_end
        )
        if not old_match:
            if not self.db.match_exists(parsed.team_home, parsed.team_away, parsed.match_time):
                self.db.insert_match(
                    division=parsed.division,
                    week=parsed.week,
                    team_home=parsed.team_home,
                    team_away=parsed.team_away,
                    match_time=parsed.match_time,
                    posted_at=int(message.created_at.timestamp()),
                )
                match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")
                self.bot.dispatch("match_logged", match_date)
            return
        if old_match["match_time"] == parsed.match_time:
            return
        await self._handle_reschedule(old_match, parsed)
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_events_cog.py -k "raw_message_edit" -v
```

Expected: all PASS

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add cogs/events.py tests/test_events_cog.py
git commit -m "feat: on_raw_message_edit detects rescheduled match posts"
```

---

## Task 4: Full `_handle_reschedule` — state-based logic + notifications

**Files:**
- Modify: `cogs/events.py`
- Modify: `cogs/reschedule.py` (just `send_thread_reschedule_notice` — full view in Task 5)

The four states:
- **State 4** (`broadcast_accepted = 1`): post `RescheduleView` to log channel, do NOT touch the match record
- **State 3** (has signups): cancel sign-up message, notify talent, delete old, insert new
- **States 1–2** (no signups): silently delete old, insert new
- All states: notify log channel with `@ManagerRole`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_events_cog.py`:

```python
async def test_handle_reschedule_clears_proposal_slot(db):
    """Rescheduling a match that is in a proposal slot clears the slot."""
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.create_proposal_message("2099-06-08",
                               int(datetime(2099, 6, 8, 0, 0, tzinfo=ET).timestamp()),
                               "2099-06-02")
    db.update_proposal_slots("2099-06-08", mid, None)

    cog = _make_cog(db, _make_match_channel(), AsyncMock())

    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    prop = db.get_proposal_message("2099-06-08")
    assert prop["slot1_match_id"] is None


async def test_handle_reschedule_sends_log_notification(db):
    """Log channel receives a reschedule notification."""
    log_ch = AsyncMock()
    db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)

    cog = _make_cog(db, _make_match_channel(), log_ch)
    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    log_ch.send.assert_called_once()
    call_text = log_ch.send.call_args[0][0]
    assert "Rescheduled" in call_text or "rescheduled" in call_text.lower()
    assert "Alpha" in call_text


async def test_handle_reschedule_state3_cancels_signup_message(db):
    """When the old match has signups, the sign-up message is edited to CANCELLED."""
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.upsert_signup(mid, "msg1", "pbp", "u1", "user1", "User One")
    db.insert_broadcast_message(mid, "msg_discord_111", "ch_signup_999")

    signup_msg = AsyncMock()
    signup_ch = AsyncMock()
    signup_ch.fetch_message = AsyncMock(return_value=signup_msg)
    log_ch = AsyncMock()

    bot = MagicMock()
    bot.dispatch = MagicMock()

    def _get_channel(ch_id):
        if str(ch_id) == "123":
            return MagicMock()
        if str(ch_id) == "456":
            return log_ch
        if str(ch_id) == "ch_signup_999":
            return signup_ch
        return None

    bot.get_channel.side_effect = _get_channel
    db.set_config("match_channel_id", "123")
    db.set_config("log_channel_id", "456")
    db.set_config("signup_channel_id", "ch_signup_999")
    cog = EventsCog(bot, db, get_teamup=lambda: None)

    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    signup_msg.edit.assert_called_once()
    edit_content = signup_msg.edit.call_args[1]["content"]
    assert "CANCELLED" in edit_content or "RESCHEDULED" in edit_content


async def test_handle_reschedule_state3_notifies_talent(db):
    """Talent who signed up receive a schedule-update notification."""
    mid = db.insert_match("Premier", "1", "Alpha", "Beta", WEEK_MON_TS, WEEK_MON_TS - 100)
    db.upsert_signup(mid, "msg1", "pbp", "u1", "user1", "User One")
    db.insert_broadcast_message(mid, "msg_discord_111", "ch_signup_999")

    updates_ch = AsyncMock()
    log_ch = AsyncMock()

    bot = MagicMock()
    bot.dispatch = MagicMock()

    def _get_channel(ch_id):
        if str(ch_id) == "123": return MagicMock()
        if str(ch_id) == "456": return log_ch
        if str(ch_id) == "ch_signup_999":
            signup_ch = AsyncMock()
            signup_ch.fetch_message = AsyncMock(return_value=AsyncMock())
            return signup_ch
        if str(ch_id) == "ch_updates_777": return updates_ch
        return None

    bot.get_channel.side_effect = _get_channel
    db.set_config("match_channel_id", "123")
    db.set_config("log_channel_id", "456")
    db.set_config("signup_channel_id", "ch_signup_999")
    db.set_config("schedule_updates_channel_id", "ch_updates_777")
    cog = EventsCog(bot, db, get_teamup=lambda: None)

    msg = _make_message(_valid_post_for_time(WEEK_WED_TS))
    msg.channel = MagicMock()
    msg.channel.id = 123
    await cog.on_message(msg)

    updates_ch.send.assert_called_once()
    call_text = updates_ch.send.call_args[0][0]
    assert "<@u1>" in call_text
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_events_cog.py -k "handle_reschedule or state3" -v
```

Expected: FAIL (log notification not sent, sign-up message not edited, talent not notified)

- [ ] **Step 3: Replace the stub `_handle_reschedule` with the full implementation**

Replace the stub in `cogs/events.py`:

```python
    async def _handle_reschedule(self, old_match: dict, parsed) -> None:
        """State-based reschedule handler. Does NOT insert the new match for State 4."""
        from cogs.reschedule import RescheduleView

        old_ts = old_match["match_time"]
        new_ts = parsed.match_time
        old_mid = old_match["id"]
        old_date = datetime.fromtimestamp(old_ts, tz=ET).strftime("%Y-%m-%d")
        new_date = datetime.fromtimestamp(new_ts, tz=ET).strftime("%Y-%m-%d")
        db = self.db
        log_ch = self._get_log_channel()
        manager_role_id = db.get_config("manager_role_id")
        manager_mention = f"<@&{manager_role_id}> " if manager_role_id else ""
        match_label = (
            f"[{old_match['division']}] "
            f"{old_match['team_home']} vs {old_match['team_away']}"
        )

        # State 4: confirmed broadcast — post action buttons, leave DB alone
        if old_match.get("broadcast_accepted"):
            if log_ch:
                view = RescheduleView(old_mid, old_ts, new_ts)
                await log_ch.send(
                    f"⚠️ **Match Time Changed — Action Required** {manager_mention}\n"
                    f"📋 {match_label}\n"
                    f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                    f"This match has a confirmed broadcast. Choose how to handle the time change:",
                    view=view,
                )
            return

        # States 1–3: pre-confirmed — always delete old, insert new
        signups = db.get_signups_for_match(old_mid)
        bcast = db.get_broadcast_message(old_mid)
        status_note = ""

        if signups and bcast:
            # State 3: cancel sign-up message and notify talent
            signup_ch_id = (db.get_config("signup_channel_id")
                            or db.get_config("broadcast_channel_id"))
            signup_ch = (self.bot.get_channel(int(signup_ch_id))
                         if signup_ch_id else None)
            if signup_ch:
                try:
                    signup_msg = await signup_ch.fetch_message(
                        int(bcast["discord_message_id"])
                    )
                    await signup_msg.edit(
                        content=(
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"❌ **CANCELLED — Match Rescheduled**\n"
                            f"📋 {match_label}\n"
                            f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                            f"This match has been rescheduled. "
                            f"A new sign-up will be posted once confirmed."
                        ),
                        view=discord.ui.View(),
                    )
                except Exception:
                    log.exception("Failed to edit sign-up message on reschedule for match %s", old_mid)

            all_uids = list({s["user_id"] for s in signups})
            mentions = " ".join(f"<@{uid}>" for uid in all_uids)
            updates_ch_id = db.get_config("schedule_updates_channel_id")
            updates_ch = (self.bot.get_channel(int(updates_ch_id))
                          if updates_ch_id else None) or signup_ch
            if updates_ch:
                try:
                    await updates_ch.send(
                        f"📢 **Schedule Update** — {match_label} has been rescheduled.\n"
                        f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                        f"{mentions} — your sign-up has been removed. "
                        f"A new sign-up will be posted if the slot is rescheduled."
                    )
                except Exception:
                    log.exception("Failed to send talent reschedule notification for match %s", old_mid)
            status_note = "Sign-up cancelled. Signed-up talent have been notified."
        elif db.get_proposal_message(old_date) and any(
            (p.get("slot1_match_id") == old_mid or p.get("slot2_match_id") == old_mid)
            for p in [db.get_proposal_message(old_date) or {}]
        ):
            status_note = "Removed from the broadcast schedule. Please reassign if needed."
        else:
            status_note = "Updated in Logged Matches."

        db.clear_match_from_proposal_slots(old_mid)
        db.delete_match_cascade(old_mid)
        new_mid = db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=new_ts,
            posted_at=int(datetime.now(tz=ET).timestamp()),
        )

        if log_ch:
            try:
                await log_ch.send(
                    f"⚠️ **Match Rescheduled** {manager_mention}\n"
                    f"📋 {match_label}\n"
                    f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n"
                    f"{status_note}"
                )
            except Exception:
                log.exception("Failed to send reschedule log notification for match %s", old_mid)

        self.bot.dispatch("match_logged", new_date)
        if old_date != new_date:
            self.bot.dispatch("match_logged", old_date)
```

- [ ] **Step 4: Create a minimal `cogs/reschedule.py` stub so the import doesn't fail**

```python
# cogs/reschedule.py
import discord
import logging

log = logging.getLogger(__name__)


class RescheduleView(discord.ui.View):
    """Posted to the log channel when a confirmed broadcast is rescheduled.
    Full implementation added in Task 5."""

    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/test_events_cog.py -k "handle_reschedule or state3" -v
```

Expected: all PASS

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add cogs/events.py cogs/reschedule.py tests/test_events_cog.py
git commit -m "feat: _handle_reschedule — state-based logic, sign-up cancellation, talent notification"
```

---

## Task 5: `RescheduleView` — confirmed-broadcast action buttons + thread notices

**Files:**
- Modify: `cogs/reschedule.py`
- Create: `tests/test_reschedule.py`

The three buttons all:
- Check manager permission (using `_manager_check` from `cogs/signup.py`)
- Disable all buttons and edit the view message to show which option was chosen
- Optionally send a thread notice via `send_thread_reschedule_notice`

`send_thread_reschedule_notice(bot, db, match_id, message_text)`:
- Looks up `thread_messages` for the match
- Fetches the thread channel
- Reconstructs mentions: `league_admin_role_id`, `team1_role_id`, `team2_role_id`, producer/observer user IDs from `talent_allocations`
- Sends `"{mentions}\n{message_text}"` to the thread

- [ ] **Step 1: Write failing tests**

Create `tests/test_reschedule.py`:

```python
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
    interaction.client.db = db
    interaction.client.get_teamup = MagicMock(return_value=MagicMock())
    interaction.client.get_channel = MagicMock(return_value=AsyncMock())
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
        "VALUES (?, ?, ?, ?)", (mid, "thread_111", "ch_222", 0)
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
    # should not raise
    await send_thread_reschedule_notice(bot, db, mid, "message")
    bot.get_channel.assert_not_called()


async def test_send_thread_notice_includes_role_mentions(db):
    from cogs.reschedule import send_thread_reschedule_notice

    mid = _make_match(db)
    db.conn.execute(
        "INSERT INTO thread_messages "
        "(match_id, thread_id, channel_id, team1_role_id, team2_role_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, "thread_111", "ch_222", "role_aaa", "role_bbb", 0)
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


# --- _InitiateSignUpButton ---

async def test_initiate_signup_button_updates_match_time(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    db.create_allocation(mid)
    db.insert_broadcast_message(mid, "msg_old_111", "ch_signup_999")

    signup_ch = AsyncMock()
    signup_ch.fetch_message = AsyncMock(return_value=AsyncMock())
    interaction = _make_interaction(db)
    interaction.client.get_channel.return_value = signup_ch
    db.set_config("signup_channel_id", "ch_signup_999")

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
    db.insert_broadcast_message(mid, "msg_old_111", "ch_signup_999")

    signup_ch = AsyncMock()
    signup_ch.fetch_message = AsyncMock(return_value=AsyncMock())
    interaction = _make_interaction(db)
    interaction.client.get_channel.return_value = signup_ch
    db.set_config("signup_channel_id", "ch_signup_999")

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
    db.set_config("broadcast_channel_id", "ch_bc_111")
    interaction.client.get_channel = MagicMock(return_value=AsyncMock())

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Cancel" in b.label)
    await btn.callback(interaction)

    teamup.delete_event.assert_called_once_with("evt_abc")


async def test_cancel_broadcast_button_denied_for_non_manager(db):
    from cogs.reschedule import RescheduleView

    mid = _make_match(db, ts=WEEK_MON_TS)
    interaction = _make_interaction(db, is_manager=False)

    view = RescheduleView(mid, WEEK_MON_TS, WEEK_WED_TS)
    btn = next(b for b in view.children if "Cancel" in b.label)
    await btn.callback(interaction)

    interaction.response.send_message.assert_called_once()
    assert "manager" in interaction.response.send_message.call_args[0][0].lower()
```

- [ ] **Step 2: Run to confirm they fail**

```
python -m pytest tests/test_reschedule.py -v
```

Expected: FAIL (`ImportError` or `AttributeError` — buttons not yet implemented)

- [ ] **Step 3: Implement full `cogs/reschedule.py`**

```python
# cogs/reschedule.py
import discord
import logging
import json
from scheduler import _SEPARATOR, match_end_ts, build_signup_message, build_approved_signup_message

log = logging.getLogger(__name__)

_THREAD_MSG_UPDATE = (
    "The updated match time has been approved by the broadcast team."
)
_THREAD_MSG_INITIATE = (
    "A new match time has been detected, the broadcast team are checking for availability. "
    "If approved a new match thread will be created."
)
_THREAD_MSG_CANCEL = (
    "A new match time has been detected, the broadcast team has decided to cancel this stream."
)


async def send_thread_reschedule_notice(bot, db, match_id: int, message_text: str) -> None:
    """Send a notice to the match thread with reconstructed mentions."""
    thread_row = db.get_thread_message(match_id)
    if not thread_row:
        return
    thread = bot.get_channel(int(thread_row["thread_id"]))
    if not thread:
        return

    mentions = []
    league_admin_role_id = db.get_config("league_admin_role_id")
    if league_admin_role_id:
        mentions.append(f"<@&{league_admin_role_id}>")
    if thread_row.get("team1_role_id"):
        mentions.append(f"<@&{thread_row['team1_role_id']}>")
    if thread_row.get("team2_role_id"):
        mentions.append(f"<@&{thread_row['team2_role_id']}>")

    alloc = db.get_allocation(match_id)
    if alloc and alloc.get("role_assignments"):
        assignments = alloc["role_assignments"]
        if isinstance(assignments, str):
            assignments = json.loads(assignments)
        seen = set()
        for role in ("producer", "observer"):
            uid = (assignments.get(role) or {}).get("user_id")
            if uid and uid not in seen:
                mentions.append(f"<@{uid}>")
                seen.add(uid)

    mention_str = " ".join(mentions)
    content = f"{mention_str}\n{message_text}" if mention_str else message_text
    try:
        await thread.send(content)
    except Exception as e:
        log.warning("Failed to send reschedule thread notice for match %s: %s", match_id, e)


def _is_manager(interaction: discord.Interaction, db) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    if db.is_manager(str(interaction.user.id)):
        return True
    role_id = db.get_config("manager_role_id")
    if role_id:
        return any(str(r.id) == role_id for r in interaction.user.roles)
    return False


def _disable_view(view: discord.ui.View) -> None:
    for child in view.children:
        child.disabled = True


class _UpdateBroadcastButton(discord.ui.Button):
    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(
            label="⚙️ Update Broadcast",
            style=discord.ButtonStyle.primary,
            custom_id=f"reschedule_update_{match_id}",
            row=0,
        )
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can use this button.", ephemeral=True
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message(
                "Match not found in database.", ephemeral=True
            )
            return

        teamup = interaction.client.get_teamup()
        event_id = match.get("teamup_event_id")

        db.update_match_time(self.match_id, self.new_ts)

        if teamup and event_id:
            try:
                title = (
                    f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
                    f" {{{self.match_id}}}"
                )
                teamup.update_event(
                    event_id, title,
                    self.new_ts, match_end_ts(self.new_ts),
                    subcalendar="accepted",
                )
            except Exception as e:
                log.warning("Failed to update TeamUp event for match %s: %s", self.match_id, e)

        fresh_match = db.get_match(self.match_id)

        signup_ch_id = (db.get_config("signup_channel_id")
                        or db.get_config("broadcast_channel_id"))
        signup_ch = (interaction.client.get_channel(int(signup_ch_id))
                     if signup_ch_id else None)
        bcast = db.get_broadcast_message(self.match_id)
        if bcast and signup_ch:
            alloc = db.get_allocation(self.match_id)
            role_assignments = {}
            if alloc and alloc.get("role_assignments"):
                ra = alloc["role_assignments"]
                role_assignments = json.loads(ra) if isinstance(ra, str) else ra
            try:
                signup_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                await signup_msg.edit(
                    content=build_approved_signup_message(fresh_match, role_assignments),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("Failed to edit sign-up message on Update Broadcast for match %s: %s",
                            self.match_id, e)

        alloc = db.get_allocation(self.match_id)
        if alloc and alloc.get("role_assignments"):
            ra = alloc["role_assignments"]
            assignments = json.loads(ra) if isinstance(ra, str) else ra
            all_uids = list({v["user_id"] for v in assignments.values() if isinstance(v, dict) and v.get("user_id")})
            if all_uids:
                mentions = " ".join(f"<@{uid}>" for uid in all_uids)
                updates_ch_id = db.get_config("schedule_updates_channel_id")
                updates_ch = (interaction.client.get_channel(int(updates_ch_id))
                              if updates_ch_id else None)
                if updates_ch:
                    match_label = (f"[{match['division']}] "
                                   f"{match['team_home']} vs {match['team_away']}")
                    try:
                        await updates_ch.send(
                            f"📢 **Schedule Update** — {match_label}\n"
                            f"~~<t:{self.old_ts}:F>~~ → <t:{self.new_ts}:F>\n\n"
                            f"{mentions} — the match time has been updated."
                        )
                    except Exception as e:
                        log.warning("Failed to send talent update notification: %s", e)

        await send_thread_reschedule_notice(
            interaction.client, db, self.match_id, _THREAD_MSG_UPDATE
        )

        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n✅ **Broadcast time updated.**",
            view=self.view,
        )


class _InitiateSignUpButton(discord.ui.Button):
    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(
            label="🔄 Initiate Sign Up",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reschedule_initiate_{match_id}",
            row=0,
        )
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can use this button.", ephemeral=True
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message(
                "Match not found in database.", ephemeral=True
            )
            return

        db.update_match_time(self.match_id, self.new_ts)
        fresh_match = db.get_match(self.match_id)

        teamup = interaction.client.get_teamup()
        event_id = match.get("teamup_event_id")
        if teamup and event_id:
            try:
                title = (
                    f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
                    f" {{{self.match_id}}}"
                )
                teamup.update_event(
                    event_id, title,
                    self.new_ts, match_end_ts(self.new_ts),
                    subcalendar="proposed",
                )
            except Exception as e:
                log.warning("Failed to update TeamUp event for Initiate Sign Up match %s: %s",
                            self.match_id, e)

        signup_ch_id = (db.get_config("signup_channel_id")
                        or db.get_config("broadcast_channel_id"))
        signup_ch = (interaction.client.get_channel(int(signup_ch_id))
                     if signup_ch_id else None)
        bcast = db.get_broadcast_message(self.match_id)

        alloc = db.get_allocation(self.match_id)
        all_uids = []
        if alloc and alloc.get("role_assignments"):
            ra = alloc["role_assignments"]
            assignments = json.loads(ra) if isinstance(ra, str) else ra
            all_uids = list({v["user_id"] for v in assignments.values()
                             if isinstance(v, dict) and v.get("user_id")})

        if bcast and signup_ch:
            try:
                signup_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                await signup_msg.edit(
                    content=(
                        f"{_SEPARATOR}\n"
                        f"🔄 **RESCHEDULED**\n"
                        f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}\n"
                        f"~~<t:{self.old_ts}:F>~~\n\n"
                        f"This match has been rescheduled. A new sign-up has been posted."
                    ),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("Failed to edit old sign-up message on Initiate Sign Up for match %s: %s",
                            self.match_id, e)

        db.reset_allocation(self.match_id)

        if signup_ch:
            from cogs.signup import SignUpView
            signups = db.get_signups_for_match(self.match_id)
            talent_role_id = db.get_config("talent_role_id")
            talent_mention = f"<@&{talent_role_id}>" if talent_role_id else ""
            try:
                new_msg = await signup_ch.send(
                    build_signup_message(fresh_match, signups,
                                        talent_role_mention=talent_mention),
                    view=SignUpView(self.match_id),
                )
                db.insert_broadcast_message(self.match_id, str(new_msg.id), str(signup_ch.id))
            except Exception as e:
                log.warning("Failed to post new sign-up message for rescheduled match %s: %s",
                            self.match_id, e)

        new_date = __import__("datetime").datetime.fromtimestamp(
            self.new_ts,
            tz=__import__("zoneinfo").ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d")
        interaction.client.dispatch("match_logged", new_date)

        if all_uids:
            mentions = " ".join(f"<@{uid}>" for uid in all_uids)
            updates_ch_id = db.get_config("schedule_updates_channel_id")
            updates_ch = (interaction.client.get_channel(int(updates_ch_id))
                          if updates_ch_id else None) or signup_ch
            if updates_ch:
                match_label = (f"[{match['division']}] "
                               f"{match['team_home']} vs {match['team_away']}")
                try:
                    await updates_ch.send(
                        f"📢 **Schedule Update** — {match_label} has been rescheduled.\n"
                        f"~~<t:{self.old_ts}:F>~~ → <t:{self.new_ts}:F>\n\n"
                        f"{mentions} — your allocation has been reset. "
                        f"A new sign-up has been posted."
                    )
                except Exception as e:
                    log.warning("Failed to send talent reschedule notification: %s", e)

        await send_thread_reschedule_notice(
            interaction.client, db, self.match_id, _THREAD_MSG_INITIATE
        )

        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n🔄 **New sign-up posted.**",
            view=self.view,
        )


class _CancelBroadcastButton(discord.ui.Button):
    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(
            label="❌ Cancel Broadcast",
            style=discord.ButtonStyle.danger,
            custom_id=f"reschedule_cancel_{match_id}",
            row=0,
        )
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can use this button.", ephemeral=True
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message(
                "Match not found in database.", ephemeral=True
            )
            return

        teamup = interaction.client.get_teamup()
        event_id = match.get("teamup_event_id")
        if teamup and event_id:
            try:
                teamup.delete_event(event_id)
            except Exception as e:
                log.warning("Failed to delete TeamUp event %s on cancel: %s", event_id, e)
            db.update_match_teamup_id(self.match_id, None)
            db.decrement_scheduled_count(match["team_home"])
            db.decrement_scheduled_count(match["team_away"])

        db.reset_allocation(self.match_id)

        signups = db.get_signups_for_match(self.match_id)
        all_uids = list({s["user_id"] for s in signups})
        mentions = " ".join(f"<@{uid}>" for uid in all_uids)

        signup_ch_id = (db.get_config("signup_channel_id")
                        or db.get_config("broadcast_channel_id"))
        signup_ch = (interaction.client.get_channel(int(signup_ch_id))
                     if signup_ch_id else None)
        bcast = db.get_broadcast_message(self.match_id)
        ts = self.old_ts

        if bcast and signup_ch:
            try:
                signup_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                await signup_msg.edit(
                    content=(
                        f"{_SEPARATOR}\n"
                        f"❌ **BROADCAST CANCELLED**\n"
                        f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}\n"
                        f"<t:{ts}:F>\n\n"
                        f"This broadcast has been cancelled by management."
                    ),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("Failed to edit sign-up message on cancel for match %s: %s",
                            self.match_id, e)

        cancel_text = (
            f"{_SEPARATOR}\n"
            f"🚫 **Broadcast Cancelled**\n"
            f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** "
            f"| <t:{ts}:F>\n\n"
            f"This broadcast has been cancelled by management."
        )
        if mentions:
            cancel_text += f"\n{mentions}"

        updates_ch_id = db.get_config("schedule_updates_channel_id")
        updates_ch = (interaction.client.get_channel(int(updates_ch_id))
                      if updates_ch_id else None) or signup_ch
        if updates_ch:
            try:
                await updates_ch.send(cancel_text)
            except Exception as e:
                log.warning("Failed to send cancel notification for match %s: %s", self.match_id, e)

        await send_thread_reschedule_notice(
            interaction.client, db, self.match_id, _THREAD_MSG_CANCEL
        )

        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n❌ **Broadcast cancelled.**",
            view=self.view,
        )


class RescheduleView(discord.ui.View):
    """Posted to the log channel when a confirmed broadcast is rescheduled.
    Not persistent — one-shot per reschedule event."""

    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts
        self.add_item(_UpdateBroadcastButton(match_id, old_ts, new_ts))
        self.add_item(_InitiateSignUpButton(match_id, old_ts, new_ts))
        self.add_item(_CancelBroadcastButton(match_id, old_ts, new_ts))
```

- [ ] **Step 4: Fix the `__import__` anti-pattern in `_InitiateSignUpButton`**

The `__import__` call is an anti-pattern. Add proper imports at the top of `cogs/reschedule.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
```

Then replace the `__import__` lines in `_InitiateSignUpButton.callback`:

```python
        new_date = datetime.fromtimestamp(self.new_ts, tz=_ET).strftime("%Y-%m-%d")
        interaction.client.dispatch("match_logged", new_date)
```

- [ ] **Step 5: Run the reschedule tests**

```
python -m pytest tests/test_reschedule.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add cogs/reschedule.py tests/test_reschedule.py
git commit -m "feat: RescheduleView — Update Broadcast, Initiate Sign Up, Cancel Broadcast buttons with thread notices"
```

---

## Task 6: Final wiring and regression check

**Files:**
- Verify: `cogs/events.py` imports are correct
- Verify: no circular imports

- [ ] **Step 1: Verify `cogs/reschedule.py` import in `events.py` is guarded**

The import `from cogs.reschedule import RescheduleView` in `_handle_reschedule` is already a local (inside-function) import to avoid circular dependency. Confirm this is the case in the implementation — the import should be inside the method body, not at the top of the file.

- [ ] **Step 2: Run the full test suite one final time**

```
python -m pytest tests/ -v
```

Expected: all PASS with no warnings about missing imports or circular dependencies.

- [ ] **Step 3: Final commit**

```bash
git add cogs/events.py cogs/reschedule.py database.py tests/
git commit -m "feat: match reschedule detection — full implementation complete"
```
