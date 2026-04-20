from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import logging

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

MATCH_DURATION_H = 2.0


def match_end_ts(start_ts: int) -> int:
    """Return the end timestamp for a match, capped at 23:59:59 ET on the same day.

    A 22:00 ET match normally ends at 00:00 ET (midnight), which TeamUp renders
    as spanning two calendar days.  Capping at 23:59:59 keeps it on one day.
    """
    end = start_ts + int(MATCH_DURATION_H * 3600)
    day_end = int(
        datetime.fromtimestamp(start_ts, tz=ET)
        .replace(hour=23, minute=59, second=59, microsecond=0)
        .timestamp()
    )
    return min(end, day_end)


# --- Talent sign-up constants ---

ROLE_EMOJIS = {
    "producer": "⌨️",   # U+2328 U+FE0F
    "observer": "🎥",   # U+1F3A5
    "pbp":      "❗",    # U+2757  play-by-play caster
    "colour":   "😊",   # U+1F60A colour caster
    "host":     "🎙️",   # U+1F399 U+FE0F  (optional)
    "analyst":  "🔍",   # U+1F50D  (optional)
}

ROLE_LABELS = {
    "producer": "Producer",
    "observer": "Observer",
    "pbp":      "Play-by-Play",
    "colour":   "Colour Caster",
    "host":     "Host",
    "analyst":  "Analyst",
}

# Discord may strip the U+FE0F variation selector from incoming reaction events,
# so build the lookup with both the canonical form and the stripped form.
_VS16 = "\uFE0F"
EMOJI_TO_ROLE: dict[str, str] = {}
for _role, _emoji in ROLE_EMOJIS.items():
    EMOJI_TO_ROLE[_emoji] = _role
    EMOJI_TO_ROLE[_emoji.replace(_VS16, "")] = _role

GREEN_CIRCLE = "🟢"                    # Manager override emoji (U+1F7E2)
REQUIRED_ROLES = {"producer", "pbp", "colour", "observer"}
SIGNUP_EMOJIS = list(ROLE_EMOJIS.values())
SIGNUP_DEADLINE_SECONDS_BEFORE = 2 * 3600   # sign-ups close 2h before match


# --- Time helpers ---

def _fmt_match_line(match: dict, fmt: str = "t") -> str:
    ts = match["match_time"]
    return f"  • [{match['division']}] {match['team_home']} vs {match['team_away']} — <t:{ts}:{fmt}>"


def _fmt_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    return dt.strftime("%A %B ") + str(dt.day)


# --- Talent sign-up helpers ---

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_CALENDAR_LINK = "[View Calendar](https://teamup.com/ksb1114dr63p4yb3gr)"


def build_signup_message(match: dict, signups: list[dict],
                         last_call: bool = False,
                         talent_role_mention: str = "") -> str:
    """Format the per-match talent sign-up message with countdown timestamps."""
    ts = match["match_time"]
    call_time = ts - 1800  # 30 min before match start
    deadline = match.get("signup_deadline")

    by_role: dict[str, list[dict]] = {r: [] for r in ROLE_EMOJIS}
    unavailable: list[dict] = []
    for s in sorted(signups, key=lambda x: x["signed_up_at"]):
        if s["role"] == "unavailable":
            unavailable.append(s)
        elif s["role"] in by_role:
            by_role[s["role"]].append(s)

    lines = [_SEPARATOR]
    if last_call:
        lines.append("❗❗ **LAST CALL** ❗❗")
    lines += [
        f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}",
        f"- Call Time: <t:{call_time}:F>",
        f"- Match Start: <t:{ts}:t>, <t:{ts}:R>",
    ]
    if deadline:
        lines.append(f"- Sign Up Deadline: <t:{deadline}:R>")
    lines += ["", "**Talent Sign-up:**"]

    for role in ROLE_EMOJIS:
        required = role in REQUIRED_ROLES
        label = ROLE_LABELS[role]
        opt_tag = " (optional)" if not required else ""
        people = by_role[role]
        if people:
            names = ", ".join(p["display_name"] for p in people)
        else:
            names = "—"
        lines.append(f"**{label}{opt_tag}:** {names}")

    unavailable_names = ", ".join(s["display_name"] for s in unavailable) if unavailable else "—"
    lines.append(f"**Unavailable:** {unavailable_names}")

    lines += [
        "",
        "*Click a role button to sign up or withdraw.*",
        "*Managers: use the **Force Schedule** button to trigger immediate talent allocation.*",
        "",
    ]
    if talent_role_mention:
        lines.append(talent_role_mention)
    lines.append(_CALENDAR_LINK)
    return "\n".join(lines)


_APPROVED_DISPLAY_ORDER = [
    ("producer",   "Producer"),
    ("observer",   "Observer"),
    ("pbp_1",      "Play-by-Play"),
    ("colour_1",   "Colour Caster"),
    ("host",       "Host"),
    ("analyst_1",  "Analyst"),
]


def build_approved_signup_message(match: dict, role_assignments: dict) -> str:
    """Sign-up message format once talent allocation is confirmed."""
    ts = match["match_time"]
    call_time = ts - 1800
    lines = [
        _SEPARATOR,
        "✅ **APPROVED**",
        f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}",
        f"- Call Time: <t:{call_time}:F>",
        f"- Match Start: <t:{ts}:t>, <t:{ts}:R>",
        "",
        "**Broadcast Talent:**",
    ]
    for key, label in _APPROVED_DISPLAY_ORDER:
        assignment = role_assignments.get(key)
        if not assignment:
            continue
        uid = assignment["user_id"]
        name = assignment["display_name"]
        lines.append(f"**{label}:** <@{uid}> — {name}")
    lines += ["", _CALENDAR_LINK]
    return "\n".join(lines)


def is_fully_staffed(signups: list[dict]) -> bool:
    """True when sign-ups meet the minimum crew requirements.

    Rules:
    - All four required roles (producer, observer, pbp, colour) must have at
      least one signup.
    - PBP and Colour must be completely disjoint sets of people.
    - Producer and Observer users cannot be in the PBP or Colour sets.
    - At least 3 unique users across all required roles.
    """
    producers: set[str] = set()
    observers: set[str] = set()
    pbps: set[str] = set()
    colours: set[str] = set()

    for s in signups:
        role = s["role"]
        uid = s["user_id"]
        if role == "producer":
            producers.add(uid)
        elif role == "observer":
            observers.add(uid)
        elif role == "pbp":
            pbps.add(uid)
        elif role == "colour":
            colours.add(uid)

    # All four required roles must be covered
    if not producers or not observers or not pbps or not colours:
        return False

    # PBP and Colour must be disjoint
    if pbps & colours:
        return False

    # Producer and Observer cannot be PBP or Colour
    casters = pbps | colours
    if (producers & casters) or (observers & casters):
        return False

    # At least 3 unique users
    return len(producers | observers | pbps | colours) >= 3


# --- Shared accept helper (called by ProposalDayView Update Schedule button) ---

async def accept_combination(combo: list[dict], date_str: str, db, teamup,
                              signup_channel, talent_role_mention: str = "") -> None:
    """Add a match combination to the Proposed Calendar and post per-match sign-up messages.

    Phase 1: create all TeamUp events (no DB writes yet — so a partial failure is safe).
    Phase 2: write DB updates.
    Phase 3: post individual sign-up messages with talent reaction buttons.
    """
    # Phase 1: create all TeamUp events
    created: list[tuple[dict, str]] = []
    for match in combo:
        title = (
            f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
            f" {{{match['id']}}}"
        )
        event_id = teamup.create_event(
            title, match["match_time"], match_end_ts(match["match_time"]),
            subcalendar="proposed"
        )
        created.append((match, event_id))

    # Phase 2: write DB updates
    for match, event_id in created:
        db.update_match_teamup_id(match["id"], event_id)
        db.increment_scheduled_count(match["team_home"])
        db.increment_scheduled_count(match["team_away"])
        deadline_ts = match["match_time"] - SIGNUP_DEADLINE_SECONDS_BEFORE
        db.set_signup_deadline(match["id"], deadline_ts)
        db.create_allocation(match["id"])

    # Phase 3: post individual sign-up messages with button views
    if signup_channel:
        from cogs.signup import SignUpView  # local import to avoid circular dependency
        posted: list = []
        try:
            for match, _ in created:
                fresh_match = db.get_match(match["id"]) or match
                signups = db.get_signups_for_match(match["id"])
                view = SignUpView(match["id"])
                msg = await signup_channel.send(
                    build_signup_message(fresh_match, signups,
                                        talent_role_mention=talent_role_mention),
                    view=view
                )
                posted.append(msg)
                db.insert_broadcast_message(match["id"], str(msg.id), str(signup_channel.id))
        except Exception:
            for msg in posted:
                try:
                    await msg.delete()
                except Exception:
                    pass
            raise


def build_matches_announcement(db) -> str:
    """Build the LOGGED MATCHES summary grouped by date and time slot."""
    from collections import defaultdict
    matches = db.get_upcoming_matches(days=7)
    if not matches:
        return ""

    # Group by ET date, then by exact timestamp
    by_date: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for match in matches:
        dt = datetime.fromtimestamp(match["match_time"], tz=ET)
        date_str = dt.strftime("%Y-%m-%d")
        by_date[date_str][match["match_time"]] += 1

    lines = [_SEPARATOR, "📋 **LOGGED MATCHES UPDATE**"]
    for i, date_str in enumerate(sorted(by_date.keys())):
        if i > 0:
            lines.append("─────────────────────")
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
        lines.append(f"\n**{dt.strftime('%A %B')}{dt.day}**")
        for ts in sorted(by_date[date_str].keys()):
            count = by_date[date_str][ts]
            noun = "match" if count == 1 else "matches"
            lines.append(f"• <t:{ts}:t> — {count} {noun} logged")

    return "\n".join(lines)
