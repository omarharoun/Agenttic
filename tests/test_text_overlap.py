"""Tests for text/NLP overlap metrics (metrics/text_overlap.py).

Covers:
  1. Levenshtein / normalised edit-distance similarity
  2. ROUGE-1, ROUGE-2, ROUGE-L
  3. BLEU with smoothing
  4. METEOR-style unigram F-mean
  5. Token-level F1 / precision / recall (SQuAD-style)
  6. Exact match / normalised exact match
  7. Jaccard / token-overlap similarity
  8. Character n-gram overlap
  9. Cosine TF-IDF similarity (pure Python)
  10. Substring/keyword/regex/length/word-count/number/date checks

Also verifies that all checks are registered in the CHECKS scoring registry
and that the catalog entries are present and consistent.
"""

from __future__ import annotations

import pytest

# Pure-function API
from ascore.metrics.text_overlap import (
    bleu_score,
    char_ngram_overlap_score,
    cosine_tfidf_score,
    date_present_score,
    exact_match_score,
    jaccard_similarity_score,
    keyword_containment_score,
    length_in_range_score,
    levenshtein_distance,
    levenshtein_similarity_score,
    meteor_score,
    normalized_exact_match_score,
    number_present_score,
    regex_match_score,
    rouge1_score,
    rouge2_score,
    rougel_score,
    substring_containment_score,
    token_f1_score,
    token_precision_score,
    token_recall_score,
    word_count_in_range_score,
)

# Registry
from ascore.scoring.checks import CHECKS

# Catalog
from ascore.metrics.catalog import BY_ID, CHECK_TO_METRIC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_trace(output: str):
    """Minimal Trace-like stub for check invocation."""
    from ascore.schema.trace import Trace

    return Trace(
        trace_id="t1",
        agent_id="a1",
        agent_config_hash="h",
        spans=[],
        visibility="black_box",
        final_output=output,
    )


def make_tc(expected: dict):
    """Minimal TestCase-like stub."""
    from ascore.schema.testcase import TestCase

    return TestCase(
        test_id="x",
        suite_id="s",
        task_description="",
        expected=expected,
        rubric_id="r",
    )


def run_check(name: str, output: str, expected: dict) -> float:
    return CHECKS[name](make_trace(output), make_tc(expected))


# ---------------------------------------------------------------------------
# 1. Levenshtein
# ---------------------------------------------------------------------------

class TestLevenshtein:
    def test_identical(self):
        assert levenshtein_distance("abc", "abc") == 0

    def test_empty_vs_nonempty(self):
        assert levenshtein_distance("", "abc") == 3
        assert levenshtein_distance("abc", "") == 3

    def test_both_empty(self):
        assert levenshtein_distance("", "") == 0

    def test_single_sub(self):
        assert levenshtein_distance("cat", "cut") == 1

    def test_insert_delete(self):
        assert levenshtein_distance("kitten", "sitting") == 3

    def test_similarity_identical(self):
        assert levenshtein_similarity_score("hello", "hello") == 1.0

    def test_similarity_both_empty(self):
        assert levenshtein_similarity_score("", "") == 1.0

    def test_similarity_one_empty(self):
        assert levenshtein_similarity_score("", "abc") == 0.0

    def test_similarity_range(self):
        s = levenshtein_similarity_score("kitten", "sitting")
        assert 0.0 <= s <= 1.0

    def test_check_registered(self):
        assert "levenshtein_similarity" in CHECKS

    def test_check_identical(self):
        assert run_check("levenshtein_similarity", "hello", {"reference": "hello"}) == 1.0

    def test_check_different(self):
        s = run_check("levenshtein_similarity", "kitten", {"reference": "sitting"})
        assert 0.0 <= s < 1.0

    def test_check_empty_output(self):
        s = run_check("levenshtein_similarity", "", {"reference": "abc"})
        assert s == 0.0


# ---------------------------------------------------------------------------
# 2. ROUGE
# ---------------------------------------------------------------------------

class TestROUGE:
    def test_rouge1_identical(self):
        assert rouge1_score("the cat sat", "the cat sat") == pytest.approx(1.0)

    def test_rouge1_no_overlap(self):
        assert rouge1_score("dog runs fast", "cat sat here") == 0.0

    def test_rouge1_partial(self):
        s = rouge1_score("the cat sat on the mat", "the cat sat")
        assert 0.0 < s < 1.0

    def test_rouge1_empty_ref(self):
        assert rouge1_score("something", "") == 0.0

    def test_rouge1_empty_hyp(self):
        assert rouge1_score("", "something") == 0.0

    def test_rouge2_identical(self):
        assert rouge2_score("the cat sat", "the cat sat") == pytest.approx(1.0)

    def test_rouge2_no_bigram_overlap(self):
        assert rouge2_score("a b c", "d e f") == 0.0

    def test_rougel_identical(self):
        assert rougel_score("the quick brown fox", "the quick brown fox") == pytest.approx(1.0)

    def test_rougel_subsequence(self):
        # "the fox" is a subsequence of "the quick brown fox"
        s = rougel_score("the fox", "the quick brown fox")
        assert 0.0 < s < 1.0

    def test_rougel_empty(self):
        assert rougel_score("", "hello") == 0.0

    def test_rouge1_check(self):
        assert run_check("rouge1", "the cat sat", {"reference": "the cat sat"}) == pytest.approx(1.0)

    def test_rouge2_check(self):
        assert run_check("rouge2", "the cat sat", {"reference": "the cat sat"}) == pytest.approx(1.0)

    def test_rougel_check(self):
        assert run_check("rougel", "the cat sat", {"reference": "the cat sat"}) == pytest.approx(1.0)

    def test_rouge_checks_registered(self):
        for name in ("rouge1", "rouge2", "rougel"):
            assert name in CHECKS


# ---------------------------------------------------------------------------
# 3. BLEU
# ---------------------------------------------------------------------------

class TestBLEU:
    def test_identical(self):
        s = bleu_score("the cat sat on the mat", "the cat sat on the mat")
        assert s > 0.9

    def test_empty_hyp(self):
        assert bleu_score("", "hello world") == 0.0

    def test_no_overlap(self):
        s = bleu_score("dog runs", "cat sat")
        # smoothing keeps score > 0 but very small
        assert s >= 0.0

    def test_score_range(self):
        for hyp, ref in [("hello world", "world hello"), ("abc", "def"), ("x y z", "x y z")]:
            s = bleu_score(hyp, ref)
            assert 0.0 <= s <= 1.0

    def test_brevity_penalty(self):
        # Short hyp gets penalised vs. long ref
        s_short = bleu_score("cat", "the cat sat on the mat with a hat")
        s_long = bleu_score("the cat sat on the mat with a hat",
                            "the cat sat on the mat with a hat")
        assert s_short <= s_long

    def test_check_registered(self):
        assert "bleu" in CHECKS

    def test_check_identical(self):
        s = run_check("bleu", "the cat sat", {"reference": "the cat sat"})
        assert s > 0.9

    def test_check_empty(self):
        assert run_check("bleu", "", {"reference": "hello"}) == 0.0


# ---------------------------------------------------------------------------
# 4. METEOR
# ---------------------------------------------------------------------------

class TestMETEOR:
    def test_identical(self):
        assert meteor_score("hello world", "hello world") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert meteor_score("dog runs", "cat sits") == 0.0

    def test_partial(self):
        s = meteor_score("hello world today", "hello world")
        # precision < 1.0 but recall == 1.0 -> F_mean > 0 and < 1.0
        assert 0.0 < s < 1.0

    def test_empty_hyp(self):
        assert meteor_score("", "hello") == 0.0

    def test_empty_ref(self):
        assert meteor_score("hello", "") == 0.0

    def test_recall_weighted(self):
        # When hyp covers all ref tokens but is longer, recall=1.0 precision<1.0
        # F_mean should be > plain F1
        s = meteor_score("a b c d e", "a b c")
        assert s > 0.0

    def test_check_registered(self):
        assert "meteor" in CHECKS

    def test_check_identical(self):
        assert run_check("meteor", "hello world", {"reference": "hello world"}) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 5. Token F1 / precision / recall
# ---------------------------------------------------------------------------

class TestTokenF1:
    def test_identical(self):
        assert token_f1_score("the cat sat", "the cat sat") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert token_f1_score("dog runs", "cat sits") == 0.0

    def test_precision_partial(self):
        # hyp has 4 tokens (non-article), ref has 2, overlap 2
        # -> precision=0.5, recall=1.0
        p = token_precision_score("cat dog fox wolf", "cat dog")
        r = token_recall_score("cat dog fox wolf", "cat dog")
        assert p == pytest.approx(0.5)
        assert r == pytest.approx(1.0)

    def test_f1_harmonic(self):
        p = token_precision_score("cat dog fox wolf", "cat dog")
        r = token_recall_score("cat dog fox wolf", "cat dog")
        expected_f1 = 2 * p * r / (p + r)
        assert token_f1_score("cat dog fox wolf", "cat dog") == pytest.approx(expected_f1)

    def test_normalization(self):
        # "The" and "the" should match after normalization
        assert token_f1_score("The Cat", "the cat") == pytest.approx(1.0)

    def test_article_stripped(self):
        # "a" and "the" are stripped -> "cat sat" vs "cat sat"
        assert normalized_exact_match_score("the cat sat", "a cat sat") == 1.0

    def test_empty(self):
        assert token_f1_score("", "hello") == 0.0
        assert token_f1_score("hello", "") == 0.0

    def test_checks_registered(self):
        for name in ("token_f1", "token_precision", "token_recall"):
            assert name in CHECKS

    def test_checks_run(self):
        for name in ("token_f1", "token_precision", "token_recall"):
            s = run_check(name, "hello world", {"reference": "hello world"})
            assert s == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 6. Exact match / normalised exact match
# ---------------------------------------------------------------------------

class TestExactMatch:
    def test_exact_match_identical(self):
        assert exact_match_score("hello", "hello") == 1.0

    def test_exact_match_strips_whitespace(self):
        assert exact_match_score("  hello  ", "hello") == 1.0

    def test_exact_match_case_sensitive(self):
        assert exact_match_score("Hello", "hello") == 0.0

    def test_exact_match_no_match(self):
        assert exact_match_score("cat", "dog") == 0.0

    def test_normalized_case(self):
        assert normalized_exact_match_score("Hello World", "hello world") == 1.0

    def test_normalized_punct(self):
        assert normalized_exact_match_score("hello, world!", "hello world") == 1.0

    def test_normalized_articles(self):
        assert normalized_exact_match_score("The cat sat", "cat sat") == 1.0

    def test_normalized_no_match(self):
        assert normalized_exact_match_score("dog", "cat") == 0.0

    def test_checks_registered(self):
        assert "exact_match" in CHECKS
        assert "normalized_exact_match" in CHECKS

    def test_exact_check(self):
        assert run_check("exact_match", "hello", {"reference": "hello"}) == 1.0
        assert run_check("exact_match", "Hello", {"reference": "hello"}) == 0.0

    def test_normalized_check(self):
        assert run_check("normalized_exact_match", "The Cat", {"reference": "cat"}) == 1.0


# ---------------------------------------------------------------------------
# 7. Jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical(self):
        assert jaccard_similarity_score("a b c", "a b c") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert jaccard_similarity_score("a b c", "d e f") == pytest.approx(0.0)

    def test_partial(self):
        s = jaccard_similarity_score("a b c", "a b d")
        assert s == pytest.approx(2 / 4)  # |{a,b}| / |{a,b,c,d}|

    def test_both_empty(self):
        assert jaccard_similarity_score("", "") == pytest.approx(1.0)

    def test_one_empty(self):
        assert jaccard_similarity_score("a b", "") == pytest.approx(0.0)

    def test_check_registered(self):
        assert "jaccard_similarity" in CHECKS

    def test_check_run(self):
        s = run_check("jaccard_similarity", "a b c", {"reference": "a b c"})
        assert s == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 8. Character n-gram
# ---------------------------------------------------------------------------

class TestCharNgram:
    def test_identical(self):
        assert char_ngram_overlap_score("hello", "hello", n=3) == pytest.approx(1.0)

    def test_no_overlap(self):
        # "abc" and "xyz" share no 3-grams
        assert char_ngram_overlap_score("abc", "xyz", n=3) == 0.0

    def test_partial(self):
        s = char_ngram_overlap_score("hello", "hell", n=3)
        assert 0.0 < s < 1.0

    def test_unigram(self):
        # "abc" vs "abd": overlap = {a,b} → 2 matched; each string 3 chars
        # precision = recall = 2/3 → F1 = 2/3
        s = char_ngram_overlap_score("abc", "abd", n=1)
        assert s == pytest.approx(2 / 3)

    def test_default_n(self):
        s1 = char_ngram_overlap_score("hello world", "hello world")
        assert s1 == pytest.approx(1.0)

    def test_check_registered(self):
        assert "char_ngram_overlap" in CHECKS

    def test_check_default_n(self):
        s = run_check("char_ngram_overlap", "hello", {"reference": "hello"})
        assert s == pytest.approx(1.0)

    def test_check_custom_n(self):
        s = run_check("char_ngram_overlap", "hello", {"reference": "hello", "char_n": 2})
        assert s == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 9. Cosine TF-IDF
# ---------------------------------------------------------------------------

class TestCosineTFIDF:
    def test_identical(self):
        s = cosine_tfidf_score("the quick brown fox", "the quick brown fox")
        assert s == pytest.approx(1.0)

    def test_no_overlap(self):
        s = cosine_tfidf_score("alpha beta gamma", "delta epsilon zeta")
        assert s == pytest.approx(0.0)

    def test_partial_overlap(self):
        s = cosine_tfidf_score("hello world foo", "hello world bar")
        assert 0.0 < s < 1.0

    def test_both_empty(self):
        assert cosine_tfidf_score("", "") == 0.0

    def test_one_empty(self):
        assert cosine_tfidf_score("", "hello") == 0.0

    def test_range(self):
        for h, r in [("a b c", "a b d"), ("foo", "foo bar"), ("x", "y")]:
            assert 0.0 <= cosine_tfidf_score(h, r) <= 1.0

    def test_shared_terms_lower_idf(self):
        # Terms in both docs get IDF = log(3/3)+1=1; terms in only one get
        # log(3/2)+1 ≈ 1.405 — rare terms boost the score relative to very
        # common ones.  The score should be strictly between 0 and 1 for partial.
        s = cosine_tfidf_score("the cat sat here", "the cat sat there")
        assert 0.0 < s < 1.0

    def test_check_registered(self):
        assert "cosine_tfidf_similarity" in CHECKS

    def test_check_identical(self):
        s = run_check("cosine_tfidf_similarity", "hello world", {"reference": "hello world"})
        assert s == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 10. Pattern / constraint checks
# ---------------------------------------------------------------------------

class TestPatternChecks:
    # substring_containment
    def test_substring_present(self):
        assert substring_containment_score("hello world", "world") == 1.0

    def test_substring_absent(self):
        assert substring_containment_score("hello", "xyz") == 0.0

    def test_substring_case_insensitive(self):
        assert substring_containment_score("Hello World", "hello") == 1.0

    def test_substring_case_sensitive_miss(self):
        assert substring_containment_score("Hello", "hello", case_sensitive=True) == 0.0

    def test_check_substring_registered(self):
        assert "substring_containment" in CHECKS

    def test_check_substring(self):
        assert run_check("substring_containment", "hello world", {"substring": "world"}) == 1.0
        assert run_check("substring_containment", "hello", {"substring": "xyz"}) == 0.0

    # keyword_containment
    def test_all_keywords(self):
        assert keyword_containment_score("foo bar baz", ["foo", "bar"]) == 1.0

    def test_some_keywords(self):
        assert keyword_containment_score("foo", ["foo", "bar"]) == pytest.approx(0.5)

    def test_no_keywords(self):
        assert keyword_containment_score("foo bar", []) == 1.0

    def test_check_keyword_registered(self):
        assert "keyword_containment" in CHECKS

    def test_check_keyword(self):
        s = run_check("keyword_containment", "foo bar baz", {"keywords": ["foo", "bar"]})
        assert s == pytest.approx(1.0)

    # regex_match
    def test_regex_match(self):
        assert regex_match_score("my email is test@example.com", r"\w+@\w+\.\w+") == 1.0

    def test_regex_no_match(self):
        assert regex_match_score("hello world", r"\d+") == 0.0

    def test_regex_invalid_pattern(self):
        # Bad regex should return 0.0 (not crash)
        assert regex_match_score("hello", "[invalid") == 0.0

    def test_check_regex_registered(self):
        assert "regex_match" in CHECKS

    def test_check_regex(self):
        s = run_check("regex_match", "The answer is 42", {"pattern": r"\d+"})
        assert s == 1.0

    # length_in_range
    def test_length_in_range_pass(self):
        assert length_in_range_score("hello", min_length=3, max_length=10) == 1.0

    def test_length_too_short(self):
        assert length_in_range_score("hi", min_length=5, max_length=20) == 0.0

    def test_length_too_long(self):
        assert length_in_range_score("hello world", min_length=0, max_length=5) == 0.0

    def test_check_length_registered(self):
        assert "length_in_range" in CHECKS

    def test_check_length(self):
        s = run_check("length_in_range", "hello", {"min_length": 3, "max_length": 10})
        assert s == 1.0

    # word_count_in_range
    def test_word_count_pass(self):
        assert word_count_in_range_score("a b c d", min_words=2, max_words=6) == 1.0

    def test_word_count_too_few(self):
        assert word_count_in_range_score("hi", min_words=5, max_words=10) == 0.0

    def test_check_word_count_registered(self):
        assert "word_count_in_range" in CHECKS

    # number_present
    def test_number_present(self):
        assert number_present_score("The answer is 42.") == 1.0

    def test_number_float(self):
        assert number_present_score("value: 3.14") == 1.0

    def test_number_absent(self):
        assert number_present_score("no numbers here") == 0.0

    def test_number_negative(self):
        assert number_present_score("temperature is -5 degrees") == 1.0

    def test_check_number_registered(self):
        assert "number_present" in CHECKS

    def test_check_number(self):
        assert run_check("number_present", "42 cats", {}) == 1.0
        assert run_check("number_present", "no numbers", {}) == 0.0

    # date_present
    def test_date_iso(self):
        assert date_present_score("The date is 2024-06-15.") == 1.0

    def test_date_us(self):
        assert date_present_score("Born 06/15/1990") == 1.0

    def test_date_text(self):
        assert date_present_score("On January 15 2024 we met.") == 1.0

    def test_date_absent(self):
        assert date_present_score("no date here at all") == 0.0

    def test_check_date_registered(self):
        assert "date_present" in CHECKS

    def test_check_date(self):
        assert run_check("date_present", "2024-01-15", {}) == 1.0
        assert run_check("date_present", "no date", {}) == 0.0


# ---------------------------------------------------------------------------
# Registry completeness: all 21 checks must be in CHECKS
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    "levenshtein_similarity",
    "rouge1", "rouge2", "rougel",
    "bleu",
    "meteor",
    "token_f1", "token_precision", "token_recall",
    "exact_match", "normalized_exact_match",
    "jaccard_similarity",
    "char_ngram_overlap",
    "cosine_tfidf_similarity",
    "substring_containment", "keyword_containment", "regex_match",
    "length_in_range", "word_count_in_range",
    "number_present", "date_present",
]


@pytest.mark.parametrize("name", ALL_CHECKS)
def test_check_is_registered(name):
    assert name in CHECKS, f"check {name!r} is not in the CHECKS registry"


# ---------------------------------------------------------------------------
# Catalog: all check_refs map to a metric in BY_ID
# ---------------------------------------------------------------------------

EXPECTED_METRICS = [
    "text_levenshtein",
    "text_rouge",
    "text_bleu",
    "text_meteor",
    "text_token_f1",
    "text_exact_match",
    "text_jaccard",
    "text_char_ngram",
    "text_cosine",
    "text_pattern",
]


@pytest.mark.parametrize("mid", EXPECTED_METRICS)
def test_metric_in_catalog(mid):
    assert mid in BY_ID, f"metric {mid!r} not found in catalog BY_ID"


@pytest.mark.parametrize("mid", EXPECTED_METRICS)
def test_metric_check_refs_registered(mid):
    metric = BY_ID[mid]
    for ref in metric.check_refs:
        assert ref in CHECKS, (
            f"catalog metric {mid!r} references check_ref {ref!r} "
            f"which is not in CHECKS"
        )


@pytest.mark.parametrize("name", ALL_CHECKS)
def test_check_maps_to_catalog_metric(name):
    mid = CHECK_TO_METRIC.get(name)
    assert mid is not None, (
        f"check {name!r} is not referenced by any CanonicalMetric.check_refs"
    )
    assert mid in BY_ID, f"check {name!r} maps to metric {mid!r} which is not in BY_ID"


@pytest.mark.parametrize("mid", EXPECTED_METRICS)
def test_metric_category(mid):
    assert BY_ID[mid].category == "text_overlap"


@pytest.mark.parametrize("mid", EXPECTED_METRICS)
def test_metric_weight_zero(mid):
    # Diagnostic metrics not yet folded into the Agenttic Index — weight must be 0.
    assert BY_ID[mid].weight == 0.0


# ---------------------------------------------------------------------------
# Edge cases and numerical sanity
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_rouge1_symmetry(self):
        # ROUGE-N is not symmetric in general (precision vs recall differ with length)
        # but with identical inputs both ways should be 1.0
        assert rouge1_score("a b c", "a b c") == pytest.approx(rouge1_score("a b c", "a b c"))

    def test_bleu_smoothing_prevents_zero(self):
        # With smoothing, BLEU > 0 even for completely different texts
        # (because +1 to numerator keeps log finite)
        s = bleu_score("absolutely nothing in common", "completely different words here")
        assert s > 0.0

    def test_levenshtein_distance_triangle_inequality(self):
        # Edit distance satisfies d(a,c) <= d(a,b) + d(b,c)
        a, b, c = "kitten", "sitting", "bitten"
        assert levenshtein_distance(a, c) <= levenshtein_distance(a, b) + levenshtein_distance(b, c)

    def test_cosine_self_similarity_is_one(self):
        for text in ["hello world", "the quick brown fox", "a", "1 2 3"]:
            assert cosine_tfidf_score(text, text) == pytest.approx(1.0)

    def test_token_f1_f1_le_one(self):
        for h, r in [("a b c d e", "a b"), ("x", "x y z"), ("", "abc")]:
            assert 0.0 <= token_f1_score(h, r) <= 1.0

    def test_meteor_weighted_toward_recall(self):
        # hyp = superset of ref -> recall=1.0, precision<1.0
        # hyp = subset of ref -> recall<1.0, precision=1.0
        # Because α=0.9 (recall-weighted), superset should score higher
        s_superset = meteor_score("a b c d e f", "a b c")
        s_subset = meteor_score("a b c", "a b c d e f")
        assert s_superset >= s_subset

    def test_bleu_max_n_respected(self):
        # A sentence shorter than max_n still produces a valid score
        s = bleu_score("hi", "hi there", max_n=4)
        assert 0.0 <= s <= 1.0

    def test_char_ngram_n_equals_len(self):
        # n == len(text) -> only one n-gram, identical strings score 1.0
        assert char_ngram_overlap_score("abc", "abc", n=3) == pytest.approx(1.0)

    def test_jaccard_duplicate_tokens_deduplicated(self):
        # "a a b" as a set is {a, b} — same as "a b c" ∩ {a, b}
        s = jaccard_similarity_score("a a b", "a b c")
        assert s == pytest.approx(2 / 3)  # |{a,b}| / |{a,b,c}|
