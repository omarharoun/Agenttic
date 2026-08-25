"""Step 63d — the claim check reaches the sign-off, on its own row."""

from agenttic.reporting.signoff_report import render as render_signoff
from agenttic.schema.signoff import build_signoff
from agenttic.verification.formal.claims import (ClaimCheck, ClaimResult,
                                                 OutOfScope, PolicyClaim)


def _claim(kind="requires_approval", tool="issue_refund", asserted=False):
    return PolicyClaim(text="you don't need approval for that", kind=kind,
                       tool=tool, asserted=asserted)


def _check():
    return ClaimCheck(
        results=[
            ClaimResult(claim_text="you don't need approval for that",
                        status="invalid", claim=_claim(),
                        violated_rule="the policy sets requires_confirmation=True "
                                      "for 'issue_refund'"),
            ClaimResult(claim_text="I can look that up", status="valid",
                        claim=_claim("permitted", "read_account", True)),
            ClaimResult(claim_text="refunds are fine", status="impossible",
                        claim=_claim("permitted", "issue_refund", True),
                        detail="the policy carries contradictory rules for "
                               "'issue_refund': allow, deny"),
        ],
        out_of_scope=[OutOfScope(claim_text="happy to help!")])


def _signoff(**kw):
    return build_signoff(signoff_id="s1", agent_id="a1", **kw)


def test_leg_rolls_up_five_values_and_out_of_scope_separately():
    leg = _signoff(claim_checks=[_check(), _check()]).claims
    assert leg.status == "populated"
    assert (leg.valid, leg.invalid, leg.impossible) == (2, 2, 2)
    assert leg.checked == 6                 # out-of-scope is NOT checked
    assert leg.out_of_scope == 2            # counted, but in no bucket


def test_impossible_is_a_policy_defect_not_a_false_claim():
    leg = _signoff(claim_checks=[_check()]).claims
    assert len(leg.false_claims) == 1       # only the INVALID one
    assert "requires_confirmation=True" in leg.false_claims[0]
    assert len(leg.policy_defects) == 1
    assert "contradictory rules" in leg.policy_defects[0]


def test_absent_artifact_leaves_the_leg_not_run():
    assert _signoff().claims.status == "not_run"


def test_leg_is_report_only_and_does_not_gate():
    """Gate v1 precedent (scoreboard): a new leg is computed and rendered
    before it blocks. An INVALID claim must not retroactively fail a sign-off
    issued under a gate that never checked claims."""
    clean = _signoff()
    before = clean.signs_off
    dirty = _signoff(claim_checks=[_check()])
    assert dirty.claims.invalid == 1
    assert dirty.signs_off == before
    assert "claims" not in dirty.LEGS      # not required for completeness
    assert "claims" not in dirty.missing_legs()


def test_report_keeps_claims_off_the_formal_row():
    text = render_signoff(_signoff(claim_checks=[_check()]))
    assert "3b · OUTPUT CLAIMS" in text
    formal_row = next(l for l in text.splitlines() if "3 · FORMAL" in l)
    assert "invalid" not in formal_row      # Hard Rule 72
    assert "out of scope, not true" in text
    assert "report-only" in text


def test_not_checked_is_rendered_rather_than_omitted():
    assert "not checked" in render_signoff(_signoff())
