"""AssistantBench answer-matching — fractional, partial-credit answer accuracy.

Faithful pure-Python port of the official AssistantBench evaluation (Yoran et
al. 2024, *AssistantBench: Can Web Agents Solve Realistic and Time-Consuming
Tasks?*, arXiv:2407.15711; leaderboard ``evaluation/evaluate_utils``). The web
agent emits a free-form final answer (a string, a number, a newline-separated
list, or one JSON object per line) and AssistantBench scores it for *partial
credit* against the gold answer — exact match is not required:

- **strings / string lists** — DROP-style token-F1: normalise (lowercase, strip
  punctuation + articles, canonicalise numbers, tokenise on space/hyphen), then
  optimally 1-1 align the predicted token-bags to the gold bags and average the
  per-bag F1. A numeric gate (``_match_numbers_if_present``) zeroes a pair when
  the gold has a number the prediction lacks.
- **numbers** — a symmetric log-ratio: ``max(0, 1 - |ln(pred/gold)|)``. Exact ->
  1.0, decaying to 0 once the prediction is off by a factor of *e* (~2.72x).
- **JSON dicts** — recall over gold keys (each value scored by its own typed
  evaluator) and precision over predicted keys, combined as an F1; lists of
  dicts are optimally aligned then averaged.

The evaluator is chosen from the *gold* answer's parsed type, and the prediction
is coerced to that type — matching the official harness. ``scipy`` is not a
dependency here, so the optimal 1-1 bag alignment is done with a small
pure-Python assignment (brute force for tiny bag counts, greedy fallback).

We score AssistantBench's *real* answers with AssistantBench's *own* metric; we
do not reproduce the paper's leaderboard numbers (those come from full live web
runs of specific agents). See assistantbench_data/ATTRIBUTION.md.
"""

from __future__ import annotations

import json
import math
import re
import string
from itertools import permutations

# -- string / list scoring (DROP-style token F1) ---------------------------

_ARTICLES = re.compile(r"\b(a|an|the)\b", re.UNICODE)
_EXCLUDE = set(string.punctuation)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except (ValueError, TypeError):
        return False


def _normalize_number(text: str) -> str:
    return str(float(text)) if _is_number(text) else text


def _remove_punc(text: str) -> str:
    if _is_number(text):
        return text
    return "".join(ch for ch in text if ch not in _EXCLUDE)


def _normalize_token(token: str) -> str:
    no_punc = _remove_punc(token.lower())
    no_num = _normalize_number(no_punc)
    no_articles = _ARTICLES.sub(" ", no_num)
    return " ".join(no_articles.split())


def _normalize_answer(text: str) -> str:
    parts = [_normalize_token(tok) for tok in re.split(" |-", text)]
    return " ".join(p for p in parts if p.strip()).strip()


def _to_bags(answer) -> list[set[str]]:
    spans = answer if isinstance(answer, (list, tuple)) else [answer]
    return [set(_normalize_answer(str(span)).split()) for span in spans]


def _compute_f1(pred_bag: set[str], gold_bag: set[str]) -> float:
    inter = len(gold_bag & pred_bag)
    precision = 1.0 if not pred_bag else inter / len(pred_bag)
    recall = 1.0 if not gold_bag else inter / len(gold_bag)
    if precision == 0.0 and recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _match_numbers_if_present(gold_bag: set[str], pred_bag: set[str]) -> bool:
    gold_nums = {w for w in gold_bag if _is_number(w)}
    pred_nums = {w for w in pred_bag if _is_number(w)}
    return (not gold_nums) or bool(gold_nums & pred_nums)


def _optimal_assignment(scores: list[list[float]]) -> float:
    """Mean of the per-gold-row max scores under the best 1-1 alignment of
    predicted bags to gold bags (rows=gold, cols=pred). Rows beyond the number of
    predictions contribute 0 — the official ``_align_bags`` semantics without a
    scipy dependency."""
    n_gold, n_pred = len(scores), len(scores[0]) if scores else 0
    denom = max(n_gold, n_pred)
    if denom == 0:
        return 0.0
    rows, cols = range(n_gold), range(n_pred)
    if min(n_gold, n_pred) <= 6:                       # exact: small bag counts
        best = 0.0
        if n_gold <= n_pred:
            for perm in permutations(cols, n_gold):
                best = max(best, sum(scores[g][perm[g]] for g in rows))
        else:
            for perm in permutations(rows, n_pred):
                best = max(best, sum(scores[perm[c]][c] for c in cols))
        return best / denom
    # greedy fallback for large lists (never hit by vendored AssistantBench data)
    used, total = set(), 0.0
    for g in sorted(rows, key=lambda g: -max(scores[g])):
        c = max((c for c in cols if c not in used), key=lambda c: scores[g][c],
                default=None)
        if c is None:
            break
        used.add(c)
        total += scores[g][c]
    return total / denom


def evaluate_strings(pred, gold) -> float:
    """DROP token-F1 between a predicted and gold answer (string or list)."""
    pred_bags, gold_bags = _to_bags(pred), _to_bags(gold)
    matrix = [[_compute_f1(p, g) if _match_numbers_if_present(g, p) else 0.0
               for p in pred_bags] for g in gold_bags]
    return _optimal_assignment(matrix)


# -- number scoring (symmetric log-ratio) ----------------------------------

def distance_function_log(pred: float, gold: float) -> float:
    if pred == gold == 0:
        return 1.0
    pred = pred or 1e-4
    gold = gold or 1e-4
    ratio = pred / gold if pred > gold else gold / pred
    return max(0.0, 1 - math.log(ratio))


def evaluate_numbers(pred, gold) -> float:
    try:
        return distance_function_log(float(pred), float(gold))
    except (ValueError, TypeError):
        return 0.0


# -- JSON-dict scoring (recall/precision F1 over keys) ----------------------

def _fix_number(value):
    if isinstance(value, str):
        cleaned = value.replace("$", " ").replace("%", " ").replace("sqft", " ")
        cleaned = cleaned.strip().replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return value
    if isinstance(value, int):
        return float(value)
    return value


def _evaluate_value(pred, gold) -> float:
    return evaluate_numbers(pred, gold) if isinstance(gold, float) \
        else evaluate_strings(pred, gold)


def _calc_recall(pred: dict, gold: dict, use_gold: bool) -> float:
    scores = []
    for key, gold_value in gold.items():
        gold_value = _fix_number(gold_value)
        pred_value = _fix_number(pred.get(key))
        if key not in pred or type(pred_value) is not type(gold_value):
            scores.append(0.0)
            continue
        ref = gold_value if use_gold else pred_value
        scores.append(evaluate_numbers(pred_value, gold_value)
                      if isinstance(ref, float) else
                      evaluate_strings(pred_value, gold_value))
    return sum(scores) / len(scores) if scores else 0.0


def _evaluate_pair_of_dicts(pred: dict, gold: dict) -> float:
    recall = _calc_recall(pred, gold, True)
    precision = _calc_recall(gold, pred, False)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate_dicts(pred: list[dict], gold: list[dict]) -> float:
    if not (isinstance(pred, list) and pred and all(isinstance(d, dict) for d in pred)):
        return 0.0
    matrix = [[_evaluate_pair_of_dicts(p, g) for p in pred] for g in gold]
    return _optimal_assignment(matrix)


# -- top-level dispatch (choose evaluator from the gold answer's type) ------

def _parse_dicts(text: str) -> list[dict] | None:
    """Parse one JSON object per non-empty line, else None."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        out.append(obj)
    return out or None


def _as_list(text: str) -> list[str] | str:
    items = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return items if len(items) > 1 else (items[0] if items else "")


def score_answer(prediction: str, gold: str) -> float:
    """AssistantBench partial-credit score in [0,1] of a free-form ``prediction``
    against the ``gold`` answer, dispatching on the gold answer's type
    (number / JSON-dict list / string-or-list)."""
    gold = (gold or "").strip()
    prediction = (prediction or "").strip()
    if not prediction:
        return 0.0
    if not gold:
        return 0.0
    if _is_number(gold):
        return evaluate_numbers(prediction, gold)
    gold_dicts = _parse_dicts(gold)
    if gold_dicts is not None:
        pred_dicts = _parse_dicts(prediction) or []
        return evaluate_dicts(pred_dicts, gold_dicts)
    return evaluate_strings(_as_list(prediction), _as_list(gold))


# -- answer rate (did the agent attempt vs abstain) ------------------------

_ABSTAIN_MARKERS = (
    "i don't know", "i do not know", "no answer", "n/a", "not sure",
    "cannot determine", "can't determine", "unable to answer", "unanswerable",
)


def is_answered(prediction: str) -> bool:
    """An AssistantBench answer is *attempted* when the agent emits a non-empty
    final answer that is not an explicit abstention. Answer rate is the fraction
    of cases attempted — the paper rewards agents that abstain rather than guess,
    so this is reported alongside (not folded into) answer accuracy."""
    text = (prediction or "").strip().lower()
    if not text:
        return False
    return not any(m == text or text.startswith(m) for m in _ABSTAIN_MARKERS)
