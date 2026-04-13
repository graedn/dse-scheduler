import re
import difflib
import json
from dataclasses import dataclass

KNOWN_DIVISIONS = ["Premier", "Division 1", "Division 2", "Division 3", "Division 4"]
FUZZY_THRESHOLD = 0.8


@dataclass
class ParsedMatch:
    division: str
    week: str
    team_home: str
    team_away: str
    match_time: int  # Unix timestamp UTC


class ParseError(Exception):
    pass


def has_required_structure(text: str) -> bool:
    lines = text.strip().split("\n")
    has_division = any(re.match(r"division\s*:", l, re.IGNORECASE) for l in lines)
    has_week = any(re.match(r"(week|round)\s*:", l, re.IGNORECASE) for l in lines)
    has_vs = any(re.search(r"\bvs\b", l, re.IGNORECASE) for l in lines)
    has_time = any(
        re.search(r"time\s*:.*<t:\d+", l, re.IGNORECASE) for l in lines
    )
    return all([has_division, has_week, has_vs, has_time])


def parse_division(text: str) -> str:
    for line in text.split("\n"):
        m = re.match(r"division\s*:\s*(.+)", line.strip(), re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            # Try matching progressively shorter tokens (split on common delimiters)
            # to handle inputs like "Premier - Season 2" or "Division 1 (Spring)"
            tokens = re.split(r"[-,(]", raw)
            candidates_to_try = [raw] + [t.strip() for t in tokens if t.strip()]
            for candidate in candidates_to_try:
                close = difflib.get_close_matches(
                    candidate, KNOWN_DIVISIONS, n=1, cutoff=FUZZY_THRESHOLD
                )
                if close:
                    return close[0]
            raise ParseError(f"Could not identify division from: '{raw}'")
    raise ParseError("No division line found")


def parse_week(text: str) -> str:
    for line in text.split("\n"):
        m = re.match(r"(week|round)\s*:\s*(.+)", line.strip(), re.IGNORECASE)
        if m:
            label = m.group(1).capitalize()
            raw = m.group(2).strip()
            num_m = re.search(r"\d+", raw)
            if num_m:
                return f"{label} {num_m.group(0)}"
            raise ParseError(f"Could not extract number from '{raw}'")
    raise ParseError("No week/round line found")


def parse_timestamp(text: str) -> int:
    for line in text.split("\n"):
        if re.match(r"time\s*:", line.strip(), re.IGNORECASE):
            m = re.search(r"<t:(\d+)(?::[a-zA-Z])?>", line)
            if m:
                return int(m.group(1))
            raise ParseError("Time line present but no valid Discord timestamp tag found")
    raise ParseError("No time line found")


def resolve_team_name(raw_name: str, db) -> str:
    """
    Fuzzy-match raw_name against existing team canonical names and aliases.
    Returns the canonical name. Creates a new team if no match found.
    Records the raw_name as an alias if it differs from canonical.
    """
    raw_clean = raw_name.strip()
    teams = db.get_all_teams()

    # Build lookup: candidate string -> canonical name
    candidates: dict[str, str] = {}
    for team in teams:
        candidates[team["name"]] = team["name"]
        for alias in json.loads(team["aliases"]):
            candidates[alias] = team["name"]

    if candidates:
        close = difflib.get_close_matches(
            raw_clean, list(candidates.keys()), n=1, cutoff=FUZZY_THRESHOLD
        )
        if close:
            canonical = candidates[close[0]]
            if raw_clean.lower() != canonical.lower() and raw_clean not in candidates:
                db.add_team_alias(canonical, raw_clean)
            return canonical

    # No match — new team
    canonical = raw_clean.title()
    db.upsert_team(canonical)
    return canonical


def parse_teams(text: str, db) -> tuple[str, str]:
    field_pattern = re.compile(r"^(division|week|round|time)\s*:", re.IGNORECASE)
    for line in text.split("\n"):
        if field_pattern.match(line.strip()):
            continue
        m = re.search(r"(.+?)\s+vs\s+(.+)", line.strip(), re.IGNORECASE)
        if m:
            raw_home = m.group(1).strip()
            raw_away = m.group(2).strip()
            home = resolve_team_name(raw_home, db)
            away = resolve_team_name(raw_away, db)
            if home == away:
                raise ParseError(f"Home and away teams cannot be the same: '{home}'")
            return home, away
    raise ParseError("No 'vs' line found for team names")


def parse_post(text: str, db) -> ParsedMatch:
    """Full parsing pipeline. Raises ParseError on any field failure."""
    division = parse_division(text)
    week = parse_week(text)
    team_home, team_away = parse_teams(text, db)
    match_time = parse_timestamp(text)
    return ParsedMatch(
        division=division,
        week=week,
        team_home=team_home,
        team_away=team_away,
        match_time=match_time,
    )
