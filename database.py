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
            CREATE INDEX IF NOT EXISTS idx_matches_match_time ON matches (match_time);
        """)
        self.conn.commit()

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
