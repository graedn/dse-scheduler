# DSE Scheduler Bot — Claude Context

A Discord bot for managing esports broadcast scheduling, talent sign-ups, and TeamUp calendar integration.

## Project Overview

- **Language**: Python 3.12, discord.py 2.x, APScheduler, SQLite via `database.py`
- **Entry point**: `bot.py` — loads cogs and starts the scheduler
- **Core modules**: `parser.py`, `scheduler.py`, `database.py`, `teamup.py`
- **Cogs**: `cogs/admin.py`, `cogs/blocks.py`, `cogs/events.py`, `cogs/talent.py`

## Architecture

```
bot.py               — bot setup, scheduled jobs, cog loading, persistent view registration
parser.py            — match post parsing (text → ParsedMatch)
scheduler.py         — scheduling algorithm, scoring, sign-up messages, proposal logic
database.py          — SQLite wrapper (all DB access goes through here)
teamup.py            — TeamUp REST API client
cogs/
  admin.py           — /set-*, /accept-broadcast, /add-manager, /new-season, /set-timezone, etc.
  blocks.py          — /block-day, /unblock-day, /list-blocks
  events.py          — on_message (match parsing and scheduling)
  talent.py          — AllocationView (single-step: 4 role selects + optional + Confirm/Cancel)
  signup.py          — SignUpView (role buttons + Force Schedule + New Match + Block Day)
  proposal.py        — ProposalView (Approve / Reject / Delete Events / Block Day buttons)
  confirm_view.py    — ConfirmationView (Ready / Reject buttons for per-talent confirmation)
```

## Key Flows

1. **Match posted** → `on_message` → `parse_post` → `schedule_for_date` (per-date asyncio lock prevents races)
2. **Scheduling** → `accept_combination` (direct add) OR `propose_change` (sent to log channel with `ProposalView`)
3. **Proposal** → manager clicks Approve/Reject/Delete/Block Day buttons → `apply_pending_change` or custom handler
4. **Sign-up** → talent clicks role button in sign-up channel → `upsert_signup` → `build_signup_message` edit
5. **Deadline check (every 5 min)** → if staffed: `send_allocation_request`; if understaffed: LAST CALL edit; if past call time and still understaffed: cancel
6. **Talent allocation** → `AllocationView` (4 required-role selects + optional host/analyst + Confirm/Cancel) sent to log channel
7. **Talent confirmation** → `ConfirmationView` in broadcast channel → talent clicks Ready/Reject → on all ready: TeamUp event moved to Accepted subcalendar

## Important Behaviour Notes

- **Sign-up messages** go to `signup_channel` (falls back to `broadcast_channel` if not set).
- **Proposals, talent allocation UIs, and LOGGED MATCHES announcements** go to the **log channel**.
- **Talent confirmation messages** go to the **broadcast channel**.
- **Sign-up deadline is 2 hours before match time** (`SIGNUP_DEADLINE_SECONDS_BEFORE = 2 * 3600`).
- **Call time is 30 minutes before match** — if crew is still incomplete at call time, the match is cancelled.
- **`is_fully_staffed`** requires all 4 required roles filled + PBP ≠ Colour + ≥ 3 unique users. Producer/Observer may share a person.
- **Force Schedule** (formerly Force Start) on the sign-up view bypasses `is_fully_staffed` and triggers allocation immediately.
- **Proposals and pending changes** are stored in `pending_changes` table; 12-hour auto-approve window.
- **`get_teamup()`** creates a new `TeamUpClient` instance each call (reads config from DB).
- **Persistent views** are re-registered on bot startup: `SignUpView`, `ProposalView`, `ConfirmationView`. Allocation views are NOT persistent — managers use Force Schedule to re-trigger after a restart.
- **TeamUp PUT requires `"id"` in the request body** (see `teamup.py update_event`).
- **NO STREAM block events** use 00:01–23:59 Eastern Time (not UTC) to avoid calendar day bleed.
- **Match events** use `match_end_ts(start_ts)` (in `scheduler.py`) which caps the end at 23:59:59 ET on the same day — prevents 22:00 matches from bleeding into the next calendar day.
- **`_SEPARATOR`** (`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`) is prepended to all bot-generated messages. Defined in `scheduler.py` and imported by other modules.
- **New Match button** skips the pending-proposals check for accepted matches (emergency swap). Scopes the check to the day via `get_pending_changes_for_date`, not globally.
- **Sign-up message states**: Active → LAST CALL (deadline missed, crew incomplete) → APPROVED (all talent confirmed, view replaced with `ApprovedSignUpView`) → CANCELLED (management cancel or call-time miss). Each state transition edits the message in-place.
- **Approved match swap (New Match on APPROVED)**: edits sign-up to CANCELLED with talent @mentions, edits the talent confirmation message to show replacement, sends a ping in the signup channel, then schedules the new match normally.

## Running the Bot

```bash
pip install -r requirements.txt
# Create .env with DISCORD_BOT_TOKEN=...
python bot.py
```

## Automated Tests

Tests live in `tests/`. Run them with:

```bash
python -m pytest tests/ -v
```

**No external services needed** — tests use in-memory SQLite (`:memory:`) and mock HTTP calls.

### Test Files

| File | Covers |
|---|---|
| `tests/test_parser.py` | Match post parsing, division/team/timestamp extraction, structure detection |
| `tests/test_scheduler.py` | Scheduling algorithm, scoring, combinations, `is_fully_staffed`, sign-up message format |
| `tests/test_database.py` | All DB CRUD: matches, teams, signups, managers, allocations, broadcast messages, season reset |
| `tests/test_teamup.py` | TeamUp API client — HTTP payload shape, subcalendar routing, error handling |

### Testing Guidelines

**Run tests after every non-trivial change.** If you add or change a feature, add tests for it following these rules:

1. **Test business logic, not mocks.** Mocks are allowed for external I/O (Discord, HTTP), but the test must exercise real logic — not just assert that a mock was called.

2. **Don't 1:1 copy the implementation.** Tests should describe *what* the behaviour should be, not mirror the *how*. A test like `assert result == "Division 1"` is good; a test that re-implements the regex is not.

3. **In-memory SQLite for DB tests.** Use `Database(":memory:")` — never the real `bot.db`.

4. **Regression tests for bugs.** When a bug is fixed, add a test that would have caught it. Example: `test_update_event_includes_id_in_payload` covers the TeamUp "id missing" 400 error.

5. **Keep tests fast and side-effect-free.** No file I/O beyond `:memory:`, no network calls, no Discord API calls.

### Adding Tests for New Features

When you add or change something:
- If it's pure logic in `scheduler.py` or `parser.py` → add to the corresponding test file
- If it's a new DB method → add to `test_database.py` using the `db` fixture
- If it's a TeamUp API change → add to `test_teamup.py` with a mocked session
- If it changes message format (sign-up message, proposal, confirmation) → add a format assertion test in `test_scheduler.py` or a new file
