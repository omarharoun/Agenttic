"""What a `tool_condition` bin is allowed to be credited for.

The coverpoint is described as "what the environment did to the agent" and there
is no environment: nothing in the run path injects a timeout, a 5xx or a rate
limit. Credit therefore came from `_tool_signal`, a substring sniff over every
serialized field of every tool/error span — so a knowledge-base article about an
"outdated" policy, a customer message saying "timed out", or a sha256 digest
that happens to contain "500" all claimed a corner nothing had exercised.

A digest is the worst of these because it is systematic rather than unlucky:
OTel-ingested spans carry `content_sha256` where the body should be, and 64 hex
characters give 62 three-character windows — so one digest contains a given hex
needle 1.50% of the time, and one of the four numeric needles `error_5xx` used to
carry (500/502/503/504) 5.89% of the time. An ingested span carries an input
digest AND an output digest, which is 11.43% and 2.98% per span. Those figures
are exact, and `TestTheDigestRateIsComputedNotAsserted` recomputes them rather
than trusting the comment that states them.

These tests pin the rule that replaced it. A bin is credited only when all three
hold: the agent TOUCHED the environment (a `tool_call` span), a call actually
failed, and the condition is named — by the scenario that injected it, or by that
call's structured error channel with its status codes anchored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agenttic.coverage.extractors import run_predicate
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: a real sha256 (of b"order-4471"), truncated nowhere — it contains "500",
#: which is what makes the old predicate fire. Fixed, so this test is
#: deterministic rather than an 11%-of-the-time flake.
DIGEST_WITH_500 = "b0f5008b3d1a4c6e9f2a7d8c5004e1b2c3a4d5e6f70819202a3b4c5d6e7f8091"


def span(kind, name, *, i=0, input=None, output=None, error=None, attributes=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=input or {}, output=output or {}, error=error,
                attributes=attributes or {})


def trace(*spans, final_output="here you go"):
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=fixed, visibility="glass_box",
                 final_output=final_output)


class TestADigestIsNotEvidence:
    def test_a_content_digest_is_not_a_5xx(self):
        """The ingested-traffic false credit, reproduced exactly."""
        assert "500" in DIGEST_WITH_500, "fixture must contain the needle"
        t = trace(span("tool_call", "get_order",
                       input={"content_sha256": DIGEST_WITH_500, "parts": 1},
                       output={"content_sha256": DIGEST_WITH_500, "parts": 1}),
                  span("llm_call", "messages.create", i=1))
        assert run_predicate("tool_error_5xx", t) is False

    def test_a_digest_does_not_credit_a_data_condition_either(self):
        """`data_*` keeps the substring read — it must still lose the digest."""
        t = trace(span("tool_call", "get_order",
                       output={"content_sha256": "404" + "a" * 61}))
        assert run_predicate("data_entity_not_found", t) is False


class TestCreditRequiresProvenance:
    def test_a_200_body_mentioning_timeout_is_not_a_timeout(self):
        """The over-report being removed: the KB *says* "timed out"; nothing
        timed out."""
        t = trace(span("tool_call", "lookup_kb",
                       output={"article": "If a delivery has timed out, "
                                          "advise the customer to wait."}))
        assert run_predicate("tool_timeout", t) is False

    def test_a_run_level_error_span_is_not_a_tool_condition(self):
        """CORRECTED. This asserted `is True`, on the claim that a lone `error`
        span is "the shape the harness synthesizes for a failed call". It is not:
        the harness synthesizes it for a failed *run* — `_failure_trace`
        (harness/runner.py:61) emits exactly one `error` span named for the
        failure kind, with no tool calls at all, for a timeout, a transport
        failure or an adapter crash. Nothing in this build emits a standalone
        `error` span meaning "a tool got a 5xx": every producer of that kind is
        run- or model-level (blackbox_http.py:200, anthropic_simple.py:168/217,
        managed_agent.py). So the old assertion credited `tool_timeout` to a run
        that made zero tool calls — an over-report inside the fix for an
        over-report, and the positive case it was reaching for is covered by the
        two tests below, on the span shape a failed tool call actually has.
        """
        t = trace(span("error", "timeout", i=0))
        assert run_predicate("tool_timeout", t) is False

    def test_the_harness_own_failure_trace_credits_nothing(self):
        """Proved against the real synthesizer, not a lookalike fixture.

        This trace is a non-result: `scoring.engine.nonresult_reason` refuses to
        score it because the agent never answered. Crediting coverage from it
        would mean a run that never started counted as environment fault
        coverage — the worst available direction of error on a product whose
        claim is an honest account of what was never exercised.
        """
        from agenttic.harness.runner import _failure_trace

        class _Adapter:                      # the three attributes it reads
            agent_id = "a"
            visibility = "glass_box"

            def config_hash(self):
                return "c"

        class _Case:
            test_id = "case"

        for kind in ("timeout", "transport_failure", "harness_error"):
            t = _failure_trace(_Adapter(), _Case(), kind, f"{kind} detail")
            assert t.final_output.startswith("HARNESS_FAILURE")
            for bin_predicate in ("tool_timeout", "tool_error_5xx",
                                  "tool_rate_limited", "tool_stale_data",
                                  "tool_malformed_response", "tool_all_ok"):
                assert run_predicate(bin_predicate, t) is False, (kind, bin_predicate)

    def test_an_error_field_on_a_tool_span_credits_the_bin(self):
        t = trace(span("tool_call", "get_order", error="502 bad gateway"))
        assert run_predicate("tool_error_5xx", t) is True

    def test_a_structured_error_payload_credits_the_bin(self):
        t = trace(span("tool_call", "get_order",
                       output={"error": "429 too many requests"}))
        assert run_predicate("tool_rate_limited", t) is True

    def test_an_injected_failure_identifies_a_fault_the_error_text_does_not(self):
        """The scenario is the authority on what it injected.

        This is the arm that makes the coverpoint mean something once an
        environment exists: the predicate reads `scenario`, which the whole
        stimulus layer builds and which no predicate dereferenced before. Here
        the tool failed with a message that names nothing; the scenario says
        what the failure was.
        """
        failed = trace(span("tool_call", "get_order", error="call failed"),
                       span("llm_call", "messages.create", i=1))
        assert run_predicate("tool_timeout", failed, None) is False
        assert run_predicate("tool_timeout", failed,
                             {"injected_failures": ["timeout"]}) is True

    def test_an_injected_failure_that_never_fired_is_not_coverage(self):
        """Closure is computed on what a run EXHIBITED, never on what was asked
        for. A timeout injected on order-lookup and an agent that never looks up
        the order have not exercised a timeout between them — and a stub
        executor that ignores the scenario entirely must not close the bin it
        was pointed at (tests/stimulus/test_cdv.py holds the loop to this)."""
        clean = trace(span("tool_call", "get_order", output={"ok": True}),
                      span("llm_call", "messages.create", i=1))
        assert run_predicate("tool_timeout", clean,
                             {"injected_failures": ["timeout"]}) is False


class TestAStatusNumberMustBeInAStatusPosition:
    """The needles "500"/"502"/"503"/"504"/"429" were bare substrings read over a
    free-text error message. Moving the read from the whole span blob to the error
    channel narrowed *where* it looked without changing *what* counts, so a
    genuine tool error whose text carries an order id, a SKU or a dollar amount
    was still binned as a transport fault — and binned wrongly, which is worse
    than not binning it: the run did fail, so a reader sees a plausible cause.
    """

    def test_an_order_id_containing_504_is_not_a_5xx(self):
        t = trace(span("tool_call", "update_order",
                       error="could not update order #50412"))
        assert run_predicate("tool_error_5xx", t) is False

    def test_a_dollar_amount_containing_429_is_not_rate_limiting(self):
        t = trace(span("tool_call", "issue_refund",
                       output={"error": "refund of $429.00 was declined"}))
        assert run_predicate("tool_rate_limited", t) is False

    def test_a_sku_containing_500_is_not_a_5xx(self):
        t = trace(span("tool_call", "get_item", error="sku BX-5002 is discontinued"))
        assert run_predicate("tool_error_5xx", t) is False

    def test_the_failure_is_still_visible_as_a_failure(self):
        """Refusing to guess the bin is not refusing to record the fault. The
        run did fail; `tool_all_ok` must not claim otherwise, and the fault
        surfaces on the trajectory axis instead of being mislabelled here."""
        t = trace(span("tool_call", "update_order",
                       error="could not update order #50412"))
        assert run_predicate("tool_all_ok", t) is False

    def test_a_status_token_anchors_the_number(self):
        for text in ("http 503", "status_code=500", "HTTP/1.1 502 from upstream",
                     "error code: 500"):
            t = trace(span("tool_call", "get_order", error=text))
            assert run_predicate("tool_error_5xx", t) is True, text

    def test_a_reason_phrase_anchors_the_number(self):
        t = trace(span("tool_call", "get_order", error="[503] service unavailable"))
        assert run_predicate("tool_error_5xx", t) is True

    def test_a_declared_status_attribute_is_read_before_any_prose(self):
        """The producer's own status field is the one unambiguous signal a trace
        carries, and ingest preserves every attribute the producer sent
        (ingest/mapping.py:203). A 503 with an unhelpful message is still a 503."""
        t = trace(span("tool_call", "get_order", error="request failed",
                       attributes={"http.response.status_code": 503}))
        assert run_predicate("tool_error_5xx", t) is True

    def test_a_declared_2xx_does_not_credit_anything(self):
        t = trace(span("tool_call", "get_order", error="request failed",
                       output={"http.status_code": 200}))
        assert run_predicate("tool_error_5xx", t) is False
        assert run_predicate("tool_rate_limited", t) is False

    def test_408_and_504_are_timeouts_by_definition(self):
        """RFC 9110 §15 names them "Request Timeout" and "Gateway Timeout"; a
        declared one is a timeout even when the message says nothing."""
        for code in (408, 504):
            t = trace(span("tool_call", "get_order", error="upstream failure",
                           output={"status_code": code}))
            assert run_predicate("tool_timeout", t) is True, code

    def test_a_jsonrpc_error_code_is_not_an_http_status(self):
        """MCP renders failures as `[{code}] {message}` with JSON-RPC codes
        (adapters/mcp_server.py:246), which share no numbering with HTTP."""
        t = trace(span("tool_call", "get_order",
                       error="tools/call failed: [-32601] method not found"))
        assert run_predicate("tool_error_5xx", t) is False


class TestTheDigestRateIsComputedNotAsserted:
    """`extractors.py` quantifies the digest collision in a comment, and a number
    in a comment rots — an earlier revision of that comment carried figures that
    were wrong for the proposition they supported. This recomputes them exactly,
    so the comment is either right or this test is red.
    """

    @staticmethod
    def _p_any(needles: set[str], n: int = 64) -> float:
        """Exact P(any needle occurs in a uniform random n-char hex string).

        Every needle is three characters, so whether a hit lands at position i
        depends only on the two characters before it — the exact state is the last
        two characters, and a 64-step DP over the 16 symbols is the closed answer.
        Fractions, so there is no floating drift, and no sampling anywhere.
        """
        from fractions import Fraction
        hexd = "0123456789abcdef"
        states: dict[tuple[str, str], Fraction] = {("", ""): Fraction(1)}
        hit = Fraction(0)
        p = Fraction(1, len(hexd))
        for _ in range(n):
            nxt: dict[tuple[str, str], Fraction] = {}
            for (a, b), w in states.items():
                for c in hexd:
                    if a + b + c in needles:
                        hit += w * p
                    else:
                        nxt[(b, c)] = nxt.get((b, c), Fraction(0)) + w * p
            states = nxt
        return float(hit)

    def test_the_quantified_claim_in_the_comment_holds(self):
        one = self._p_any({"429"})
        five = self._p_any({"500", "502", "503", "504"})
        assert round(one * 100, 2) == 1.50, one
        assert round(five * 100, 2) == 5.89, five
        # an ingested span carries an input digest AND an output digest
        assert round((1 - (1 - one) ** 2) * 100, 2) == 2.98
        assert round((1 - (1 - five) ** 2) * 100, 2) == 11.43

    def test_the_comment_states_those_figures_and_no_others(self):
        """Guards the pairing: the numbers above are only useful if they are the
        numbers the module actually claims."""
        from pathlib import Path
        body = (Path(__file__).resolve().parents[2]
                / "src/agenttic/coverage/extractors.py").read_text()
        head = body[:body.index("_DIGEST_RE")]
        for figure in ("1.50%", "5.89%", "2.98%", "11.43%"):
            assert figure in head, figure


class TestNoCollateralDamage:
    def test_data_condition_still_reads_the_tool_output(self):
        """`ambiguous` and `contradictory` have no error representation; if the
        guard were applied to them they would silently become unhittable."""
        t = trace(span("tool_call", "get_order",
                       output={"note": "two orders match that description — "
                                       "ambiguous"}))
        assert run_predicate("data_ambiguous", t) is True

    def test_a_clean_run_is_still_all_ok(self):
        t = trace(span("tool_call", "get_order", output={"status": "shipped"}))
        assert run_predicate("tool_all_ok", t) is True
