# Deadlock League Broadcast Bot

A Discord bot that monitors your public league server for match posts, automatically selects 2–3 matches per day for broadcast, and manages a TeamUp calendar for your broadcast team.

---

## First-Time Setup

The bot needs four pieces of information before it will do anything. Run these slash commands in your **admin server** after inviting the bot to both servers:

### 1. Set the match channel
This is the channel in your **public league server** where players post their match times.
```
/set-match-channel #channel
```

### 2. Set the broadcast channel
This is the channel in your **private admin server** where the bot will post draft schedules, flags, and notifications.
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

### Check your configuration
```
/status
```
Shows all four settings with ✅/❌ indicators. The bot won't process any match posts until all four are set.

---

## How It Works

### Match posts (public server)
Players post their match times in this format in the match channel:

```
Division: Premier
Week: 3
Team Alpha vs Team Beta
Time: <t:1713387600:F>
```

- `Division:` — one of: Premier, Division 1, Division 2, Division 3, Division 4
- `Week:` or `Round:` — for playoffs, use Round instead of Week
- The `Time:` line must use a Discord timestamp tag (`<t:UNIX:F>`) — players generate these at hammertime.cyou or similar tools
- Typos in team names and division names are handled automatically (fuzzy matching)

If the bot can't parse a post, it replies to the player in the public channel and posts a notification to your admin channel.

### Scheduling
When a valid match post arrives, the bot:

1. **If nothing is scheduled for that day** — immediately picks the best combination of 2–3 matches and adds them to the TeamUp calendar. Posts a confirmation to the admin channel.
2. **If matches are already scheduled for that day** — scores the new combination against the existing one. If the new combination is better, posts a draft proposal to the admin channel for review.

The bot also runs a **daily sweep at 3am ET** to review the whole week and catch anything that was missed.

### Scoring priority
- Weekdays: consecutive back-to-back matches (e.g. 7pm + 9pm) score highest, followed by 8pm prime time
- Weekends: consecutive pairs score highest
- Teams that have been broadcast less often are preferred (fairness balancing)

### Draft proposals
When the bot proposes a schedule change, it posts to your admin broadcast channel showing the current vs. proposed schedule and the reason. You have **12 hours** to react with ❌ to reject it. If no reaction is added, it auto-approves.

---

## Admin Commands

All commands require Administrator permission.

| Command | Description |
|---|---|
| `/set-match-channel #channel` | Set the channel to watch for match posts |
| `/unset-match-channel` | Stop watching for match posts |
| `/set-broadcast-channel #channel` | Set the admin channel for drafts and flags |
| `/unset-broadcast-channel` | Stop posting to the admin channel |
| `/set-teamup-calendar <id>` | Set the TeamUp calendar ID |
| `/set-teamup-key <key>` | Set the TeamUp API key |
| `/status` | Show current configuration and any missing settings |
| `/broadcast-done <match-id>` | Mark a match as broadcast — increments team tallies |
| `/block-day YYYY-MM-DD [reason]` | Block a day from scheduling (e.g. holiday, tournament conflict) |
| `/unblock-day YYYY-MM-DD` | Remove a block for a day |
| `/list-blocks` | Show all blocked days |
| `/reset` | Erase all bot data and start fresh (requires `/reset confirm:True`) |

---

## After a Broadcast

Once you've streamed a match, run:
```
/broadcast-done <match-id>
```
The match ID is shown in the schedule notifications the bot posts. This increments the broadcast count for both teams so the scheduling algorithm deprioritises them in future weeks.

---

## Blocking Days

If there's a day you can't stream (holiday, another event, etc.):
```
/block-day 2026-04-20 Easter
/block-day 2026-05-01
```
This creates an all-day block on the TeamUp calendar and prevents the bot from scheduling matches that day. Remove it with `/unblock-day YYYY-MM-DD`.

---

## Keeping the Bot Running

The bot runs as long as the terminal window is open. If you close it, the bot goes offline.

For always-on hosting, consider:
- A cheap VPS (e.g. DigitalOcean, Hetzner, Linode) running the bot as a background service
- A free tier on Railway or Render (check their terms for bots)

To restart after closing the terminal:
```
cd "c:/Discord Bot Project"
python bot.py
```

---

## Resetting the Bot

If you want to start completely fresh:
```
/reset confirm:True
```
This clears all match history, team tallies, and configuration. It does **not** delete events already on the TeamUp calendar — clear those manually in TeamUp if needed.
