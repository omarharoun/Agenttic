"""SPEC-13 Step 63b/c/d — claim checking and the five-valued result."""

import typing

import pytest

from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.verification.formal.claims import (
    ClaimStatus, PolicyClaim, check_claim, check_output, policy_conflicts,
    render_report, translate)
from agenttic.verification.formal.graph import from_enforcement_policy


def _policy(*rules):
    return EnforcementPolicy(policy_id="p1", agent_id="a1", rules=list(rules))


SHOP = _policy(
    Rule(rule_id="r1", lane="lane1", action="require_approval",
         matcher={"tool": "issue_refund"}),
    Rule(rule_id="r2", lane="lane1", action="allow", matcher={"tool": "get_order"}),
    Rule(rule_id="r3", lane="lane2", action="deny",
         matcher={"tool": "delete_account"}),
)


def graph():
    return from_enforcement_policy(SHOP, confirmable=["issue_refund"])


def fixed_extractor(*claims):
    """A deterministic stand-in for the LLM extraction step."""
    return lambda _output: list(claims)


# --- the result type -------------------------------------------------------- #

def test_the_result_type_is_five_valued():
    assert set(typing.get_args(ClaimStatus)) == {
        "valid", "invalid", "satisfiable", "ambiguous", "impossible"}


def test_no_function_defaults_an_unresolved_check_to_a_verdict():
    """Acceptance: enforced by inspection over the result-producing functions,
    not by convention. Every ``ClaimResult(...)`` construction must name its
    status explicitly — a defaulted status is how false confidence gets in."""
    import inspect

    from agenttic.verification.formal import claims as mod
    src = inspect.getsource(mod)
    # the dataclass must not give `status` a default
    assert "status: ClaimStatus\n" in src, "ClaimResult.status must have no default"
    for call in src.split("ClaimResult(")[1:]:
        head = call[:200]
        assert "status=" in head, f"ClaimResult built without an explicit status: {head!r}"


# --- validation ------------------------------------------------------------- #

def test_a_true_approval_claim_is_valid():
    r = check_claim(graph(), PolicyClaim(
        text="Refunds need a manager's approval.", kind="requires_approval",
        tool="issue_refund", asserted=True))
    assert r.status == "valid"
    assert r.finding_kind() == ""


def test_a_false_approval_claim_is_invalid_and_names_the_rule():
    """Acceptance: an INVALID result always renders the violated rule next to
    the claim text."""
    r = check_claim(graph(), PolicyClaim(
        text="You don't need approval for this refund.",
        kind="requires_approval", tool="issue_refund", asserted=False))
    assert r.status == "invalid"
    assert r.finding_kind() == "agent_finding"
    assert "requires_confirmation=True" in r.violated_rule
    rendered = r.render()
    assert "You don't need approval" in rendered and "requires_confirmation" in rendered


def test_denied_tool_claimed_as_permitted_is_invalid():
    r = check_claim(graph(), PolicyClaim(
        text="I can delete your account.", kind="permitted",
        tool="delete_account", asserted=True))
    assert r.status == "invalid"


def test_gated_permission_is_satisfiable_not_valid():
    """A refund is reachable only after confirmation, so "I can issue a refund"
    is consistent with the policy but not entailed by it. Never merged into
    VALID."""
    r = check_claim(graph(), PolicyClaim(
        text="I can issue that refund.", kind="permitted",
        tool="issue_refund", asserted=True))
    assert r.status == "satisfiable"
    assert "approval" in r.detail
    assert "weaker than VALID" in r.render()


def test_a_tool_the_policy_does_not_govern_is_ambiguous_not_valid():
    r = check_claim(graph(), PolicyClaim(
        text="I can wire you money.", kind="permitted", tool="wire_transfer"))
    assert r.status == "ambiguous"
    assert r.finding_kind() == "evidence_finding"


# --- IMPOSSIBLE: the policy's problem, never the agent's -------------------- #

def test_a_self_contradictory_policy_is_impossible_and_is_a_suite_finding():
    """Acceptance: contradictory policy => IMPOSSIBLE, classified suite_finding,
    and it does not lower the agent's score."""
    bad = _policy(
        Rule(rule_id="r1", lane="lane1", action="allow", matcher={"tool": "refund"}),
        Rule(rule_id="r2", lane="lane1", action="deny", matcher={"tool": "refund"}),
    )
    assert policy_conflicts(bad) == {"refund": ["allow", "deny"]}
    g = from_enforcement_policy(bad)
    r = check_claim(g, PolicyClaim(text="I can refund that.", kind="permitted",
                                   tool="refund"),
                    conflicts=policy_conflicts(bad))
    assert r.status == "impossible"
    assert r.finding_kind() == "suite_finding"
    # attributed to the policy document, never to the agent (Hard Rule 71)
    assert "defect in the policy, not in the agent" in r.render()
    assert "contradictory rules for 'refund'" in r.render()


def test_the_conflict_is_invisible_without_this_check():
    """from_enforcement_policy resolves allow-vs-deny by rank, silently. That is
    exactly why IMPOSSIBLE has to be detected on the source policy."""
    bad = _policy(
        Rule(rule_id="r1", lane="lane1", action="allow", matcher={"tool": "refund"}),
        Rule(rule_id="r2", lane="lane1", action="deny", matcher={"tool": "refund"}),
    )
    assert from_enforcement_policy(bad).tool("refund").denied is True  # collapsed


# --- translation ------------------------------------------------------------ #

def test_multi_run_disagreement_is_ambiguous_never_a_forced_verdict():
    """Acceptance: disagreement produces AMBIGUOUS, never a forced VALID."""
    runs = iter([
        [{"text": "No approval needed.", "kind": "requires_approval",
          "tool": "issue_refund", "asserted": False}],
        [{"text": "No approval needed.", "kind": "permitted",
          "tool": "issue_refund", "asserted": True}],
        [{"text": "No approval needed.", "kind": "requires_approval",
          "tool": "issue_refund", "asserted": False}],
    ])
    check = check_output("...", graph(), lambda _o: next(runs), n_runs=3)
    statuses = {r.status for r in check.results}
    assert statuses == {"ambiguous"}
    assert all(r.agreement[0] < r.agreement[1] for r in check.results)


def test_unanimous_translation_is_validated():
    claim = {"text": "Refunds need approval.", "kind": "requires_approval",
             "tool": "issue_refund", "asserted": True}
    check = check_output("...", graph(), fixed_extractor(claim), n_runs=3)
    assert [r.status for r in check.results] == ["valid"]


def test_a_claim_naming_no_policy_variable_is_out_of_scope_not_a_bucket():
    """Acceptance: out-of-scope claims are not sent to the solver and are
    counted in none of the five buckets."""
    check = check_output("...", graph(), fixed_extractor(
        {"text": "I'm happy to help you today!"}), n_runs=2)
    assert check.results == []
    assert sum(check.counts().values()) == 0
    assert [o.claim_text for o in check.out_of_scope] == ["I'm happy to help you today!"]


def test_out_of_scope_is_distinct_from_ambiguous():
    """"Not a policy claim" and "policy claim we failed to translate" are
    different findings and must not be conflated."""
    check = check_output("...", graph(), fixed_extractor(
        {"text": "Have a nice day."},
        {"text": "I can wire money.", "kind": "permitted", "tool": "wire_x"}), n_runs=2)
    # neither is a bucket: an unknown tool never resolves to a known variable
    assert len(check.out_of_scope) == 2
    assert check.results == []


# --- the report row --------------------------------------------------------- #

def test_the_per_case_row_reports_all_five_buckets():
    check = check_output("...", graph(), fixed_extractor(
        {"text": "Refunds need approval.", "kind": "requires_approval",
         "tool": "issue_refund", "asserted": True},
        {"text": "I can delete your account.", "kind": "permitted",
         "tool": "delete_account", "asserted": True}), n_runs=2)
    row = check.row()
    assert "output claims: 2 checked" in row
    assert "1 valid" in row and "1 invalid" in row
    assert "satisfiable" in row and "ambiguous" in row and "impossible" in row


def test_report_separates_policy_defects_from_agent_findings():
    bad = _policy(
        Rule(rule_id="r1", lane="lane1", action="allow", matcher={"tool": "refund"}),
        Rule(rule_id="r2", lane="lane1", action="deny", matcher={"tool": "refund"}),
    )
    g = from_enforcement_policy(bad)
    check = check_output("...", g, fixed_extractor(
        {"text": "I can refund that.", "kind": "permitted", "tool": "refund"}),
        policy=bad, n_runs=2)
    text = render_report(check)
    assert "POLICY-DOCUMENT DEFECTS" in text
    assert "not scored against" in text


def test_the_renderer_refuses_an_unqualified_claim():
    """Hard Rule 62 — every rendered claim carries its scope in the same
    sentence, enforced by the existing assert_scoped."""
    check = check_output("...", graph(), fixed_extractor(
        {"text": "Refunds need approval.", "kind": "requires_approval",
         "tool": "issue_refund", "asserted": True}), n_runs=2)
    text = render_report(check)
    assert "Limit:" in text
    assert "SCOPE:" in text


def test_every_rendered_result_carries_its_limit():
    for kind, tool, asserted in [("requires_approval", "issue_refund", True),
                                 ("requires_approval", "issue_refund", False),
                                 ("permitted", "issue_refund", True),
                                 ("permitted", "nope", True)]:
        r = check_claim(graph(), PolicyClaim(text="x", kind=kind, tool=tool,
                                             asserted=asserted))
        assert "Limit:" in r.render(), r.status
