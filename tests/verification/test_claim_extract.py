"""SPEC-13 Step 63b — the translation half, and its wiring into `verify_op`.

Every test here runs offline against a stub client. That is the point of the
two-stage split: `claims` decides and never calls a model, `claim_extract`
calls a model and never decides, so the whole path is exercisable with no key.
"""

import json
import types

import pytest

from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.verification.claim_extract import (ClaimExtractionError,
                                                 model_extractor,
                                                 static_extractor)
from agenttic.verification.formal.claims import check_output
from agenttic.verification.formal.graph import from_enforcement_policy

POLICY = EnforcementPolicy(
    policy_id="p1", agent_id="a1",
    rules=[Rule(rule_id="r1", lane="lane1", action="require_approval",
                matcher={"tool": "issue_refund"}),
           Rule(rule_id="r2", lane="lane1", action="allow",
                matcher={"tool": "get_order"})])
GRAPH = from_enforcement_policy(POLICY, confirmable=["issue_refund"])
TOOLS = sorted(e.tool for e in GRAPH.edges)

LIE = {"text": "You don't need approval for that refund.",
       "kind": "requires_approval", "tool": "issue_refund", "asserted": False}


class StubClient:
    """Returns a scripted JSON body per call, and counts calls."""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create)

    def __init_stop__(self, stop):          # set by the stop_reason tests
        self.stop = stop

    def _create(self, **kw):
        self.calls.append(kw)
        body = self.bodies[min(len(self.calls) - 1, len(self.bodies) - 1)]
        if isinstance(body, Exception):
            raise body
        block = types.SimpleNamespace(type="text", text=body)
        return types.SimpleNamespace(content=[block],
                                     stop_reason=getattr(self, "stop", "end_turn"))


def _body(*claims):
    return json.dumps({"claims": list(claims)})


# --- the extractor ---------------------------------------------------------- #

def test_it_asks_for_json_and_names_the_real_tools():
    c = StubClient(_body(LIE))
    model_extractor(c, TOOLS)("whatever")
    kw = c.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["model"] == "claude-opus-5"
    prompt = kw["messages"][0]["content"]
    for tool in TOOLS:
        assert tool in prompt, "the model must be told which tools exist"


def test_every_invocation_is_a_fresh_call():
    """`translate` samples the extractor n times and treats agreement as
    confidence. A memoized extractor would return one opinion three times and
    manufacture unanimity, so each call must hit the model again."""
    c = StubClient(_body(LIE))
    extract = model_extractor(c, TOOLS)
    extract("out"), extract("out"), extract("out")
    assert len(c.calls) == 3


def test_an_unparseable_response_raises_rather_than_reading_as_no_claims():
    for bad in ("not json at all", json.dumps({"wrong_key": []}),
                json.dumps({"claims": "not a list"})):
        with pytest.raises(ClaimExtractionError):
            model_extractor(StubClient(bad), TOOLS)("out")


def test_a_failing_client_call_becomes_an_extraction_error():
    with pytest.raises(ClaimExtractionError):
        model_extractor(StubClient(RuntimeError("503")), TOOLS)("out")


def test_out_of_scope_dicts_are_passed_through_not_filtered_here():
    """`claims._parse` owns the definition of out-of-scope. Filtering here too
    would let the two definitions drift."""
    raw = model_extractor(
        StubClient(_body({"text": "Happy to help!", "kind": "", "tool": "",
                          "asserted": True})), TOOLS)("out")
    assert len(raw) == 1



def test_a_truncated_response_says_so_rather_than_blaming_the_json():
    """A clipped reply is NO answer, not a malformed one. Reporting it as an
    unparseable claim list blames the shape of a reply that never finished and
    hides a ceiling the caller can raise."""
    c = StubClient(_body(LIE)[:40])      # valid JSON, cut off mid-object
    c.stop = "max_tokens"
    with pytest.raises(ClaimExtractionError) as e:
        model_extractor(c, TOOLS, max_tokens=123)("out")
    assert "123-token ceiling" in str(e.value)
    assert "unchecked, not clean" in str(e.value)


def test_a_refusal_is_named_as_one():
    c = StubClient(_body())
    c.stop = "refusal"
    with pytest.raises(ClaimExtractionError) as e:
        model_extractor(c, TOOLS)("out")
    assert "declined" in str(e.value)


def test_the_ceiling_is_generous_enough_for_a_chatty_agent():
    """The default is the regression this guards: at 2000 a long final message
    produced a long claim list, clipped it, and the case went silently
    unchecked — on exactly the outputs most likely to carry a real claim."""
    c = StubClient(_body(LIE))
    model_extractor(c, TOOLS)("out")
    assert c.calls[0]["max_tokens"] >= 16000


def test_a_stop_reason_the_stub_never_sets_still_reads_normally():
    """Backwards compatibility: a response object with no stop_reason at all
    (an older client, or a stub) must not be treated as a failure."""
    c = StubClient(_body(LIE))
    c.stop = None
    assert len(model_extractor(c, TOOLS)("out")) == 1


# --- end to end, still offline ---------------------------------------------- #

def test_a_lie_about_approval_is_caught_as_invalid():
    extract = model_extractor(StubClient(_body(LIE)), TOOLS)
    check = check_output("...", GRAPH, extract, policy=POLICY, n_runs=3)
    assert [r.status for r in check.results] == ["invalid"]
    assert "requires_confirmation=True" in check.results[0].violated_rule


def test_runs_that_disagree_produce_ambiguous_never_a_verdict():
    """The confidence signal: two runs map the sentence, one does not."""
    c = StubClient(_body(LIE), _body(LIE), _body())
    check = check_output("...", GRAPH, model_extractor(c, TOOLS),
                         policy=POLICY, n_runs=3)
    assert [r.status for r in check.results] == ["ambiguous"]
    assert check.results[0].agreement == (2, 3)


def test_static_extractor_is_deterministic_and_therefore_always_agrees():
    check = check_output("...", GRAPH, static_extractor([LIE]), policy=POLICY,
                         n_runs=3)
    assert [r.status for r in check.results] == ["invalid"]
