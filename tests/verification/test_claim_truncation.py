"""A truncated reachability search must not produce a verdict.

`check_claim` answers ``permitted`` by counting how many *reachable* states
enable the tool. The search is capped. Before this, hitting the cap returned a
strict subset of the reachable states and the caller read `enabled ==
len(states)` off it as "enabled in every reachable state" — a VALID the search
never established. `prove` has always refused that (it returns ``unbounded``,
never ``proven``, at the cap); this pins the same rule for proof(claim).
"""

import dataclasses

from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.verification.formal.claims import PolicyClaim, check_claim
from agenttic.verification.formal.graph import from_enforcement_policy

POLICY = EnforcementPolicy(
    policy_id="p1", agent_id="a1",
    rules=[Rule(rule_id="r1", lane="lane1", action="allow",
                matcher={"tool": "get_order"}),
           Rule(rule_id="r2", lane="lane2", action="deny",
                matcher={"tool": "delete_account"})])


def graph():
    return from_enforcement_policy(POLICY, confirmable=[])


def permitted(tool="get_order", asserted=True):
    return PolicyClaim(text=f"I can call {tool}", kind="permitted", tool=tool,
                       asserted=asserted)


def test_an_unexplored_state_space_yields_no_verdict():
    """max_states=0 truncates immediately: nothing about reachability is known,
    so neither VALID nor INVALID is available."""
    r = check_claim(graph(), permitted(), max_states=0)
    assert r.status == "ambiguous"
    assert "incomplete" in r.detail
    assert r.finding_kind() == "evidence_finding"


def test_truncation_does_not_round_to_valid():
    """The specific regression. `authenticate` carries no guards, so it IS
    enabled in every reachable state and a complete search returns VALID —
    which is exactly how the old code reached VALID off a truncated set too,
    by comparing `enabled` against a `len(states)` that was not the real one.
    """
    full = check_claim(graph(), permitted("authenticate"))
    assert full.status == "valid", "precondition: this claim IS valid when checked"
    assert check_claim(graph(), permitted("authenticate"),
                       max_states=0).status == "ambiguous"


def test_truncation_does_not_round_to_invalid_either():
    """Unsound in both directions — a negated claim must not be rescued into
    INVALID by a search that never ran."""
    assert check_claim(graph(), permitted("authenticate", asserted=False),
                       max_states=0).status == "ambiguous"


def test_a_gated_tool_is_still_satisfiable_when_the_search_completes():
    """The guard must not swallow the SATISFIABLE case: `get_order` is gated on
    authentication, so it is enabled in some reachable states but not all."""
    r = check_claim(graph(), permitted("get_order"))
    assert r.status == "satisfiable"
    assert "gated on authentication" in r.detail


def test_a_denial_still_resolves_without_exploring():
    """`denied` is a rule on the edge, not a fact about reachability, so the
    cap must not downgrade it. Refusing a verdict we CAN soundly make would be
    the opposite failure."""
    r = check_claim(graph(), permitted("delete_account"), max_states=0)
    assert r.status == "invalid"
    assert "denying" in r.violated_rule


def test_a_non_reachability_claim_is_unaffected_by_the_cap():
    """requires_auth/approval/entity read the edge directly."""
    r = check_claim(graph(), PolicyClaim(text="needs approval",
                                         kind="requires_approval",
                                         tool="get_order", asserted=False),
                    max_states=0)
    assert r.status == "valid"


def test_an_infinite_state_space_yields_no_verdict():
    g = dataclasses.replace(graph(), unbounded=True)
    r = check_claim(g, permitted())
    assert r.status == "ambiguous"
    assert "not finite" in r.detail


def test_the_ambiguous_sentence_does_not_blame_translation():
    """AMBIGUOUS now covers three causes; only one is a translation failure.
    Saying 'could not be soundly translated' about a capped search would
    misreport where the uncertainty came from."""
    r = check_claim(graph(), permitted(), max_states=0)
    assert "could not be soundly checked" in r.render()
    assert "Limit:" in r.render()
