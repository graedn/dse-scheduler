from itertools import combinations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import json

ET = ZoneInfo("America/New_York")

MATCH_DURATION_H = 2.0
OVERLAP_MAX_H = 1.5       # matches closer than this are considered overlapping
PAIR_MIN_H = 1.9          # ~2h pair: minimum gap for back-to-back pair bonus
PAIR_MAX_H = 2.1          # ~2h pair: maximum gap for back-to-back pair bonus
SLOT_ALIGN_BONUS = 75     # bonus per match that fills a 2h slot adjacent to a scheduled match

# --- Talent sign-up constants ---

ROLE_EMOJIS = {
    "producer": "⌨️",
    "caster":   "🎙️",
    "observer": "🎥",
    "analyst":  "🔍",
}
# Discord may strip the U+FE0F variation selector from incoming reaction events,
# so build the lookup with both the canonical form and the stripped form.
_VS16 = "\uFE0F"
EMOJI_TO_ROLE: dict[str, str] = {}
for _role, _emoji in ROLE_EMOJIS.items():
    EMOJI_TO_ROLE[_emoji] = _role
    EMOJI_TO_ROLE[_emoji.replace(_VS16, "")] = _role

ROLE_LIMITS = {"producer": 1, "caster": 2, "observer": 1, "analyst": 2}
REQUIRED_ROLES = {"producer", "caster", "observer"}
SIGNUP_EMOJIS = list(ROLE_EMOJIS.values())
PRIME_HOUR_ET = 20          # 8pm
SECONDARY_HOURS_ET = [18, 22]  # 6pm, 10pm
TIME_TOLERANCE_H = 15 / 60  # 15 minutes
MAX_MATCHES_PER_DAY = 3


# --- Time helpers ---

def get_et_hour(unix_ts: int) -> float:
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    return dt.hour + dt.minute / 60.0


def is_weekend(unix_ts: int) -> bool:
    return datetime.fromtimestamp(unix_ts, tz=ET).weekday() >= 5


# --- Pair logic ---

def are_consecutive(ts1: int, ts2: int) -> bool:
    gap_h = (ts2 - ts1) / 3600.0
    return PAIR_MIN_H <= gap_h <= PAIR_MAX_H


def has_overlap(ts1: int, ts2: int) -> bool:
    gap_h = (ts2 - ts1) / 3600.0
    return gap_h < OVERLAP_MAX_H


# --- Combination generation ---

def generate_combinations(matches: list[dict]) -> list[list[dict]]:
    """All valid 2–3 match combinations where every consecutive pair is ~2h apart."""
    valid = []
    for size in range(2, min(len(matches) + 1, MAX_MATCHES_PER_DAY + 1)):
        for combo in combinations(matches, size):
            sorted_combo = sorted(combo, key=lambda m: m["match_time"])
            if all(
                are_consecutive(sorted_combo[i]["match_time"], sorted_combo[i + 1]["match_time"])
                for i in range(len(sorted_combo) - 1)
            ):
                valid.append(list(sorted_combo))
    return valid


# --- Scoring ---

def score_combination(combo: list[dict], weekend: bool, db) -> int:
    sorted_combo = sorted(combo, key=lambda m: m["match_time"])
    score = 0

    for match in sorted_combo:
        hour = get_et_hour(match["match_time"])
        if weekend:
            if abs(hour - PRIME_HOUR_ET) <= TIME_TOLERANCE_H:
                score += 50
            elif any(abs(hour - h) <= TIME_TOLERANCE_H for h in SECONDARY_HOURS_ET):
                score += 20
        else:
            if abs(hour - PRIME_HOUR_ET) <= TIME_TOLERANCE_H:
                score += 100
            elif any(abs(hour - h) <= TIME_TOLERANCE_H for h in SECONDARY_HOURS_ET):
                score += 30

    pair_bonus = 100 if weekend else 110
    for i in range(len(sorted_combo) - 1):
        if are_consecutive(sorted_combo[i]["match_time"], sorted_combo[i + 1]["match_time"]):
            score += pair_bonus

    for match in sorted_combo:
        for team_name in [match["team_home"], match["team_away"]]:
            team = db.get_team(team_name)
            if team:
                score -= 10 * team["scheduled_count"]

    return score


def combo_match_ids(combo: list[dict]) -> list[int]:
    return sorted(m["id"] for m in combo)


def best_combination(matches: list[dict], db,
                     scheduled: list[dict] = None) -> Optional[list[dict]]:
    if len(matches) < 2:
        return None
    weekend = is_weekend(matches[0]["match_time"])
    combos = generate_combinations(matches)
    if not combos:
        return None

    # Times already on the calendar — new matches that slot exactly 2h away get a bonus
    anchor_times = [m["match_time"] for m in scheduled] if scheduled else []

    def _score(combo):
        base = score_combination(combo, weekend, db)
        if not anchor_times:
            return base
        bonus = 0
        for match in combo:
            for anchor in anchor_times:
                gap_h = abs(match["match_time"] - anchor) / 3600.0
                if PAIR_MIN_H <= gap_h <= PAIR_MAX_H:
                    bonus += SLOT_ALIGN_BONUS
        return base + bonus

    scored = [(c, _score(c)) for c in combos]
    max_score = max(s for _, s in scored)
    tied = [c for c, s in scored if s == max_score]

    def team_fairness(combo):
        total = 0
        for match in combo:
            for name in [match["team_home"], match["team_away"]]:
                team = db.get_team(name)
                if team:
                    total += team["scheduled_count"] + team["broadcast_count"]
        return total

    return min(tied, key=team_fairness)


# --- Proposal message formatting ---

def _fmt_match_line(match: dict, fmt: str = "t") -> str:
    ts = match["match_time"]
    return f"  • [{match['division']}] {match['team_home']} vs {match['team_away']} — <t:{ts}:{fmt}>"


def _fmt_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    return dt.strftime("%A %B ") + str(dt.day)


# --- Talent sign-up helpers ---

def build_signup_message(match: dict, signups: list[dict]) -> str:
    """Format the per-match talent sign-up message.

    Shows primary slots (filled or open) and a backup list for overflow.
    Editing this message live as reactions come in is the primary UX.
    """
    ts = match["match_time"]
    by_role: dict[str, list[dict]] = {r: [] for r in ROLE_EMOJIS}
    for s in sorted(signups, key=lambda x: x["signed_up_at"]):
        if s["role"] in by_role:
            by_role[s["role"]].append(s)

    lines = [
        f"📋 **[{match['division']}] {match['team_home']} vs {match['team_away']}**",
        f"🗓️ <t:{ts}:F>",
        "",
        "**Talent Sign-up:**",
    ]

    for role, emoji in ROLE_EMOJIS.items():
        limit = ROLE_LIMITS[role]
        required = role in REQUIRED_ROLES
        label = role.capitalize() + ("s" if limit > 1 else "")
        opt_tag = "" if required else " *(optional)*"

        filled = by_role[role]
        primaries = filled[:limit]
        backups = filled[limit:]

        slots = []
        for i in range(limit):
            if i < len(primaries):
                p = primaries[i]
                slots.append(f"{p['display_name']} ({p['username']})")
            else:
                slots.append("—")

        line = f"{emoji} **{label}**{opt_tag}: {' | '.join(slots)}"
        if backups:
            backup_lines = "\n".join(
                f"  ↳ *Backup {i + 1}: {b['display_name']} ({b['username']})*"
                for i, b in enumerate(backups)
            )
            line += f"\n{backup_lines}"
        lines.append(line)

    lines += ["", "*React to sign up. Remove your reaction to withdraw.*"]
    return "\n".join(lines)


def is_fully_staffed(signups: list[dict]) -> bool:
    """True when every required role has at least its minimum number of sign-ups."""
    by_role: dict[str, int] = {}
    for s in signups:
        by_role[s["role"]] = by_role.get(s["role"], 0) + 1
    return all(by_role.get(role, 0) >= ROLE_LIMITS[role] for role in REQUIRED_ROLES)


def build_talent_description(signups: list[dict]) -> str:
    """Plain-text talent roster for the TeamUp event description field."""
    by_role: dict[str, list[dict]] = {}
    for s in sorted(signups, key=lambda x: x["signed_up_at"]):
        by_role.setdefault(s["role"], []).append(s)

    parts = []
    for role in ["producer", "caster", "observer", "analyst"]:
        people = by_role.get(role, [])[:ROLE_LIMITS[role]]
        if not people:
            continue
        label = role.capitalize() + ("s" if len(people) > 1 else "")
        names = ", ".join(f"{p['display_name']} ({p['username']})" for p in people)
        parts.append(f"{label}: {names}")
    return "\n".join(parts)


def build_proposal_message(date_str: str, current_combo: list[dict],
                            proposed_combo: list[dict],
                            current_score: int, proposed_score: int, db) -> str:
    current_lines = "\n".join(
        _fmt_match_line(m, fmt="F") for m in sorted(current_combo, key=lambda m: m["match_time"])
    )
    proposed_lines = "\n".join(
        _fmt_match_line(m, fmt="F") for m in sorted(proposed_combo, key=lambda m: m["match_time"])
    )
    team_info = []
    for match in proposed_combo:
        for name in [match["team_home"], match["team_away"]]:
            team = db.get_team(name)
            bc = team["broadcast_count"] if team else 0
            team_info.append(f"{name} ({bc} prior broadcasts)")

    return (
        f"📋 **Broadcast Schedule Proposal — {_fmt_date(date_str)}**\n\n"
        f"**Current schedule:**\n{current_lines}\n\n"
        f"**Proposed schedule:**\n{proposed_lines}\n\n"
        f"**Reason:** Proposed combination scores {proposed_score} vs current {current_score}.\n"
        f"Teams: {', '.join(team_info)}\n\n"
        f"React with ✅ to approve now or ❌ to reject. Auto-approves in 12 hours."
    )


# --- Shared accept / propose helpers (called by both EventsCog and daily sweep) ---

async def accept_combination(combo: list[dict], date_str: str, db, teamup,
                              broadcast_channel) -> None:
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
        end_ts = match["match_time"] + int(MATCH_DURATION_H * 3600)
        event_id = teamup.create_event(title, match["match_time"], end_ts, subcalendar="proposed")
        created.append((match, event_id))

    # Phase 2: write DB updates
    for match, event_id in created:
        db.update_match_teamup_id(match["id"], event_id)
        db.increment_scheduled_count(match["team_home"])
        db.increment_scheduled_count(match["team_away"])

    # Phase 3: post individual sign-up messages
    if broadcast_channel:
        for match, _ in created:
            signups = db.get_signups_for_match(match["id"])
            msg = await broadcast_channel.send(build_signup_message(match, signups))
            db.insert_broadcast_message(match["id"], str(msg.id), str(broadcast_channel.id))
            for emoji in SIGNUP_EMOJIS:
                await msg.add_reaction(emoji)


async def propose_change(date_str: str, current: list[dict], proposed: list[dict],
                          current_score: int, proposed_score: int,
                          db, broadcast_channel) -> None:
    """Post a draft proposal to the broadcast channel and store a pending change."""
    if not broadcast_channel:
        return
    msg_text = build_proposal_message(
        date_str, current, proposed, current_score, proposed_score, db
    )
    msg = await broadcast_channel.send(msg_text)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    old_event_ids = [m["teamup_event_id"] for m in current if m.get("teamup_event_id")]
    new_match_ids = [m["id"] for m in proposed]
    db.insert_pending_change(
        description=msg_text,
        old_event_ids=old_event_ids,
        new_match_ids=new_match_ids,
        discord_message_id=str(msg.id),
    )


async def apply_pending_change(change: dict, db, teamup,
                               broadcast_channel) -> Optional[str]:
    """Delete old TeamUp events and accept the proposed combination.

    Used for both manual ✅ approvals and auto-approval after 12 hours.
    Marks the change as approved and returns the date string, or None.
    """
    old_ids = json.loads(change["old_event_ids"])
    new_match_ids = json.loads(change["new_match_ids"])

    for event_id in old_ids:
        try:
            teamup.delete_event(event_id)
        except Exception:
            pass
        for match in db.get_matches_by_teamup_event_id(event_id):
            db.update_match_teamup_id(match["id"], None)
            db.decrement_scheduled_count(match["team_home"])
            db.decrement_scheduled_count(match["team_away"])

    new_matches = [db.get_match(mid) for mid in new_match_ids]
    new_matches = [m for m in new_matches if m]
    date_str = None
    if new_matches:
        date_str = datetime.fromtimestamp(
            new_matches[0]["match_time"], tz=ET
        ).strftime("%Y-%m-%d")
        await accept_combination(new_matches, date_str, db, teamup, broadcast_channel)

    db.resolve_pending_change(change["id"], approved=True)
    return date_str


async def process_expired_changes(db, teamup, broadcast_channel) -> None:
    """Auto-approve pending changes whose 12-hour window has passed."""
    for change in db.get_expired_pending_changes():
        date_str = await apply_pending_change(change, db, teamup, broadcast_channel)
        if broadcast_channel and date_str:
            await broadcast_channel.send(
                f"✅ Schedule proposal auto-approved for {_fmt_date(date_str)}."
            )


async def schedule_for_date(date_str: str, db, teamup, broadcast_channel,
                            log_channel=None) -> None:
    """Canonical scheduling logic for one date.

    Direct-add rules (no approval needed):
      - Nothing scheduled for the day → add the match immediately.
      - Match is within ~2h of an existing PROPOSED (not accepted) match → add it alongside.

    Proposal rule (checkmark/X approval needed):
      - Leftover matches that couldn't fill an open slot exist, AND a better
        combination is possible by rearranging proposed (not accepted) matches.

    Accepted matches (talent confirmed) are never displaced automatically.
    """
    if db.get_blocked_day(date_str):
        return

    all_matches = db.get_matches_for_date(date_str)
    proposed = [m for m in all_matches if m.get("teamup_event_id") and not m.get("broadcast_accepted")]
    unscheduled = [m for m in all_matches if not m.get("teamup_event_id")]

    if not unscheduled:
        return

    # --- Step 1: direct-add qualifying matches ---
    not_added: list[dict] = []
    for match in unscheduled:
        if not proposed:
            qualifies = True  # Nothing on the calendar yet
        else:
            qualifies = any(
                PAIR_MIN_H <= abs(match["match_time"] - p["match_time"]) / 3600.0 <= PAIR_MAX_H
                for p in proposed
            )

        if qualifies:
            if teamup:
                await accept_combination([match], date_str, db, teamup, broadcast_channel)
                proposed.append(match)  # keep local list in sync for subsequent checks
            else:
                if log_channel:
                    await log_channel.send(
                        f"⚠️ TeamUp not configured — "
                        f"{match['team_home']} vs {match['team_away']} "
                        f"for {_fmt_date(date_str)} stored but not added to calendar."
                    )
        else:
            not_added.append(match)

    # --- Step 2: check if leftover matches form a better combo → proposal ---
    if not_added and proposed:
        all_non_accepted = proposed + not_added
        best = best_combination(all_non_accepted, db, proposed)
        if best is not None and combo_match_ids(best) != combo_match_ids(proposed):
            current_score = score_combination(proposed, is_weekend(proposed[0]["match_time"]), db)
            best_score = score_combination(best, is_weekend(best[0]["match_time"]), db)
            if best_score > current_score:
                if broadcast_channel:
                    await propose_change(
                        date_str, proposed, best, current_score, best_score,
                        db, broadcast_channel,
                    )
                elif log_channel:
                    await log_channel.send(
                        f"⚠️ Broadcast channel not configured — "
                        f"dropping proposal for {_fmt_date(date_str)}."
                    )

    # --- Step 3: log matches that couldn't be scheduled ---
    if log_channel and not_added and not (not_added and proposed):
        n = len(all_matches)
        await log_channel.send(
            f"ℹ️ Match stored for **{_fmt_date(date_str)}** "
            f"({n} match{'es' if n != 1 else ''} logged — "
            f"need a ~2h partner to schedule)."
        )


async def run_morning_check(db, teamup, broadcast_channel) -> None:
    """9am check: ensure today's unscheduled matches are on the Proposed Calendar."""
    today_str = datetime.now(tz=ET).strftime("%Y-%m-%d")
    if not teamup or not db.get_matches_for_date(today_str):
        return
    await schedule_for_date(today_str, db, teamup, broadcast_channel)


async def run_daily_sweep(db, teamup, broadcast_channel) -> None:
    """3am sweep: evaluate all upcoming days this week and process expired changes."""
    today = datetime.now(tz=ET).date()
    monday = today - timedelta(days=today.weekday())

    for i in range(7):
        day = monday + timedelta(days=i)
        if day < today:
            continue
        await schedule_for_date(day.isoformat(), db, teamup, broadcast_channel)

    await process_expired_changes(db, teamup, broadcast_channel)


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

    lines = ["📋 **LOGGED MATCHES**"]
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
