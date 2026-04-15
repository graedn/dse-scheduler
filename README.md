# Deadlock League Broadcast Bot

A Discord bot that monitors your public league server for match posts, automatically selects matches for broadcast using a strict 2-hour scheduling window, manages a TeamUp calendar for your broadcast team, and coordinates talent sign-ups per match.

---

## First-Time Setup

The bot needs four required settings before it will do anything. Run these slash commands after inviting the bot to your server(s):

### 1. Set the match channel
The channel in your **public league server** where players post their match times.
```
/set-match-channel #channel
```

### 2. Set the broadcast channel
The channel in your **private admin server** where the bot posts schedules, proposals, sign-up messages, and notifications.
```
/set-broadcast-channel #channel
```

### 3. Set your TeamUp calendar ID
Found in your TeamUp calendar URL: `teamup.com/YOUR-CALENDAR-ID`
```
/set-teamup-calendar <calendar-id>
```

### 4. Set your TeamUp API key
Generate one from your TeamUp account settings.
```
/set-teamup-key <api-key>
```

### Optional: Set a log channel
A separate channel for bot logs, parse errors, scheduling summaries, and history scan results. If not set, these messages go to the broadcast channel.
```
/set-log-channel #channel
```

### Check your configuration
```
/status
```
Shows all settings with ✅/❌ indicators. The bot won't process any match posts until the four required settings are configured.

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
A schedule change is sent as a proposal to the broadcast channel when:
- Matches are already proposed for that day and rescheduling them produces a better combination

Proposals post to the broadcast channel showing the current vs. proposed schedule. React within **12 hours**:
- ✅ — Approve immediately
- ❌ — Reject

If no reaction is added, the proposal **auto-approves after 12 hours**.

### Pair window
The bot only considers match combinations where consecutive matches are **exactly ~2 hours apart** (1.9–2.1h window). Combinations with wider gaps are never proposed.

### Scoring priority
- Matches starting closer to existing anchored times on the same day score higher (slot-alignment bonus)
- Teams broadcast less often are preferred (fairness balancing)
- Weekday evenings score slightly higher than weekend evenings

### History scan
When the bot starts and all required settings are configured, it automatically scans up to 500 messages in the match channel and logs any future matches not yet in the database. The full scheduling logic runs for each date with new matches.

You can trigger this manually at any time:
```
/sync-history
```

### Automated schedule
The bot runs several background jobs daily (all times Eastern):

| Time | Job |
|------|-----|
| 3:00 AM | Full weekly sweep — reviews all upcoming dates and re-evaluates scheduling |
| 9:00 AM | Day-of check — posts any unscheduled matches for today |
| 11:00 AM | Posts upcoming match summary to broadcast channel |
| 11:00 PM | Posts upcoming match summary to broadcast channel |

---

## Talent Sign-Up

When a match is confirmed to the calendar, the bot posts a sign-up message in the broadcast channel. Broadcast team members react to claim roles.

### Required roles
| Emoji | Role | Slots |
|-------|------|-------|
| ⌨️ | Producer | 1 |
| 🎙️ | Caster | 2 |
| 🎥 | Observer | 1 |
| 🔍 | Analyst | 2 *(optional)* |

### How it works
- React with the emoji to sign up for a role
- Remove your reaction to withdraw
- The message edits itself live as people sign up
- Primary slots fill in order of first reaction; overflow appears as numbered backups:
  ```
  🎙️ Casters: John Doe (johndoe) | Jane Smith (janesmith)
    ↳ Backup 1: Alex (alexgg)
    ↳ Backup 2: Sam (samcasts)
  ```

### Auto-finalization
When all **required** roles are filled (1 producer, 2 casters, 1 observer), the bot automatically:
1. Moves the match to the **Accepted Calendar** on TeamUp
2. Adds a talent description to the calendar event listing who fills each role
3. Updates the sign-up message with a confirmation footer
4. Posts a notification to the log channel

---

## Admin Commands

All commands require Administrator permission.

### Configuration
| Command | Description |
|---|---|
| `/set-match-channel #channel` | Set the channel to watch for match posts |
| `/unset-match-channel` | Stop watching for match posts |
| `/set-broadcast-channel #channel` | Set the admin channel for schedules and sign-up messages |
| `/unset-broadcast-channel` | Stop posting to the broadcast channel |
| `/set-log-channel #channel` | Set a separate channel for logs and error notifications |
| `/unset-log-channel` | Remove the log channel |
| `/set-teamup-calendar <id>` | Set the TeamUp calendar ID |
| `/set-teamup-key <key>` | Set the TeamUp API key |
| `/status` | Show current configuration and any missing settings |
| `/test-teamup` | Test the TeamUp API connection |

### Match management
| Command | Description |
|---|---|
| `/sync-history` | Scan the match channel history and log any future matches |
| `/announce-matches` | Post the upcoming matches summary to the broadcast channel now |
| `/accept-broadcast <match-id>` | Manually move a match to the Accepted Calendar |
| `/broadcast-done <match-id>` | Mark a match as broadcast-complete (increments team tallies) |

### Day blocking
| Command | Description |
|---|---|
| `/block-day YYYY-MM-DD [reason]` | Block a day from scheduling (e.g. holiday, event conflict) |
| `/unblock-day YYYY-MM-DD` | Remove a block for a day |
| `/list-blocks` | Show all blocked days |

### Reset
| Command | Description |
|---|---|
| `/reset confirm:True` | Erase all bot data and delete all TeamUp calendar events |

---

## After a Broadcast

Once you've streamed a match:
```
/broadcast-done <match-id>
```
The match ID appears in the scheduling notifications the bot posts. This increments the broadcast count for both teams so the algorithm deprioritizes them in future selections.

---

## Blocking Days

If there's a day you can't stream:
```
/block-day 2026-04-20 Easter
/block-day 2026-05-01
```
This creates an all-day "NO STREAM" block on the TeamUp calendar and prevents the bot from scheduling matches that day. Remove it with `/unblock-day YYYY-MM-DD`.

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

## Resetting the Bot

To start completely fresh:
```
/reset confirm:True
```
This deletes all TeamUp calendar events (matches and blocked days), then clears all match history, sign-up records, team tallies, and configuration from the database.
