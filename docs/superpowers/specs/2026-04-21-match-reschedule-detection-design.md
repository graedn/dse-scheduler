# Match Reschedule Detection — Design Spec
_2026-04-21_

## Problem

When a team edits their match post or posts a new message for the same matchup in the same week with a different time, the bot currently logs a second entry alongside the original. This leaves ghost entries in Logged Matches and can cause sign-up messages and proposal slots to reference stale data.

---

## Detection

Both trigger paths re-use the same reschedule check.

**Trigger A — Message edit**
`on_raw_message_edit` listener in `events.py`. Fetch and re-parse the edited message. If it parses as a valid match post and the message is in the configured match channel, run the reschedule check.

**Trigger B — New message, same week**
In the existing `on_message`, after parsing succeeds and before inserting, run the reschedule check.

**The check**
Look up any existing DB match with the same `(team_home, team_away)` whose `match_time` falls in the same Monday 00:00 ET → Sunday 23:59:59 ET window. Week boundaries are computed in ET.

- Found, same timestamp → existing duplicate logic (no-op, silently ignore).
- Found, different timestamp → reschedule flow.
- Not found → normal new-match insert.

**New DB method**
`get_match_by_teams_in_week(team_home, team_away, week_start_ts, week_end_ts) → Optional[dict]`

---

## State-Based Handling

### States 1–3: Pre-confirmed matches

| State | Condition | Automated action |
|---|---|---|
| Just logged | No proposal slot, no sign-up message | Delete old, insert new |
| In proposal slot | Proposal slot assigned, no signups yet | Clear slot, delete old, insert new |
| Has signups | Sign-up message exists with signups | Cancel sign-up (edit to CANCELLED), notify talent, clear slot, delete old, insert new |

**In all three states**, the log channel receives:
> ⚠️ **Match Rescheduled** — `@ManagerRole`
> `[Division] Team A vs Team B`
> ~~`<t:old_ts:F>`~~ → `<t:new_ts:F>`
> *(+ status line: "removed from proposal" / "sign-up cancelled, talent notified")*

`match_logged` is dispatched for the new date (and old date if different) to refresh proposal messages.

**Talent notification (State 3 only)** — sent to `schedule_updates_channel` (falls back to broadcast channel):
> 📢 **Schedule Update** — `[Division] Team A vs Team B` has been rescheduled.
> ~~`<t:old_ts:F>`~~ → `<t:new_ts:F>`
> `@mention1 @mention2` — your sign-up has been removed. A new sign-up will be posted if the slot is rescheduled.

**DB operations for States 1–3**
- `delete_match_cascade(match_id)` — deletes match row + signups + broadcast_messages + allocation
- `clear_match_from_proposal_slots(match_id)` — sets `slot1_match_id`/`slot2_match_id` to NULL where they reference the old match
- `insert_match(...)` for the new time

### State 4: Confirmed broadcast (`broadcast_accepted = 1`)

No automatic action. The log channel receives a manager ping with three buttons:

---

**⚙️ Update Broadcast**
> The match time has changed. Update the broadcast to reflect the new time — notify assigned talent, update the calendar. No changes to the crew.

**Action:** Update `match_time` in DB → update TeamUp event to new time → edit sign-up message to reflect new time (APPROVED state retained) → edit confirmation message → notify talent in `schedule_updates_channel`.

---

**🔄 Initiate Sign Up**
> Start fresh sign-ups for the rescheduled time. The existing sign-up is marked as rescheduled and a new one is posted. Assigned talent are notified.

**Action:** Edit old sign-up message to RESCHEDULED (no buttons) → reset allocation → post new sign-up message for new match time → notify talent in `schedule_updates_channel`.

---

**❌ Cancel Broadcast**
> Cancel this broadcast entirely. The sign-up is closed and all assigned talent are notified.

**Action:** Reuse existing `_cancel_broadcast` logic from `cogs/talent.py`.

---

The `RescheduleView` is posted once; after any button is clicked the buttons are disabled and the message is edited to show which option was chosen.

**New file:** `cogs/reschedule.py` — contains `RescheduleView(match_id, old_ts, new_ts)` and its three button classes.

---

## Thread Notifications (Confirmed State Only)

After the manager selects an option, a follow-up message is sent into the match's existing thread (`thread_messages` looked up by `match_id`). If no thread exists the step is silently skipped.

| Action chosen | Thread message |
|---|---|
| Update Broadcast | "The updated match time has been approved by the broadcast team." |
| Initiate Sign Up | "A new match time has been detected, the broadcast team are checking for availability. If approved a new match thread will be created." |
| Cancel Broadcast | "A new match time has been detected, the broadcast team has decided to cancel this stream." |

**Mentions:** Each thread message @mentions the same parties as the original thread post, reconstructed from:
- `league_admin_role_id` config
- `team1_role_id` / `team2_role_id` from `thread_messages`
- Producer and Observer user IDs from the match's `talent_allocations`

---

## New DB Methods Summary

| Method | Purpose |
|---|---|
| `get_match_by_teams_in_week(team_home, team_away, week_start_ts, week_end_ts)` | Find same-matchup in same Mon–Sun ET week |
| `delete_match_cascade(match_id)` | Delete match + signups + broadcast_messages + allocation |
| `clear_match_from_proposal_slots(match_id)` | Null out proposal slot references |
| `update_match_time(match_id, new_ts)` | Update match timestamp in-place (Update Broadcast) |

---

## Files Changed

| File | Change |
|---|---|
| `database.py` | 4 new methods above |
| `cogs/events.py` | `on_raw_message_edit`, `_handle_reschedule()` helper |
| `cogs/reschedule.py` | New — `RescheduleView` + 3 button classes |
| `cogs/threads.py` | `send_thread_reschedule_notice(bot, match_id, message)` helper |
| `bot.py` | Register `RescheduleView` as persistent view on startup? No — not needed, one-shot per reschedule event |

---

## Out of Scope

- Reschedule detection during history scan (`_scan_match_history`) — scan only inserts, does not deduplicate across timestamps.
- Handling a match that was rescheduled multiple times in a short window (last-writer-wins is acceptable).
- Approved matches that are rescheduled to a different day while the thread ready-check is in flight.
