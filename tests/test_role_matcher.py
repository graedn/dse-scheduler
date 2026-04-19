"""Tests for role_matcher.py — IDF-based confidence scoring."""
import pytest
from role_matcher import (
    find_best_match,
    _tokenize,
    _build_idf,
    _cosine,
    _tfidf_vec,
    CONFIDENCE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

def test_tokenize_lowercases():
    assert _tokenize("Alpha Wolves") == ["alpha", "wolves"]


def test_tokenize_removes_stop_words():
    tokens = _tokenize("Team Alpha")
    assert "team" not in tokens
    assert "alpha" in tokens


def test_tokenize_strips_punctuation():
    # "fc" is a stop word and is filtered; "alpha" is kept
    assert _tokenize("FC Alpha!") == ["alpha"]


def test_tokenize_numbers_kept():
    assert "9000" in _tokenize("Division 9000")


def test_tokenize_empty_string():
    assert _tokenize("") == []


def test_tokenize_all_stop_words():
    assert _tokenize("Team The And") == []


# ---------------------------------------------------------------------------
# _build_idf
# ---------------------------------------------------------------------------

def test_build_idf_empty_corpus():
    assert _build_idf([]) == {}


def test_build_idf_single_doc_unique_term():
    idf = _build_idf(["Alpha"])
    assert "alpha" in idf


def test_build_idf_common_terms_lower_weight():
    # "alpha" appears in all 3 docs — "beta" only once
    idf = _build_idf(["Alpha Wolves", "Alpha Foxes", "Alpha Sharks", "Beta Squad"])
    assert idf["alpha"] < idf["beta"]


def test_build_idf_stop_words_excluded():
    idf = _build_idf(["Team Alpha", "Team Beta"])
    assert "team" not in idf


# ---------------------------------------------------------------------------
# find_best_match
# ---------------------------------------------------------------------------

def test_exact_match_high_confidence():
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes")]
    role_id, conf = find_best_match("Alpha Wolves", roles)
    assert role_id == "111"
    assert conf >= CONFIDENCE_THRESHOLD


def test_partial_match_scores_highest_for_containing_role():
    # "Wolves" alone is below threshold (a single word can't reliably match),
    # but "Alpha Wolves" should score higher than "Beta Foxes".
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes")]
    _, conf_111 = find_best_match("Wolves", [("111", "Alpha Wolves")])
    _, conf_222 = find_best_match("Wolves", [("222", "Beta Foxes")])
    assert conf_111 > conf_222


def test_no_match_below_threshold_returns_none():
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes")]
    role_id, conf = find_best_match("Gamma Sharks", roles)
    assert role_id is None
    assert conf < CONFIDENCE_THRESHOLD


def test_empty_roles_list():
    role_id, conf = find_best_match("Alpha Wolves", [])
    assert role_id is None
    assert conf == 0.0


def test_all_stop_word_team_name_returns_none():
    roles = [("111", "Alpha Wolves")]
    role_id, conf = find_best_match("Team The", roles)
    assert role_id is None


def test_stop_words_in_team_name_not_weighted():
    # "Team Alpha Wolves" vs "Alpha Wolves" — stop word removal should align them
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes")]
    role_id, conf = find_best_match("Team Alpha Wolves", roles)
    assert role_id == "111"
    assert conf >= CONFIDENCE_THRESHOLD


def test_role_with_only_stop_words_skipped():
    # A role named "The Team" tokenizes to nothing — should not error
    roles = [("111", "The Team"), ("222", "Alpha Wolves")]
    role_id, conf = find_best_match("Alpha Wolves", roles)
    assert role_id == "222"


def test_best_match_is_returned_not_first():
    # "Foxes" should match "Beta Foxes" not "Alpha Wolves"
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes")]
    role_id, conf = find_best_match("Beta Foxes", roles)
    assert role_id == "222"


def test_test1_role_matches_test1_team():
    """The /test-thread command uses 'test1' as team name and expects 'test1' role."""
    roles = [("999", "test1"), ("888", "Alpha Wolves"), ("777", "Moderator")]
    role_id, conf = find_best_match("test1", roles)
    assert role_id == "999"
    assert conf >= CONFIDENCE_THRESHOLD


def test_unknown_team_below_threshold():
    """Clearly invented team name should not match anything."""
    roles = [("111", "Alpha Wolves"), ("222", "Beta Foxes"), ("333", "Gamma Bears")]
    role_id, conf = find_best_match("Zyx9000 Phantoms", roles)
    assert role_id is None


def test_confidence_is_between_0_and_1():
    roles = [("111", "Alpha Wolves")]
    _, conf = find_best_match("Something Random Here", roles)
    assert 0.0 <= conf <= 1.0


def test_idf_downweights_common_discord_role_words():
    # "Gaming" appears in multiple roles — team name without it should still match
    roles = [
        ("111", "Alpha Gaming"),
        ("222", "Beta Gaming"),
        ("333", "Gamma Gaming"),
        ("444", "Alpha Wolves"),
    ]
    role_id, conf = find_best_match("Alpha Wolves", roles)
    assert role_id == "444"
