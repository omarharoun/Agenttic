"""Text / NLP overlap metrics — deterministic, pure-Python reimplementations of
standard public methods. No external NLP libraries required.

Each metric is also registered as a deterministic check in the scoring CHECKS
registry (via ``@check("name")``), so a rubric criterion with
``scorer="code"`` and ``check_ref="<name>"`` scores through the normal run →
score → scorecard pipeline.

Metric groups implemented here (all standard public algorithms):
  - Levenshtein / normalised edit-distance similarity
  - ROUGE-1, ROUGE-2, ROUGE-L  (Lin 2004)
  - BLEU with add-1 smoothing   (Papineni et al. 2002 + Chen & Cherry 2014)
  - METEOR-style unigram F-mean (Banerjee & Lavie 2005, no chunking penalty)
  - Token-level F1 / precision / recall  (Rajpurkar et al. 2016, SQuAD-style)
  - Exact match / normalised exact match (SQuAD-style)
  - Jaccard / token-overlap similarity
  - Character n-gram overlap F1 (default n=3)
  - Cosine similarity over TF-IDF bag-of-words vectors (pure Python, no deps)
  - Substring containment, keyword containment, regex match
  - Length-in-range, word-count-in-range, number-present, date-present

LICENSING: every implementation is written from the public algorithm definitions
(papers, Wikipedia, SQuAD evaluation script). No code was copied from Future AGI
or any unlicensed artefact. Standard algorithms are not copyrightable.
"""

from __future__ import annotations

import math
import re
import string
from collections import Counter
from typing import Sequence

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)
_ARTICLES = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

# Simple date-like patterns: ISO (2024-01-15), US (01/15/2024), text (Jan 15 2024)
_DATE_PAT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"          # ISO
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"   # US slash
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
    re.IGNORECASE,
)
_NUMBER_PAT = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _tokenize(text: str) -> list[str]:
    """Lowercase whitespace-split tokeniser — same across all metrics for
    consistency. No stemming or stopword removal (metrics are pure overlap)."""
    return (text or "").lower().split()


def _ngrams(tokens: Sequence[str], n: int) -> Counter:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _char_ngrams(text: str, n: int) -> Counter:
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def _tf(tokens: list[str]) -> dict[str, float]:
    """Relative term frequency."""
    c = Counter(tokens)
    total = sum(c.values())
    return {t: v / total for t, v in c.items()} if total else {}


def _squad_normalize(s: str) -> str:
    """SQuAD answer normalization: lowercase, strip punctuation and articles."""
    s = (s or "").lower()
    s = s.translate(_PUNCT_TABLE)
    s = _ARTICLES.sub(" ", s)
    return _WHITESPACE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# 1. Levenshtein / normalised edit-distance similarity
# ---------------------------------------------------------------------------

def levenshtein_distance(s: str, t: str) -> int:
    """Standard DP edit distance (insert / delete / substitute each cost 1)."""
    s, t = s or "", t or ""
    m, n = len(s), len(t)
    # Use a single row of length n+1, updated in-place.
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev_row = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            if s[i - 1] == t[j - 1]:
                dp[j] = prev_row[j - 1]
            else:
                dp[j] = 1 + min(prev_row[j], dp[j - 1], prev_row[j - 1])
    return dp[n]


def levenshtein_similarity_score(hyp: str, ref: str) -> float:
    """Normalised edit-distance similarity in [0, 1] (1 = identical)."""
    if not hyp and not ref:
        return 1.0
    d = levenshtein_distance(hyp, ref)
    return 1.0 - d / max(len(hyp), len(ref))


# ---------------------------------------------------------------------------
# 2. ROUGE (Lin, 2004)
# ---------------------------------------------------------------------------

def _lcs_length(a: list, b: list) -> int:
    """LCS length by DP in O(m·n) time, O(n) space."""
    m, n = len(a), len(b)
    dp = [0] * (n + 1)
    for i in range(m):
        prev = dp[:]
        for j in range(n):
            dp[j + 1] = prev[j] + 1 if a[i] == b[j] else max(dp[j + 1], dp[j])
    return dp[n]


def rouge_n_score(hyp: str, ref: str, n: int) -> float:
    """ROUGE-N F1 (precision-recall harmonic mean over n-gram overlap)."""
    h_tokens = _tokenize(hyp)
    r_tokens = _tokenize(ref)
    h_ng = _ngrams(h_tokens, n)
    r_ng = _ngrams(r_tokens, n)
    if not r_ng:
        return 0.0
    overlap = sum((h_ng & r_ng).values())
    h_total = sum(h_ng.values())
    r_total = sum(r_ng.values())
    prec = overlap / h_total if h_total else 0.0
    rec = overlap / r_total
    if prec + rec == 0.0:
        return 0.0
    return 2.0 * prec * rec / (prec + rec)


def rouge1_score(hyp: str, ref: str) -> float:
    """ROUGE-1 F1 (unigram overlap)."""
    return rouge_n_score(hyp, ref, n=1)


def rouge2_score(hyp: str, ref: str) -> float:
    """ROUGE-2 F1 (bigram overlap)."""
    return rouge_n_score(hyp, ref, n=2)


def rougel_score(hyp: str, ref: str) -> float:
    """ROUGE-L F1 (longest common subsequence)."""
    h_tokens = _tokenize(hyp)
    r_tokens = _tokenize(ref)
    if not h_tokens or not r_tokens:
        return 0.0
    lcs = _lcs_length(h_tokens, r_tokens)
    prec = lcs / len(h_tokens)
    rec = lcs / len(r_tokens)
    if prec + rec == 0.0:
        return 0.0
    return 2.0 * prec * rec / (prec + rec)


# ---------------------------------------------------------------------------
# 3. BLEU with smoothing (Papineni et al. 2002 + Chen & Cherry 2014, method 1)
# ---------------------------------------------------------------------------

def bleu_score(hyp: str, ref: str, max_n: int = 4) -> float:
    """Corpus BLEU with add-1 smoothing (Chen & Cherry 2014 method 1).

    Single-reference, 1..max_n uniform weights. Returns a score in [0, 1].
    """
    h_tokens = _tokenize(hyp)
    r_tokens = _tokenize(ref)
    if not h_tokens:
        return 0.0
    # Brevity penalty
    bp = 1.0 if len(h_tokens) >= len(r_tokens) else math.exp(1.0 - len(r_tokens) / len(h_tokens))
    log_avg = 0.0
    for n in range(1, max_n + 1):
        h_ng = _ngrams(h_tokens, n)
        r_ng = _ngrams(r_tokens, n)
        # Add-1 smoothing: numerator += 1, denominator += 1
        matched = sum((h_ng & r_ng).values()) + 1
        total = sum(h_ng.values()) + 1
        log_avg += math.log(matched / total)
    return bp * math.exp(log_avg / max_n)


# ---------------------------------------------------------------------------
# 4. METEOR-style unigram F-mean (Banerjee & Lavie 2005, no chunking penalty)
# ---------------------------------------------------------------------------

def meteor_score(hyp: str, ref: str) -> float:
    """METEOR-style unigram F-mean (without fragmentation penalty).

    Weighted harmonic mean with recall weight α=0.9:
      F = P·R / (α·R + (1-α)·P) ≡ 10·P·R / (9·P + R)
    """
    h_tokens = _tokenize(hyp)
    r_tokens = _tokenize(ref)
    if not h_tokens or not r_tokens:
        return 0.0
    matched = sum((Counter(h_tokens) & Counter(r_tokens)).values())
    if matched == 0:
        return 0.0
    prec = matched / len(h_tokens)
    rec = matched / len(r_tokens)
    return 10.0 * prec * rec / (9.0 * prec + rec)


# ---------------------------------------------------------------------------
# 5. Token-level F1 / precision / recall  (SQuAD-style, Rajpurkar et al. 2016)
# ---------------------------------------------------------------------------

def _squad_token_counts(hyp: str, ref: str) -> tuple[Counter, Counter, int]:
    """Return (hyp_counter, ref_counter, overlap_count) after SQuAD normalisation."""
    h_cnt = Counter(_squad_normalize(hyp).split())
    r_cnt = Counter(_squad_normalize(ref).split())
    overlap = sum((h_cnt & r_cnt).values())
    return h_cnt, r_cnt, overlap


def token_f1_score(hyp: str, ref: str) -> float:
    """SQuAD token-level F1."""
    h_cnt, r_cnt, overlap = _squad_token_counts(hyp, ref)
    if not overlap:
        return 0.0
    prec = overlap / sum(h_cnt.values())
    rec = overlap / sum(r_cnt.values())
    return 2.0 * prec * rec / (prec + rec)


def token_precision_score(hyp: str, ref: str) -> float:
    """SQuAD token-level precision."""
    h_cnt, _, overlap = _squad_token_counts(hyp, ref)
    h_total = sum(h_cnt.values())
    return overlap / h_total if h_total else 0.0


def token_recall_score(hyp: str, ref: str) -> float:
    """SQuAD token-level recall."""
    _, r_cnt, overlap = _squad_token_counts(hyp, ref)
    r_total = sum(r_cnt.values())
    return overlap / r_total if r_total else 0.0


# ---------------------------------------------------------------------------
# 6. Exact match / normalised exact match
# ---------------------------------------------------------------------------

def exact_match_score(hyp: str, ref: str) -> float:
    """Strict exact match (strip leading/trailing whitespace only)."""
    return 1.0 if (hyp or "").strip() == (ref or "").strip() else 0.0


def normalized_exact_match_score(hyp: str, ref: str) -> float:
    """SQuAD-style normalised exact match (lowercase, no punctuation/articles)."""
    return 1.0 if _squad_normalize(hyp) == _squad_normalize(ref) else 0.0


# ---------------------------------------------------------------------------
# 7. Jaccard / token-overlap similarity
# ---------------------------------------------------------------------------

def jaccard_similarity_score(hyp: str, ref: str) -> float:
    """Jaccard index over token sets: |A∩B| / |A∪B|."""
    h_set = set(_tokenize(hyp))
    r_set = set(_tokenize(ref))
    if not h_set and not r_set:
        return 1.0
    if not h_set or not r_set:
        return 0.0
    return len(h_set & r_set) / len(h_set | r_set)


# ---------------------------------------------------------------------------
# 8. Character n-gram overlap F1  (default n=3)
# ---------------------------------------------------------------------------

def char_ngram_overlap_score(hyp: str, ref: str, n: int = 3) -> float:
    """Character n-gram overlap F1 (precision–recall harmonic mean)."""
    h_ng = _char_ngrams(hyp or "", n)
    r_ng = _char_ngrams(ref or "", n)
    if not r_ng:
        return 0.0
    overlap = sum((h_ng & r_ng).values())
    h_total = sum(h_ng.values())
    r_total = sum(r_ng.values())
    prec = overlap / h_total if h_total else 0.0
    rec = overlap / r_total
    if prec + rec == 0.0:
        return 0.0
    return 2.0 * prec * rec / (prec + rec)


# ---------------------------------------------------------------------------
# 9. Cosine similarity over TF-IDF vectors (pure Python, no external deps)
# ---------------------------------------------------------------------------

def cosine_tfidf_score(hyp: str, ref: str) -> float:
    """Cosine similarity over smoothed TF-IDF vectors.

    Corpus = the two documents being compared (so rare-to-one document terms
    get higher IDF weight than terms shared by both). Smoothed IDF:
      idf(t) = log((N+1)/(df+1)) + 1   (N=2, df∈{1,2})
    """
    h_tokens = _tokenize(hyp)
    r_tokens = _tokenize(ref)
    if not h_tokens or not r_tokens:
        return 0.0
    h_tf = _tf(h_tokens)
    r_tf = _tf(r_tokens)
    vocab = set(h_tf) | set(r_tf)
    # Smoothed IDF in a 2-document corpus
    idf: dict[str, float] = {}
    for term in vocab:
        df = (1 if term in h_tf else 0) + (1 if term in r_tf else 0)
        idf[term] = math.log((3) / (df + 1)) + 1.0  # N+1=3, df+1
    h_vec = {t: h_tf.get(t, 0.0) * idf[t] for t in vocab}
    r_vec = {t: r_tf.get(t, 0.0) * idf[t] for t in vocab}
    dot = sum(h_vec[t] * r_vec[t] for t in vocab)
    norm_h = math.sqrt(sum(v * v for v in h_vec.values()))
    norm_r = math.sqrt(sum(v * v for v in r_vec.values()))
    if norm_h == 0.0 or norm_r == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_h * norm_r)))


# ---------------------------------------------------------------------------
# 10. Pattern / constraint checks (no reference needed — output only)
# ---------------------------------------------------------------------------

def substring_containment_score(text: str, substring: str, *, case_sensitive: bool = False) -> float:
    """1.0 if ``substring`` appears in ``text``, else 0.0."""
    if not case_sensitive:
        return 1.0 if substring.lower() in (text or "").lower() else 0.0
    return 1.0 if substring in (text or "") else 0.0


def keyword_containment_score(text: str, keywords: list[str], *, case_sensitive: bool = False) -> float:
    """Fraction of ``keywords`` found in ``text`` (1.0 = all present)."""
    if not keywords:
        return 1.0
    hay = (text or "") if case_sensitive else (text or "").lower()
    hits = sum(1 for kw in keywords if (kw if case_sensitive else kw.lower()) in hay)
    return hits / len(keywords)


def regex_match_score(text: str, pattern: str) -> float:
    """1.0 if ``pattern`` (re.search) finds a match in ``text``, else 0.0."""
    try:
        return 1.0 if re.search(pattern, text or "") else 0.0
    except re.error:
        return 0.0


def length_in_range_score(text: str, *, min_length: int = 0, max_length: int = 10_000) -> float:
    """1.0 if ``len(text)`` is within [min_length, max_length], else 0.0."""
    n = len(text or "")
    return 1.0 if min_length <= n <= max_length else 0.0


def word_count_in_range_score(text: str, *, min_words: int = 0, max_words: int = 10_000) -> float:
    """1.0 if word-count is within [min_words, max_words], else 0.0."""
    n = len((text or "").split())
    return 1.0 if min_words <= n <= max_words else 0.0


def number_present_score(text: str) -> float:
    """1.0 if at least one number (int or float) appears in ``text``, else 0.0."""
    return 1.0 if _NUMBER_PAT.search(text or "") else 0.0


def date_present_score(text: str) -> float:
    """1.0 if at least one date-like string appears in ``text``, else 0.0."""
    return 1.0 if _DATE_PAT.search(text or "") else 0.0


# ---------------------------------------------------------------------------
# Check registrations (scoring CHECKS registry)
# ---------------------------------------------------------------------------
# All checks read ground truth from test_case.expected and score the agent's
# trace.final_output. Convention: the primary reference text key is "reference".
# ---------------------------------------------------------------------------

# Importing here rather than at the top of the file to avoid a circular import
# (checks.py imports canonical_checks, which is a sibling module; keeping
# registrations at the bottom keeps the pure-function block importable standalone).

from ascore.scoring.checks import _need, check  # noqa: E402
from ascore.schema.trace import Trace  # noqa: E402
from ascore.schema.testcase import TestCase  # noqa: E402


# -- 1. Levenshtein ---------------------------------------------------------

@check("levenshtein_similarity")
def _check_levenshtein_similarity(trace: Trace, tc: TestCase) -> float:
    """Normalised edit-distance similarity between the agent's output and the
    reference string (``expected["reference"]``). Score in [0, 1]."""
    ref = str(_need(tc, "reference"))
    return levenshtein_similarity_score(trace.final_output or "", ref)


# -- 2. ROUGE ---------------------------------------------------------------

@check("rouge1")
def _check_rouge1(trace: Trace, tc: TestCase) -> float:
    """ROUGE-1 F1 (unigram token overlap) vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return rouge1_score(trace.final_output or "", ref)


@check("rouge2")
def _check_rouge2(trace: Trace, tc: TestCase) -> float:
    """ROUGE-2 F1 (bigram token overlap) vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return rouge2_score(trace.final_output or "", ref)


@check("rougel")
def _check_rougel(trace: Trace, tc: TestCase) -> float:
    """ROUGE-L F1 (LCS-based overlap) vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return rougel_score(trace.final_output or "", ref)


# -- 3. BLEU ----------------------------------------------------------------

@check("bleu")
def _check_bleu(trace: Trace, tc: TestCase) -> float:
    """BLEU-4 with add-1 smoothing vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return bleu_score(trace.final_output or "", ref)


# -- 4. METEOR --------------------------------------------------------------

@check("meteor")
def _check_meteor(trace: Trace, tc: TestCase) -> float:
    """METEOR-style unigram F-mean vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return meteor_score(trace.final_output or "", ref)


# -- 5. Token F1 / precision / recall ---------------------------------------

@check("token_f1")
def _check_token_f1(trace: Trace, tc: TestCase) -> float:
    """SQuAD token-level F1 vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return token_f1_score(trace.final_output or "", ref)


@check("token_precision")
def _check_token_precision(trace: Trace, tc: TestCase) -> float:
    """SQuAD token-level precision vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return token_precision_score(trace.final_output or "", ref)


@check("token_recall")
def _check_token_recall(trace: Trace, tc: TestCase) -> float:
    """SQuAD token-level recall vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return token_recall_score(trace.final_output or "", ref)


# -- 6. Exact match ---------------------------------------------------------

@check("exact_match")
def _check_exact_match(trace: Trace, tc: TestCase) -> float:
    """Strict exact match vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return exact_match_score(trace.final_output or "", ref)


@check("normalized_exact_match")
def _check_normalized_exact_match(trace: Trace, tc: TestCase) -> float:
    """SQuAD-style normalised exact match vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return normalized_exact_match_score(trace.final_output or "", ref)


# -- 7. Jaccard -------------------------------------------------------------

@check("jaccard_similarity")
def _check_jaccard_similarity(trace: Trace, tc: TestCase) -> float:
    """Jaccard token-set similarity vs ``expected["reference"]``."""
    ref = str(_need(tc, "reference"))
    return jaccard_similarity_score(trace.final_output or "", ref)


# -- 8. Character n-gram ----------------------------------------------------

@check("char_ngram_overlap")
def _check_char_ngram_overlap(trace: Trace, tc: TestCase) -> float:
    """Character n-gram overlap F1 vs ``expected["reference"]``.
    n defaults to 3; override with ``expected["char_n"]``."""
    ref = str(_need(tc, "reference"))
    n = int((tc.expected or {}).get("char_n", 3))
    return char_ngram_overlap_score(trace.final_output or "", ref, n=n)


# -- 9. Cosine TF-IDF -------------------------------------------------------

@check("cosine_tfidf_similarity")
def _check_cosine_tfidf(trace: Trace, tc: TestCase) -> float:
    """TF-IDF cosine similarity vs ``expected["reference"]`` (pure Python)."""
    ref = str(_need(tc, "reference"))
    return cosine_tfidf_score(trace.final_output or "", ref)


# -- 10. Pattern / constraint checks ----------------------------------------

@check("substring_containment")
def _check_substring_containment(trace: Trace, tc: TestCase) -> float:
    """1.0 if ``expected["substring"]`` appears in the agent's output."""
    substring = str(_need(tc, "substring"))
    case_sensitive = bool((tc.expected or {}).get("case_sensitive", False))
    return substring_containment_score(trace.final_output or "", substring,
                                       case_sensitive=case_sensitive)


@check("keyword_containment")
def _check_keyword_containment(trace: Trace, tc: TestCase) -> float:
    """Fraction of ``expected["keywords"]`` (list) found in the agent's output."""
    keywords = list(_need(tc, "keywords"))
    case_sensitive = bool((tc.expected or {}).get("case_sensitive", False))
    return keyword_containment_score(trace.final_output or "", keywords,
                                     case_sensitive=case_sensitive)


@check("regex_match")
def _check_regex_match(trace: Trace, tc: TestCase) -> float:
    """1.0 if ``expected["pattern"]`` (re.search) matches the agent's output."""
    pattern = str(_need(tc, "pattern"))
    return regex_match_score(trace.final_output or "", pattern)


@check("length_in_range")
def _check_length_in_range(trace: Trace, tc: TestCase) -> float:
    """1.0 if character length is within [expected["min_length"], expected["max_length"]]."""
    exp = tc.expected or {}
    min_len = int(exp.get("min_length", 0))
    max_len = int(exp.get("max_length", 10_000))
    return length_in_range_score(trace.final_output or "", min_length=min_len, max_length=max_len)


@check("word_count_in_range")
def _check_word_count_in_range(trace: Trace, tc: TestCase) -> float:
    """1.0 if word count is within [expected["min_words"], expected["max_words"]]."""
    exp = tc.expected or {}
    min_w = int(exp.get("min_words", 0))
    max_w = int(exp.get("max_words", 10_000))
    return word_count_in_range_score(trace.final_output or "", min_words=min_w, max_words=max_w)


@check("number_present")
def _check_number_present(trace: Trace, tc: TestCase) -> float:
    """1.0 if the agent's output contains at least one number."""
    return number_present_score(trace.final_output or "")


@check("date_present")
def _check_date_present(trace: Trace, tc: TestCase) -> float:
    """1.0 if the agent's output contains at least one date-like string."""
    return date_present_score(trace.final_output or "")
