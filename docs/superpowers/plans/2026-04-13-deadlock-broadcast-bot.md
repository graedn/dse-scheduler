# Deadlock Broadcast Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python Discord bot that monitors a public server for Deadlock league match posts, selects 2–3 matches per day for broadcast using a scoring engine, and manages a TeamUp calendar with draft approval flow in a private admin server.

**Architecture:** A single discord.py bot process connects to two Discord servers simultaneously. A SQLite database stores all state. An APScheduler cron triggers a daily 3am sweep. All scheduling logic lives in pure functions for testability.

**Tech Stack:** Python 3.11+, discord.py 2.x, APScheduler 3.x, requests, python-dotenv, pytest, pytest-asyncio

---

## File Structure

```
c:/Discord Bot Project/
├── bot.py                  # Entry point: creates bot, wires cogs, starts scheduler
├── database.py             # All SQLite CRUD — one class, every table
├── parser.py               # Post parsing pipeline: structure check + fuzzy matching
├── scheduler.py            # Scoring engine, combination logic, sweep + proposal helpers
├── teamup.py               # TeamUp HTTP client
├── cogs/
│   ├── __init__.py
│   ├── admin.py            # /set-*, /unset-*, /status, /reset, /broadcast-done
│   ├── blocks.py           # /block-day, /unblock-day, /list-blocks
│   └── events.py           # on_message, on_guild_channel_delete, on_raw_reaction_add
├── tests/
│   ├── __init__.py
│   ├── test_database.py
│   ├── test_parser.py
│   ├── test_scheduler.py
│   └── test_teamup.py
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `cogs/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
discord.py>=2.3.0
apscheduler>=3.10.0
requests>=2.31.0
python-dotenv>=1.0.0
pytest>=7.4.0
pytest-asyncio>=0.21.0
```

- [ ] **Step 2: Create `.env.example`**

```
DISCORD_BOT_TOKEN=your_token_here
```

- [ ] **Step 3: Create `.gitignore`**

```
.env
bot.db
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: Create empty `cogs/__init__.py` and `tests/__init__.py`**

Both files are empty. Just create them so Python treats the directories as packages.

- [ ] **Step 5: Install dependencies**

Run: `pip install -r requirements.txt`

Expected: All packages install without errors.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore cogs/__init__.py tests/__init__.py
git commit -m "feat: project setup and dependencies"
```

---

## Task 2: Database Layer

**Files:**
- Create: `database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_database.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database.py -v`

Expected: `ImportError: No module named 'database'`

- [ ] **Step 3: Create `database.py`**

```python
import sqlite3
import json
import time
from typing import Optional


class Database:
    def __init__(self, db_path: str = "bot.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division TEXT NOT NULL,
                week TEXT NOT NULL,
                team_home TEXT NOT NULL,
                team_away TEXT NOT NULL,
                match_time INTEGER NOT NULL,
                posted_at INTEGER NOT NULL,
                teamup_event_id TEXT,
                broadcast_done INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS teams (
                name TEXT PRIMARY KEY,
                aliases TEXT NOT NULL DEFAULT '[]',
                scheduled_count INTEGER NOT NULL DEFAULT 0,
                broadcast_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pending_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposed_at INTEGER NOT NULL,
                auto_approve_at INTEGER NOT NULL,
                description TEXT NOT NULL,
                old_event_ids TEXT NOT NULL DEFAULT '[]',
                new_match_ids TEXT NOT NULL DEFAULT '[]',
                approved INTEGER,
                discord_message_id TEXT
            );
            CREATE TABLE IF NOT EXISTS blocked_days (
                date TEXT PRIMARY KEY,
                reason TEXT,
                teamup_event_id TEXT
            );
        """)
        self.conn.commit()

    # --- Config ---

    def get_config(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_config(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def delete_config(self, key: str):
        self.conn.execute("DELETE FROM config WHERE key = ?", (key,))
        self.conn.commit()

    # --- Matches ---

    def insert_match(self, division: str, week: str, team_home: str, team_away: str,
                     match_time: int, posted_at: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO matches (division, week, team_home, team_away, match_time, posted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (division, week, team_home, team_away, match_time, posted_at)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_match(self, match_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_matches_for_date(self, date_str: str) -> list[dict]:
        """All matches whose match_time falls on date_str (YYYY-MM-DD) in ET."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ?",
            (int(day_start.timestamp()), int(day_end.timestamp()))
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scheduled_matches_for_date(self, date_str: str) -> list[dict]:
        """Matches on date_str that have a TeamUp event ID (i.e., on the calendar)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ? "
            "AND teamup_event_id IS NOT NULL",
            (int(day_start.timestamp()), int(day_end.timestamp()))
        ).fetchall()
        return [dict(r) for r in rows]

    def update_match_teamup_id(self, match_id: int, event_id: Optional[str]):
        self.conn.execute(
            "UPDATE matches SET teamup_event_id = ? WHERE id = ?", (event_id, match_id)
        )
        self.conn.commit()

    def mark_broadcast_done(self, match_id: int):
        self.conn.execute(
            "UPDATE matches SET broadcast_done = 1 WHERE id = ?", (match_id,)
        )
        self.conn.commit()

    # --- Teams ---

    def get_team(self, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM teams WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_teams(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM teams").fetchall()
        return [dict(r) for r in rows]

    def upsert_team(self, name: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO teams (name, aliases, scheduled_count, broadcast_count) "
            "VALUES (?, '[]', 0, 0)",
            (name,)
        )
        self.conn.commit()

    def add_team_alias(self, canonical_name: str, alias: str):
        row = self.conn.execute(
            "SELECT aliases FROM teams WHERE name = ?", (canonical_name,)
        ).fetchone()
        if not row:
            return
        aliases = json.loads(row["aliases"])
        if alias not in aliases:
            aliases.append(alias)
            self.conn.execute(
                "UPDATE teams SET aliases = ? WHERE name = ?",
                (json.dumps(aliases), canonical_name)
            )
            self.conn.commit()

    def increment_scheduled_count(self, team_name: str):
        self.conn.execute(
            "UPDATE teams SET scheduled_count = scheduled_count + 1 WHERE name = ?",
            (team_name,)
        )
        self.conn.commit()

    def increment_broadcast_count(self, team_name: str):
        self.conn.execute(
            "UPDATE teams SET broadcast_count = broadcast_count + 1 WHERE name = ?",
            (team_name,)
        )
        self.conn.commit()

    def decrement_scheduled_count(self, team_name: str):
        self.conn.execute(
            "UPDATE teams SET scheduled_count = MAX(0, scheduled_count - 1) WHERE name = ?",
            (team_name,)
        )
        self.conn.commit()

    # --- Pending Changes ---

    def insert_pending_change(self, description: str, old_event_ids: list[str],
                               new_match_ids: list[int],
                               discord_message_id: Optional[str] = None) -> int:
        now = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO pending_changes "
            "(proposed_at, auto_approve_at, description, old_event_ids, new_match_ids, discord_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, now + 43200, description,
             json.dumps(old_event_ids), json.dumps(new_match_ids), discord_message_id)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_pending_change(self, change_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM pending_changes WHERE id = ?", (change_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_pending_change_by_message(self, discord_message_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM pending_changes WHERE discord_message_id = ? AND approved IS NULL",
            (discord_message_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_expired_pending_changes(self) -> list[dict]:
        now = int(time.time())
        rows = self.conn.execute(
            "SELECT * FROM pending_changes WHERE approved IS NULL AND auto_approve_at <= ?",
            (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_pending_change(self, change_id: int, approved: bool):
        self.conn.execute(
            "UPDATE pending_changes SET approved = ? WHERE id = ?",
            (1 if approved else 0, change_id)
        )
        self.conn.commit()

    # --- Blocked Days ---

    def insert_blocked_day(self, date: str, reason: Optional[str],
                            teamup_event_id: Optional[str]):
        self.conn.execute(
            "INSERT OR REPLACE INTO blocked_days (date, reason, teamup_event_id) VALUES (?, ?, ?)",
            (date, reason, teamup_event_id)
        )
        self.conn.commit()

    def get_blocked_day(self, date: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM blocked_days WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def delete_blocked_day(self, date: str):
        self.conn.execute("DELETE FROM blocked_days WHERE date = ?", (date,))
        self.conn.commit()

    def get_all_blocked_days(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM blocked_days ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Reset ---

    def reset_all(self):
        self.conn.executescript("""
            DELETE FROM config;
            DELETE FROM matches;
            DELETE FROM teams;
            DELETE FROM pending_changes;
            DELETE FROM blocked_days;
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py tests/__init__.py
git commit -m "feat: database layer with full CRUD and tests"
```

---

## Task 3: Post Parser

**Files:**
- Create: `parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parser.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`

Expected: `ImportError: No module named 'parser'`

- [ ] **Step 3: Create `parser.py`**

```python
import re
import difflib
import json
from dataclasses import dataclass
from typing import Optional

KNOWN_DIVISIONS = ["Premier", "Division 1", "Division 2", "Division 3", "Division 4"]
FUZZY_THRESHOLD = 0.8


@dataclass
class ParsedMatch:
    division: str
    week: str
    team_home: str
    team_away: str
    match_time: int  # Unix timestamp UTC


class ParseError(Exception):
    pass


def has_required_structure(text: str) -> bool:
    lines = text.strip().split("\n")
    has_division = any(re.match(r"division\s*:", l, re.IGNORECASE) for l in lines)
    has_week = any(re.match(r"(week|round)\s*:", l, re.IGNORECASE) for l in lines)
    has_vs = any(re.search(r"\bvs\b", l, re.IGNORECASE) for l in lines)
    has_time = any(
        re.search(r"time\s*:.*<t:\d+", l, re.IGNORECASE) for l in lines
    )
    return all([has_division, has_week, has_vs, has_time])


def parse_division(text: str) -> str:
    for line in text.split("\n"):
        m = re.match(r"division\s*:\s*(.+)", line.strip(), re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            matches = difflib.get_close_matches(
                raw, KNOWN_DIVISIONS, n=1, cutoff=FUZZY_THRESHOLD
            )
            if matches:
                return matches[0]
            raise ParseError(f"Could not identify division from: '{raw}'")
    raise ParseError("No division line found")


def parse_week(text: str) -> str:
    for line in text.split("\n"):
        m = re.match(r"(week|round)\s*:\s*(.+)", line.strip(), re.IGNORECASE)
        if m:
            label = m.group(1).capitalize()
            raw = m.group(2).strip()
            num_m = re.search(r"\d+", raw)
            if num_m:
                return f"{label} {num_m.group(0)}"
            raise ParseError(f"Could not extract number from '{raw}'")
    raise ParseError("No week/round line found")


def parse_timestamp(text: str) -> int:
    for line in text.split("\n"):
        if re.match(r"time\s*:", line.strip(), re.IGNORECASE):
            m = re.search(r"<t:(\d+)(?::[a-zA-Z])?>", line)
            if m:
                return int(m.group(1))
            raise ParseError("Time line present but no valid Discord timestamp tag found")
    raise ParseError("No time line found")


def resolve_team_name(raw_name: str, db) -> str:
    """
    Fuzzy-match raw_name against existing team canonical names and aliases.
    Returns the canonical name. Creates a new team if no match found.
    Records the raw_name as an alias if it differs from canonical.
    """
    raw_clean = raw_name.strip()
    teams = db.get_all_teams()

    # Build lookup: candidate string -> canonical name
    candidates: dict[str, str] = {}
    for team in teams:
        candidates[team["name"]] = team["name"]
        for alias in json.loads(team["aliases"]):
            candidates[alias] = team["name"]

    if candidates:
        close = difflib.get_close_matches(
            raw_clean, list(candidates.keys()), n=1, cutoff=FUZZY_THRESHOLD
        )
        if close:
            canonical = candidates[close[0]]
            if raw_clean.lower() != canonical.lower() and raw_clean not in candidates:
                db.add_team_alias(canonical, raw_clean)
            return canonical

    # No match — new team
    canonical = raw_clean.title()
    db.upsert_team(canonical)
    return canonical


def parse_teams(text: str, db) -> tuple[str, str]:
    field_pattern = re.compile(r"^(division|week|round|time)\s*:", re.IGNORECASE)
    for line in text.split("\n"):
        if field_pattern.match(line.strip()):
            continue
        m = re.search(r"(.+?)\s+vs\s+(.+)", line.strip(), re.IGNORECASE)
        if m:
            raw_home = m.group(1).strip()
            raw_away = m.group(2).strip()
            home = resolve_team_name(raw_home, db)
            away = resolve_team_name(raw_away, db)
            return home, away
    raise ParseError("No 'vs' line found for team names")


def parse_post(text: str, db) -> ParsedMatch:
    """Full parsing pipeline. Raises ParseError on any field failure."""
    division = parse_division(text)
    week = parse_week(text)
    team_home, team_away = parse_teams(text, db)
    match_time = parse_timestamp(text)
    return ParsedMatch(
        division=division,
        week=week,
        team_home=team_home,
        team_away=team_away,
        match_time=match_time,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parser.py tests/test_parser.py
git commit -m "feat: post parser with fuzzy matching and team name learning"
```

---

## Task 4: Scheduling Engine

**Files:**
- Create: `scheduler.py`
- Create: `tests/test_scheduler.py`

Timestamps used in tests (all Saturday 2024-04-20 in ET):
- 6pm ET = 1713650400
- 7pm ET = 1713654000
- 8pm ET = 1713657600
- 9pm ET = 1713661200
- 10pm ET = 1713664800

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler.py`:

```python
import pytest
from unittest.mock import MagicMock
from scheduler import (
    get_et_hour, is_weekend, are_consecutive, has_overlap,
    generate_combinations, score_combination, best_combination,
    combo_match_ids, build_proposal_message,
)

# Saturday 2024-04-20 timestamps in ET
TS_6PM  = 1713650400
TS_7PM  = 1713654000
TS_8PM  = 1713657600
TS_9PM  = 1713661200
TS_10PM = 1713664800

# Weekday: Tuesday 2024-04-16 8pm ET
TS_WD_8PM = 1713312000
TS_WD_7PM = 1713308400
TS_WD_9PM = 1713315600
TS_WD_6PM = 1713304800


def make_match(ts: int, match_id: int = 1,
               home: str = "Team A", away: str = "Team B",
               division: str = "Premier") -> dict:
    return {
        "id": match_id,
        "match_time": ts,
        "team_home": home,
        "team_away": away,
        "division": division,
        "week": "Week 1",
    }


def make_db(teams: dict = None) -> MagicMock:
    """teams = {name: (scheduled_count, broadcast_count)}"""
    db = MagicMock()
    teams = teams or {}
    def get_team(name):
        if name in teams:
            sc, bc = teams[name]
            return {"name": name, "scheduled_count": sc, "broadcast_count": bc}
        return None
    db.get_team.side_effect = get_team
    return db


# --- Time helpers ---

def test_get_et_hour_8pm():
    assert get_et_hour(TS_8PM) == pytest.approx(20.0, abs=0.1)


def test_is_weekend_saturday():
    assert is_weekend(TS_8PM) is True


def test_is_weekend_tuesday():
    assert is_weekend(TS_WD_8PM) is False


# --- Consecutive / overlap ---

def test_are_consecutive_7pm_9pm():
    assert are_consecutive(TS_7PM, TS_9PM) is True


def test_are_consecutive_8pm_10pm():
    assert are_consecutive(TS_8PM, TS_10PM) is True


def test_are_consecutive_too_close():
    assert are_consecutive(TS_8PM, TS_8PM + 3000) is False  # ~50 min gap


def test_are_consecutive_too_far():
    assert are_consecutive(TS_6PM, TS_10PM) is False  # 4h gap


def test_has_overlap_true():
    assert has_overlap(TS_8PM, TS_8PM + 3000) is True  # 50 min gap


def test_has_overlap_false():
    assert has_overlap(TS_7PM, TS_9PM) is False  # 2h gap is not an overlap


# --- Combinations ---

def test_generate_combinations_2_matches():
    matches = [make_match(TS_7PM, 1), make_match(TS_9PM, 2)]
    combos = generate_combinations(matches)
    assert len(combos) == 1
    assert len(combos[0]) == 2


def test_generate_combinations_excludes_overlapping():
    # 8pm and 8:30pm overlap (30 min gap < 1.5h)
    matches = [make_match(TS_8PM, 1), make_match(TS_8PM + 1800, 2)]
    combos = generate_combinations(matches)
    assert combos == []


def test_generate_combinations_max_3():
    matches = [
        make_match(TS_6PM, 1), make_match(TS_8PM, 2),
        make_match(TS_10PM, 3), make_match(TS_7PM, 4),
    ]
    combos = generate_combinations(matches)
    assert all(len(c) <= 3 for c in combos)


def test_generate_combinations_only_1_match_returns_empty():
    combos = generate_combinations([make_match(TS_8PM, 1)])
    assert combos == []


# --- Scoring weekday ---

def test_score_weekday_solo_8pm():
    db = make_db()
    combo = [make_match(TS_WD_8PM, 1)]
    score = score_combination(combo, weekend=False, db=db)
    assert score == 100


def test_score_weekday_solo_6pm():
    db = make_db()
    combo = [make_match(TS_WD_6PM, 1)]
    score = score_combination(combo, weekend=False, db=db)
    assert score == 30


def test_score_weekday_consecutive_7_9pm_beats_solo_8pm():
    db = make_db()
    combo_consecutive = [make_match(TS_WD_7PM, 1), make_match(TS_WD_9PM, 2)]
    combo_8pm = [make_match(TS_WD_8PM, 3)]
    assert score_combination(combo_consecutive, weekend=False, db=db) > \
           score_combination(combo_8pm, weekend=False, db=db)


def test_score_weekday_team_penalty():
    db = make_db({"Team A": (2, 0), "Team B": (1, 0)})
    combo = [make_match(TS_WD_8PM, 1, home="Team A", away="Team B")]
    score = score_combination(combo, weekend=False, db=db)
    # 100 (8pm) - 10*2 (Team A) - 10*1 (Team B) = 70
    assert score == 70


# --- Scoring weekend ---

def test_score_weekend_consecutive_beats_8pm():
    db = make_db()
    combo_pair = [make_match(TS_7PM, 1), make_match(TS_9PM, 2)]
    combo_8pm = [make_match(TS_8PM, 3)]
    assert score_combination(combo_pair, weekend=True, db=db) > \
           score_combination(combo_8pm, weekend=True, db=db)


def test_score_weekend_solo_8pm():
    db = make_db()
    combo = [make_match(TS_8PM, 1)]
    assert score_combination(combo, weekend=True, db=db) == 50


# --- Best combination ---

def test_best_combination_picks_highest_score():
    db = make_db()
    matches = [
        make_match(TS_WD_7PM, 1, home="Team A", away="Team B"),
        make_match(TS_WD_9PM, 2, home="Team C", away="Team D"),
        make_match(TS_WD_6PM, 3, home="Team E", away="Team F"),
    ]
    best = best_combination(matches, db)
    # 7pm+9pm consecutive should beat any solo match
    ids = combo_match_ids(best)
    assert 1 in ids and 2 in ids


def test_best_combination_none_when_fewer_than_2():
    db = make_db()
    assert best_combination([make_match(TS_8PM, 1)], db) is None


def test_best_combination_tiebreak_by_team_counts():
    # Two solo matches at 8pm — pick team with lower count
    db = make_db({"Team A": (3, 0), "Team B": (3, 0), "Team C": (0, 0), "Team D": (0, 0)})
    matches = [
        make_match(TS_WD_8PM, 1, home="Team A", away="Team B"),
        make_match(TS_WD_8PM + 7200, 2, home="Team C", away="Team D"),
    ]
    best = best_combination(matches, db)
    # Both combos have same time score but match 2 has lower team counts
    assert combo_match_ids(best) == [1, 2]  # Both are in the best 2-match combo
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`

Expected: `ImportError: No module named 'scheduler'`

- [ ] **Step 3: Create `scheduler.py`**

```python
from itertools import combinations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import json

ET = ZoneInfo("America/New_York")

MATCH_DURATION_H = 2.0
CONSECUTIVE_MIN_H = 1.5
CONSECUTIVE_MAX_H = 2.5
PRIME_HOUR_ET = 20          # 8pm
SECONDARY_HOURS_ET = [18, 22]  # 6pm, 10pm
TIME_TOLERANCE_H = 15 / 60  # 15 minutes
MAX_MATCHES_PER_DAY = 3


# --- Time helpers ---

def get_et_hour(unix_ts: int) -> float:
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    return dt.hour + dt.minute / 60.0


def is_weekend(unix_ts: int) -> bool:
    return datetime.fromtimestamp(unix_ts, tz=ET).weekday() >= 5


# --- Pair logic ---

def are_consecutive(ts1: int, ts2: int) -> bool:
    gap_h = (ts2 - ts1) / 3600.0
    return CONSECUTIVE_MIN_H <= gap_h <= CONSECUTIVE_MAX_H


def has_overlap(ts1: int, ts2: int) -> bool:
    gap_h = (ts2 - ts1) / 3600.0
    return gap_h < CONSECUTIVE_MIN_H


# --- Combination generation ---

def generate_combinations(matches: list[dict]) -> list[list[dict]]:
    """All valid non-overlapping 2–3 match combinations."""
    valid = []
    for size in range(2, min(len(matches) + 1, MAX_MATCHES_PER_DAY + 1)):
        for combo in combinations(matches, size):
            sorted_combo = sorted(combo, key=lambda m: m["match_time"])
            overlapping = any(
                has_overlap(sorted_combo[i]["match_time"], sorted_combo[i + 1]["match_time"])
                for i in range(len(sorted_combo) - 1)
            )
            if not overlapping:
                valid.append(list(sorted_combo))
    return valid


# --- Scoring ---

def score_combination(combo: list[dict], weekend: bool, db) -> int:
    sorted_combo = sorted(combo, key=lambda m: m["match_time"])
    score = 0

    for match in sorted_combo:
        hour = get_et_hour(match["match_time"])
        if weekend:
            if abs(hour - PRIME_HOUR_ET) <= TIME_TOLERANCE_H:
                score += 50
            elif any(abs(hour - h) <= TIME_TOLERANCE_H for h in SECONDARY_HOURS_ET):
                score += 20
        else:
            if abs(hour - PRIME_HOUR_ET) <= TIME_TOLERANCE_H:
                score += 100
            elif any(abs(hour - h) <= TIME_TOLERANCE_H for h in SECONDARY_HOURS_ET):
                score += 30

    pair_bonus = 100 if weekend else 80
    for i in range(len(sorted_combo) - 1):
        if are_consecutive(sorted_combo[i]["match_time"], sorted_combo[i + 1]["match_time"]):
            score += pair_bonus

    for match in sorted_combo:
        for team_name in [match["team_home"], match["team_away"]]:
            team = db.get_team(team_name)
            if team:
                score -= 10 * team["scheduled_count"]

    return score


def combo_match_ids(combo: list[dict]) -> list[int]:
    return sorted(m["id"] for m in combo)


def best_combination(matches: list[dict], db) -> Optional[list[dict]]:
    if len(matches) < 2:
        return None
    weekend = is_weekend(matches[0]["match_time"])
    combos = generate_combinations(matches)
    if not combos:
        return None
    scored = [(c, score_combination(c, weekend, db)) for c in combos]
    max_score = max(s for _, s in scored)
    tied = [c for c, s in scored if s == max_score]

    def team_fairness(combo):
        total = 0
        for match in combo:
            for name in [match["team_home"], match["team_away"]]:
                team = db.get_team(name)
                if team:
                    total += team["scheduled_count"] + team["broadcast_count"]
        return total

    return min(tied, key=team_fairness)


# --- Proposal message formatting ---

def _fmt_match_line(match: dict) -> str:
    dt = datetime.fromtimestamp(match["match_time"], tz=ET)
    time_str = dt.strftime("%I:%M %p ET").lstrip("0")
    return f"  • [{match['division']}] {match['team_home']} vs {match['team_away']} — {time_str}"


def _fmt_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    # Remove leading zero from day without platform-specific flags
    return dt.strftime("%A %B ") + str(dt.day)


def build_proposal_message(date_str: str, current_combo: list[dict],
                            proposed_combo: list[dict],
                            current_score: int, proposed_score: int, db) -> str:
    current_lines = "\n".join(
        _fmt_match_line(m) for m in sorted(current_combo, key=lambda m: m["match_time"])
    )
    proposed_lines = "\n".join(
        _fmt_match_line(m) for m in sorted(proposed_combo, key=lambda m: m["match_time"])
    )
    team_info = []
    for match in proposed_combo:
        for name in [match["team_home"], match["team_away"]]:
            team = db.get_team(name)
            bc = team["broadcast_count"] if team else 0
            team_info.append(f"{name} ({bc} prior broadcasts)")

    return (
        f"📋 **Broadcast Schedule Proposal — {_fmt_date(date_str)}**\n\n"
        f"**Current schedule:**\n{current_lines}\n\n"
        f"**Proposed schedule:**\n{proposed_lines}\n\n"
        f"**Reason:** Proposed combination scores {proposed_score} vs current {current_score}.\n"
        f"Teams: {', '.join(team_info)}\n\n"
        f"React with ❌ to reject. Auto-approves in 12 hours."
    )


# --- Shared accept / propose helpers (called by both EventsCog and daily sweep) ---

async def accept_combination(combo: list[dict], date_str: str, db, teamup,
                              broadcast_channel) -> None:
    """Post a combination to TeamUp and notify the broadcast channel."""
    for match in combo:
        title = f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
        end_ts = match["match_time"] + int(MATCH_DURATION_H * 3600)
        event_id = teamup.create_event(title, match["match_time"], end_ts)
        db.update_match_teamup_id(match["id"], event_id)
        db.increment_scheduled_count(match["team_home"])
        db.increment_scheduled_count(match["team_away"])

    if broadcast_channel:
        lines = "\n".join(
            _fmt_match_line(m) for m in sorted(combo, key=lambda m: m["match_time"])
        )
        await broadcast_channel.send(
            f"📅 **Matches added for {_fmt_date(date_str)}:**\n{lines}"
        )


async def propose_change(date_str: str, current: list[dict], proposed: list[dict],
                          current_score: int, proposed_score: int,
                          db, broadcast_channel) -> None:
    """Post a draft proposal to the broadcast channel and store a pending change."""
    if not broadcast_channel:
        return
    msg_text = build_proposal_message(
        date_str, current, proposed, current_score, proposed_score, db
    )
    msg = await broadcast_channel.send(msg_text)
    await msg.add_reaction("❌")
    old_event_ids = [m["teamup_event_id"] for m in current if m.get("teamup_event_id")]
    new_match_ids = [m["id"] for m in proposed]
    db.insert_pending_change(
        description=msg_text,
        old_event_ids=old_event_ids,
        new_match_ids=new_match_ids,
        discord_message_id=str(msg.id),
    )


async def process_expired_changes(db, teamup, broadcast_channel) -> None:
    """Auto-approve pending changes whose 12-hour window has passed."""
    expired = db.get_expired_pending_changes()
    for change in expired:
        old_ids = json.loads(change["old_event_ids"])
        new_match_ids = json.loads(change["new_match_ids"])

        # Remove old TeamUp events and clear their DB references
        for event_id in old_ids:
            try:
                teamup.delete_event(event_id)
            except Exception:
                pass
            rows = db.conn.execute(
                "SELECT * FROM matches WHERE teamup_event_id = ?", (event_id,)
            ).fetchall()
            for row in rows:
                db.update_match_teamup_id(row["id"], None)
                db.decrement_scheduled_count(row["team_home"])
                db.decrement_scheduled_count(row["team_away"])
            db.conn.commit()

        # Accept new combination
        new_matches = [db.get_match(mid) for mid in new_match_ids]
        new_matches = [m for m in new_matches if m]
        if new_matches:
            date_str = datetime.fromtimestamp(
                new_matches[0]["match_time"], tz=ET
            ).strftime("%Y-%m-%d")
            await accept_combination(new_matches, date_str, db, teamup, broadcast_channel)

        db.resolve_pending_change(change["id"], approved=True)
        if broadcast_channel:
            await broadcast_channel.send(
                f"✅ Schedule proposal auto-approved for {_fmt_date(date_str)}."
            )


async def run_daily_sweep(db, teamup, broadcast_channel) -> None:
    """3am sweep: evaluate all upcoming days this week and process expired changes."""
    today = datetime.now(tz=ET).date()
    monday = today - timedelta(days=today.weekday())

    for i in range(7):
        day = monday + timedelta(days=i)
        if day < today:
            continue
        date_str = day.isoformat()

        if db.get_blocked_day(date_str):
            continue

        all_matches = db.get_matches_for_date(date_str)
        if len(all_matches) < 2:
            continue

        scheduled = db.get_scheduled_matches_for_date(date_str)
        best = best_combination(all_matches, db)
        if best is None:
            continue

        if not scheduled:
            await accept_combination(best, date_str, db, teamup, broadcast_channel)
        else:
            weekend = is_weekend(scheduled[0]["match_time"])
            current_score = score_combination(scheduled, weekend, db)
            proposed_score = score_combination(best, is_weekend(best[0]["match_time"]), db)
            if combo_match_ids(best) == combo_match_ids(scheduled):
                continue
            if proposed_score <= current_score:
                continue
            await propose_change(
                date_str, scheduled, best, current_score, proposed_score,
                db, broadcast_channel
            )

    await process_expired_changes(db, teamup, broadcast_channel)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: scheduling engine — scoring, combinations, proposal helpers"
```

---

## Task 5: TeamUp API Client

**Files:**
- Create: `teamup.py`
- Create: `tests/test_teamup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teamup.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from teamup import TeamUpClient, TeamUpError

BASE = "https://api.teamup.com/my-calendar"


@pytest.fixture
def client():
    return TeamUpClient(api_key="test-key", calendar_key="my-calendar")


def mock_response(status: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.ok = (status < 400)
    resp.status_code = status
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def test_get_events_calls_correct_url(client):
    with patch.object(client.session, "get", return_value=mock_response(200, {"events": []})) as mock_get:
        result = client.get_events("2024-04-20", "2024-04-20")
        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert "my-calendar/events" in url
        assert result == []


def test_get_events_returns_event_list(client):
    events = [{"id": "1", "title": "Test"}]
    with patch.object(client.session, "get", return_value=mock_response(200, {"events": events})):
        result = client.get_events("2024-04-20", "2024-04-20")
        assert result == events


def test_create_event_returns_id(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "abc123"}})):
        event_id = client.create_event("[Premier] A vs B", 1713657600, 1713664800)
        assert event_id == "abc123"


def test_create_event_sends_correct_title(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("[Premier] A vs B", 1713657600, 1713664800)
        payload = mock_post.call_args[1]["json"]
        assert payload["title"] == "[Premier] A vs B"


def test_create_event_all_day_flag(client):
    with patch.object(client.session, "post", return_value=mock_response(200, {"event": {"id": "1"}})) as mock_post:
        client.create_event("🚫 NO STREAM", 1713657600, 1713744000, all_day=True)
        payload = mock_post.call_args[1]["json"]
        assert payload["all_day"] is True


def test_update_event_calls_put(client):
    with patch.object(client.session, "put", return_value=mock_response(200, {})) as mock_put:
        client.update_event("event-1", "[D1] C vs D", 1713657600, 1713664800)
        mock_put.assert_called_once()
        url = mock_put.call_args[0][0]
        assert "events/event-1" in url


def test_delete_event_calls_delete(client):
    with patch.object(client.session, "delete", return_value=mock_response(200, {})) as mock_del:
        client.delete_event("event-1")
        mock_del.assert_called_once()
        url = mock_del.call_args[0][0]
        assert "events/event-1" in url


def test_raises_teamup_error_on_bad_status(client):
    with patch.object(client.session, "get", return_value=mock_response(401, {"error": "Unauthorized"})):
        with pytest.raises(TeamUpError):
            client.get_events("2024-04-20", "2024-04-20")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_teamup.py -v`

Expected: `ImportError: No module named 'teamup'`

- [ ] **Step 3: Create `teamup.py`**

```python
import requests
from datetime import datetime, timezone
from typing import Optional

TEAMUP_BASE_URL = "https://api.teamup.com"


class TeamUpError(Exception):
    pass


class TeamUpClient:
    def __init__(self, api_key: str, calendar_key: str):
        self.api_key = api_key
        self.calendar_key = calendar_key
        self.session = requests.Session()
        self.session.headers.update({
            "Teamup-Token": api_key,
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{TEAMUP_BASE_URL}/{self.calendar_key}/{path}"

    def _check(self, resp: requests.Response):
        if not resp.ok:
            raise TeamUpError(f"TeamUp API error {resp.status_code}: {resp.text}")

    def get_events(self, start_date: str, end_date: str) -> list[dict]:
        """Fetch events between two dates (YYYY-MM-DD)."""
        resp = self.session.get(
            self._url("events"),
            params={"startDate": start_date, "endDate": end_date},
        )
        self._check(resp)
        return resp.json().get("events", [])

    def create_event(self, title: str, start_ts: int, end_ts: int,
                     all_day: bool = False) -> str:
        """Create an event. Returns the TeamUp event ID as a string."""
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        payload = {
            "title": title,
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "all_day": all_day,
        }
        resp = self.session.post(self._url("events"), json=payload)
        self._check(resp)
        return str(resp.json()["event"]["id"])

    def update_event(self, event_id: str, title: str,
                     start_ts: int, end_ts: int) -> None:
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        payload = {
            "title": title,
            "start_dt": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_dt": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        resp = self.session.put(self._url(f"events/{event_id}"), json=payload)
        self._check(resp)

    def delete_event(self, event_id: str) -> None:
        resp = self.session.delete(self._url(f"events/{event_id}"))
        self._check(resp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_teamup.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add teamup.py tests/test_teamup.py
git commit -m "feat: TeamUp API client with full CRUD and tests"
```

---

## Task 6: Admin Commands Cog

**Files:**
- Create: `cogs/admin.py`

No unit tests for cogs — Discord interactions require integration testing with a live bot. Verify manually in Task 9.

- [ ] **Step 1: Create `cogs/admin.py`**

```python
import discord
from discord import app_commands
from discord.ext import commands
from database import Database
from typing import Callable, Optional


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database,
                 get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

    def _admin_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    @app_commands.command(name="set-match-channel",
                          description="Set the channel to watch for match posts")
    async def set_match_channel(self, interaction: discord.Interaction,
                                 channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("match_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Match channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-match-channel",
                          description="Unlink the match channel")
    async def unset_match_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("match_channel_id")
        await interaction.response.send_message(
            "✅ Match channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-broadcast-channel",
                          description="Set the admin channel for drafts and flags")
    async def set_broadcast_channel(self, interaction: discord.Interaction,
                                     channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("broadcast_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Broadcast channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-broadcast-channel",
                          description="Unlink the broadcast channel")
    async def unset_broadcast_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("broadcast_channel_id")
        await interaction.response.send_message(
            "✅ Broadcast channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-teamup-calendar",
                          description="Set the TeamUp calendar ID")
    async def set_teamup_calendar(self, interaction: discord.Interaction,
                                   calendar_id: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("teamup_calendar_id", calendar_id)
        await interaction.response.send_message(
            "✅ TeamUp calendar ID saved.", ephemeral=True
        )

    @app_commands.command(name="set-teamup-key",
                          description="Set the TeamUp API key")
    async def set_teamup_key(self, interaction: discord.Interaction, api_key: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("teamup_api_key", api_key)
        await interaction.response.send_message(
            "✅ TeamUp API key saved.", ephemeral=True
        )

    @app_commands.command(name="status",
                          description="Show current bot configuration")
    async def status(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        match_ch = self.db.get_config("match_channel_id")
        broadcast_ch = self.db.get_config("broadcast_channel_id")
        calendar_id = self.db.get_config("teamup_calendar_id")
        api_key = self.db.get_config("teamup_api_key")

        def ch_str(ch_id: Optional[str]) -> str:
            return f"<#{ch_id}>" if ch_id else "❌ Not set"

        lines = [
            "**Bot Status**",
            f"Match channel: {ch_str(match_ch)}",
            f"Broadcast channel: {ch_str(broadcast_ch)}",
            f"TeamUp calendar: {'✅ Set' if calendar_id else '❌ Not set'}",
            f"TeamUp API key: {'✅ Set' if api_key else '❌ Not set'}",
        ]
        missing = []
        if not match_ch: missing.append("`/set-match-channel`")
        if not broadcast_ch: missing.append("`/set-broadcast-channel`")
        if not calendar_id: missing.append("`/set-teamup-calendar`")
        if not api_key: missing.append("`/set-teamup-key`")
        if missing:
            lines.append(f"\n⚠️ Missing config: {', '.join(missing)}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="broadcast-done",
                          description="Mark a match as broadcast-complete")
    async def broadcast_done(self, interaction: discord.Interaction, match_id: int):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        match = self.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                f"❌ No match found with ID #{match_id}", ephemeral=True
            )
            return
        if match["broadcast_done"]:
            await interaction.response.send_message(
                f"⚠️ Match #{match_id} is already marked as done.", ephemeral=True
            )
            return
        self.db.mark_broadcast_done(match_id)
        self.db.increment_broadcast_count(match["team_home"])
        self.db.increment_broadcast_count(match["team_away"])
        await interaction.response.send_message(
            f"✅ Match #{match_id} ({match['team_home']} vs {match['team_away']}) "
            f"marked as broadcast complete.",
            ephemeral=True,
        )

    @app_commands.command(name="reset",
                          description="Reset the bot to its original state")
    async def reset(self, interaction: discord.Interaction, confirm: bool = False):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ This will erase all bot data including match history, team tallies, "
                "and configuration.\nRun `/reset confirm:True` to proceed.",
                ephemeral=True,
            )
            return
        self.db.reset_all()
        await interaction.response.send_message(
            "✅ Bot has been reset to its original state.", ephemeral=True
        )
```

- [ ] **Step 2: Commit**

```bash
git add cogs/admin.py
git commit -m "feat: admin slash commands cog"
```

---

## Task 7: Blocks Cog

**Files:**
- Create: `cogs/blocks.py`

- [ ] **Step 1: Create `cogs/blocks.py`**

```python
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from database import Database
from typing import Callable, Optional

BLOCK_PREFIX = "🚫 NO STREAM"


class BlocksCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

    def _admin_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    @app_commands.command(name="block-day",
                          description="Block a day from broadcast scheduling")
    async def block_day(self, interaction: discord.Interaction,
                        date: str, reason: Optional[str] = None):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True
            )
            return

        title = f"{BLOCK_PREFIX} — {reason}" if reason else BLOCK_PREFIX
        event_id = None
        teamup = self.get_teamup()
        if teamup:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            event_id = teamup.create_event(
                title,
                int(day_start.timestamp()),
                int(day_end.timestamp()),
                all_day=True,
            )

        self.db.insert_blocked_day(date, reason, event_id)
        suffix = f": {reason}" if reason else ""
        await interaction.response.send_message(
            f"✅ {date} blocked{suffix}.", ephemeral=True
        )

    @app_commands.command(name="unblock-day",
                          description="Remove a broadcast block for a day")
    async def unblock_day(self, interaction: discord.Interaction, date: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        blocked = self.db.get_blocked_day(date)
        if not blocked:
            await interaction.response.send_message(
                f"⚠️ {date} is not blocked.", ephemeral=True
            )
            return
        teamup = self.get_teamup()
        if teamup and blocked.get("teamup_event_id"):
            teamup.delete_event(blocked["teamup_event_id"])
        self.db.delete_blocked_day(date)
        await interaction.response.send_message(
            f"✅ Block removed for {date}.", ephemeral=True
        )

    @app_commands.command(name="list-blocks",
                          description="List all upcoming blocked days")
    async def list_blocks(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        blocks = self.db.get_all_blocked_days()
        if not blocks:
            await interaction.response.send_message(
                "No blocked days configured.", ephemeral=True
            )
            return
        lines = ["**Blocked Days:**"]
        for b in blocks:
            reason_str = f" — {b['reason']}" if b.get("reason") else ""
            lines.append(f"  • {b['date']}{reason_str}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
```

- [ ] **Step 2: Commit**

```bash
git add cogs/blocks.py
git commit -m "feat: block-day slash commands cog"
```

---

## Task 8: Events Cog

**Files:**
- Create: `cogs/events.py`

- [ ] **Step 1: Create `cogs/events.py`**

```python
import discord
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable

from database import Database
from parser import has_required_structure, parse_post, ParseError
from scheduler import (
    best_combination, score_combination, combo_match_ids,
    is_weekend, accept_combination, propose_change,
)

ET = ZoneInfo("America/New_York")


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        match_channel_id = self.db.get_config("match_channel_id")
        if not match_channel_id or str(message.channel.id) != match_channel_id:
            return
        if not has_required_structure(message.content):
            return  # Silent ignore

        try:
            parsed = parse_post(message.content, self.db)
        except ParseError:
            await self._flag_error(message)
            return

        match_id = self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=parsed.match_time,
            posted_at=int(message.created_at.timestamp()),
        )

        match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")

        if self.db.get_blocked_day(match_date):
            return

        all_matches = self.db.get_matches_for_date(match_date)
        scheduled = self.db.get_scheduled_matches_for_date(match_date)
        best = best_combination(all_matches, self.db)

        if best is None:
            return

        teamup = self.get_teamup()
        broadcast_ch = self._get_broadcast_channel()

        if not scheduled:
            if teamup:
                await accept_combination(best, match_date, self.db, teamup, broadcast_ch)
        else:
            weekend = is_weekend(scheduled[0]["match_time"])
            current_score = score_combination(scheduled, weekend, self.db)
            proposed_score = score_combination(best, is_weekend(best[0]["match_time"]), self.db)
            if combo_match_ids(best) == combo_match_ids(scheduled):
                return
            if proposed_score <= current_score:
                return
            await propose_change(
                match_date, scheduled, best, current_score, proposed_score,
                self.db, broadcast_ch,
            )

    async def _flag_error(self, message: discord.Message):
        flag_text = (
            f"⚠️ Could not parse a match post from **{message.author.display_name}**. "
            f"Please review:\n```{message.content[:500]}```"
        )
        await message.reply(flag_text)
        broadcast_ch = self._get_broadcast_channel()
        if broadcast_ch:
            await broadcast_ch.send(flag_text)

    def _get_broadcast_channel(self):
        ch_id = self.db.get_config("broadcast_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != "❌":
            return
        change = self.db.get_pending_change_by_message(str(payload.message_id))
        if not change:
            return
        self.db.resolve_pending_change(change["id"], approved=False)
        broadcast_ch = self._get_broadcast_channel()
        if broadcast_ch:
            await broadcast_ch.send("❌ Schedule proposal rejected.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        channel_id = str(channel.id)
        match_ch = self.db.get_config("match_channel_id")
        broadcast_ch = self.db.get_config("broadcast_channel_id")
        warning = (
            "⚠️ A configured channel was deleted. "
            "Use `/set-match-channel` or `/set-broadcast-channel` to reconfigure."
        )
        if channel_id == match_ch:
            self.db.delete_config("match_channel_id")
            remaining = self._get_broadcast_channel()
            if remaining:
                await remaining.send(warning)
                return
        if channel_id == broadcast_ch:
            self.db.delete_config("broadcast_channel_id")
            print(f"[WARNING] Broadcast channel {channel_id} deleted. No channel to send warning.")
```

- [ ] **Step 2: Commit**

```bash
git add cogs/events.py
git commit -m "feat: events cog — message listener, reaction handler, channel deletion"
```

---

## Task 9: Bot Entry Point

**Files:**
- Create: `bot.py`
- Create: `.env` (from `.env.example` — not committed)

- [ ] **Step 1: Create `bot.py`**

```python
import os
import asyncio
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from database import Database
from teamup import TeamUpClient
from scheduler import run_daily_sweep
from cogs.admin import AdminCog
from cogs.blocks import BlocksCog
from cogs.events import EventsCog

load_dotenv()
ET = ZoneInfo("America/New_York")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()


def get_teamup() -> "TeamUpClient | None":
    api_key = db.get_config("teamup_api_key")
    calendar_key = db.get_config("teamup_calendar_id")
    if api_key and calendar_key:
        return TeamUpClient(api_key, calendar_key)
    return None


async def daily_sweep_job():
    teamup = get_teamup()
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    if teamup and broadcast_ch:
        await run_daily_sweep(db, teamup, broadcast_ch)
    else:
        print("[sweep] Skipped — missing TeamUp credentials or broadcast channel.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    print("Slash commands synced.")
    scheduler.start()
    print("Scheduler started.")


async def main():
    global scheduler
    scheduler = AsyncIOScheduler(timezone=ET)
    scheduler.add_job(daily_sweep_job, "cron", hour=3, minute=0)

    await bot.add_cog(AdminCog(bot, db, get_teamup))
    await bot.add_cog(BlocksCog(bot, db, get_teamup))
    await bot.add_cog(EventsCog(bot, db, get_teamup))

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")

    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create `.env` from the example**

Copy `.env.example` to `.env` and fill in your real bot token:

```
DISCORD_BOT_TOKEN=your_real_token_here
```

Do NOT commit `.env`. It is in `.gitignore`.

- [ ] **Step 3: Run the full test suite one final time**

Run: `pytest tests/ -v`

Expected: All tests PASS with no errors.

- [ ] **Step 4: Start the bot and verify startup**

Run: `python bot.py`

Expected output:
```
Logged in as YourBot#1234 (ID: 123456789)
Slash commands synced.
Scheduler started.
```

- [ ] **Step 5: Smoke-test slash commands in Discord**

In the admin server:
1. Run `/status` — should show all fields as "❌ Not set"
2. Run `/set-match-channel #your-test-channel` — should confirm
3. Run `/set-broadcast-channel #your-admin-channel` — should confirm
4. Run `/status` — should show both channels as set
5. Run `/reset` — should show warning message
6. Run `/reset confirm:True` — should wipe config
7. Run `/status` — should show all fields as "❌ Not set" again

- [ ] **Step 6: Smoke-test match post parsing**

In the configured match channel, post:

```
Division: Premier
Week: 1
Team Alpha vs Team Beta
Time: <t:1713657600:F>
```

Expected: Bot parses silently (no error). If TeamUp is not configured yet, no calendar event is created. Bot stores the match in the DB.

Post a malformed entry:

```
Division: Zzzzz
Week: 1
Team Alpha vs Team Beta
Time: <t:1713657600:F>
```

Expected: Bot replies with ⚠️ parse error message.

- [ ] **Step 7: Final commit**

```bash
git add bot.py
git commit -m "feat: bot entry point with scheduler and cog wiring"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All spec sections mapped to tasks
  - Section 1 (Data model) → Task 2
  - Section 2 (Parsing) → Task 3
  - Section 3 (Commands) → Tasks 6 & 7 + smoke test in Task 9
  - Section 4 (TeamUp API) → Task 5
  - Section 5 (Scheduling logic) → Task 4
  - Section 6 (Trigger logic) → Tasks 4 & 8
  - Section 7 (Draft format) → Task 4 (`build_proposal_message`)
  - Channel deletion → Task 8 (`on_guild_channel_delete`)
  - `/reset` confirm flow → Task 6
- [x] **No placeholders:** All steps contain complete code
- [x] **Type consistency:** `combo_match_ids`, `score_combination`, `best_combination`, `accept_combination`, `propose_change` used consistently across Tasks 4 and 8
- [x] **`discord_message_id`** added to `pending_changes` table (Task 2) and used in Task 8 reaction handler
- [x] **`decrement_scheduled_count`** added to `Database` (Task 2) and used in `process_expired_changes` (Task 4)
