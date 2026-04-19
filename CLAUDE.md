# DSE Scheduler Bot — Claude Context

A Discord bot for managing esports broadcast scheduling, talent sign-ups, and TeamUp calendar integration.

## Project Overview

- **Language**: Python 3.12, discord.py 2.x, APScheduler, SQLite via `database.py`
- **Entry point**: `bot.py` — loads cogs and starts the scheduler
- **Core modules**: `parser.py`, `scheduler.py`, `database.py`, `teamup.py`
- **Cogs**: `cogs/admin.py`, `cogs/blocks.py`, `cogs/events.py`, `cogs/signup.py`, `cogs/talent.py`, `cogs/weekly_proposals.py`, `cogs/confirm_view.py`

## Architecture

```
bot.py                — bot setup, scheduled jobs, cog loading, persistent view registration
parser.py             — match post parsing (text → ParsedMatch)
scheduler.py          — sign-up message builders, is_fully_staffed, accept_combination
database.py           — SQLite wrapper (all DB access goes through here)
teamup.py             — TeamUp REST API client
cogs/
  admin.py            — /set-*, /accept-broadcast, /add-manager, /new-season, /set-timezone,
                        /post-weekly-proposals, /clear-message-history, etc.
  blocks.py           — /block-day, /unblock-day, /list-blocks
  events.py           — on_message (match parsing), dispatches "match_logged" event
  talent.py           — AllocationView (Phase 1: 4 required role selects + Continue/Cancel)
                        AllocationConfirmView (Phase 2: Host/Analyst selects + Confirm/Cancel)
  signup.py           — SignUpView (role buttons + Unavailable + Force Schedule + New Match + Block Day)
  weekly_proposals.py — ProposalDayView, BlockedDayView, create_weekly_proposals, mark_passed_proposals
  confirm_view.py     — ConfirmationView (Ready / Reject buttons for per-talent confirmation)
```

## Key Flows

1. **Match posted** → `on_message` → `parse_post` → log to DB → `bot.dispatch("match_logged", date_str)`
2. **Match logged** → `WeeklyProposalsCog.on_match_logged` → refresh proposal message for that date
3. **Proposals** → manager uses slot dropdowns in Proposal Channel (one message per day, posted Sunday 11pm ET or via `/post-weekly-proposals`), clicks Update Schedule → `accept_combination` for new slots, unschedule removed slots
4. **Sign-up messages** → posted when manager clicks Update Schedule on a proposal → include `@Talent Role` mention
5. **Sign-up** → talent clicks role button → `upsert_signup` → `build_signup_message` edit; first time filling staffing criteria → notify log channel with `@Manager Role` ping
6. **Deadline check (every 5 min)** → if staffed: `send_allocation_request`; if understaffed: LAST CALL edit; if past call time and still understaffed: cancel
7. **Talent allocation** → two-phase flow in log channel:
   - **Phase 1** (`AllocationView`): Producer / Observer / Play-by-Play / Colour single-selects (all sign-ups shown in each), Continue → / Cancel Broadcast buttons. Continue validates required roles, PBP ≠ Colour, Producer/Observer not in casters.
   - **Phase 2** (`AllocationConfirmView`): Host (optional) / Analyst (optional) selects + Confirm Allocation / Cancel Broadcast. Message is edited in-place on Continue.
8. **Talent confirmation** → `ConfirmationView` in broadcast channel → talent clicks Ready/Reject → on all ready: TeamUp event moved to Accepted subcalendar; on Reject: allocation reset, AllocationView re-posted

## Important Behaviour Notes

- **Sign-up messages** go to `signup_channel` (falls back to `broadcast_channel` if not set).
- **Proposals and talent allocation UIs** go to the **log channel**.
- **Talent confirmation messages** go to the **broadcast channel**.
- **Schedule update pings** go to `schedule_updates_channel_id` when a proposal change, cancellation, or block affects users who already signed up. Falls back to broadcast channel if not set.
- **Broadcast Cancelled notification** is sent to `schedule_updates_channel_id` (falls back to broadcast channel). The sign-up message is always edited to show CANCELLED regardless.
- **Sign-up deadline is 2 hours before match time** (`SIGNUP_DEADLINE_SECONDS_BEFORE = 2 * 3600`).
- **Call time is 30 minutes before match** — if crew is still incomplete at call time, the match is cancelled.
- **`is_fully_staffed`** requires all 4 required roles filled + PBP ≠ Colour + Producer/Observer not in PBP/Colour sets + ≥ 3 unique users across required roles. Producer and Observer may share a person. This governs sign-up sufficiency (not allocation). Up to 2 people can sign up for PBP or Colour to give managers options.
- **Allocation role keys**: `producer`, `observer`, `pbp_1`, `colour_1` (required); `host`, `analyst_1` (optional). Managers select exactly one PBP and one Colour in the allocation UI.
- **Force Schedule** on the sign-up view bypasses `is_fully_staffed` and triggers allocation immediately.
- **`get_teamup()`** creates a new `TeamUpClient` instance each call (reads config from DB).
- **Persistent views** re-registered on bot startup: `SignUpView`, `ApprovedSignUpView`, `ConfirmationView`, `ProposalDayView` (open proposals), `BlockedDayView` (blocked proposals). Allocation views are NOT persistent — managers use Force Schedule to re-trigger after a restart.
- **`_proposal_selections` cache**: stored on `interaction.client._proposal_selections` as a dict keyed by `(date_str, slot)`. `_ProposalSlotSelect.callback` writes to it; `_UpdateScheduleButton.callback` reads and clears it. Fallback is the DB-saved slot values. Required because `interaction.message.components` only reflects default values, not the user's live in-session selection.
- **TeamUp PUT requires `"id"` in the request body** (see `teamup.py update_event`).
- **NO STREAM block events** use 00:01–23:59 Eastern Time (not UTC) to avoid calendar day bleed.
- **Match events** use `match_end_ts(start_ts)` (in `scheduler.py`) which caps the end at 23:59:59 ET on the same day — prevents 22:00 matches from bleeding into the next calendar day.
- **`_SEPARATOR`** (`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`) is prepended to all bot-generated messages. Defined in `scheduler.py` and imported by other modules.
- **New Match button** on sign-up view allows emergency match swap (skips proposal flow). Scopes duplicate check to the day via `get_pending_changes_for_date`.
- **Sign-up message states**: Active → LAST CALL (deadline missed, crew incomplete) → APPROVED (all talent confirmed, view replaced with `ApprovedSignUpView`) → CANCELLED (management cancel or call-time miss). Each state transition edits the message in-place.
- **Approved match swap (New Match on APPROVED)**: edits sign-up to CANCELLED with talent @mentions, edits the talent confirmation message to show replacement, sends a ping in the signup channel, then schedules the new match normally.
- **Unavailable button** (red, row 1 of sign-up view): marks user as unavailable for that match; calls `remove_all_signups_for_user` and `increment_talent_unavailable`. Clicking a role while unavailable removes the unavailable flag. First interaction increments `response_count` in the `talent` table.
- **Sign-up button row layout**: Row 0 — Producer/Observer/Play-by-Play/Colour Caster (blue). Row 1 — Host (green) / Analyst (green) / Unavailable (red). Row 2 — Force Schedule (green) / New Match (grey) / Block Day (red).
- **Criteria met notification**: when `is_fully_staffed` first becomes true for a match and status is "pending", signup.py sets status to "criteria_met" and sends a notification to the log channel with a `@Manager Role` ping and link to the sign-up message.
- **Weekly proposals**: every Sunday 11pm ET, `create_weekly_proposals` posts 7 messages (Mon–Sun) to `proposal_channel_id`. Each message shows Current Schedule, Logged Matches, two slot dropdowns, and Update Schedule / Clear Selections / Block Day buttons. When a day is blocked, buttons are replaced with a single Unblock Day button (`BlockedDayView`). `mark_passed_proposals` (called in each scan job) marks past proposals "passed" and removes their buttons.
- **`/post-weekly-proposals`**: manual command to create proposals from today through this Sunday. Has a `force: bool` param; without `force=True`, skips days that already have a Discord message posted.
- **Scan schedule**: 9am, 6pm, 11:59pm ET (skipped Sundays). Each scan calls `events_cog._scan_match_history(limit=500)` then `mark_passed_proposals`.
- **Command permission levels**: Configuration commands (`/set-*`, `/unset-*`, `/status`, `/test-teamup`, `/clear-message-history`) → Administrator. Match management (`/sync-history`, `/announce-matches`, `/accept-broadcast`, `/broadcast-done`, `/set-timezone`, `/post-weekly-proposals`) → Manager. Manager management (`/add-manager-role`, `/remove-manager-role`, `/add-manager`, `/remove-manager`) → Administrator; `/list-managers` → Manager. Day blocking (`/block-day`, `/unblock-day`, `/list-blocks`) → Manager. Season reset (`/new-season`, `/reset`) → Administrator. `/talent` → any user.
- **Manager role auto-assignment**: `manager_role_id` config key stores the Discord role ID set via `/add-manager-role`. `/add-manager` and `/remove-manager` require this to be set first; they call `member.add_roles`/`member.remove_roles`. Bot needs Manage Roles permission and its role must be above the manager role in the hierarchy. If role assignment fails with `discord.Forbidden`, the DB change still completes and a warning is sent.
- **`/broadcast-done`** is not required for bot-managed broadcasts — the bot increments team tallies automatically when all talent confirm. Use only for broadcasts completed outside of the bot's normal flow.
- **`/talent` display format**: `{display_name} ({username}) — {bc} - {rc} - {uc}` (broadcast count, response count, unavailable count), sorted by `SUM(broadcast_count + response_count + unavailable_count) DESC`.
- **`/clear-message-history`**: purges all messages from the specified channel using `channel.purge(limit=None)`. Administrator only.

## Config Keys (stored in DB via `/set-*` commands)

| Key | Set by | Purpose |
|---|---|---|
| `log_channel_id` | `/set-log-channel` | Allocation UIs, match announcements, errors |
| `broadcast_channel_id` | `/set-broadcast-channel` | Talent confirmation messages |
| `signup_channel_id` | `/set-signup-channel` | Sign-up messages (falls back to broadcast) |
| `match_channel_id` | `/set-match-channel` | Scanned for match posts |
| `proposal_channel_id` | `/set-proposal-channel` | Weekly proposal messages |
| `schedule_updates_channel_id` | `/set-schedule-updates-channel` | Pings when schedule changes or cancellations affect signed-up talent |
| `talent_role_id` | `/add-talent-role` | @mention included in sign-up messages |
| `manager_role_id` | `/add-manager-role` | @mention in criteria-met notifications; used by /add-manager |
| `teamup_api_key` | `/set-teamup-key` | TeamUp API authentication |
| `teamup_calendar_id` | `/set-teamup-calendar` | TeamUp calendar root key |

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
| `tests/test_scheduler.py` | `is_fully_staffed` rules, sign-up message format, `build_approved_signup_message` |
| `tests/test_database.py` | All DB CRUD: matches, teams, signups, managers, allocations, broadcast messages, proposals, talent counts (broadcast/response/unavailable), season reset |
| `tests/test_teamup.py` | TeamUp API client — HTTP payload shape, subcalendar routing, error handling |
| `tests/test_scheduling_flow.py` | `proposal_messages` DB layer — create, update slots, set status, get open/blocked/week |
| `tests/test_events_cog.py` | `_scan_match_history` and `on_message` — match insertion, duplicate skipping, `match_logged` dispatch |
| `tests/test_view_callbacks.py` | Ready/Reject/Finalize (confirm_view), `_ContinueButton` phase-1 validation, `_ConfirmButton` (AllocationConfirmView), `cancel_broadcast`, `_NewMatchSelect` flow; `build_confirmation_message` and `build_talent_description_from_assignments` |
| `tests/test_weekly_proposals.py` | `build_proposal_day_content` content sections, `mark_passed_proposals` state transitions, `_UnblockDayButton` (reverts status, deletes TeamUp event) |

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
- If it changes message format (sign-up message, confirmation, proposal content) → add a format assertion test in the relevant test file
- If it's a Phase 1 allocation flow → add to `TestContinueButton` in `test_view_callbacks.py`
- If it's a Phase 2 allocation flow → add to `TestConfirmButton` using `_make_phase2_view` and real DB
- If it's weekly proposal logic or proposal view callbacks → add to `test_weekly_proposals.py`
- Call `db.create_allocation(match_id)` before testing `_ConfirmButton` paths that reach `set_allocation_assignments`
