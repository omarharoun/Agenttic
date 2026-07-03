"""Deterministic structured-output, SQL, format, number, and date checks.

Registered into the scoring CHECKS registry via the ``@check`` decorator.
Covers (this module):

- JSON validity & schema conformance (json_schema_valid, json_required_keys,
  json_type_shape_match, json_no_extra_keys)
- Structured extraction accuracy (structured_extraction_exact,
  structured_extraction_normalized)
- SQL syntax validity (sql_is_valid; via sqlglot)
- Number and date extraction (number_match, date_match)
- Format validators (is_email_format, is_url_format, is_phone_format,
  is_uuid_format, is_iso_date_format, enum_membership)

The retrieval, ranking, set/list, and span checks are in the second half of
this same file (see below the "PART 2" marker).

All algorithms are reimplemented from standard public definitions (no copied
licensed code).
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ascore.schema.testcase import TestCase
from ascore.schema.trace import Trace
from ascore.scoring.checks import _need, check

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
