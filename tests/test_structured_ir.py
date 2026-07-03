"""Unit tests for ascore.metrics.structured_ir — all 27 checks.

Each check has at minimum:
- A pass case (score == 1.0)
- A fail case (score == 0.0)
- Representative edge cases (partial credit, empty input, etc.)

Uses the same fixture helpers as tests/test_checks.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

# Import the module so all checks are registered in CHECKS
import ascore.metrics.structured_ir  # noqa: F401
from ascore.scoring.checks import CHECKS

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def span(kind, name):
    return Span(
        span_id=uuid.uuid4().hex[:12], kind=kind, name=name,
        start_time=NOW, end_time=NOW,
    )


def trace(final_output="ok", tools=(), steps=2, cost=0.01):
    spans = [span("llm_call", "model")] + [span("tool_call", t) for t in tools]
    spans.append(span("final_output", "final_output"))
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
        test_case_id="tc-1", spans=spans, visibility="glass_box",
        final_output=final_output, total_cost_usd=cost,
        total_steps=steps, schema_version=SCHEMA_VERSION,
    )


def tc(expected):
    return TestCase(
        test_id="tc-1", suite_id="s-1", task_description="t",
        input={}, expected=expected, rubric_id="r-1",
    )


# ---------------------------------------------------------------------------
# Registry smoke test
# ---------------------------------------------------------------------------

EXPECTED_CHECKS = {
    "json_schema_valid", "json_required_keys", "json_type_shape_match",
    "json_no_extra_keys", "structured_extraction_exact",
    "structured_extraction_normalized", "sql_is_valid", "number_match",
    "date_match", "is_email_format", "is_url_format", "is_phone_format",
    "is_uuid_format", "is_iso_date_format", "enum_membership",
    "ir_ndcg_at_k", "ir_mrr", "ir_precision_at_k", "ir_recall_at_k",
    "ir_map", "ir_hit_rate", "set_precision_score", "set_recall_score",
    "set_f1_score", "ordering_kendall_tau", "ordering_spearman",
    "span_overlap_f1",
}


def test_all_checks_registered():
    """Every expected check name is present in the CHECKS registry."""
    missing = EXPECTED_CHECKS - set(CHECKS.keys())
    assert not missing, f"Missing from CHECKS: {missing}"


# ---------------------------------------------------------------------------
# JSON family
# ---------------------------------------------------------------------------

class TestJsonSchemaValid:
    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}

    def test_pass(self):
        t = trace(final_output='{"name": "Alice"}')
        assert CHECKS["json_schema_valid"](t, tc({"json_schema": self.schema})) == 1.0

    def test_fail_bad_json(self):
        t = trace(final_output="not json")
        assert CHECKS["json_schema_valid"](t, tc({"json_schema": self.schema})) == 0.0

    def test_fail_schema_violation(self):
        t = trace(final_output='{"name": 42}')  # name must be string
        assert CHECKS["json_schema_valid"](t, tc({"json_schema": self.schema})) == 0.0

    def test_pass_nested_schema(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}},
                  "required": ["count"]}
        t = trace(final_output='{"count": 5}')
        assert CHECKS["json_schema_valid"](t, tc({"json_schema": schema})) == 1.0


class TestJsonRequiredKeys:
    def test_pass_all_present(self):
        t = trace(final_output='{"a": 1, "b": 2, "c": 3}')
        assert CHECKS["json_required_keys"](t, tc({"required_keys": ["a", "b"]})) == 1.0

    def test_fail_key_missing(self):
        t = trace(final_output='{"a": 1}')
        assert CHECKS["json_required_keys"](t, tc({"required_keys": ["a", "b"]})) == 0.5

    def test_fail_not_json(self):
        t = trace(final_output="not json")
        assert CHECKS["json_required_keys"](t, tc({"required_keys": ["a"]})) == 0.0

    def test_fail_not_dict(self):
        t = trace(final_output='["a", "b"]')
        assert CHECKS["json_required_keys"](t, tc({"required_keys": ["a"]})) == 0.0

    def test_empty_required_always_passes(self):
        t = trace(final_output="not json")
        assert CHECKS["json_required_keys"](t, tc({"required_keys": []})) == 1.0

    def test_partial_credit(self):
        t = trace(final_output='{"x": 1}')
        score = CHECKS["json_required_keys"](t, tc({"required_keys": ["x", "y", "z"]}))
        assert abs(score - 1 / 3) < 1e-9


class TestJsonTypeShapeMatch:
    def test_pass_exact_types(self):
        out = '{"name": "Alice", "age": 30, "active": true, "scores": [1,2], "meta": {}}'
        shape = {"name": "str", "age": "int", "active": "bool",
                 "scores": "list", "meta": "dict"}
        t = trace(final_output=out)
        assert CHECKS["json_type_shape_match"](t, tc({"json_type_shape": shape})) == 1.0

    def test_pass_aliases(self):
        out = '{"val": 3.14, "items": [], "obj": null}'
        shape = {"val": "number", "items": "array", "obj": "null"}
        t = trace(final_output=out)
        assert CHECKS["json_type_shape_match"](t, tc({"json_type_shape": shape})) == 1.0

    def test_fail_wrong_type(self):
        t = trace(final_output='{"age": "thirty"}')
        assert CHECKS["json_type_shape_match"](t, tc({"json_type_shape": {"age": "int"}})) == 0.0

    def test_fail_bad_json(self):
        t = trace(final_output="not json")
        assert CHECKS["json_type_shape_match"](t, tc({"json_type_shape": {"x": "str"}})) == 0.0

    def test_int_accepted_for_float(self):
        # An integer is acceptable for "float"/"number"
        t = trace(final_output='{"v": 5}')
        assert CHECKS["json_type_shape_match"](t, tc({"json_type_shape": {"v": "float"}})) == 1.0

    def test_partial_credit(self):
        out = '{"a": "yes", "b": 42}'
        # "a" is string (correct), "b" should be string but is int (wrong)
        shape = {"a": "string", "b": "string"}
        score = CHECKS["json_type_shape_match"](trace(final_output=out), tc({"json_type_shape": shape}))
        assert abs(score - 0.5) < 1e-9


class TestJsonNoExtraKeys:
    def test_pass_exact_keys(self):
        t = trace(final_output='{"a": 1, "b": 2}')
        assert CHECKS["json_no_extra_keys"](t, tc({"allowed_keys": ["a", "b"]})) == 1.0

    def test_pass_subset_of_allowed(self):
        t = trace(final_output='{"a": 1}')
        assert CHECKS["json_no_extra_keys"](t, tc({"allowed_keys": ["a", "b", "c"]})) == 1.0

    def test_fail_extra_key(self):
        t = trace(final_output='{"a": 1, "z": 99}')
        assert CHECKS["json_no_extra_keys"](t, tc({"allowed_keys": ["a"]})) == 0.0

    def test_fail_not_dict(self):
        t = trace(final_output='[1, 2]')
        assert CHECKS["json_no_extra_keys"](t, tc({"allowed_keys": ["a"]})) == 0.0

    def test_fail_bad_json(self):
        t = trace(final_output="oops")
        assert CHECKS["json_no_extra_keys"](t, tc({"allowed_keys": ["a"]})) == 0.0


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------

class TestStructuredExtractionExact:
    def test_pass(self):
        t = trace(final_output='{"name": "Alice", "city": "Paris"}')
        assert CHECKS["structured_extraction_exact"](
            t, tc({"extracted_fields": {"name": "Alice", "city": "Paris"}})) == 1.0

    def test_pass_strips_whitespace(self):
        t = trace(final_output='{"name": "  Alice  "}')
        assert CHECKS["structured_extraction_exact"](
            t, tc({"extracted_fields": {"name": "Alice"}})) == 1.0

    def test_fail_wrong_value(self):
        t = trace(final_output='{"name": "Bob"}')
        assert CHECKS["structured_extraction_exact"](
            t, tc({"extracted_fields": {"name": "Alice"}})) == 0.0

    def test_fail_not_dict(self):
        t = trace(final_output='"hello"')
        assert CHECKS["structured_extraction_exact"](
            t, tc({"extracted_fields": {"name": "hello"}})) == 0.0

    def test_partial_credit(self):
        t = trace(final_output='{"a": "1", "b": "wrong"}')
        score = CHECKS["structured_extraction_exact"](
            t, tc({"extracted_fields": {"a": "1", "b": "2"}}))
        assert abs(score - 0.5) < 1e-9


class TestStructuredExtractionNormalized:
    def test_pass_case_insensitive(self):
        t = trace(final_output='{"name": "ALICE"}')
        assert CHECKS["structured_extraction_normalized"](
            t, tc({"extracted_fields": {"name": "alice"}})) == 1.0

    def test_pass_whitespace_collapsed(self):
        t = trace(final_output='{"city": "New   York"}')
        assert CHECKS["structured_extraction_normalized"](
            t, tc({"extracted_fields": {"city": "new york"}})) == 1.0

    def test_fail_different_value(self):
        t = trace(final_output='{"name": "bob"}')
        assert CHECKS["structured_extraction_normalized"](
            t, tc({"extracted_fields": {"name": "alice"}})) == 0.0

    def test_fail_not_dict(self):
        t = trace(final_output="not json")
        assert CHECKS["structured_extraction_normalized"](
            t, tc({"extracted_fields": {"x": "y"}})) == 0.0


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

class TestSqlIsValid:
    def test_pass_simple_select(self):
        t = trace(final_output="SELECT id, name FROM users WHERE active = 1")
        assert CHECKS["sql_is_valid"](t, tc({})) == 1.0

    def test_pass_from_fence(self):
        sql = "```sql\nSELECT * FROM orders\n```"
        t = trace(final_output=sql)
        assert CHECKS["sql_is_valid"](t, tc({})) == 1.0

    def test_fail_invalid_sql(self):
        t = trace(final_output="SELECT FROM WHERE;")
        assert CHECKS["sql_is_valid"](t, tc({})) == 0.0

    def test_fail_empty(self):
        t = trace(final_output="")
        assert CHECKS["sql_is_valid"](t, tc({})) == 0.0

    def test_pass_with_dialect(self):
        t = trace(final_output="SELECT id FROM users LIMIT 10")
        assert CHECKS["sql_is_valid"](t, tc({"sql_dialect": "mysql"})) == 1.0


# ---------------------------------------------------------------------------
# Number / date
# ---------------------------------------------------------------------------

class TestNumberMatch:
    def test_pass_exact(self):
        t = trace(final_output="The answer is 42")
        assert CHECKS["number_match"](t, tc({"expected_number": 42})) == 1.0

    def test_pass_within_tolerance(self):
        t = trace(final_output="3.14159")
        assert CHECKS["number_match"](
            t, tc({"expected_number": 3.14159, "tolerance": 1e-4})) == 1.0

    def test_fail_out_of_tolerance(self):
        t = trace(final_output="5.0")
        assert CHECKS["number_match"](t, tc({"expected_number": 10.0})) == 0.0

    def test_fail_no_number(self):
        t = trace(final_output="no numbers here")
        assert CHECKS["number_match"](t, tc({"expected_number": 42})) == 0.0

    def test_pass_relative_tolerance(self):
        t = trace(final_output="100.5")
        assert CHECKS["number_match"](
            t, tc({"expected_number": 100.0, "tolerance": 0.01,
                   "relative_tolerance": True})) == 1.0

    def test_fail_relative_tolerance(self):
        t = trace(final_output="200")
        assert CHECKS["number_match"](
            t, tc({"expected_number": 100.0, "tolerance": 0.01,
                   "relative_tolerance": True})) == 0.0

    def test_negative_number(self):
        t = trace(final_output="result: -3.5")
        assert CHECKS["number_match"](
            t, tc({"expected_number": -3.5, "tolerance": 1e-9})) == 1.0


class TestDateMatch:
    def test_pass(self):
        t = trace(final_output="The date is 2024-03-15 today")
        assert CHECKS["date_match"](t, tc({"expected_date": "2024-03-15"})) == 1.0

    def test_fail_wrong_date(self):
        t = trace(final_output="2024-03-16")
        assert CHECKS["date_match"](t, tc({"expected_date": "2024-03-15"})) == 0.0

    def test_fail_no_date(self):
        t = trace(final_output="no date in here")
        assert CHECKS["date_match"](t, tc({"expected_date": "2024-03-15"})) == 0.0

    def test_pass_embedded(self):
        t = trace(final_output="Report as of 2023-12-31.")
        assert CHECKS["date_match"](t, tc({"expected_date": "2023-12-31"})) == 1.0


# ---------------------------------------------------------------------------
# Format validators
# ---------------------------------------------------------------------------

class TestIsEmailFormat:
    def test_pass(self):
        assert CHECKS["is_email_format"](trace(final_output="user@example.com"), tc({})) == 1.0

    def test_pass_plus(self):
        assert CHECKS["is_email_format"](trace(final_output="user+tag@sub.example.org"), tc({})) == 1.0

    def test_fail_no_at(self):
        assert CHECKS["is_email_format"](trace(final_output="notanemail"), tc({})) == 0.0

    def test_fail_extra_text(self):
        assert CHECKS["is_email_format"](trace(final_output="email: user@example.com"), tc({})) == 0.0

    def test_fail_empty(self):
        assert CHECKS["is_email_format"](trace(final_output=""), tc({})) == 0.0


class TestIsUrlFormat:
    def test_pass_https(self):
        assert CHECKS["is_url_format"](trace(final_output="https://www.example.com/path?q=1"), tc({})) == 1.0

    def test_pass_ftp(self):
        assert CHECKS["is_url_format"](trace(final_output="ftp://files.example.com/data.zip"), tc({})) == 1.0

    def test_fail_no_scheme(self):
        assert CHECKS["is_url_format"](trace(final_output="www.example.com"), tc({})) == 0.0

    def test_fail_plain_text(self):
        assert CHECKS["is_url_format"](trace(final_output="not a url"), tc({})) == 0.0


class TestIsPhoneFormat:
    def test_pass_us(self):
        assert CHECKS["is_phone_format"](trace(final_output="+1 (555) 123-4567"), tc({})) == 1.0

    def test_pass_international(self):
        assert CHECKS["is_phone_format"](trace(final_output="+44 20 7946 0958"), tc({})) == 1.0

    def test_fail_too_short(self):
        assert CHECKS["is_phone_format"](trace(final_output="123"), tc({})) == 0.0

    def test_fail_letters(self):
        assert CHECKS["is_phone_format"](trace(final_output="call me maybe"), tc({})) == 0.0

    def test_fail_not_enough_digits(self):
        # 7 chars but only 3 digits
        assert CHECKS["is_phone_format"](trace(final_output="++-((.."), tc({})) == 0.0


class TestIsUuidFormat:
    def test_pass(self):
        assert CHECKS["is_uuid_format"](
            trace(final_output="550e8400-e29b-41d4-a716-446655440000"), tc({})) == 1.0

    def test_pass_uppercase(self):
        assert CHECKS["is_uuid_format"](
            trace(final_output="550E8400-E29B-41D4-A716-446655440000"), tc({})) == 1.0

    def test_fail_short(self):
        assert CHECKS["is_uuid_format"](trace(final_output="1234-5678"), tc({})) == 0.0

    def test_fail_no_dashes(self):
        assert CHECKS["is_uuid_format"](trace(final_output="550e8400e29b41d4a716446655440000"), tc({})) == 0.0


class TestIsIsoDateFormat:
    def test_pass(self):
        assert CHECKS["is_iso_date_format"](trace(final_output="2024-02-29"), tc({})) == 1.0  # leap year

    def test_fail_not_real_date(self):
        assert CHECKS["is_iso_date_format"](trace(final_output="2023-02-29"), tc({})) == 0.0  # not a leap year

    def test_fail_wrong_format(self):
        assert CHECKS["is_iso_date_format"](trace(final_output="29/02/2024"), tc({})) == 0.0

    def test_fail_with_time(self):
        assert CHECKS["is_iso_date_format"](trace(final_output="2024-02-29T12:00"), tc({})) == 0.0

    def test_pass_valid(self):
        assert CHECKS["is_iso_date_format"](trace(final_output="2024-12-31"), tc({})) == 1.0


class TestEnumMembership:
    def test_pass_exact(self):
        t = trace(final_output="option_a")
        assert CHECKS["enum_membership"](t, tc({"enum_values": ["option_a", "option_b"]})) == 1.0

    def test_pass_case_insensitive(self):
        t = trace(final_output="OPTION_A")
        assert CHECKS["enum_membership"](t, tc({"enum_values": ["option_a", "option_b"]})) == 1.0

    def test_fail_not_in_enum(self):
        t = trace(final_output="option_c")
        assert CHECKS["enum_membership"](t, tc({"enum_values": ["option_a", "option_b"]})) == 0.0

    def test_fail_empty(self):
        t = trace(final_output="")
        assert CHECKS["enum_membership"](t, tc({"enum_values": ["a", "b"]})) == 0.0

    def test_pass_strips_whitespace(self):
        t = trace(final_output="  yes  ")
        assert CHECKS["enum_membership"](t, tc({"enum_values": ["yes", "no"]})) == 1.0


# ---------------------------------------------------------------------------
# IR / ranking
# ---------------------------------------------------------------------------

class TestIrNdcgAtK:
    def test_pass_perfect_ranking(self):
        # All relevant items at top
        out = json.dumps(["doc1", "doc2", "doc3", "doc4"])
        t = trace(final_output=out)
        score = CHECKS["ir_ndcg_at_k"](t, tc({"relevant_ids": ["doc1", "doc2"], "k": 4}))
        assert abs(score - 1.0) < 1e-9

    def test_fail_no_relevant_in_top_k(self):
        out = json.dumps(["docX", "docY"])
        t = trace(final_output=out)
        score = CHECKS["ir_ndcg_at_k"](t, tc({"relevant_ids": ["doc1"], "k": 2}))
        assert score == 0.0

    def test_partial_relevant_second(self):
        # Relevant at position 2 (not ideal)
        out = json.dumps(["docX", "doc1"])
        t = trace(final_output=out)
        score = CHECKS["ir_ndcg_at_k"](t, tc({"relevant_ids": ["doc1"], "k": 2}))
        # DCG = 1/log2(3), IDCG = 1/log2(2) = 1.0
        import math
        expected = (1.0 / math.log2(3)) / 1.0
        assert abs(score - expected) < 1e-9

    def test_empty_relevant_vacuously_perfect(self):
        t = trace(final_output='["a", "b"]')
        score = CHECKS["ir_ndcg_at_k"](t, tc({"relevant_ids": []}))
        assert score == 1.0

    def test_default_k_10(self):
        # Works without explicit k
        out = json.dumps(["doc1"])
        t = trace(final_output=out)
        score = CHECKS["ir_ndcg_at_k"](t, tc({"relevant_ids": ["doc1"]}))
        assert score == 1.0


class TestIrMrr:
    def test_pass_first(self):
        out = json.dumps(["doc1", "doc2"])
        t = trace(final_output=out)
        assert CHECKS["ir_mrr"](t, tc({"relevant_ids": ["doc1"]})) == 1.0

    def test_pass_second(self):
        out = json.dumps(["docX", "doc1"])
        t = trace(final_output=out)
        assert abs(CHECKS["ir_mrr"](t, tc({"relevant_ids": ["doc1"]})) - 0.5) < 1e-9

    def test_fail_not_found(self):
        out = json.dumps(["docX", "docY"])
        t = trace(final_output=out)
        assert CHECKS["ir_mrr"](t, tc({"relevant_ids": ["doc1"]})) == 0.0

    def test_empty_list(self):
        assert CHECKS["ir_mrr"](trace(final_output="[]"), tc({"relevant_ids": ["doc1"]})) == 0.0


class TestIrPrecisionAtK:
    def test_pass_all_relevant(self):
        out = json.dumps(["a", "b"])
        t = trace(final_output=out)
        assert CHECKS["ir_precision_at_k"](t, tc({"relevant_ids": ["a", "b"], "k": 2})) == 1.0

    def test_fail_none_relevant(self):
        out = json.dumps(["x", "y"])
        t = trace(final_output=out)
        assert CHECKS["ir_precision_at_k"](t, tc({"relevant_ids": ["a", "b"], "k": 2})) == 0.0

    def test_partial(self):
        out = json.dumps(["a", "x"])
        t = trace(final_output=out)
        score = CHECKS["ir_precision_at_k"](t, tc({"relevant_ids": ["a"], "k": 2}))
        assert abs(score - 0.5) < 1e-9

    def test_empty_output(self):
        assert CHECKS["ir_precision_at_k"](trace(final_output="[]"), tc({"relevant_ids": ["a"]})) == 0.0


class TestIrRecallAtK:
    def test_pass_all_found(self):
        out = json.dumps(["a", "b", "c"])
        t = trace(final_output=out)
        assert CHECKS["ir_recall_at_k"](t, tc({"relevant_ids": ["a", "b"], "k": 2})) == 1.0

    def test_fail_none_found(self):
        out = json.dumps(["x", "y"])
        t = trace(final_output=out)
        assert CHECKS["ir_recall_at_k"](t, tc({"relevant_ids": ["a", "b"], "k": 2})) == 0.0

    def test_partial(self):
        out = json.dumps(["a", "x"])
        t = trace(final_output=out)
        score = CHECKS["ir_recall_at_k"](t, tc({"relevant_ids": ["a", "b"], "k": 2}))
        assert abs(score - 0.5) < 1e-9

    def test_empty_relevant_vacuously_perfect(self):
        t = trace(final_output='["x"]')
        assert CHECKS["ir_recall_at_k"](t, tc({"relevant_ids": [], "k": 5})) == 1.0


class TestIrMap:
    def test_pass_all_found_in_order(self):
        out = json.dumps(["a", "b", "c"])
        t = trace(final_output=out)
        # P@1=1, P@2=1, P@3=1  → AP = 3/3 = 1.0 (3 relevant)
        score = CHECKS["ir_map"](t, tc({"relevant_ids": ["a", "b", "c"]}))
        assert abs(score - 1.0) < 1e-9

    def test_fail_none_found(self):
        out = json.dumps(["x", "y"])
        t = trace(final_output=out)
        assert CHECKS["ir_map"](t, tc({"relevant_ids": ["a", "b"]})) == 0.0

    def test_partial_ap(self):
        # rank1=x (miss), rank2=a (hit, P@2=0.5), rank3=b (hit, P@3=0.667)
        out = json.dumps(["x", "a", "b"])
        t = trace(final_output=out)
        score = CHECKS["ir_map"](t, tc({"relevant_ids": ["a", "b"]}))
        # AP = (0.5 + 2/3) / 2
        expected = (0.5 + 2 / 3) / 2
        assert abs(score - expected) < 1e-6

    def test_empty_relevant_vacuously_perfect(self):
        assert CHECKS["ir_map"](trace(final_output='["x"]'), tc({"relevant_ids": []})) == 1.0


class TestIrHitRate:
    def test_pass_first_hit(self):
        out = json.dumps(["doc1", "doc2"])
        t = trace(final_output=out)
        assert CHECKS["ir_hit_rate"](t, tc({"relevant_ids": ["doc1"], "k": 5})) == 1.0

    def test_fail_no_hit(self):
        out = json.dumps(["x", "y"])
        t = trace(final_output=out)
        assert CHECKS["ir_hit_rate"](t, tc({"relevant_ids": ["doc1"], "k": 2})) == 0.0

    def test_hit_at_edge_of_k(self):
        out = json.dumps(["x", "y", "doc1"])
        t = trace(final_output=out)
        assert CHECKS["ir_hit_rate"](t, tc({"relevant_ids": ["doc1"], "k": 3})) == 1.0

    def test_miss_beyond_k(self):
        out = json.dumps(["x", "y", "doc1"])
        t = trace(final_output=out)
        assert CHECKS["ir_hit_rate"](t, tc({"relevant_ids": ["doc1"], "k": 2})) == 0.0


# ---------------------------------------------------------------------------
# IR: list parsing edge cases
# ---------------------------------------------------------------------------

class TestIrListParsing:
    def test_array_in_text(self):
        # Agent wraps the list in prose
        out = 'The top results are: ["doc1", "doc2", "doc3"]'
        t = trace(final_output=out)
        score = CHECKS["ir_mrr"](t, tc({"relevant_ids": ["doc1"]}))
        assert score == 1.0

    def test_newline_fallback(self):
        # One item per line fallback
        out = "doc2\ndoc1\ndoc3"
        t = trace(final_output=out)
        score = CHECKS["ir_mrr"](t, tc({"relevant_ids": ["doc1"]}))
        assert abs(score - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# Set / list accuracy
# ---------------------------------------------------------------------------

class TestSetPrecisionScore:
    def test_pass_perfect(self):
        t = trace(final_output='["a", "b"]')
        assert CHECKS["set_precision_score"](t, tc({"expected_set": ["a", "b"]})) == 1.0

    def test_partial(self):
        t = trace(final_output='["a", "x"]')
        score = CHECKS["set_precision_score"](t, tc({"expected_set": ["a"]}))
        assert abs(score - 0.5) < 1e-9

    def test_fail_none_match(self):
        t = trace(final_output='["x", "y"]')
        assert CHECKS["set_precision_score"](t, tc({"expected_set": ["a", "b"]})) == 0.0

    def test_fail_empty_predicted(self):
        t = trace(final_output="[]")
        assert CHECKS["set_precision_score"](t, tc({"expected_set": ["a"]})) == 0.0


class TestSetRecallScore:
    def test_pass_perfect(self):
        t = trace(final_output='["a", "b", "extra"]')
        assert CHECKS["set_recall_score"](t, tc({"expected_set": ["a", "b"]})) == 1.0

    def test_partial(self):
        t = trace(final_output='["a"]')
        score = CHECKS["set_recall_score"](t, tc({"expected_set": ["a", "b"]}))
        assert abs(score - 0.5) < 1e-9

    def test_fail_none_match(self):
        t = trace(final_output='["x"]')
        assert CHECKS["set_recall_score"](t, tc({"expected_set": ["a", "b"]})) == 0.0

    def test_empty_gold_vacuously_perfect(self):
        assert CHECKS["set_recall_score"](trace(final_output='["x"]'), tc({"expected_set": []})) == 1.0


class TestSetF1Score:
    def test_pass_perfect(self):
        t = trace(final_output='["a", "b"]')
        assert CHECKS["set_f1_score"](t, tc({"expected_set": ["a", "b"]})) == 1.0

    def test_fail_no_overlap(self):
        t = trace(final_output='["x", "y"]')
        assert CHECKS["set_f1_score"](t, tc({"expected_set": ["a", "b"]})) == 0.0

    def test_partial_f1(self):
        # predicted={"a","x"}, gold={"a","b"}
        # precision=0.5, recall=0.5, F1=0.5
        t = trace(final_output='["a", "x"]')
        score = CHECKS["set_f1_score"](t, tc({"expected_set": ["a", "b"]}))
        assert abs(score - 0.5) < 1e-9

    def test_both_empty(self):
        assert CHECKS["set_f1_score"](trace(final_output="[]"), tc({"expected_set": []})) == 1.0

    def test_empty_predicted(self):
        assert CHECKS["set_f1_score"](trace(final_output="[]"), tc({"expected_set": ["a"]})) == 0.0


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestOrderingKendallTau:
    def test_pass_perfect_order(self):
        out = json.dumps(["a", "b", "c"])
        t = trace(final_output=out)
        score = CHECKS["ordering_kendall_tau"](t, tc({"expected_order": ["a", "b", "c"]}))
        assert abs(score - 1.0) < 1e-9

    def test_fail_reversed_order(self):
        out = json.dumps(["c", "b", "a"])
        t = trace(final_output=out)
        score = CHECKS["ordering_kendall_tau"](t, tc({"expected_order": ["a", "b", "c"]}))
        assert abs(score - 0.0) < 1e-9

    def test_neutral_one_item(self):
        out = json.dumps(["a"])
        t = trace(final_output=out)
        score = CHECKS["ordering_kendall_tau"](t, tc({"expected_order": ["a"]}))
        assert abs(score - 0.5) < 1e-9

    def test_partial_tau(self):
        # ["a","c","b"] vs gold ["a","b","c"]: a-b concordant, a-c concordant, c-b discordant
        # tau = (2-1)/3 = 1/3 → mapped to (1/3+1)/2 = 2/3
        out = json.dumps(["a", "c", "b"])
        t = trace(final_output=out)
        score = CHECKS["ordering_kendall_tau"](t, tc({"expected_order": ["a", "b", "c"]}))
        expected = (1 / 3 + 1.0) / 2.0
        assert abs(score - expected) < 1e-9


class TestOrderingSpearman:
    def test_pass_perfect_order(self):
        out = json.dumps(["a", "b", "c"])
        t = trace(final_output=out)
        score = CHECKS["ordering_spearman"](t, tc({"expected_order": ["a", "b", "c"]}))
        assert abs(score - 1.0) < 1e-9

    def test_fail_reversed_order(self):
        out = json.dumps(["c", "b", "a"])
        t = trace(final_output=out)
        score = CHECKS["ordering_spearman"](t, tc({"expected_order": ["a", "b", "c"]}))
        assert abs(score - 0.0) < 1e-9

    def test_neutral_one_item(self):
        out = json.dumps(["a"])
        t = trace(final_output=out)
        score = CHECKS["ordering_spearman"](t, tc({"expected_order": ["a"]}))
        assert abs(score - 0.5) < 1e-9

    def test_partial_spearman(self):
        # ["a","c","b"] agent; gold ["a","b","c"]
        # shared=all 3: agent_pos a=0,c=1,b=2; gold_pos a=0,b=1,c=2
        # dense agent order (a,c,b) = (0,1,2); dense gold for (a,c,b) = a→0, c→2, b→1
        # d² = (0-0)² + (1-2)² + (2-1)² = 0+1+1 = 2
        # rho = 1 - 6*2/(3*8) = 1 - 12/24 = 0.5 → (0.5+1)/2 = 0.75
        out = json.dumps(["a", "c", "b"])
        t = trace(final_output=out)
        score = CHECKS["ordering_spearman"](t, tc({"expected_order": ["a", "b", "c"]}))
        assert abs(score - 0.75) < 1e-9


# ---------------------------------------------------------------------------
# Span overlap F1
# ---------------------------------------------------------------------------

class TestSpanOverlapF1:
    # Character-level mode (dict spans)
    def test_char_pass_exact(self):
        gold = [{"start": 0, "end": 5}]
        pred = [{"start": 0, "end": 5}]
        t = trace(final_output="hello world")
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": gold, "predicted_spans": pred}))
        assert score == 1.0

    def test_char_fail_no_overlap(self):
        gold = [{"start": 0, "end": 5}]
        pred = [{"start": 10, "end": 15}]
        t = trace(final_output="hello world!!")
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": gold, "predicted_spans": pred}))
        assert score == 0.0

    def test_char_partial_overlap(self):
        gold = [{"start": 0, "end": 4}]   # positions 0,1,2,3
        pred = [{"start": 2, "end": 6}]   # positions 2,3,4,5
        t = trace(final_output="hello world")
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": gold, "predicted_spans": pred}))
        # overlap=2, pred=4, gold=4; P=0.5, R=0.5, F1=0.5
        assert abs(score - 0.5) < 1e-9

    def test_char_empty_gold(self):
        t = trace(final_output="text")
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": [], "predicted_spans": []}))
        assert score == 1.0

    # Token-level mode (string spans)
    def test_token_pass_exact(self):
        t = trace(final_output='["the quick brown fox"]')
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": ["the quick brown fox"]}))
        assert score == 1.0

    def test_token_fail_no_overlap(self):
        t = trace(final_output='["hello world"]')
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": ["foo bar baz"]}))
        assert score == 0.0

    def test_token_partial(self):
        # predicted has "quick" only; gold has "quick brown"
        # pred_bag: {quick:1}, gold_bag: {quick:1, brown:1}
        # common=1, P=1/1=1, R=1/2=0.5, F1=2/3
        t = trace(final_output='["quick"]')
        score = CHECKS["span_overlap_f1"](
            t, tc({"gold_spans": ["quick brown"]}))
        assert abs(score - 2 / 3) < 1e-9

    def test_empty_gold_spans_vacuously_correct(self):
        t = trace(final_output='["something"]')
        score = CHECKS["span_overlap_f1"](t, tc({"gold_spans": []}))
        assert score == 1.0
