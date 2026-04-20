# DSE Scheduler Bot

A Discord bot that monitors your league server for match posts, manages a weekly broadcast schedule via proposal messages, coordinates talent sign-ups and allocation per match, and syncs everything to a TeamUp calendar.

---

## First-Time Setup

Run these slash commands after inviting the bot to your server(s):

### Required

```
/set-match-channel #channel
```
The channel in your league server where players post their match times.

```
/set-broadcast-channel #channel
```
The channel for talent confirmation messages.

```
/set-log-channel #channel
```
The channel for allocation UIs, match announcements, weekly proposals, and bot logs.

```
/set-teamup-calendar <calendar-id>
```
Found in your TeamUp URL: `teamup.com/YOUR-CALENDAR-ID`

```
/set-teamup-key <api-key>
```
Generate one from your TeamUp account settings.

### Recommended

```
/set-signup-channel #channel
```
Where talent sign-up messages are posted. Falls back to the broadcast channel if not set.

```
/set-proposal-channel #channel
```
Where weekly schedule proposal messages are posted (one per day, Mon–Sun). Falls back to the log channel if not set.

```
/set-schedule-updates-channel #channel
```
Where pings go when a schedule change or cancellation affects talent who already signed up. Falls back to the broadcast channel if not set.

```
/add-manager-role @role
```
The Discord role automatically assigned to managers. Must be set before `/add-manager` can be used.

```
/add-talent-role @role
```
The role @mentioned in sign-up messages to alert talent.

### Check your configuration
```
/status
```
Shows all settings with ✅/❌ indicators.

---

## Match Post Format

Players post their match times in the match channel:

```
Division: Premier
Week: 3
Team Alpha vs Team Beta
Time: <t:1713387600:F>
```

**Accepted variations:**
- `Division:` — with or without a space after the colon
- `Week:` or `Round:` — colon is optional (`Week 3` or `Week: 3`)
- Division names: Premier, Division 1–4 (fuzzy matched)
- `Time:` — must use a Discord timestamp tag (`<t:UNIX:F>`)

**Generating a Discord timestamp:**
Use the built-in `@time` command in Discord: `today 10pm`, `tuesday 18:00`, `sunday 6pm`

> ⚠️ `@time` uses a **24-hour clock** — `6pm` needs `pm`, otherwise it sets 6 AM.

If a post is missing the timestamp or can't be parsed, the bot DMs the player with what went wrong.

---

## Weekly Broadcast Proposals

Every **Sunday at 11pm ET**, the bot posts seven proposal messages (Monday–Sunday) to the Proposal Channel. Managers use these to decide which matches to broadcast each day.

Each proposal message shows:
- **Current Schedule** — matches assigned to broadcast slots
- **Logged Matches** — all other matches posted for that day (unassigned)
- Two slot dropdowns — select up to two matches to broadcast
- **Update Schedule** — confirms the selection, creates TeamUp events, posts sign-up messages
- **Clear Selections** — removes all assignments and cancels sign-up messages for the day
- **Block Day** — marks the day as NO STREAM, cancels any scheduled matches, and replaces buttons with **Unblock Day**
- **Unblock Day** (shown when blocked) — reverts the day to open, removes the NO STREAM TeamUp event

### Manual proposals
If the bot was offline during the Sunday job, or you need to set up proposals mid-week:
```
/post-weekly-proposals
```
Creates proposal messages from today through Sunday. Use `force:True` to overwrite already-posted messages.

### Match logging
When a match is posted in the match channel, the bot logs it and immediately updates the corresponding proposal message so managers can see it in the dropdowns.

You can also trigger a full history scan manually:
```
/sync-history
```

---

## Talent Sign-Up

When a match is confirmed via Update Schedule, the bot posts a sign-up message in the **sign-up channel** with a `@Talent Role` ping. Broadcast team members click buttons to claim roles.

### Roles
| Role | Required | Notes |
|------|----------|-------|
| Producer | ✅ | May double as Observer |
| Observer | ✅ | May be filled by the Producer |
| Play-by-Play | ✅ | Must be a unique person |
| Colour Caster | ✅ | Must be different from PBP |
| Host | Optional | |
| Analyst | Optional | |
| Unavailable | — | Marks you unavailable; removes any existing role sign-ups |

Any number of people can sign up for any role — managers choose one person per role during allocation.

### Criteria for auto-trigger
Sign-ups are considered complete when:
- All four required roles are filled
- PBP and Colour Caster are different people
- At least 3 unique users across required roles (Producer/Observer can share)

When criteria are met, the log channel receives a `@Manager Role` ping with a link to the sign-up message.

### Sign-up timeline
| Event | When |
|-------|------|
| Sign-up deadline | 2 hours before match |
| **❗❗ LAST CALL ❗❗** edit | Deadline passed, crew incomplete |
| Call time | 30 minutes before match |
| Cancellation | Call time passed, crew still incomplete |

When the deadline passes with a full crew, the bot automatically sends the talent allocation UI to the log channel.

### Sign-up message states
| Status | Description |
|--------|-------------|
| Active | Sign-ups open — all role buttons visible |
| ❗❗ LAST CALL | Deadline passed, crew incomplete |
| ✅ APPROVED | All talent confirmed — shows allocated roster |
| ❌ CANCELLED | Cancelled by management or deadline missed |

### Manager-only buttons on sign-up messages
- **Force Schedule** — bypass the deadline and trigger allocation immediately
- **New Match** — swap in a different unscheduled match from the same day
- **Block Day** — block the day and cancel all sign-up messages

---

## Talent Allocation

When sign-ups close with a full crew, the bot posts a two-phase allocation UI in the log channel.

**Phase 1 — Required roles:**
Dropdown selects for Producer, Observer, Play-by-Play, and Colour Caster. All sign-ups appear in every dropdown. Click **Continue →** after making selections. The Continue button validates that:
- All four required roles are selected
- PBP and Colour are different people
- Producer and Observer are not also selected as PBP or Colour

**Phase 2 — Optional roles:**
The message updates in place to show Host and Analyst dropdowns (default: None). Click **Confirm Allocation** to finalise, or **Cancel Broadcast** at any point to abort.

Once confirmed, a **talent confirmation message** is posted in the **broadcast channel**. Each assigned talent must click **Ready**. If anyone clicks **Reject**, the allocation resets and Phase 1 is re-posted.

When all required talent confirm:
- The TeamUp event moves to the **Accepted Calendar**
- Talent broadcast counts are incremented
- A confirmation notice is posted in the log channel

---

## Commands

> **Permission levels**
> - **Administrator** — Discord server administrator only
> - **Manager** — administrators and users added via `/add-manager`
> - **Anyone** — no permission required

### Configuration *(Administrator)*
| Command | Description |
|---|---|
| `/set-match-channel #channel` | Set the channel to watch for match posts |
| `/unset-match-channel` | Stop watching for match posts |
| `/set-broadcast-channel #channel` | Set the channel for talent confirmations |
| `/unset-broadcast-channel` | Remove the broadcast channel |
| `/set-signup-channel #channel` | Set the channel for sign-up messages |
| `/unset-signup-channel` | Remove the sign-up channel (falls back to broadcast) |
| `/set-log-channel #channel` | Set the channel for logs, allocation UIs, and match announcements |
| `/unset-log-channel` | Remove the log channel |
| `/set-proposal-channel #channel` | Set the channel for weekly proposal messages |
| `/unset-proposal-channel` | Remove the proposal channel |
| `/set-schedule-updates-channel #channel` | Set the channel for schedule change pings |
| `/unset-schedule-updates-channel` | Remove the schedule updates channel |
| `/set-teamup-calendar <id>` | Set the TeamUp calendar ID |
| `/set-teamup-key <key>` | Set the TeamUp API key |
| `/add-talent-role @role` | Set the talent role @mentioned in sign-up messages |
| `/remove-talent-role` | Remove the configured talent role |
| `/clear-message-history #channel` | Purge all messages from a channel |
| `/status` | Show current configuration and any missing settings |
| `/test-teamup` | Test the TeamUp API connection |

### Match management *(Manager)*
| Command | Description |
|---|---|
| `/sync-history` | Scan match channel history and log any future matches not yet in the database |
| `/post-weekly-proposals [force:True]` | Post proposal messages from today through Sunday. Use `force:True` to overwrite already-posted messages |
| `/announce-matches` | Post the upcoming match summary to the log channel now |
| `/accept-broadcast <match-id>` | Manually move a match to the Accepted Calendar |
| `/broadcast-done <match-id>` | Mark a match broadcast-complete outside the bot's normal flow (increments team tallies). Not required for bot-managed broadcasts — use only when the confirmation flow was bypassed |
| `/set-timezone` | Set your preferred timezone for time displays |

### Manager management *(Administrator for add/remove/role; Manager for list)*
| Command | Description |
|---|---|
| `/add-manager-role @role` | Set the Discord role assigned to managers (run this first) |
| `/remove-manager-role` | Clear the configured manager role |
| `/add-manager @user` | Grant manager permissions and assign the manager role |
| `/remove-manager @user` | Revoke manager permissions and remove the manager role |
| `/list-managers` | List all current managers |

> `/add-manager-role` must be configured before `/add-manager` or `/remove-manager` can be used. The bot requires **Manage Roles** permission and its role must be ranked above the manager role.

### Day blocking *(Manager)*
| Command | Description |
|---|---|
| `/block-day YYYY-MM-DD [reason]` | Block a day — adds a NO STREAM event to TeamUp |
| `/unblock-day YYYY-MM-DD` | Remove a block |
| `/list-blocks` | Show all blocked days |

### Season reset *(Administrator)*
| Command | Description |
|---|---|
| `/new-season confirm:True` | Clear match/sign-up/team data, reset IDs to 1. Preserves config and managers |
| `/reset confirm:True` | Erase all data including config; delete all TeamUp calendar events |

### General *(Anyone)*
| Command | Description |
|---|---|
| `/talent` | List talent sorted by total activity (broadcasts, responses, unavailable) |

---

## Blocking Days

To mark a day as NO STREAM:
```
/block-day 2026-05-01 Holiday
```
This creates a 00:01–23:59 ET block event on TeamUp. Remove it with `/unblock-day YYYY-MM-DD`.

Block Day is also available as a button on sign-up messages and on proposal messages. When a proposal day is blocked, the proposal buttons are replaced with a single **Unblock Day** button.

---

## After a Broadcast

The bot handles completion automatically when all talent confirm — team tallies increment and the TeamUp event moves to Accepted without any manual steps.

`/broadcast-done` is only needed when a match was streamed outside the bot's normal flow (confirmation skipped, bot was offline, etc.):
```
/broadcast-done <match-id>
```
The match ID appears in sign-up messages and scheduling notifications.

---

## Season Reset

To clear match data, team tallies, and sign-up history while keeping channel config and managers:
```
/new-season confirm:True
```

To start completely fresh (wipes everything including config):
```
/reset confirm:True
```

---

## Running the Bot

```bash
pip install -r requirements.txt
# Create .env with DISCORD_BOT_TOKEN=...
python bot.py
```

For always-on hosting, consider a VPS (DigitalOcean, Hetzner, Linode) running the bot as a background service, or platforms like Railway or Render.
