from itertools import combinations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import json

ET = ZoneInfo("America/New_York")

MATCH_DURATION_H = 2.0
CONSECUTIVE_MIN_H = 1.5
CONSECUTIVE_MAX_H = 2.5
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
    return CONSECUTIVE_MIN_H <= gap_h <= CONSECUTIVE_MAX_H


def has_overlap(ts1: int, ts2: int) -> bool:
    gap_h = (ts2 - ts1) / 3600.0
    return gap_h < CONSECUTIVE_MIN_H


# --- Combination generation ---

def generate_combinations(matches: list[dict]) -> list[list[dict]]:
    """All valid non-overlapping 2–3 match combinations."""
    valid = []
    for size in range(2, min(len(matches) + 1, MAX_MATCHES_PER_DAY + 1)):
        for combo in combinations(matches, size):
            sorted_combo = sorted(combo, key=lambda m: m["match_time"])
            overlapping = any(
                has_overlap(sorted_combo[i]["match_time"], sorted_combo[i + 1]["match_time"])
                for i in range(len(sorted_combo) - 1)
            )
            if not overlapping:
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


def best_combination(matches: list[dict], db) -> Optional[list[dict]]:
    if len(matches) < 2:
        return None
    weekend = is_weekend(matches[0]["match_time"])
    combos = generate_combinations(matches)
    if not combos:
        return None
    scored = [(c, score_combination(c, weekend, db)) for c in combos]
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

def _fmt_match_line(match: dict) -> str:
    dt = datetime.fromtimestamp(match["match_time"], tz=ET)
    time_str = dt.strftime("%I:%M %p ET").lstrip("0")
    return f"  • [{match['division']}] {match['team_home']} vs {match['team_away']} — {time_str}"


def _fmt_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    return dt.strftime("%A %B ") + str(dt.day)


def build_proposal_message(date_str: str, current_combo: list[dict],
                            proposed_combo: list[dict],
                            current_score: int, proposed_score: int, db) -> str:
    current_lines = "\n".join(
        _fmt_match_line(m) for m in sorted(current_combo, key=lambda m: m["match_time"])
    )
    proposed_lines = "\n".join(
        _fmt_match_line(m) for m in sorted(proposed_combo, key=lambda m: m["match_time"])
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
        f"React with ❌ to reject. Auto-approves in 12 hours."
    )


# --- Shared accept / propose helpers (called by both EventsCog and daily sweep) ---

async def accept_combination(combo: list[dict], date_str: str, db, teamup,
                              broadcast_channel) -> None:
    """Post a combination to TeamUp and notify the broadcast channel.

    Creates all TeamUp events before writing any DB updates so a partial
    TeamUp failure does not leave the DB in an inconsistent state.
    """
    # Phase 1: create all TeamUp events (may raise TeamUpError — no DB writes yet)
    created: list[tuple[dict, str]] = []
    for match in combo:
        title = f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
        end_ts = match["match_time"] + int(MATCH_DURATION_H * 3600)
        event_id = teamup.create_event(title, match["match_time"], end_ts)
        created.append((match, event_id))

    # Phase 2: all events created — now write DB updates
    for match, event_id in created:
        db.update_match_teamup_id(match["id"], event_id)
        db.increment_scheduled_count(match["team_home"])
        db.increment_scheduled_count(match["team_away"])

    if broadcast_channel:
        lines = "\n".join(
            _fmt_match_line(m) for m in sorted(combo, key=lambda m: m["match_time"])
        )
        await broadcast_channel.send(
            f"📅 **Matches added for {_fmt_date(date_str)}:**\n{lines}"
        )


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
    await msg.add_reaction("❌")
    old_event_ids = [m["teamup_event_id"] for m in current if m.get("teamup_event_id")]
    new_match_ids = [m["id"] for m in proposed]
    db.insert_pending_change(
        description=msg_text,
        old_event_ids=old_event_ids,
        new_match_ids=new_match_ids,
        discord_message_id=str(msg.id),
    )


async def process_expired_changes(db, teamup, broadcast_channel) -> None:
    """Auto-approve pending changes whose 12-hour window has passed."""
    expired = db.get_expired_pending_changes()
    for change in expired:
        old_ids = json.loads(change["old_event_ids"])
        new_match_ids = json.loads(change["new_match_ids"])

        # Remove old TeamUp events and clear their DB references
        for event_id in old_ids:
            try:
                teamup.delete_event(event_id)
            except Exception:
                pass
            for match in db.get_matches_by_teamup_event_id(event_id):
                db.update_match_teamup_id(match["id"], None)
                db.decrement_scheduled_count(match["team_home"])
                db.decrement_scheduled_count(match["team_away"])

        # Accept new combination
        new_matches = [db.get_match(mid) for mid in new_match_ids]
        new_matches = [m for m in new_matches if m]
        date_str = None
        if new_matches:
            date_str = datetime.fromtimestamp(
                new_matches[0]["match_time"], tz=ET
            ).strftime("%Y-%m-%d")
            await accept_combination(new_matches, date_str, db, teamup, broadcast_channel)

        db.resolve_pending_change(change["id"], approved=True)
        if broadcast_channel and date_str:
            await broadcast_channel.send(
                f"✅ Schedule proposal auto-approved for {_fmt_date(date_str)}."
            )


async def run_daily_sweep(db, teamup, broadcast_channel) -> None:
    """3am sweep: evaluate all upcoming days this week and process expired changes."""
    today = datetime.now(tz=ET).date()
    monday = today - timedelta(days=today.weekday())

    for i in range(7):
        day = monday + timedelta(days=i)
        if day < today:
            continue
        date_str = day.isoformat()

        if db.get_blocked_day(date_str):
            continue

        all_matches = db.get_matches_for_date(date_str)
        if len(all_matches) < 2:
            continue

        scheduled = db.get_scheduled_matches_for_date(date_str)
        best = best_combination(all_matches, db)
        if best is None:
            continue

        if not scheduled:
            await accept_combination(best, date_str, db, teamup, broadcast_channel)
        else:
            weekend = is_weekend(scheduled[0]["match_time"])
            current_score = score_combination(scheduled, weekend, db)
            proposed_score = score_combination(best, is_weekend(best[0]["match_time"]), db)
            if combo_match_ids(best) == combo_match_ids(scheduled):
                continue
            if proposed_score <= current_score:
                continue
            await propose_change(
                date_str, scheduled, best, current_score, proposed_score,
                db, broadcast_channel,
            )

    await process_expired_changes(db, teamup, broadcast_channel)
