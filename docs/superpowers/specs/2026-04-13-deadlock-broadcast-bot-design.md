# Deadlock League Broadcast Bot — Design Spec

**Date:** 2026-04-13
**Status:** Approved

---

## Overview

A Discord bot that monitors a public league server for player-posted match times, selects 2–3 matches per day for broadcast, manages a TeamUp calendar for the broadcast team, and posts draft schedules to a private admin server for review. Built in Python using discord.py.

---

## 1. Data Model (SQLite)

### `matches`
| Field | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-incremented broadcast ID |
| `division` | TEXT | Premier / Division 1–4 |
| `week` | TEXT | "1"–"8" or "Round X" during playoffs |
| `team_home` | TEXT | Canonical home team name |
| `team_away` | TEXT | Canonical away team name |
| `match_time` | INTEGER | Unix timestamp (UTC), extracted from Discord `<t:...>` tag |
| `posted_at` | INTEGER | Unix timestamp when the Discord post was made |
| `teamup_event_id` | TEXT | TeamUp event ID once on the calendar (nullable) |
| `broadcast_done` | BOOLEAN | Set to TRUE by `/broadcast-done <id>` |

### `teams`
| Field | Type | Description |
|---|---|---|
| `name` | TEXT PK | Canonical team name |
| `aliases` | TEXT | JSON list of observed spelling variants |
| `scheduled_count` | INTEGER | Times selected for broadcast |
| `broadcast_count` | INTEGER | Times confirmed via `/broadcast-done` |

### `pending_changes`
| Field | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Change ID |
| `proposed_at` | INTEGER | Unix timestamp when draft was posted |
| `auto_approve_at` | INTEGER | `proposed_at + 43200` (12 hours) |
| `description` | TEXT | Human-readable summary of the proposed change |
| `old_event_ids` | TEXT | JSON list of TeamUp event IDs being replaced |
| `new_match_ids` | TEXT | JSON list of match IDs being proposed |
| `approved` | BOOLEAN | NULL = pending, TRUE = approved, FALSE = rejected |

### `config`
| Field | Type | Description |
|---|---|---|
| `key` | TEXT PK | Config key |
| `value` | TEXT | Config value |

Stores: `match_channel_id`, `broadcast_channel_id`, `teamup_calendar_id`, `teamup_api_key`.

### `blocked_days`
| Field | Type | Description |
|---|---|---|
| `date` | TEXT PK | ISO date string (YYYY-MM-DD) |
| `reason` | TEXT | Optional reason |
| `teamup_event_id` | TEXT | TeamUp all-day block event ID |

---

## 2. Post Format & Parsing

### Expected format (posted by players in the public server)

```
Division: Premier
Week: 3
Team Alpha vs Team Beta
Time: <t:1713387600:F>
```

During playoffs, `Week:` is replaced with `Round:`. Both are accepted.

### Parsing pipeline

**Step 1 — Structure check (silent ignore)**

The bot checks for all four required fields using regex:
- A line matching `Division:` (case-insensitive)
- A line matching `Week:` or `Round:` (case-insensitive)
- A line containing ` vs ` (case-insensitive)
- A line matching `Time:` containing a Discord timestamp tag `<t:DIGITS:?LETTER?>`

If any field is missing, the post is silently ignored.

**Step 2 — Field parsing with fuzzy matching**

- **Division:** Fuzzy-matched against `["Premier", "Division 1", "Division 2", "Division 3", "Division 4"]` using `difflib.get_close_matches`. Match confidence threshold: 80%. Below threshold → flagged.
- **Week/Round:** Extracts numeric value from the line. Handles `"Week: 3"`, `"wk3"`, `"week3"`, etc. Unresolvable → flagged.
- **Teams:** Split on ` vs ` (case-insensitive). Each side stripped of extra whitespace. Fuzzy-matched against existing team names in the database. If a team is new, it's created. If confidence is below 80% against any existing team → flagged.
- **Team name learning:** Each team stores observed spelling variants. After 2+ consistent spellings, the most frequent variant becomes canonical. New posts are silently corrected to canonical if confidence ≥ 80%.
- **Time:** Raw Unix timestamp extracted from `<t:TIMESTAMP:...>` via regex. Malformed tag → flagged.

**Step 3 — Flag behavior**

When a post passes the structure check but fails field parsing:
1. Bot replies to the message in the public server channel.
2. Bot posts a notification to the admin broadcast channel.

Message format:
> ⚠️ Could not parse a match post from **@username**. Please review:
> `[quoted post content]`

The match is not added to the database until re-posted correctly.

---

## 3. Configuration Commands

All commands are slash commands restricted to users with the `Administrator` permission.

| Command | Description |
|---|---|
| `/set-match-channel #channel` | Sets the public server channel to watch for match posts |
| `/set-broadcast-channel #channel` | Sets the admin server channel for drafts, flags, and notifications |
| `/unset-match-channel` | Unlinks the match channel; bot stops watching for posts |
| `/unset-broadcast-channel` | Unlinks the broadcast channel; bot stops posting drafts and flags |
| `/set-teamup-calendar <calendar-id>` | Sets the TeamUp calendar ID |
| `/set-teamup-key <api-key>` | Stores the TeamUp API key |
| `/status` | Shows current config and next 3am run time |
| `/broadcast-done <id>` | Marks match ID as followed through; increments team `broadcast_count` |
| `/block-day YYYY-MM-DD [reason]` | Creates an all-day `🚫 NO STREAM` block event on TeamUp and local DB |
| `/unblock-day YYYY-MM-DD` | Removes the block event for that day |
| `/list-blocks` | Lists all upcoming blocked days |
| `/reset` | Resets the bot to its original state (see below) |

The bot refuses to process match posts until `match_channel_id`, `broadcast_channel_id`, `teamup_calendar_id`, and `teamup_api_key` are all configured. `/status` surfaces any missing config.

### `/reset` behaviour

Clears all bot state: config, matches, teams, pending changes, and blocked days. Does **not** delete events already posted to TeamUp (those must be cleared manually in TeamUp). Requires a confirmation prompt before executing:

> ⚠️ This will erase all bot data including match history, team tallies, and configuration. Type `/reset confirm` to proceed.

### Channel deletion handling

The bot listens for Discord's `on_guild_channel_delete` event. If the deleted channel matches a configured channel ID:
- The corresponding config entry is cleared (`match_channel_id` or `broadcast_channel_id`)
- The bot posts a warning in the remaining configured channel (if any): `⚠️ A configured channel was deleted. Use /set-match-channel or /set-broadcast-channel to reconfigure.`
- If no channel is available to post the warning, it is logged to console only.

---

## 4. TeamUp API Integration

| Operation | Endpoint | When |
|---|---|---|
| Read calendar state | `GET /events` | At each 3am sweep, to sync before re-evaluating |
| Create broadcast event | `POST /events` | When a match combination is accepted (auto or approved) |
| Update broadcast event | `PUT /events/{id}` | When a schedule adjustment is approved |
| Delete broadcast event | `DELETE /events/{id}` | When a match is removed from the broadcast schedule |
| Create block event | `POST /events` | `/block-day` command (all-day event) |
| Delete block event | `DELETE /events/{id}` | `/unblock-day` command |

**Event title format:**
```
[Premier] Team Alpha vs Team Beta
```

**Block event title format:**
```
🚫 NO STREAM — [reason]
```

The bot identifies block events by the `🚫 NO STREAM` prefix and never modifies them through the scheduling logic.

The `GET /events` sync at 3am reconciles the database against the live calendar, handling any manual edits made directly in TeamUp.

---

## 5. Scheduling Logic

### Constants
- Match duration: 2 hours (used for overlap detection and consecutive-pair identification)
- Max matches per day on the broadcast calendar: 3
- Prime weekday time: 8pm EST
- Secondary weekday times: 6pm EST, 10pm EST
- Consecutive pair: next match starts between 1.5h and 2.5h after previous match's start time (i.e., back-to-back, e.g. 7pm + 9pm)

### Valid combination rules
1. 2 or 3 matches from available posts for a given day
2. No two matches overlap (second match starts < 1.5h after first match starts)
3. Day is not blocked

### Scoring — Weekday (Mon–Fri)

| Condition | Points |
|---|---|
| Match starts at exactly 8pm EST (±15 min tolerance) | +100 |
| Each consecutive pair in the combination | +80 |
| Match starts at 6pm or 10pm EST (±15 min tolerance) | +30 |
| All other times | +0 |
| Each team in combination: subtract per `scheduled_count` | −10 × count |

A 7pm + 9pm consecutive pair scores 160, which beats a solo 8pm (100). This is intentional — consecutive pairs fill the broadcast window better than a single prime-time match.

### Scoring — Weekend (Sat–Sun)

| Condition | Points |
|---|---|
| Each consecutive pair in the combination | +100 |
| Match starts at 8pm EST (±15 min tolerance) | +50 |
| Match starts at 6pm or 10pm EST (±15 min tolerance) | +20 |
| All other times | +0 |
| Each team in combination: subtract per `scheduled_count` | −10 × count |

Consecutive pairs are the clear weekend priority.

### Tiebreak
When combinations score equally: pick the combination whose teams have the lowest combined `scheduled_count + broadcast_count`.

---

## 6. Trigger Logic

### Trigger A — New match post arrives (real-time)

1. Parse and validate the post (see Section 2).
2. Store match in database.
3. Check TeamUp for existing broadcast events on that match's day.

```
Nothing scheduled for that day?
    └── Score all valid combinations for the day
        └── Auto-accept best combination immediately
            ├── POST events to TeamUp
            └── Notify admin channel: "📅 Matches added for [date]: [summary]"

Something already scheduled?
    └── Score all valid combinations (existing + new match)
        ├── Best combination = current schedule → no action
        └── Best combination ≠ current schedule
            └── Post draft proposal to admin channel
                ├── Shows current vs proposed schedule and score delta
                └── Auto-approve after 12 hours (❌ reaction to reject)
```

### Trigger B — 3am daily sweep (APScheduler cron)

1. Sync with TeamUp via `GET /events`.
2. Reconcile database against live calendar state.
3. For each upcoming day in the current week (that isn't blocked):
   a. Score all valid combinations of posted matches.
   b. If best combination differs from current calendar → post draft proposal to admin channel.
   c. If a day has valid matches but nothing on the calendar → auto-accept immediately.
4. Process any pending changes whose `auto_approve_at` has passed.

---

## 7. Draft Proposal Format

Posted in the admin broadcast channel:

```
📋 Broadcast Schedule Proposal — Saturday April 19

Current schedule:
  • [Premier] Team A vs Team B — 8:00pm EST

Proposed schedule:
  • [Division 1] Team C vs Team D — 7:00pm EST
  • [Premier] Team E vs Team F — 9:00pm EST

Reason: Consecutive pair scores higher (200 pts) than current solo 8pm (95 pts).
        Team C and Team D have 0 prior broadcasts. Team A has 2.

React with ❌ to reject. Auto-approves in 12 hours.
```

---

## 8. Technology Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| Discord library | discord.py |
| Scheduling | APScheduler |
| Database | SQLite via `sqlite3` (stdlib) |
| Fuzzy matching | `difflib` (stdlib) |
| HTTP client | `requests` |
| Config | Environment variables + `/set-*` commands stored in DB |

---

## 9. Out of Scope

- Web dashboard
- Multi-bot / multi-process deployment
- Automatic stream detection or Twitch integration
- Match result tracking beyond the `/broadcast-done` command
- Player registration or team roster management
