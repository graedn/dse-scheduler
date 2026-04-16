# Deadlock League Broadcast Bot

A Discord bot that monitors your public league server for match posts, automatically selects matches for broadcast using a strict 2-hour scheduling window, manages a TeamUp calendar for your broadcast team, and coordinates talent sign-ups and allocation per match.

---

## First-Time Setup

The bot needs four required settings before it will do anything. Run these slash commands after inviting the bot to your server(s):

### 1. Set the match channel
The channel in your **public league server** where players post their match times.
```
/set-match-channel #channel
```

### 2. Set the broadcast channel
The channel in your **private admin server** where the bot posts talent confirmations and broadcast notifications.
```
/set-broadcast-channel #channel
```

### 3. Set the sign-up channel
The channel where talent sign-up messages are posted (can be the same as broadcast, or a separate channel).
```
/set-signup-channel #channel
```

### 4. Set your TeamUp calendar ID
Found in your TeamUp calendar URL: `teamup.com/YOUR-CALENDAR-ID`
```
/set-teamup-calendar <calendar-id>
```

### 5. Set your TeamUp API key
Generate one from your TeamUp account settings.
```
/set-teamup-key <api-key>
```

### Optional: Set a log channel
A separate channel for bot logs, parse errors, scheduling summaries, proposals, talent allocation UIs, and history scan results. If not set, these messages go to the broadcast channel.
```
/set-log-channel #channel
```

### Check your configuration
```
/status
```
Shows all settings with ✅/❌ indicators. The bot won't process any match posts until the required settings are configured.

---

## Match Post Format

Players post their match times in the match channel using this format:

```
Division: Premier
Week: 3
Team Alpha vs Team Beta
Time: <t:1713387600:F>
```

**Accepted variations:**
- `Division:` — with or without a space after the colon (`Division: 1` or `Division:1`)
- `Week:` or `Round:` — colon is optional (`Week 3` or `Week: 3`)
- Division names: Premier, Division 1–4 (fuzzy matched)
- `Time:` — must use a Discord timestamp tag (`<t:UNIX:F>`)

**Generating a Discord timestamp:**
Use the built-in `@time` command in Discord:
- `today 10pm`
- `tuesday 18:00`
- `sunday 6pm`

> ⚠️ `@time` uses a **24-hour clock** — if you forget `pm`, it will set an AM time.

If a post is missing the timestamp or can't be parsed, the bot DMs the player with what went wrong.

---

## How Scheduling Works

### Direct-add (immediate)
A match is added directly to the TeamUp calendar (no approval needed) when:
- There are **no other matches scheduled for that day**, or
- The match time is **within 2 hours** of an already-proposed match on that day

### Proposal (requires approval)
A schedule change is sent as a proposal to the **log channel** when:
- Matches are already proposed for that day and rescheduling them produces a better combination

Proposals show the current vs. proposed schedule with four action buttons:
- **Approve** — accept the new schedule immediately
- **Reject** — keep the current schedule
- **Delete Events** — remove the proposed events from the calendar
- **Block Day** — remove all events for the day and add a NO STREAM block

If no action is taken, the proposal **auto-approves after 12 hours**.

### Pair window
The bot only considers match combinations where consecutive matches are **exactly ~2 hours apart** (1.9–2.1h window). Combinations with wider gaps are never proposed.

### Scoring priority
- Matches starting closer to existing anchored times on the same day score higher (slot-alignment bonus)
- Teams broadcast less often are preferred (fairness balancing)
- Weekday evenings score slightly higher than weekend evenings

### History scan
When the bot starts, it automatically scans up to 500 messages in the match channel and logs any future matches not yet in the database. The full scheduling logic runs for each date with new matches.

You can trigger this manually at any time:
```
/sync-history
```

### Automated schedule
The bot runs several background jobs daily (all times Eastern):

| Time | Job |
|------|-----|
| 3:00 AM | Full weekly sweep — reviews all upcoming dates and re-evaluates scheduling |
| 9:00 AM | Day-of check — posts sign-up messages for any unscheduled matches today |
| 11:00 AM | Posts upcoming match summary to log channel |
| 11:00 PM | Posts upcoming match summary to log channel |
| Every 5 min | Sign-up deadline check — triggers Last Call or cancellation |

---

## Talent Sign-Up

When a match is confirmed to the calendar, the bot posts a sign-up message in the **sign-up channel**. Broadcast team members click buttons to claim roles.

### Roles
| Role | Required | Notes |
|------|----------|-------|
| Producer | ✅ | May double as Observer |
| Observer | ✅ | May be filled by the Producer |
| Play-by-Play | ✅ | Must be a unique person |
| Colour Caster | ✅ | Must be a unique person, different from PBP |
| Host | Optional | |
| Analyst | Optional | |

### Auto-trigger requirements
Sign-ups are considered complete when:
- All four required roles are filled
- Play-by-Play and Colour Caster are **different people**
- At least **3 unique users** are signed up across required roles (Producer/Observer can share)

### Sign-up timeline
| Event | When |
|-------|------|
| Sign-up deadline | 2 hours before match |
| **❗❗ LAST CALL ❗❗** edit | If deadline passes and crew is incomplete |
| Call time | 30 minutes before match |
| Cancellation | If call time passes and crew is still incomplete |

When the sign-up deadline passes and the crew **is** complete, the bot automatically sends the talent allocation UI to the log channel.

If the crew is incomplete at deadline, the sign-up message is edited with a **LAST CALL** warning. If it's still incomplete at call time, the match is removed from the calendar and marked cancelled — no ping is sent.

### Sign-up message states

| Status | When | Buttons |
|--------|------|---------|
| Active | Match is scheduled, sign-ups open | All role buttons + Force Schedule + New Match + Block Day |
| ❗❗ LAST CALL | Deadline passed, crew incomplete | Same |
| ✅ APPROVED | All talent confirmed | New Match + Block Day only (shows allocated roster) |
| ❌ CANCELLED | Cancelled by management or deadline missed | None |

### Sign-up message buttons (manager-only)
- **Force Schedule** — bypass the deadline and trigger talent allocation immediately
- **New Match** — swap in a different unscheduled match from the same day; on an APPROVED message, also edits the talent confirmation message and pings allocated talent
- **Block Day** — remove all scheduled matches for the day and add a NO STREAM block

---

## Talent Allocation

When sign-ups close with a full crew, the bot posts a talent allocation message in the **log channel**. Managers use dropdown selects to assign each required role, then confirm.

If an optional Host or Analysts signed up, a second step appears to select them.

Once confirmed, a **talent confirmation message** is sent to the **broadcast channel** listing the full crew. Each assigned person must click **Ready** to confirm. If anyone clicks **Reject**, the allocation resets and the process restarts.

When all required talent confirm:
- The TeamUp event is moved to the **Accepted Calendar**
- Talent broadcast counts are incremented
- A confirmation notice is posted to the log channel

---

## Admin Commands

### Configuration
| Command | Description |
|---|---|
| `/set-match-channel #channel` | Set the channel to watch for match posts |
| `/unset-match-channel` | Stop watching for match posts |
| `/set-broadcast-channel #channel` | Set the channel for talent confirmations and notifications |
| `/unset-broadcast-channel` | Remove the broadcast channel |
| `/set-signup-channel #channel` | Set the channel for talent sign-up messages |
| `/unset-signup-channel` | Remove the sign-up channel (falls back to broadcast) |
| `/set-log-channel #channel` | Set the channel for logs, proposals, and allocation UIs |
| `/unset-log-channel` | Remove the log channel |
| `/set-teamup-calendar <id>` | Set the TeamUp calendar ID |
| `/set-teamup-key <key>` | Set the TeamUp API key |
| `/status` | Show current configuration and any missing settings |
| `/test-teamup` | Test the TeamUp API connection |
| `/set-timezone` | Set your personal timezone for time displays (e.g. New Match picker) |

### Match management
| Command | Description |
|---|---|
| `/sync-history` | Scan the match channel history and log any future matches |
| `/announce-matches` | Post the upcoming matches summary to the log channel now |
| `/accept-broadcast <match-id>` | Manually move a match to the Accepted Calendar |
| `/broadcast-done <match-id>` | Mark a match as broadcast-complete (increments team tallies) |

### Manager management
| Command | Description |
|---|---|
| `/add-manager @user` | Grant broadcast manager permissions (can use manager buttons/commands) |
| `/remove-manager @user` | Revoke manager permissions |
| `/list-managers` | List all current managers |

### Day blocking
| Command | Description |
|---|---|
| `/block-day YYYY-MM-DD [reason]` | Block a day from scheduling — adds a NO STREAM event to TeamUp |
| `/unblock-day YYYY-MM-DD` | Remove a block for a day |
| `/list-blocks` | Show all blocked days |

### Season reset
| Command | Description |
|---|---|
| `/new-season confirm:True` | Clear all match/sign-up/team data, reset IDs to 1 (preserves config and managers) |
| `/reset confirm:True` | Erase all bot data including config, delete all TeamUp calendar events |

---

## After a Broadcast

Once you've streamed a match:
```
/broadcast-done <match-id>
```
The match ID appears in the sign-up message and scheduling notifications. This increments the broadcast count for both teams so the algorithm deprioritises them in future selections.

---

## Blocking Days

If there's a day you can't stream:
```
/block-day 2026-04-20 Easter
/block-day 2026-05-01
```
This creates a 12:01 AM – 11:59 PM Eastern block event on the TeamUp calendar and prevents the bot from scheduling matches that day. Remove it with `/unblock-day YYYY-MM-DD`.

The Block Day action is also available as a button on sign-up messages and on proposals in the log channel.

---

## Keeping the Bot Running

The bot runs as long as the terminal is open. To restart:
```
cd "c:/Discord Bot Project"
python bot.py
```

For always-on hosting, consider:
- A VPS (DigitalOcean, Hetzner, Linode) running the bot as a background service
- Railway or Render (check their terms for bots)

---

## Resetting for a New Season

To clear match data, team tallies, and sign-up history while keeping your channel config and managers:
```
/new-season confirm:True
```
Match IDs, broadcast message IDs, and sign-up IDs reset to 1.

To start completely fresh (wipes everything):
```
/reset confirm:True
```
This deletes all TeamUp calendar events, then clears all data including configuration.
