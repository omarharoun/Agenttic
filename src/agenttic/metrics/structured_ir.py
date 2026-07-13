"""Deterministic structured-output, retrieval, and ranking evaluation checks.

Registered into the scoring CHECKS registry via the ``@check`` decorator.
Covers:

- JSON validity & schema conformance (json_schema_valid, json_required_keys,
  json_type_shape_match, json_no_extra_keys)
- Structured extraction accuracy (structured_extraction_exact,
  structured_extraction_normalized)
- SQL syntax validity (sql_is_valid; via sqlglot)
- Number and date extraction (number_match, date_match)
- Format validators (is_email_format, is_url_format, is_phone_format,
  is_uuid_format, is_iso_date_format, enum_membership)
- Information-retrieval / ranking (ir_ndcg_at_k, ir_mrr, ir_precision_at_k,
  ir_recall_at_k, ir_map, ir_hit_rate)
- Set / list accuracy (set_precision_score, set_recall_score, set_f1_score,
  ordering_kendall_tau, ordering_spearman)
- Span / citation overlap (span_overlap_f1)

All algorithms are reimplemented from standard public definitions (no copied
licensed code).
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Trace
from agenttic.scoring.checks import _need, check

# ---------------------------------------------------------------------------
# Helpers (Part 1)
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
    "null": type(None),
    "none": type(None),
}


def _parse_json_output(output: str) -> Any:
    """Try to parse *output* as JSON; raise ValueError on failure."""
    try:
        return json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# JSON family
# ---------------------------------------------------------------------------


@check("json_schema_valid")
def json_schema_valid(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* is valid JSON that passes the JSON Schema in
    ``expected["json_schema"]``; 0.0 if JSON is malformed or schema fails.

    Uses ``jsonschema.validate`` (Draft 7 by default).
    """
    schema = _need(tc, "json_schema")
    try:
        import jsonschema  # lazy import; declared transitive dep
    except ImportError:  # pragma: no cover
        # jsonschema unavailable — degrade gracefully: just check valid JSON
        try:
            _parse_json_output(trace.final_output)
            return 1.0
        except ValueError:
            return 0.0
    try:
        instance = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    try:
        jsonschema.validate(instance, schema)
        return 1.0
    except jsonschema.ValidationError:
        return 0.0
    except jsonschema.SchemaError:
        return 0.0


@check("json_required_keys")
def json_required_keys(trace: Trace, tc: TestCase) -> float:
    """Fraction of ``expected["required_keys"]`` present in the JSON dict output.

    Returns 0.0 if the output is not valid JSON or not a dict.
    """
    required = list(_need(tc, "required_keys"))
    if not required:
        return 1.0
    try:
        parsed = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    hits = sum(1 for k in required if k in parsed)
    return hits / len(required)


@check("json_type_shape_match")
def json_type_shape_match(trace: Trace, tc: TestCase) -> float:
    """Fraction of fields in ``expected["json_type_shape"]`` that have the
    declared Python type.

    ``json_type_shape`` is a ``{field: type_name}`` dict where type_name is one
    of: str/string, int/integer, float/number, bool/boolean, list/array,
    dict/object, null/none.

    Returns 0.0 if the output is not valid JSON or not a dict.
    """
    shape: dict[str, str] = _need(tc, "json_type_shape")
    if not shape:
        return 1.0
    try:
        parsed = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    hits = 0
    for field, type_name in shape.items():
        expected_type = _TYPE_MAP.get((type_name or "").lower())
        if expected_type is None:
            continue  # unknown type name — skip
        if field in parsed:
            value = parsed[field]
            if expected_type is float and isinstance(value, int):
                # JSON integers are acceptable for "number" / "float"
                hits += 1
            elif isinstance(value, expected_type):
                hits += 1
    return hits / len(shape)


@check("json_no_extra_keys")
def json_no_extra_keys(trace: Trace, tc: TestCase) -> float:
    """1.0 if the JSON dict output contains ONLY keys from
    ``expected["allowed_keys"]``; 0.0 otherwise or if not a dict.
    """
    allowed = set(_need(tc, "allowed_keys"))
    try:
        parsed = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    extra = set(parsed.keys()) - allowed
    return 1.0 if not extra else 0.0


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------


@check("structured_extraction_exact")
def structured_extraction_exact(trace: Trace, tc: TestCase) -> float:
    """Fraction of fields in ``expected["extracted_fields"]`` where
    ``str(parsed[k]).strip() == str(v).strip()`` (exact string match after
    stripping whitespace).

    Parses *final_output* as JSON; returns 0.0 if not a dict.
    """
    fields: dict[str, Any] = _need(tc, "extracted_fields")
    if not fields:
        return 1.0
    try:
        parsed = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    hits = sum(
        1 for k, v in fields.items()
        if k in parsed and str(parsed[k]).strip() == str(v).strip()
    )
    return hits / len(fields)


def _normalize_str(s: str) -> str:
    """Lower-case and collapse whitespace."""
    return re.sub(r"\s+", " ", s.lower()).strip()


@check("structured_extraction_normalized")
def structured_extraction_normalized(trace: Trace, tc: TestCase) -> float:
    """Fraction of fields in ``expected["extracted_fields"]`` that match after
    case-folding and whitespace collapsing.

    Parses *final_output* as JSON; returns 0.0 if not a dict.
    """
    fields: dict[str, Any] = _need(tc, "extracted_fields")
    if not fields:
        return 1.0
    try:
        parsed = _parse_json_output(trace.final_output)
    except ValueError:
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    hits = sum(
        1 for k, v in fields.items()
        if k in parsed
        and _normalize_str(str(parsed[k])) == _normalize_str(str(v))
    )
    return hits / len(fields)


# ---------------------------------------------------------------------------
# SQL validity
# ---------------------------------------------------------------------------

_SQL_FENCE_RE = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Return SQL from a ```sql ... ``` fence, or the raw text."""
    m = _SQL_FENCE_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()


@check("sql_is_valid")
def sql_is_valid(trace: Trace, tc: TestCase) -> float:
    """1.0 if the SQL in *final_output* parses without error (sqlglot).

    SQL is extracted from a ```sql…``` fence if present; otherwise the full
    output is tried as-is.  ``expected["sql_dialect"]`` (optional, default
    None) selects the sqlglot dialect.  Returns 0.5 if sqlglot is unavailable.
    """
    dialect = (tc.expected or {}).get("sql_dialect", None)
    sql_text = _extract_sql(trace.final_output)
    if not sql_text:
        return 0.0
    try:
        import sqlglot  # available per pyproject.toml
        import sqlglot.errors
    except ImportError:  # pragma: no cover
        return 0.5
    try:
        result = sqlglot.parse(
            sql_text,
            dialect=dialect,
            error_level=sqlglot.errors.ErrorLevel.RAISE,
        )
        # parse returns a list; empty list means nothing was parsed
        return 1.0 if result else 0.0
    except sqlglot.errors.ParseError:
        return 0.0


# ---------------------------------------------------------------------------
# Number / date extraction
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@check("number_match")
def number_match(trace: Trace, tc: TestCase) -> float:
    """1.0 if the first number found in *final_output* matches
    ``expected["expected_number"]`` within tolerance.

    Optional keys:
    - ``expected["tolerance"]`` (float, default 1e-6): absolute tolerance.
    - ``expected["relative_tolerance"]`` (bool, default False): if True,
      tolerance is a fraction of the expected value.
    """
    expected_num = float(_need(tc, "expected_number"))
    tolerance = float((tc.expected or {}).get("tolerance", 1e-6))
    relative = bool((tc.expected or {}).get("relative_tolerance", False))

    m = _NUMBER_RE.search(trace.final_output or "")
    if not m:
        return 0.0
    try:
        got = float(m.group())
    except ValueError:
        return 0.0

    if relative:
        base = abs(expected_num) if expected_num != 0 else 1.0
        return 1.0 if abs(got - expected_num) <= tolerance * base else 0.0
    return 1.0 if abs(got - expected_num) <= tolerance else 0.0


@check("date_match")
def date_match(trace: Trace, tc: TestCase) -> float:
    """1.0 if the first ISO date (YYYY-MM-DD) in *final_output* matches
    ``expected["expected_date"]`` (also YYYY-MM-DD).
    """
    expected_date = str(_need(tc, "expected_date")).strip()
    m = _ISO_DATE_RE.search(trace.final_output or "")
    if not m:
        return 0.0
    return 1.0 if m.group(1) == expected_date else 0.0


# ---------------------------------------------------------------------------
# Format validators
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
_URL_RE = re.compile(
    r"^(https?|ftp)://"                          # scheme
    r"[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"   # path etc.
    r"$",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@check("is_email_format")
def is_email_format(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) matches a simplified RFC 5322 email
    pattern; 0.0 otherwise.
    """
    return 1.0 if _EMAIL_RE.fullmatch((trace.final_output or "").strip()) else 0.0


@check("is_url_format")
def is_url_format(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) is a valid http/https/ftp URL; 0.0
    otherwise.
    """
    return 1.0 if _URL_RE.fullmatch((trace.final_output or "").strip()) else 0.0


@check("is_phone_format")
def is_phone_format(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) looks like a phone number: 7-20
    characters of digits, ``+``, ``-``, ``.``, ``(``, ``)``, or spaces, with
    at least 7 digit characters; 0.0 otherwise.
    """
    text = (trace.final_output or "").strip()
    if not text:
        return 0.0
    if not re.fullmatch(r"[0-9+\-.()\s]{7,20}", text):
        return 0.0
    digit_count = sum(c.isdigit() for c in text)
    return 1.0 if digit_count >= 7 else 0.0


@check("is_uuid_format")
def is_uuid_format(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) matches the UUID hex format
    (8-4-4-4-12, case-insensitive); 0.0 otherwise.
    """
    return 1.0 if _UUID_RE.fullmatch((trace.final_output or "").strip()) else 0.0


@check("is_iso_date_format")
def is_iso_date_format(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) is a strict YYYY-MM-DD date that also
    represents a real calendar date; 0.0 otherwise.
    """
    text = (trace.final_output or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return 0.0
    try:
        date.fromisoformat(text)
        return 1.0
    except ValueError:
        return 0.0


@check("enum_membership")
def enum_membership(trace: Trace, tc: TestCase) -> float:
    """1.0 if *final_output* (stripped) is in ``expected["enum_values"]``
    (case-insensitive comparison); 0.0 otherwise.
    """
    enum_values = [str(v) for v in _need(tc, "enum_values")]
    text = (trace.final_output or "").strip().lower()
    return 1.0 if text in {v.strip().lower() for v in enum_values} else 0.0


# ---------------------------------------------------------------------------
# Helpers (Part 2) — list parsing
# ---------------------------------------------------------------------------


def _parse_list_output(output: str) -> list:
    """Best-effort extraction of a ranked list from *output*.

    Priority:
    1. Parse the whole string as a JSON list.
    2. Find the first ``[...]`` block in the text and parse that.
    3. Split by newlines, strip each line, drop blanks.
    """
    text = (output or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    # Try to find a JSON array anywhere in the text
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    # Fall back: one item per non-blank line
    return [line.strip() for line in text.splitlines() if line.strip()]


def _relevant_ids(tc: TestCase) -> set[str]:
    """Return the set of relevant IDs, always as strings."""
    raw = _need(tc, "relevant_ids")
    return {str(x) for x in raw}


def _ranked_list(trace: Trace) -> list[str]:
    """Parse the agent's ranked list from *final_output*, as strings."""
    return [str(x) for x in _parse_list_output(trace.final_output)]


# ---------------------------------------------------------------------------
# IR / ranking checks
# ---------------------------------------------------------------------------


@check("ir_ndcg_at_k")
def ir_ndcg_at_k(trace: Trace, tc: TestCase) -> float:
    """nDCG@k (Järvelin & Kekäläinen 2002).

    ``expected["relevant_ids"]``: set/list of relevant item IDs (strings).
    ``expected["k"]`` (optional, default 10): cutoff depth.

    DCG@k = Σ_{i=1}^{k} rel(i) / log2(i+1)
    IDCG@k = DCG for ideal ranking (all relevant first)
    nDCG@k = DCG@k / IDCG@k  (0.0 if no relevant items)
    """
    relevant = _relevant_ids(tc)
    k = int((tc.expected or {}).get("k", 10))
    ranked = _ranked_list(trace)

    if not relevant:
        return 1.0  # nothing to retrieve; vacuously perfect

    top_k = ranked[:k]
    dcg = sum(
        1.0 / math.log2(i + 2)        # log2(rank + 1), rank is 0-indexed
        for i, item in enumerate(top_k)
        if item in relevant
    )
    n_ideal = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(n_ideal))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


@check("ir_mrr")
def ir_mrr(trace: Trace, tc: TestCase) -> float:
    """Mean Reciprocal Rank: 1/rank of the first relevant item in *final_output*.

    ``expected["relevant_ids"]``: set/list of relevant item IDs.

    Returns 0.0 if no relevant item appears.
    """
    relevant = _relevant_ids(tc)
    ranked = _ranked_list(trace)
    for i, item in enumerate(ranked):
        if item in relevant:
            return 1.0 / (i + 1)
    return 0.0


@check("ir_precision_at_k")
def ir_precision_at_k(trace: Trace, tc: TestCase) -> float:
    """Precision@k: fraction of the top-k results that are relevant.

    ``expected["relevant_ids"]``: set/list of relevant item IDs.
    ``expected["k"]`` (optional, default 10): cutoff depth.
    """
    relevant = _relevant_ids(tc)
    k = int((tc.expected or {}).get("k", 10))
    top_k = _ranked_list(trace)[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for item in top_k if item in relevant)
    return hits / len(top_k)


@check("ir_recall_at_k")
def ir_recall_at_k(trace: Trace, tc: TestCase) -> float:
    """Recall@k: fraction of all relevant items found in the top-k results.

    ``expected["relevant_ids"]``: set/list of relevant item IDs.
    ``expected["k"]`` (optional, default 10): cutoff depth.
    """
    relevant = _relevant_ids(tc)
    k = int((tc.expected or {}).get("k", 10))
    if not relevant:
        return 1.0
    top_k = _ranked_list(trace)[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / len(relevant)


@check("ir_map")
def ir_map(trace: Trace, tc: TestCase) -> float:
    """Mean Average Precision (MAP) — single-query AP.

    AP = (Σ P@i for each hit position i) / |relevant|.

    ``expected["relevant_ids"]``: set/list of relevant item IDs.
    Returns 1.0 if no relevant items exist (vacuously perfect).
    """
    relevant = _relevant_ids(tc)
    if not relevant:
        return 1.0
    ranked = _ranked_list(trace)
    hits = 0
    precision_sum = 0.0
    for i, item in enumerate(ranked):
        if item in relevant:
            hits += 1
            precision_sum += hits / (i + 1)
    if hits == 0:
        return 0.0
    return precision_sum / len(relevant)


@check("ir_hit_rate")
def ir_hit_rate(trace: Trace, tc: TestCase) -> float:
    """Hit-rate@k: 1.0 if any relevant item appears in the top-k results,
    else 0.0.

    ``expected["relevant_ids"]``: set/list of relevant item IDs.
    ``expected["k"]`` (optional, default 10): cutoff depth.
    """
    relevant = _relevant_ids(tc)
    k = int((tc.expected or {}).get("k", 10))
    top_k = _ranked_list(trace)[:k]
    return 1.0 if any(item in relevant for item in top_k) else 0.0


# ---------------------------------------------------------------------------
# Set / list accuracy
# ---------------------------------------------------------------------------


def _parse_predicted_set(trace: Trace) -> set[str]:
    """Parse *final_output* as a JSON list and return a set of strings."""
    return {str(x) for x in _parse_list_output(trace.final_output)}


@check("set_precision_score")
def set_precision_score(trace: Trace, tc: TestCase) -> float:
    """|predicted ∩ gold| / |predicted|.

    ``expected["expected_set"]``: iterable of gold item IDs.
    Returns 0.0 if the predicted set is empty.
    """
    gold = {str(x) for x in _need(tc, "expected_set")}
    predicted = _parse_predicted_set(trace)
    if not predicted:
        return 0.0
    return len(predicted & gold) / len(predicted)


@check("set_recall_score")
def set_recall_score(trace: Trace, tc: TestCase) -> float:
    """|predicted ∩ gold| / |gold|.

    ``expected["expected_set"]``: iterable of gold item IDs.
    Returns 1.0 if gold is empty (vacuously perfect recall).
    """
    gold = {str(x) for x in _need(tc, "expected_set")}
    if not gold:
        return 1.0
    predicted = _parse_predicted_set(trace)
    return len(predicted & gold) / len(gold)


@check("set_f1_score")
def set_f1_score(trace: Trace, tc: TestCase) -> float:
    """Harmonic mean of set precision and recall.

    ``expected["expected_set"]``: iterable of gold item IDs.
    Returns 1.0 if both gold and predicted are empty.
    Returns 0.0 if exactly one of them is empty.
    """
    gold = {str(x) for x in _need(tc, "expected_set")}
    predicted = _parse_predicted_set(trace)

    if not gold and not predicted:
        return 1.0
    if not predicted or not gold:
        return 0.0

    intersection = len(predicted & gold)
    precision = intersection / len(predicted)
    recall = intersection / len(gold)
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


# ---------------------------------------------------------------------------
# Ordering metrics (Kendall tau, Spearman rho)
# ---------------------------------------------------------------------------


def _shared_order(
    agent_list: list[str], gold_list: list[str]
) -> tuple[list[int], list[int]]:
    """Return (agent_ranks, gold_ranks) for elements present in both lists.

    Ranks are 0-based positions in the respective list.  Only elements that
    appear in BOTH lists are included.
    """
    agent_pos = {item: i for i, item in enumerate(agent_list)}
    gold_pos = {item: i for i, item in enumerate(gold_list)}
    common = sorted(agent_pos.keys() & gold_pos.keys(), key=lambda x: agent_pos[x])
    if not common:
        return [], []
    a_ranks = [agent_pos[x] for x in common]
    g_ranks = [gold_pos[x] for x in common]
    return a_ranks, g_ranks


@check("ordering_kendall_tau")
def ordering_kendall_tau(trace: Trace, tc: TestCase) -> float:
    """Kendall tau between agent's ranked list and ``expected["expected_order"]``.

    Only items present in BOTH lists are compared.  tau ∈ [-1, 1] is mapped to
    [0, 1] as (tau + 1) / 2.  Returns 0.5 (neutral) if fewer than 2 shared
    items exist.

    Implemented from scratch (O(n²) concordant/discordant pair counting).
    """
    gold_list = [str(x) for x in _need(tc, "expected_order")]
    agent_list = [str(x) for x in _parse_list_output(trace.final_output)]

    a_ranks, g_ranks = _shared_order(agent_list, gold_list)
    n = len(a_ranks)
    if n < 2:
        return 0.5  # neutral — not enough data to rank

    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a_diff = a_ranks[i] - a_ranks[j]
            g_diff = g_ranks[i] - g_ranks[j]
            product = a_diff * g_diff
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
            # product == 0 → tie in one list, contributes to neither

    total_pairs = n * (n - 1) // 2
    if total_pairs == 0:
        return 0.5
    tau = (concordant - discordant) / total_pairs
    return (tau + 1.0) / 2.0


@check("ordering_spearman")
def ordering_spearman(trace: Trace, tc: TestCase) -> float:
    """Spearman rank correlation between agent's list and
    ``expected["expected_order"]``.

    Only items present in BOTH lists are considered.  Standard formula:
    rho = 1 - 6 Σd² / (n(n²-1)), where d = difference in dense ranks within
    the shared set.  rho ∈ [-1, 1] is mapped to [0, 1] as (rho + 1) / 2.
    Returns 0.5 (neutral) if fewer than 2 shared items.
    """
    gold_list = [str(x) for x in _need(tc, "expected_order")]
    agent_list = [str(x) for x in _parse_list_output(trace.final_output)]

    a_ranks, g_ranks = _shared_order(agent_list, gold_list)
    n = len(a_ranks)
    if n < 2:
        return 0.5

    def _dense_rank(positions: list[int]) -> list[int]:
        order = sorted(range(n), key=lambda i: positions[i])
        ranks = [0] * n
        for rank, idx in enumerate(order):
            ranks[idx] = rank
        return ranks

    a_dense = _dense_rank(a_ranks)
    g_dense = _dense_rank(g_ranks)

    sum_d_sq = sum((a - g) ** 2 for a, g in zip(a_dense, g_dense))
    denom = n * (n * n - 1)
    if denom == 0:
        return 0.5
    rho = 1.0 - 6.0 * sum_d_sq / denom
    return (rho + 1.0) / 2.0


# ---------------------------------------------------------------------------
# Span / citation overlap
# ---------------------------------------------------------------------------


def _char_positions(span: dict[str, int]) -> set[int]:
    """Return the set of character positions [start, end)."""
    start = int(span.get("start", 0))
    end = int(span.get("end", 0))
    return set(range(start, end))


def _token_bag(text: str) -> dict[str, int]:
    """Case-insensitive token count bag (SQuAD-style)."""
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    bag: dict[str, int] = {}
    for t in tokens:
        bag[t] = bag.get(t, 0) + 1
    return bag


def _token_overlap_f1(pred_texts: list[str], gold_texts: list[str]) -> float:
    """SQuAD-style token-level F1 over lists of text spans."""

    def _count(texts: list[str]) -> dict[str, int]:
        bag: dict[str, int] = {}
        for t in texts:
            for tok, cnt in _token_bag(t).items():
                bag[tok] = bag.get(tok, 0) + cnt
        return bag

    pred_bag = _count(pred_texts)
    gold_bag = _count(gold_texts)
    common_tokens = sum(min(pred_bag.get(t, 0), cnt) for t, cnt in gold_bag.items())

    pred_len = sum(pred_bag.values())
    gold_len = sum(gold_bag.values())

    if pred_len == 0 and gold_len == 0:
        return 1.0
    if common_tokens == 0:
        return 0.0
    precision = common_tokens / pred_len
    recall = common_tokens / gold_len
    return 2.0 * precision * recall / (precision + recall)


@check("span_overlap_f1")
def span_overlap_f1(trace: Trace, tc: TestCase) -> float:
    """Character-level or token-level span F1 depending on gold span format.

    ``expected["gold_spans"]`` is either:

    - A list of ``{"start": int, "end": int}`` dicts → **character-level F1**.
      Predicted spans come from ``expected["predicted_spans"]`` (same format).
      F1 = 2·|P∩G| / (|P| + |G|) where P and G are sets of character positions.

    - A list of strings → **token-level F1** (SQuAD-style).
      Predicted spans come from parsing *final_output* as a JSON list of strings.
      F1 uses the standard token-bag overlap.

    Returns 1.0 when there are no gold spans (vacuously correct).
    """
    gold_spans = _need(tc, "gold_spans")

    if not gold_spans:
        return 1.0  # no gold spans — vacuously correct

    # Detect mode by examining the first element
    first = gold_spans[0]

    if isinstance(first, dict):
        # Character-level mode
        pred_spans = _need(tc, "predicted_spans")
        gold_chars: set[int] = set()
        for s in gold_spans:
            gold_chars |= _char_positions(s)
        pred_chars: set[int] = set()
        for s in pred_spans:
            pred_chars |= _char_positions(s)

        if not gold_chars and not pred_chars:
            return 1.0
        if not gold_chars or not pred_chars:
            return 0.0

        intersection = len(pred_chars & gold_chars)
        precision = intersection / len(pred_chars)
        recall = intersection / len(gold_chars)
        if precision + recall == 0.0:
            return 0.0
        return 2.0 * precision * recall / (precision + recall)

    # Token-level mode (gold_spans is list of strings)
    gold_texts = [str(s) for s in gold_spans]
    pred_raw = _parse_list_output(trace.final_output)
    pred_texts = [str(s) for s in pred_raw]
    return _token_overlap_f1(pred_texts, gold_texts)
