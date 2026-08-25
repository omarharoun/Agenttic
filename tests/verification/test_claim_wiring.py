"""Step 63d — claim checking reaches `verify_op`, and stays opt-in there.

`verify_op` runs on the normal path for every run and its docstring promises
ZERO model calls. Claim checking needs a model, so it must be opt-in and must
stay silent — and honest about being silent — when it is not switched on.
"""

import json
import types

from agenttic.ops import verify_op
from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.schema.signoff import VerificationSignoff
from agenttic.verification.claim_extract import model_extractor, static_extractor

from .conftest import span, trace

POLICY = EnforcementPolicy(
    policy_id="p1", agent_id="a1",
    rules=[Rule(rule_id="r1", lane="lane1", action="require_approval",
                matcher={"tool": "issue_refund"})])
LIE = {"text": "No approval needed.", "kind": "requires_approval",
       "tool": "issue_refund", "asserted": False}
TRUE = {"text": "That needs approval.", "kind": "requires_approval",
        "tool": "issue_refund", "asserted": True}


def _leg(**kw):
    _, summary = verify_op([trace(span("llm_call", "respond"), final_output="No approval needed.")], **kw)
    return VerificationSignoff.model_validate(summary["signoff"]).claims


def test_without_an_extractor_the_leg_is_not_run():
    """`not_run` is the honest reading. It must never render as a clean check."""
    leg = _leg()
    assert leg.status == "not_run"
    assert leg.checked == 0 and leg.invalid == 0


def test_an_extractor_without_a_policy_checks_nothing():
    """A claim is checked AGAINST a policy. Half the inputs is not half a check."""
    assert _leg(claim_extractor=static_extractor([LIE])).status == "not_run"


def test_a_policy_without_an_extractor_checks_nothing():
    assert _leg(enforcement_policy=POLICY).status == "not_run"


def test_the_default_path_makes_no_model_calls(no_network):
    """The promise in `verify_op`'s docstring, enforced: with no extractor,
    nothing here may open a socket."""
    assert _leg().status == "not_run"


def test_a_false_claim_reaches_the_leg():
    leg = _leg(claim_extractor=static_extractor([LIE]), enforcement_policy=POLICY)
    assert leg.status == "populated"
    assert (leg.invalid, leg.checked) == (1, 1)
    assert "requires_confirmation=True" in leg.false_claims[0]


def test_a_true_claim_reaches_it_as_valid():
    leg = _leg(claim_extractor=static_extractor([TRUE]), enforcement_policy=POLICY)
    assert (leg.valid, leg.invalid) == (1, 0)
    assert leg.false_claims == []


def test_a_failed_extraction_is_recorded_not_dropped():
    """The denominator hole. A trace whose claims could not be extracted was
    not checked; silently skipping it would make `0 invalid` mean nothing."""
    class Boom:
        def __init__(self):
            self.messages = types.SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("503")))

    leg = _leg(claim_extractor=model_extractor(Boom(), ["issue_refund"]),
               enforcement_policy=POLICY)
    assert leg.status == "populated"
    assert leg.extraction_failures == 1
    assert leg.checked == 0
    assert leg.invalid == 0, "a failure is not a finding"


def test_a_policy_governing_no_tools_yields_no_verdicts():
    """A policy with no rules compiles to a graph with no tools, so a claim
    naming one references no policy variable. That is OUT OF SCOPE — reported,
    and deliberately not a verdict in either direction."""
    leg = _leg(claim_extractor=static_extractor([LIE]),
               enforcement_policy=EnforcementPolicy(policy_id="empty",
                                                    agent_id="a1", rules=[]))
    assert leg.checked == 0
    assert (leg.valid, leg.invalid) == (0, 0)
    assert leg.out_of_scope == 1


def test_the_leg_still_does_not_gate():
    """Report-only under gate v1 holds through the real wiring, not just in
    the schema test."""
    _, summary = verify_op([trace(span("llm_call", "respond"), final_output="No approval needed.")],
                           claim_extractor=static_extractor([LIE]),
                           enforcement_policy=POLICY)
    s = VerificationSignoff.model_validate(summary["signoff"])
    assert s.claims.invalid == 1
    assert "claims" not in s.LEGS
    assert not any("claim" in w.lower() for w in s._scoreboard_refusals())
