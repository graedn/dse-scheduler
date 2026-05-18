# Allocation Edit, Carry-Over & Optional-Role Confirmation — Design Spec
_2026-05-02_

## Problem

Every allocation change currently pings everyone and forces full re-confirmation:

- Swapping a proposal slot to a different matchup at the **same time** discards all sign-ups, the allocation, and every confirmation — even though talent availability is time-based, not opponent-based.
- A single Reject resets the entire allocation and re-posts the full two-phase `AllocationView`; all talent get re-pinged and must re-confirm.
- After a broadcast is approved there is no way to swap one person who drops out without cancelling and rebuilding.
- Optional roles (Host / Analyst) have no confirmation status in the confirmation message at all.

Goal: make allocation changes **surgical** — only the person who actually changed is pinged — and stop throwing away still-valid state when only the opponent changes.

---

## Decisions (locked during brainstorming)

| Topic | Decision |
|---|---|
| Confirmed broadcast, slot swapped to same-time matchup | **Carry silently, notify only.** Crew + confirmations + approved state preserved; one notice posted (thread + updates channel); nobody re-confirms. |
| Single-role replacement while waiting on the new person | **Hold approved, track silently.** Broadcast stays APPROVED / on Accepted calendar; only the replacement is pinged; confirmation message shows them `[No Response]`; re-finalize is a safe no-op if already accepted. |
| Optional-role (Host/Analyst) confirmations | **Track but don't gate.** They get Ready/Reject + a status row + Awaiting mention, but finalization gates on the 4 required roles only. An optional Reject flags the manager (no auto-replace prompt). |
| Replacement candidate pool | **Manager picks from anyone who signed up for the match** (minus Unavailable). May re-pick someone already in another slot. |
| Carry-over trigger paths | **All three:** proposal slot swap, New Match button, match-post edit (same time, different teams). |
| Edit Allocation button scope | **Per-role swap, repeatable.** Pick a role → pick replacement → Apply; repeat for other roles; can fill/clear empty optional slots. Only the changed person is pinged. |

---

## Architecture

Approach A (selected): one shared single-role-replacement mechanism plus carry-over DB copy helpers. No schema migration. Reuses existing patterns: `discord.ui.View` + `Select` + `Button`, JSON `confirmations`/`role_assignments`, `_finalize_match`, `_manager_check`.

Rejected alternatives:
- **B — inline each feature separately:** duplicates replace/confirm logic across `confirm_view`, `talent`, approved-message handler; guaranteed drift.
- **C — refactor `confirmations` to role-keyed `{role_key: {...}}`:** cleaner model but a format change touching every confirmation read/write and all persisted allocations; blast radius exceeds the problem.

---

## Component 1 — Carry-over on match change (same time)

Module-level helper (in `cogs/talent.py`, allocation domain):

```
async def carry_over_if_same_time(bot, db, old_match_id, new_match_id) -> str | None
```

Returns a short status note, or `None` when `match_time` differs (caller falls back to today's fresh-start behaviour).

When `old.match_time == new.match_time`:

- **DB copy** (new methods, see Component 5): `copy_signups(old_id, new_id)` re-keys `broadcast_signups` rows to the new match_id, preserving `role`, `user_id`, `username`, `display_name`, `signed_up_at`. `copy_allocation(old_id, new_id)` copies the `talent_allocations` row's `role_assignments`, `confirmations`, `status`, `confirmation_message_id`, `confirmation_channel_id`, `allocation_message_id`, `allocation_channel_id`.
- **Stage-aware behaviour** keyed on the carried allocation `status`:
  - **Sign-up stage** (`pending` / `sent` / `last_call`, or no allocation row): copy sign-ups only. The new match's sign-up message reflects carried availabilities. No pings.
  - **`awaiting_confirm`**: copy sign-ups + allocation + confirmations. Edit the **existing** confirmation message in place to the new team names. People already `[Ready]` stay Ready; pending stay pending. Post one notice to the match thread (if any) and the updates channel: "opponent changed, same time — your confirmation still stands."
  - **`accepted`**: copy everything; broadcast stays APPROVED. Edit confirmation message + sign-up message + thread to the new teams; update the TeamUp event title/description to the new teams (same time, same Accepted sub-calendar). One notice; no re-confirm.
- The carried `confirmation_message_id` now points at the new match, so the existing `ConfirmationView` buttons keep working without reposting.
- Old match handling matches today (its TeamUp event removed by the calling path's existing logic). Carry-over runs **before** the path's `reset_allocation`/`delete_match_cascade` so source state is still readable.

**Trigger wiring:**
- **Proposal slot swap** — `cogs/weekly_proposals.py` `_UpdateScheduleButton`: when a slot's old match is replaced by a new match, call `carry_over_if_same_time` before unscheduling the old one; skip the fresh sign-up post when carry-over succeeds.
- **New Match button** — `cogs/signup.py` `_NewMatchSelect`: when the replacement match shares the time, carry over instead of fresh sign-up.
- **Match-post edit (same time, different teams)** — `cogs/events.py` `_handle_reschedule`: detection precedence is (1) existing exact-team reschedule check (same `team_home`/`team_away`, different time) first; (2) **else** if the edited post's `match_time` exactly equals an existing scheduled match (`teamup_event_id IS NOT NULL`) in the same Mon–Sun ET week whose teams differ, treat it as a same-slot swap and run `carry_over_if_same_time` against that match. New DB lookup: `get_scheduled_match_at_time_in_week(match_time, week_start_ts, week_end_ts) -> Optional[dict]`.

---

## Component 2 — Single-role replacement core

**Helper** (in `cogs/talent.py`):

```
async def replace_allocation_role(bot, db, match_id, role_key, new_assignment | None) -> None
```

- Loads allocation, parses `role_assignments`.
- Sets `role_assignments[role_key]` to the new person, or removes the key when clearing an optional slot (`new_assignment is None`).
- `confirmations`: remove the outgoing person's entry **iff they hold no other assigned role**; add the new person as pending (`None`) **unless** the new person already holds another role they keep and is already `True` (then preserve `True`).
- Persist via `set_allocation_assignments`. **Status is unchanged** — `awaiting_confirm` stays, `accepted` stays accepted (hold-approved-track-silently).
- Rebuild and **edit the existing confirmation message** in place (new lineup; the replaced row shows `[No Response]`).
- Send a **separate short ping to the new person only**, in the broadcast channel as a reply to the confirmation message: `@NewPerson — you've been assigned **<Role>** for [Division] A vs B | <t:ts:F>. Please confirm on the message above.` No one else is pinged.
- If status was `accepted`, the TeamUp event stays on the Accepted calendar; when the replacement clicks Ready, the existing `ReadyButton → _finalize_match` path runs and is a **safe no-op on an already-accepted match** (re-marks accepted, refreshes the TeamUp description with the new roster, re-edits the APPROVED sign-up roster).

**`ReplaceRoleView`** (in `cogs/talent.py`; posted to the **log channel**, `_manager_check`-gated):

- Row 0 — role `Select`: Producer / Observer / Play-by-Play / Colour Caster / Host *(optional)* / Analyst *(optional)*. Selecting an unassigned optional slot = "add"; an assigned slot = "replace".
- Row 1 — candidate `Select`: every distinct user who signed up for this match, minus those marked Unavailable, sorted by sign-up time, label `display_name [N bcasts] (signed-up role)`. Plus a `— Clear (optional roles only) —` sentinel option.
- Row 2 — **Apply** (validates, then calls `replace_allocation_role`) / **Done** (stops the view, disables children, edits the message to a closed state).
- Repeatable: after Apply the view persists so the manager can swap another role; Done closes it. `timeout=86400` like the existing allocation views (not persistent across restarts — manager re-invokes via Edit Allocation if needed).
- Validation on Apply: clearing is allowed only for `host`/`analyst`; required roles must always have an assignee. Selecting the same person already in that role is a no-op with an ephemeral note.

Reject→replace and Edit Allocation both spawn this one view (or call the helper directly). It is the single source of truth.

---

## Component 3 — Reject → single-role replace

Rewrites `RejectButton` in `cogs/confirm_view.py`.

**Required-role rejecter:**
- `set_confirmation(match_id, user_id, False)`; mark them Unavailable + `increment_talent_unavailable` (existing behaviour from the prior Unavailable fix).
- Edit the confirmation message in place so they show `[Rejected]` (existing behaviour).
- **No `reset_allocation`.** Other talents' confirmations are preserved.
- Post a `ReplaceRoleView` to the **log channel** with a manager ping, **pre-selected to the rejected role**: `❌ {name} rejected **<Role>** for [match]. Pick a replacement — only the new person will be pinged.`
- Manager picks the replacement → `replace_allocation_role` → only the replacement is pinged; everyone else keeps `[Ready]`.
- Broadcast stays in `awaiting_confirm` / `accepted`; it just waits on the one new person.

**Optional-role (Host/Analyst) rejecter:**
- Record the rejection; show `[Rejected]` on the message.
- **No auto replace prompt.** Post an informational flag to the log channel with a manager ping: `⚠️ {name} rejected the optional **<Role>** for [match]. Use Edit Allocation to replace them if needed.`
- Finalization unaffected (optional doesn't gate).

---

## Component 4 — Edit Allocation button on approved broadcasts

New `EditAllocationButton` added to `ApprovedSignUpView` in `cogs/signup.py`. New row layout: New Match / Block Day / Create Thread / **Edit Allocation**.

- `_manager_check`-gated; non-managers get the standard ephemeral refusal.
- On click: posts a fresh `ReplaceRoleView` to the **log channel** (not ephemeral — persists, visible to other managers), titled `✏️ Editing allocation — [Division] A vs B | <t:ts:F>`, no role pre-selected.
- Manager picks role → picks replacement (or Clear for optional) → Apply; repeatable; Done closes.
- Each Apply runs `replace_allocation_role`; lineup updated, confirmation message edited, **only the changed person pinged**. Broadcast stays APPROVED / Accepted throughout.
- The replacement clicking Ready re-runs `_finalize_match` (no-op-safe; refreshes TeamUp roster description + the APPROVED sign-up roster).
- Edge: if a required role's holder is replaced and the new person hasn't confirmed, the broadcast is **not** reverted; the confirmation message shows that role `[No Response]`, and a transient note is appended to the approved sign-up message: `⚠️ Lineup edited — awaiting confirmation from {new person}.` cleared when they confirm.

---

## Component 5 — Optional-role confirmations + data model

**Confirmations model (no schema migration).** Today `confirmations` (`talent_allocations.confirmations`, JSON) holds only required-role user IDs. Change: at allocation-confirm time, populate it with **every assigned person** (required + Host/Analyst). Finalization gating moves from "all values `True`" to "all **required-role** users `True`", where required user IDs come from the existing `_get_required_user_ids(role_assignments)` (producer/observer/pbp_1/colour_1). Optional users are displayed and awaited but never gate.

**`build_confirmation_message`** (`cogs/confirm_view.py`): optional roles render with the same `[Ready] / [Rejected] / [No Response]` tag as required (today they render plain `(optional): @user — name`). The "Awaiting confirmation from" line includes pending optional users. Required-vs-optional gating is internal only; the message reads uniformly.

**`_finalize_match` / ReadyButton** (`cogs/confirm_view.py`): readiness check becomes "all required-role users are `True`" instead of "all `confirmations` values `True`". `ReadyButton`/`RejectButton` already resolve the clicker by `user_id` against `confirmations`, which now also contains optional users — no view change needed. `ConfirmationView` stays the persistent 2-button view keyed by match_id.

**New DB methods** (`database.py`, no `ALTER TABLE`):
- `copy_signups(old_match_id, new_match_id) -> int` — inserts copies of `broadcast_signups` rows under the new match_id; returns count.
- `copy_allocation(old_match_id, new_match_id) -> None` — upserts the `talent_allocations` row for the new match with the old row's `role_assignments`, `confirmations`, `status`, `confirmation_message_id/channel_id`, `allocation_message_id/channel_id`.
- `get_scheduled_match_at_time_in_week(match_time, week_start_ts, week_end_ts) -> Optional[dict]` — a scheduled (`teamup_event_id IS NOT NULL`) match at exactly `match_time` within the ET week, used by the match-post-edit same-time-different-teams detection.

`set_allocation_assignments` already serialises the `confirmations` dict verbatim, so the larger dict flows through with no signature change.

---

## Files Changed

| File | Change |
|---|---|
| `database.py` | `copy_signups`, `copy_allocation`, `get_scheduled_match_at_time_in_week` |
| `cogs/talent.py` | `replace_allocation_role`, `ReplaceRoleView`, `carry_over_if_same_time` |
| `cogs/confirm_view.py` | `RejectButton` rewrite (required vs optional); optional-role rendering in `build_confirmation_message`; required-only finalize gate |
| `cogs/signup.py` | `EditAllocationButton` on `ApprovedSignUpView`; New Match carry-over wiring |
| `cogs/weekly_proposals.py` | Proposal-swap carry-over wiring in `_UpdateScheduleButton` |
| `cogs/events.py` | Same-time-different-teams detection + carry-over in `_handle_reschedule` |
| `tests/test_database.py` | `copy_signups`, `copy_allocation`, `get_scheduled_match_at_time_in_week` |
| `tests/test_view_callbacks.py` | `replace_allocation_role`, `ReplaceRoleView`, reject→replace (required/optional), `EditAllocationButton` gating, required-only finalize gate, optional rendering |
| `tests/test_weekly_proposals.py` | Proposal-swap carry-over (same time vs different time) |
| `tests/test_events_cog.py` | Match-post-edit same-time-different-teams carry-over and fallback |

---

## Implementation Phasing

Each phase is independently testable and shippable.

1. DB copy/lookup helpers + confirmations model change (`database.py`, finalize gate).
2. `replace_allocation_role` + `ReplaceRoleView` (`cogs/talent.py`).
3. Reject → single-role replace rewrite (`cogs/confirm_view.py`).
4. Edit Allocation button (`cogs/signup.py`).
5. Optional-role confirmation rendering + Awaiting list (`cogs/confirm_view.py`).
6. Carry-over wiring into the three change paths (`weekly_proposals.py`, `signup.py`, `events.py`).

---

## Testing Approach

In-memory SQLite (`Database(":memory:")`) + mocked Discord, matching the existing suites.

- **DB**: copy helpers re-key rows and preserve JSON; same-time lookup returns the right scheduled match and excludes unscheduled ones.
- **Replace core**: only the swapped role's confirmation resets; other confirmations preserved; `accepted` stays `accepted`; clearing allowed for optional only; outgoing user's `confirmations` entry removed only when they hold no other role.
- **Reject→replace**: required rejecter triggers a pre-selected `ReplaceRoleView` and does not reset others; optional rejecter only flags.
- **Edit Allocation**: manager-gated; Apply runs the helper; broadcast stays approved; transient awaiting note added/cleared.
- **Optional confirmations**: `[Ready]/[Rejected]/[No Response]` rendered for Host/Analyst; Awaiting includes pending optionals; `_finalize_match` finalizes with optionals still pending but blocks while a required role is pending.
- **Carry-over**: each of the three paths copies state when `match_time` is equal and falls back to fresh-start when it differs.

---

## Out of Scope

- Editing the **time** of an already-allocated match (handled by existing reschedule detection / RescheduleView).
- Multi-match (paired-slot) atomic carry-over interactions beyond per-match copy.
- Persisting `ReplaceRoleView` across bot restarts (manager re-invokes via Edit Allocation; consistent with existing non-persistent allocation views).
- Notifying talent of an opponent change when the time also changed (that is a reschedule, not a carry-over).
