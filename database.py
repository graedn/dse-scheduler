import sqlite3
import json
import time
from typing import Optional


AUTO_APPROVE_SECONDS = 43200  # 12 hours


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
                broadcast_done INTEGER NOT NULL DEFAULT 0,
                broadcast_accepted INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS broadcast_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL UNIQUE,
                discord_message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS broadcast_signups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                role TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                signed_up_at INTEGER NOT NULL,
                UNIQUE (match_id, role, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_matches_match_time ON matches (match_time);
            CREATE INDEX IF NOT EXISTS idx_broadcast_messages_msg_id ON broadcast_messages (discord_message_id);
            CREATE INDEX IF NOT EXISTS idx_broadcast_signups_match ON broadcast_signups (match_id);
            CREATE TABLE IF NOT EXISTS managers (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                added_by TEXT NOT NULL,
                added_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS talent (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                broadcast_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS talent_allocations (
                match_id INTEGER PRIMARY KEY,
                role_assignments TEXT,
                confirmations TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                confirmation_message_id TEXT,
                confirmation_channel_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_talent_alloc_conf_msg
                ON talent_allocations (confirmation_message_id);
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'America/New_York'
            );
            CREATE TABLE IF NOT EXISTS proposal_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                week_start TEXT NOT NULL,
                discord_message_id TEXT,
                channel_id TEXT,
                day_ts INTEGER NOT NULL,
                slot1_match_id INTEGER,
                slot2_match_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS thread_messages (
                match_id INTEGER PRIMARY KEY,
                thread_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                team1_role_id TEXT,
                team2_role_id TEXT,
                team1_low_confidence INTEGER NOT NULL DEFAULT 0,
                team2_low_confidence INTEGER NOT NULL DEFAULT 0,
                ready_check_message_id TEXT,
                ready_check_responses TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            );
        """)
        # Migrate existing databases that predate the broadcast_accepted column
        try:
            self.conn.execute(
                "ALTER TABLE matches ADD COLUMN broadcast_accepted INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.commit()
        except Exception:
            pass  # Column already exists
        # Migrate: sign-up deadline per match
        try:
            self.conn.execute("ALTER TABLE matches ADD COLUMN signup_deadline INTEGER")
            self.conn.commit()
        except Exception:
            pass  # Column already exists
        # Migrate: allocation message tracking (so displaced allocations can be edited)
        try:
            self.conn.execute(
                "ALTER TABLE talent_allocations ADD COLUMN allocation_message_id TEXT"
            )
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn.execute(
                "ALTER TABLE talent_allocations ADD COLUMN allocation_channel_id TEXT"
            )
            self.conn.commit()
        except Exception:
            pass
        # Migrate: response_count tracks how many matches a talent has responded to
        try:
            self.conn.execute(
                "ALTER TABLE talent ADD COLUMN response_count INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.commit()
        except Exception:
            pass
        # Migrate: unavailable_count tracks how many times a talent clicked Unavailable
        try:
            self.conn.execute(
                "ALTER TABLE talent ADD COLUMN unavailable_count INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.commit()
        except Exception:
            pass

    def _et_day_range(self, date_str: str) -> tuple[int, int]:
        """Return (start_ts, end_ts) Unix seconds for date_str in Eastern Time."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        return int(day_start.timestamp()), int(day_end.timestamp())

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
        start_ts, end_ts = self._et_day_range(date_str)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ?",
            (start_ts, end_ts)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scheduled_matches_for_date(self, date_str: str) -> list[dict]:
        """Matches on date_str with a TeamUp event ID (i.e., on the calendar)."""
        start_ts, end_ts = self._et_day_range(date_str)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ? "
            "AND teamup_event_id IS NOT NULL",
            (start_ts, end_ts)
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

    def mark_broadcast_accepted(self, match_id: int):
        self.conn.execute(
            "UPDATE matches SET broadcast_accepted = 1 WHERE id = ?", (match_id,)
        )
        self.conn.commit()

    def clear_broadcast_accepted(self, match_id: int):
        self.conn.execute(
            "UPDATE matches SET broadcast_accepted = 0 WHERE id = ?", (match_id,)
        )
        self.conn.commit()

    def get_upcoming_matches(self, days: int = 7) -> list[dict]:
        """All matches in the next N days, ordered by match_time."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now = datetime.now(tz=ZoneInfo("America/New_York"))
        end = now + timedelta(days=days)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ? ORDER BY match_time",
            (int(now.timestamp()), int(end.timestamp()))
        ).fetchall()
        return [dict(r) for r in rows]

    def match_exists(self, team_home: str, team_away: str, match_time: int) -> bool:
        row = self.conn.execute(
            "SELECT id FROM matches WHERE team_home = ? AND team_away = ? AND match_time = ?",
            (team_home, team_away, match_time)
        ).fetchone()
        return row is not None

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

    def get_matches_by_teamup_event_id(self, event_id: str) -> list[dict]:
        """Get all matches associated with a given TeamUp event ID."""
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE teamup_event_id = ?", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Teams ---

    def get_team(self, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM teams WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_teams(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
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
            (now, now + AUTO_APPROVE_SECONDS, description,
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

    def update_pending_change_message(self, change_id: int, discord_message_id: str,
                                       description: str) -> None:
        self.conn.execute(
            "UPDATE pending_changes SET discord_message_id = ?, description = ? WHERE id = ?",
            (discord_message_id, description, change_id)
        )
        self.conn.commit()

    def get_all_pending_changes(self) -> list[dict]:
        """All unresolved pending changes (for re-registering ProposalViews on startup)."""
        rows = self.conn.execute(
            "SELECT * FROM pending_changes WHERE approved IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_awaiting_confirmation_matches(self) -> list[dict]:
        """Match IDs of allocations awaiting talent confirmation (for ConfirmationView startup)."""
        rows = self.conn.execute(
            "SELECT match_id FROM talent_allocations WHERE status = 'awaiting_confirm'"
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

    # --- Broadcast messages (sign-up messages per match) ---

    def insert_broadcast_message(self, match_id: int, discord_message_id: str,
                                  channel_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO broadcast_messages "
            "(match_id, discord_message_id, channel_id) VALUES (?, ?, ?)",
            (match_id, discord_message_id, channel_id)
        )
        self.conn.commit()

    def get_broadcast_message(self, match_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM broadcast_messages WHERE match_id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_match_by_broadcast_message(self, discord_message_id: str) -> Optional[dict]:
        """Return the match associated with a sign-up message, or None."""
        row = self.conn.execute(
            "SELECT m.* FROM matches m "
            "JOIN broadcast_messages bm ON m.id = bm.match_id "
            "WHERE bm.discord_message_id = ?",
            (discord_message_id,)
        ).fetchone()
        return dict(row) if row else None

    # --- Broadcast signups (talent sign-up per match) ---

    def upsert_signup(self, match_id: int, message_id: str, role: str,
                      user_id: str, username: str, display_name: str) -> bool:
        """Insert a signup. Returns True if newly inserted, False if already exists."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO broadcast_signups "
            "(match_id, message_id, role, user_id, username, display_name, signed_up_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (match_id, message_id, role, user_id, username, display_name, int(time.time()))
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_signup(self, match_id: int, role: str, user_id: str) -> bool:
        """Remove a signup. Returns True if a row was deleted."""
        cur = self.conn.execute(
            "DELETE FROM broadcast_signups "
            "WHERE match_id = ? AND role = ? AND user_id = ?",
            (match_id, role, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_signups_for_match(self, match_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM broadcast_signups WHERE match_id = ? ORDER BY signed_up_at",
            (match_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def copy_signups(self, old_match_id: int, new_match_id: int) -> int:
        """Copy all broadcast_signups rows from one match to another. Returns count copied."""
        rows = self.conn.execute(
            "SELECT role, user_id, username, display_name, signed_up_at "
            "FROM broadcast_signups WHERE match_id = ?",
            (old_match_id,)
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "INSERT OR IGNORE INTO broadcast_signups "
                "(match_id, message_id, role, user_id, username, display_name, signed_up_at) "
                "VALUES (?, '', ?, ?, ?, ?, ?)",
                (new_match_id, r["role"], r["user_id"], r["username"],
                 r["display_name"], r["signed_up_at"])
            )
        self.conn.commit()
        return len(rows)

    def get_all_matches_with_teamup_id(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE teamup_event_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_accepted_broadcast_matches(self) -> list[dict]:
        """Accepted matches that still have a sign-up broadcast message (for persistent view re-registration)."""
        rows = self.conn.execute(
            "SELECT m.* FROM matches m "
            "JOIN broadcast_messages bm ON m.id = bm.match_id "
            "WHERE m.broadcast_accepted = 1"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_active_sign_up_matches(self) -> list[dict]:
        """Matches that have a broadcast sign-up message and are not yet talent-accepted."""
        rows = self.conn.execute(
            "SELECT m.* FROM matches m "
            "JOIN broadcast_messages bm ON m.id = bm.match_id "
            "WHERE m.broadcast_accepted = 0 AND m.teamup_event_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Managers ---

    def add_manager(self, user_id: str, username: str, display_name: str,
                    added_by: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO managers "
            "(user_id, username, display_name, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, display_name, added_by, int(time.time()))
        )
        self.conn.commit()

    def remove_manager(self, user_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM managers WHERE user_id = ?", (user_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def is_manager(self, user_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM managers WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def get_all_managers(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM managers ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Signup deadline ---

    def set_signup_deadline(self, match_id: int, deadline: int) -> None:
        self.conn.execute(
            "UPDATE matches SET signup_deadline = ? WHERE id = ?", (deadline, match_id)
        )
        self.conn.commit()

    def get_matches_past_deadline(self) -> list[dict]:
        """Matches whose sign-up deadline has passed but no allocation record exists yet."""
        now = int(time.time())
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE signup_deadline IS NOT NULL "
            "AND signup_deadline <= ? AND broadcast_accepted = 0 "
            "AND teamup_event_id IS NOT NULL "
            "AND id NOT IN (SELECT match_id FROM talent_allocations)",
            (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_changes_for_date(self, date_str: str) -> list[dict]:
        """Pending changes that involve at least one match on the given date."""
        pending = self.get_all_pending_changes()
        day_match_ids = {m["id"] for m in self.get_matches_for_date(date_str)}
        result = []
        for change in pending:
            new_ids = set(json.loads(change.get("new_match_ids") or "[]"))
            if new_ids & day_match_ids:
                result.append(change)
                continue
            for eid in json.loads(change.get("old_event_ids") or "[]"):
                if any(m["id"] in day_match_ids
                       for m in self.get_matches_by_teamup_event_id(eid)):
                    result.append(change)
                    break
        return result

    def get_matches_past_calltime_last_call(self) -> list[dict]:
        """Matches in 'last_call' allocation state whose call time (30 min before match) has passed."""
        now = int(time.time())
        call_offset = 1800  # seconds before match_time
        rows = self.conn.execute(
            "SELECT m.* FROM matches m "
            "JOIN talent_allocations ta ON m.id = ta.match_id "
            "WHERE ta.status = 'last_call' "
            "AND (m.match_time - ?) <= ? "
            "AND m.broadcast_accepted = 0",
            (call_offset, now)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Talent broadcast counts ---

    def get_talent_count(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT broadcast_count FROM talent WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["broadcast_count"] if row else 0

    def increment_talent_broadcast(self, user_id: str, username: str,
                                   display_name: str) -> None:
        self.conn.execute(
            "INSERT INTO talent (user_id, username, display_name, broadcast_count) "
            "VALUES (?, ?, ?, 1) ON CONFLICT(user_id) DO UPDATE SET "
            "broadcast_count = broadcast_count + 1, "
            "username = excluded.username, display_name = excluded.display_name",
            (user_id, username, display_name)
        )
        self.conn.commit()

    def increment_talent_response(self, user_id: str, username: str,
                                   display_name: str) -> None:
        """Increment response_count by 1. Inserts the row if it doesn't exist yet."""
        self.conn.execute(
            "INSERT INTO talent (user_id, username, display_name, broadcast_count, response_count) "
            "VALUES (?, ?, ?, 0, 1) ON CONFLICT(user_id) DO UPDATE SET "
            "response_count = response_count + 1, "
            "username = excluded.username, display_name = excluded.display_name",
            (user_id, username, display_name)
        )
        self.conn.commit()

    def get_all_talent(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM talent "
            "WHERE broadcast_count > 0 OR response_count > 0 OR unavailable_count > 0 "
            "ORDER BY (broadcast_count + response_count + unavailable_count) DESC, display_name ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_talent_unavailable(self, user_id: str, username: str,
                                     display_name: str) -> None:
        """Increment unavailable_count by 1. Inserts the row if it doesn't exist yet."""
        self.conn.execute(
            "INSERT INTO talent (user_id, username, display_name, broadcast_count, "
            "response_count, unavailable_count) "
            "VALUES (?, ?, ?, 0, 0, 1) ON CONFLICT(user_id) DO UPDATE SET "
            "unavailable_count = unavailable_count + 1, "
            "username = excluded.username, display_name = excluded.display_name",
            (user_id, username, display_name)
        )
        self.conn.commit()

    def remove_all_signups_for_user(self, match_id: int, user_id: str) -> int:
        """Remove all sign-up rows for a user on a match. Returns number of rows deleted."""
        cur = self.conn.execute(
            "DELETE FROM broadcast_signups WHERE match_id = ? AND user_id = ?",
            (match_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount

    # --- Talent allocations ---

    def create_allocation(self, match_id: int) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT OR IGNORE INTO talent_allocations "
            "(match_id, confirmations, status, created_at, updated_at) "
            "VALUES (?, '{}', 'pending', ?, ?)",
            (match_id, now, now)
        )
        self.conn.commit()

    def get_allocation(self, match_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM talent_allocations WHERE match_id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_allocation_message(self, match_id: int,
                               message_id: str, channel_id: str) -> None:
        """Store the Discord message ID of the allocation UI so it can be edited later."""
        self.conn.execute(
            "UPDATE talent_allocations SET allocation_message_id = ?, "
            "allocation_channel_id = ?, updated_at = ? WHERE match_id = ?",
            (message_id, channel_id, int(time.time()), match_id)
        )
        self.conn.commit()

    def get_allocation_by_confirmation_message(self,
                                               message_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM talent_allocations WHERE confirmation_message_id = ?",
            (message_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_allocation_assignments(self, match_id: int, role_assignments: dict,
                                   confirmations: dict,
                                   confirmation_message_id: Optional[str],
                                   confirmation_channel_id: Optional[str]) -> None:
        now = int(time.time())
        self.conn.execute(
            "UPDATE talent_allocations SET "
            "role_assignments = ?, confirmations = ?, status = 'awaiting_confirm', "
            "confirmation_message_id = ?, confirmation_channel_id = ?, updated_at = ? "
            "WHERE match_id = ?",
            (json.dumps(role_assignments), json.dumps(confirmations),
             confirmation_message_id, confirmation_channel_id, now, match_id)
        )
        self.conn.commit()

    def update_allocation_lineup(self, match_id: int, role_assignments: dict,
                                 confirmations: dict) -> None:
        """Update role_assignments + confirmations only, leaving status,
        confirmation_message_id and channel intact (used by single-role swaps
        so an accepted broadcast is not downgraded)."""
        self.conn.execute(
            "UPDATE talent_allocations SET role_assignments = ?, confirmations = ?, "
            "updated_at = ? WHERE match_id = ?",
            (json.dumps(role_assignments), json.dumps(confirmations),
             int(time.time()), match_id)
        )
        self.conn.commit()

    def set_confirmation(self, match_id: int, user_id: str, confirmed: bool) -> None:
        row = self.conn.execute(
            "SELECT confirmations FROM talent_allocations WHERE match_id = ?",
            (match_id,)
        ).fetchone()
        if not row:
            return
        confirmations = json.loads(row["confirmations"])
        confirmations[user_id] = confirmed
        self.conn.execute(
            "UPDATE talent_allocations SET confirmations = ?, updated_at = ? "
            "WHERE match_id = ?",
            (json.dumps(confirmations), int(time.time()), match_id)
        )
        self.conn.commit()

    def get_confirmations(self, match_id: int) -> dict:
        row = self.conn.execute(
            "SELECT confirmations FROM talent_allocations WHERE match_id = ?",
            (match_id,)
        ).fetchone()
        return json.loads(row["confirmations"]) if row else {}

    def set_allocation_status(self, match_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE talent_allocations SET status = ?, updated_at = ? WHERE match_id = ?",
            (status, int(time.time()), match_id)
        )
        self.conn.commit()

    def reset_allocation(self, match_id: int) -> None:
        """Clear assignment data so the manager can re-allocate."""
        self.conn.execute(
            "UPDATE talent_allocations SET "
            "role_assignments = NULL, confirmations = '{}', status = 'pending', "
            "confirmation_message_id = NULL, confirmation_channel_id = NULL, "
            "updated_at = ? WHERE match_id = ?",
            (int(time.time()), match_id)
        )
        self.conn.commit()

    def copy_allocation(self, old_match_id: int, new_match_id: int) -> None:
        """Copy the talent_allocations row from one match onto another (upsert)."""
        src = self.conn.execute(
            "SELECT role_assignments, confirmations, status, "
            "confirmation_message_id, confirmation_channel_id, "
            "allocation_message_id, allocation_channel_id "
            "FROM talent_allocations WHERE match_id = ?",
            (old_match_id,)
        ).fetchone()
        if not src:
            return
        now = int(time.time())
        self.conn.execute(
            "INSERT OR IGNORE INTO talent_allocations "
            "(match_id, confirmations, status, created_at, updated_at) "
            "VALUES (?, '{}', 'pending', ?, ?)",
            (new_match_id, now, now)
        )
        self.conn.execute(
            "UPDATE talent_allocations SET role_assignments = ?, confirmations = ?, "
            "status = ?, confirmation_message_id = ?, confirmation_channel_id = ?, "
            "allocation_message_id = ?, allocation_channel_id = ?, updated_at = ? "
            "WHERE match_id = ?",
            (src["role_assignments"], src["confirmations"], src["status"],
             src["confirmation_message_id"], src["confirmation_channel_id"],
             src["allocation_message_id"], src["allocation_channel_id"],
             now, new_match_id)
        )
        self.conn.commit()

    # --- User settings ---

    def get_user_timezone(self, user_id: str) -> str:
        row = self.conn.execute(
            "SELECT timezone FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["timezone"] if row else "America/New_York"

    def set_user_timezone(self, user_id: str, tz_name: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, timezone) VALUES (?, ?)",
            (user_id, tz_name)
        )
        self.conn.commit()

    # --- Proposal messages (weekly schedule proposals) ---

    def create_proposal_message(self, date_str: str, day_ts: int,
                                week_start: str) -> int:
        """Create a proposal message row for a date. Returns the row id."""
        now = int(time.time())
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO proposal_messages "
            "(date, week_start, day_ts, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'open', ?, ?)",
            (date_str, week_start, day_ts, now, now)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_proposal_message(self, date_str: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM proposal_messages WHERE date = ?", (date_str,)
        ).fetchone()
        return dict(row) if row else None

    def get_proposal_messages_for_week(self, week_start: str) -> list[dict]:
        """All proposal messages for a given week_start (YYYY-MM-DD of Monday)."""
        rows = self.conn.execute(
            "SELECT * FROM proposal_messages WHERE week_start = ? ORDER BY date",
            (week_start,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_open_proposal_messages(self) -> list[dict]:
        """All proposal messages still in 'open' status."""
        rows = self.conn.execute(
            "SELECT * FROM proposal_messages WHERE status = 'open' ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_blocked_proposal_messages(self) -> list[dict]:
        """All proposal messages with 'blocked' status."""
        rows = self.conn.execute(
            "SELECT * FROM proposal_messages WHERE status = 'blocked' ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_proposal_slots(self, date_str: str,
                              slot1_match_id: Optional[int],
                              slot2_match_id: Optional[int]) -> None:
        self.conn.execute(
            "UPDATE proposal_messages SET slot1_match_id = ?, slot2_match_id = ?, "
            "updated_at = ? WHERE date = ?",
            (slot1_match_id, slot2_match_id, int(time.time()), date_str)
        )
        self.conn.commit()

    def set_proposal_status(self, date_str: str, status: str) -> None:
        """Set proposal status: 'open', 'blocked', or 'passed'."""
        self.conn.execute(
            "UPDATE proposal_messages SET status = ?, updated_at = ? WHERE date = ?",
            (status, int(time.time()), date_str)
        )
        self.conn.commit()

    def set_proposal_discord_message(self, date_str: str,
                                     message_id: str, channel_id: str) -> None:
        self.conn.execute(
            "UPDATE proposal_messages SET discord_message_id = ?, channel_id = ?, "
            "updated_at = ? WHERE date = ?",
            (message_id, channel_id, int(time.time()), date_str)
        )
        self.conn.commit()

    def get_unscheduled_matches_for_date(self, date_str: str) -> list[dict]:
        """Matches on date_str with no TeamUp event ID (i.e., not yet on the calendar)."""
        start_ts, end_ts = self._et_day_range(date_str)
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE match_time >= ? AND match_time <= ? "
            "AND teamup_event_id IS NULL ORDER BY match_time",
            (start_ts, end_ts)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Thread messages ---

    def insert_thread_message(
        self,
        match_id: int,
        thread_id: str,
        channel_id: str,
        team1_role_id: Optional[str],
        team2_role_id: Optional[str],
        team1_low_confidence: int = 0,
        team2_low_confidence: int = 0,
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO thread_messages "
            "(match_id, thread_id, channel_id, team1_role_id, team2_role_id, "
            "team1_low_confidence, team2_low_confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (match_id, thread_id, channel_id, team1_role_id, team2_role_id,
             team1_low_confidence, team2_low_confidence, int(time.time())),
        )
        self.conn.commit()

    def get_thread_message(self, match_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM thread_messages WHERE match_id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_thread_by_id(self, thread_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_thread_roles(
        self,
        match_id: int,
        team1_role_id: Optional[str],
        team2_role_id: Optional[str],
        team1_low_confidence: int,
        team2_low_confidence: int,
    ) -> None:
        self.conn.execute(
            "UPDATE thread_messages SET team1_role_id = ?, team2_role_id = ?, "
            "team1_low_confidence = ?, team2_low_confidence = ? WHERE match_id = ?",
            (team1_role_id, team2_role_id,
             team1_low_confidence, team2_low_confidence, match_id),
        )
        self.conn.commit()

    def set_thread_ready_check_message(self, match_id: int, message_id: str) -> None:
        self.conn.execute(
            "UPDATE thread_messages SET ready_check_message_id = ? WHERE match_id = ?",
            (message_id, match_id),
        )
        self.conn.commit()

    def set_thread_ready_check_response(
        self, match_id: int, user_id: str, ready: bool
    ) -> None:
        row = self.conn.execute(
            "SELECT ready_check_responses FROM thread_messages WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        if not row:
            return
        responses: dict = json.loads(row["ready_check_responses"])
        responses[user_id] = ready
        self.conn.execute(
            "UPDATE thread_messages SET ready_check_responses = ? WHERE match_id = ?",
            (json.dumps(responses), match_id),
        )
        self.conn.commit()

    def get_thread_ready_check_responses(self, match_id: int) -> dict:
        row = self.conn.execute(
            "SELECT ready_check_responses FROM thread_messages WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        return json.loads(row["ready_check_responses"]) if row else {}

    def get_approved_matches_needing_ready_check(self) -> list[dict]:
        """Accepted matches whose match_time is within 30 min and have a thread but no ready check."""
        import time as _time
        now = int(_time.time())
        cutoff = now + 30 * 60
        rows = self.conn.execute(
            "SELECT m.* FROM matches m "
            "JOIN thread_messages t ON t.match_id = m.id "
            "WHERE m.broadcast_accepted = 1 "
            "AND m.match_time > ? AND m.match_time <= ? "
            "AND t.ready_check_message_id IS NULL",
            (now, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_threads_with_pending_ready_check(self) -> list[dict]:
        """All thread rows that have a ready_check_message_id (for persistent view re-registration)."""
        rows = self.conn.execute(
            "SELECT * FROM thread_messages WHERE ready_check_message_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def reset_season(self) -> None:
        """Clear all season data while preserving config and managers."""
        self.conn.executescript("""
            DELETE FROM matches;
            DELETE FROM teams;
            DELETE FROM pending_changes;
            DELETE FROM blocked_days;
            DELETE FROM broadcast_messages;
            DELETE FROM broadcast_signups;
            DELETE FROM talent;
            DELETE FROM talent_allocations;
            DELETE FROM proposal_messages;
            DELETE FROM thread_messages;
            DELETE FROM sqlite_sequence WHERE name IN (
                'matches', 'pending_changes', 'broadcast_messages',
                'broadcast_signups', 'talent_allocations', 'proposal_messages'
            );
        """)
        self.conn.commit()

    # --- Reset ---

    def reset_all(self):
        self.conn.executescript("""
            DELETE FROM config;
            DELETE FROM matches;
            DELETE FROM teams;
            DELETE FROM pending_changes;
            DELETE FROM blocked_days;
            DELETE FROM broadcast_messages;
            DELETE FROM broadcast_signups;
            DELETE FROM managers;
            DELETE FROM talent;
            DELETE FROM talent_allocations;
            DELETE FROM user_settings;
            DELETE FROM proposal_messages;
            DELETE FROM thread_messages;
            DELETE FROM sqlite_sequence WHERE name IN (
                'matches', 'pending_changes', 'broadcast_messages',
                'broadcast_signups', 'talent_allocations', 'proposal_messages'
            );
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()
