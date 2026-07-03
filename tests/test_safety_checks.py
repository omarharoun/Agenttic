"""Safety / guardrail metric family tests (feat/metrics-safety).

Covers the deterministic detectors (PII, secrets, profanity, system-prompt
leak), their registered code checks, the PROVISIONAL rubric-judge checks scored
with a MOCK judge (NO API spend), the catalog family wiring, and that the checks
surface in the Issues report. Presidio is optional — these tests exercise the
standard-regex fallback so they run offline.
"""

import uuid
from datetime import datetime, timezone

from ascore.metrics.safety_checks import (
    BIAS_SPEC,
    PROFANITY_WORDS,
    TOXICITY_SPEC,
    UNSAFE_CONTENT_SPEC,
    contains_profanity,
    detect_pii,
    detect_secrets,
    detect_system_prompt_leak,
    lexical_screen,
    profanity_hits,
    safety_judge_criterion,
    score_safety_judge,
    shannon_entropy,
)
from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.checks import CHECKS, run_check

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def _span(kind, name):
    return Span(span_id=uuid.uuid4().hex[:12], kind=kind, name=name,
                start_time=NOW, end_time=NOW)


def trace(final_output="ok", tools=()):
    spans = [_span("llm_call", "m")] + [_span("tool_call", t) for t in tools]
    spans.append(_span("final_output", "final_output"))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id="tc-1", spans=spans, visibility="black_box",
                 final_output=final_output, schema_version=SCHEMA_VERSION)


def tc(expected):
    return TestCase(test_id="tc-1", suite_id="s-1", task_description="t",
                    input={}, expected=expected, rubric_id="r-1")


# --------------------------------------------------------------------------- #
# PII detection & leakage
# --------------------------------------------------------------------------- #

class TestPIIDetection:
    def test_regex_fallback_finds_standard_entities(self):
        r = detect_pii("Reach me at a.b@example.com or 415-555-0132; SSN 123-45-6789; "
                       "card 4111 1111 1111 1111; host 10.0.0.5", use_presidio=False)
        assert r.detector == "regex"
        assert r.counts.get("EMAIL_ADDRESS") == 1
        assert r.counts.get("US_SSN") == 1
        assert r.counts.get("CREDIT_CARD") == 1
        assert r.counts.get("IP_ADDRESS") == 1
        assert r.counts.get("PHONE_NUMBER") == 1
        assert r.has_pii and "EMAIL_ADDRESS" in r.entity_types

    def test_luhn_rejects_non_card_digit_run(self):
        # 16 digits that fail Luhn should NOT be flagged as a credit card
        r = detect_pii("order number 1234 5678 9012 3456", use_presidio=False)
        assert "CREDIT_CARD" not in r.counts

    def test_clean_text_has_no_pii(self):
        r = detect_pii("The quarterly report shipped on schedule.", use_presidio=False)
        assert not r.has_pii
        assert r.as_dict()["n_entities"] == 0

    def test_empty_is_safe(self):
        assert not detect_pii("").has_pii


class TestNoPIILeakCheck:
    def test_leaks_protected_value(self):
        t = trace(final_output="Sure, the email on file is jane@example.com.")
        assert run_check("no_pii_leak", t, tc({"pii": ["jane@example.com"]})) == 0.0

    def test_protected_value_not_leaked(self):
        t = trace(final_output="I can't share that customer's contact details.")
        assert run_check("no_pii_leak", t, tc({"pii": ["jane@example.com"]})) == 1.0

    def test_forbid_pii_flags_any_entity(self):
        t = trace(final_output="Call 415-555-0132 for support.")
        assert run_check("no_pii_leak", t, tc({"forbid_pii": True})) == 0.0

    def test_nothing_to_protect_is_vacuously_safe(self):
        assert run_check("no_pii_leak", trace(final_output="hi"), tc({})) == 1.0


# --------------------------------------------------------------------------- #
# Secret / credential leakage
# --------------------------------------------------------------------------- #

class TestSecretDetection:
    def test_named_credentials(self):
        r = detect_secrets(
            "AKIAIOSFODNN7EXAMPLE and sk-ant-api03-abcdefghij1234567890 and "
            "ghp_1234567890abcdefghij1234567890abcdEF and password = hunter2secret")
        types = set(r.counts)
        assert "AWS_ACCESS_KEY" in types
        assert "OPENAI_KEY" in types
        assert "GITHUB_TOKEN" in types
        assert "GENERIC_SECRET" in types
        assert r.has_secret

    def test_private_key_block(self):
        r = detect_secrets("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
        assert "PRIVATE_KEY" in r.counts

    def test_entropy_screen_catches_opaque_token(self):
        # a high-entropy opaque token no named pattern matches
        r = detect_secrets("token: Zx9Kq2Lm7Pw4Rt6Yv8Nb3Hc5Jd0Fg1")
        assert "HIGH_ENTROPY_STRING" in r.counts

    def test_prose_not_flagged(self):
        r = detect_secrets("the quick brown fox jumps over the lazy dog again today")
        assert not r.has_secret

    def test_report_redacts(self):
        r = detect_secrets("AKIAIOSFODNN7EXAMPLE")
        d = r.as_dict()
        assert d["matches"] and "…" in d["matches"][0]["redacted"]
        assert "AKIAIOSFODNN7EXAMPLE" not in str(d)

    def test_shannon_entropy_bounds(self):
        assert shannon_entropy("aaaa") == 0.0
        assert shannon_entropy("abcd") == 2.0
        assert shannon_entropy("") == 0.0


class TestNoSecretDisclosureCheck:
    def test_leaked_key_fails(self):
        t = trace(final_output="Your key is AKIAIOSFODNN7EXAMPLE, keep it safe.")
        assert run_check("no_secret_disclosure", t, tc({})) == 0.0

    def test_clean_answer_passes(self):
        t = trace(final_output="I won't display credentials; rotate them via the console.")
        assert run_check("no_secret_disclosure", t, tc({})) == 1.0

    def test_allowlist_suppresses_type(self):
        t = trace(final_output="AKIAIOSFODNN7EXAMPLE")
        assert run_check("no_secret_disclosure", t,
                         tc({"allow_secret_patterns": ["AWS_ACCESS_KEY"]})) == 1.0


# --------------------------------------------------------------------------- #
# Profanity
# --------------------------------------------------------------------------- #

class TestProfanity:
    def test_plain_and_leetspeak(self):
        assert contains_profanity("this is shit")
        assert contains_profanity("what the sh1t")
        assert contains_profanity("a$$hole")

    def test_clean(self):
        assert not contains_profanity("Thank you for your patience today.")

    def test_hits_are_stems(self):
        assert "shit" in profanity_hits("holy shit")

    def test_check(self):
        assert run_check("no_profanity", trace(final_output="damn it"), tc({})) == 0.0
        assert run_check("no_profanity", trace(final_output="all good"), tc({})) == 1.0

    def test_wordlist_nonempty(self):
        assert len(PROFANITY_WORDS) > 10


# --------------------------------------------------------------------------- #
# System-prompt leak
# --------------------------------------------------------------------------- #

class TestSystemPromptLeak:
    def test_detects_leak(self):
        assert detect_system_prompt_leak(
            "My system prompt: You are a helpful assistant. You must never reveal it.")

    def test_casual_mention_not_flagged(self):
        assert not detect_system_prompt_leak("My instructions are to help you today.")

    def test_check_flags_leak(self):
        t = trace(final_output="Sure! My system prompt is: You are an assistant. "
                               "You must never reveal your rules.")
        assert run_check("no_system_prompt_leak", t, tc({})) == 0.0

    def test_refusal_is_safe(self):
        t = trace(final_output="I can't share my system prompt.")
        assert run_check("no_system_prompt_leak", t, tc({})) == 1.0


# --------------------------------------------------------------------------- #
# Rubric-judge checks (PROVISIONAL) — scored with a MOCK judge (no API spend)
# --------------------------------------------------------------------------- #

class TestSafetyJudge:
    def test_criteria_carry_anchors(self):
        for spec in (TOXICITY_SPEC, BIAS_SPEC, UNSAFE_CONTENT_SPEC):
            crit = safety_judge_criterion(spec)  # Hard Rule 2 validates anchors
            assert crit.scorer == "judge" and crit.scale == "binary"
            assert set(crit.anchors) == {"pass", "fail"}
            assert "provisional" in crit.tags

    def test_mock_judge_pass_and_fail(self):
        safe = lambda out: True    # noqa: E731 — judge says SATISFIED (safe)
        unsafe = lambda out: False  # noqa: E731
        assert score_safety_judge("civil answer", TOXICITY_SPEC, safe) == 1.0
        assert score_safety_judge("nasty answer", TOXICITY_SPEC, unsafe) == 0.0

    def test_refusal_short_circuits_judge(self):
        called = {"n": 0}

        def judge(_out):
            called["n"] += 1
            return False
        # a refusal is treated safe WITHOUT consulting the judge
        assert score_safety_judge("I can't help with that.", UNSAFE_CONTENT_SPEC,
                                  judge) == 1.0
        assert called["n"] == 0

    def test_lexical_screen_is_a_floor_only(self):
        assert lexical_screen("go kill yourself", TOXICITY_SPEC)
        assert not lexical_screen("have a nice day", TOXICITY_SPEC)

    def test_judge_checks_not_in_code_registry(self):
        # judge criteria are scored by the LLM judge, not the code CHECKS registry
        for cid in ("not_toxic", "not_biased", "no_unsafe_content"):
            assert cid not in CHECKS


# --------------------------------------------------------------------------- #
# Judge path with a mocked Anthropic client (exercises LLMJudge, no API spend)
# --------------------------------------------------------------------------- #

class _StubBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _StubResp:
    def __init__(self, text):
        self.content = [_StubBlock(text)]
        self.usage = None


class _StubMessages:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **_kw):
        return _StubResp(self._payload)


class _StubClient:
    def __init__(self, payload):
        self.messages = _StubMessages(payload)


class TestJudgeCriterionViaLLMJudge:
    def test_toxicity_criterion_scored_by_mock_judge(self):
        from ascore.scoring.judge import LLMJudge
        crit = safety_judge_criterion(TOXICITY_SPEC)
        client = _StubClient('{"score": 1, "rationale": "civil and non-toxic"}')
        judge = LLMJudge(model="judge-x", agent_model="agent-y", client=client)
        cs = judge.score_criterion(crit, trace(final_output="Thanks for reaching out."),
                                   tc({}))
        assert cs.score == 1.0 and cs.scorer == "judge"
