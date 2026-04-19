"""IDF-weighted cosine similarity for matching team names to Discord roles."""
import math
import re
from typing import Optional

CONFIDENCE_THRESHOLD = 0.85

_STOP_WORDS = frozenset({
    "team", "the", "and", "of", "fc", "esports", "gaming", "club",
    "gg", "org", "united", "city", "squad", "collective",
})


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS]


def _build_idf(corpus: list[str]) -> dict[str, float]:
    N = len(corpus)
    if N == 0:
        return {}
    df: dict[str, int] = {}
    for doc in corpus:
        for tok in set(_tokenize(doc)):
            df[tok] = df.get(tok, 0) + 1
    return {tok: math.log((N + 1) / (cnt + 1)) + 1.0 for tok, cnt in df.items()}


def _tfidf_vec(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    vec: dict[str, float] = {}
    for t in tokens:
        vec[t] = vec.get(t, 0.0) + idf.get(t, 1.0)
    return vec


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(t, 0.0) * v for t, v in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_best_match(
    team_name: str,
    roles: list[tuple[str, str]],
) -> tuple[Optional[str], float]:
    """Return (role_id, confidence) for the best-matching Discord role.

    role_id is None when best confidence < CONFIDENCE_THRESHOLD.
    roles is a list of (role_id, role_name) tuples.
    """
    if not roles:
        return None, 0.0

    role_names = [name for _, name in roles]
    idf = _build_idf(role_names + [team_name])

    query_tokens = _tokenize(team_name)
    if not query_tokens:
        return None, 0.0

    query_vec = _tfidf_vec(query_tokens, idf)
    best_id: Optional[str] = None
    best_conf = 0.0

    for role_id, role_name in roles:
        role_tokens = _tokenize(role_name)
        if not role_tokens:
            continue
        role_vec = _tfidf_vec(role_tokens, idf)
        conf = _cosine(query_vec, role_vec)
        if conf > best_conf:
            best_conf = conf
            best_id = role_id

    if best_conf >= CONFIDENCE_THRESHOLD:
        return best_id, best_conf
    return None, best_conf
