"""SPEC-13 M43 — formal verification of the authorization layer (Step 63).

Covers the four acceptance criteria and anti-pattern §7.6 (overclaimed proofs):
the result type must stay four-valued, and nothing that was bounded-checked,
unbounded, or skipped may ever report `proven`.
"""

from __future__ import annotations

import pytest

from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.verification.formal import (
    PolicyGraph, ProofResult, ToolEdge, assert_scoped,
    from_enforcement_policy, no_cross_tenant_exposure, no_tool_after_revocation,
    no_tool_without_confirmation, no_write_from_unauthenticated,
    no_write_without_prior_read, prove, prove_all, render_report, z3_available)
from agenttic.verification.formal.properties import unbounded_rate_property

BASE = [
    ToolEdge("authenticate", requires_auth=False, grants_auth=True),
    ToolEdge("get_order", action_class="read", loads_entity=True),
    ToolEdge("confirm::issue_refund", confirms="issue_refund"),
]


def holed_graph() -> PolicyGraph:
    """The hole: issue_refund is guarded by auth + entity, but NOT confirmation."""
    return PolicyGraph(edges=BASE + [
        ToolEdge("issue_refund", action_class="write", requires_entity=True)])


def fixed_graph() -> PolicyGraph:
    return PolicyGraph(edges=BASE + [
        ToolEdge("issue_refund", action_class="write", requires_entity=True,
                 requires_confirmation=True)])


# --- 1. a policy with a hole yields a concrete counterexample path --------- #

def test_policy_with_a_hole_produces_a_concrete_counterexample_path():
    r = prove(holed_graph(), no_tool_without_confirmation("issue_refund"))
    assert r.status == "counterexample"
    assert not r.ok
    assert r.path, "a counterexample must be a concrete path, not a verdict"
    assert r.path[0].startswith("start")
    assert "issue_refund" in " ".join(r.path)
    assert "VIOLATION" in r.path[-1]
    # the claim says NOT proven, and still carries its limit
    assert "NOT proven" in r.claim() and "Limit:" in r.claim()


# --- 2. the corrected policy discharges as proven -------------------------- #

def test_corrected_policy_discharges_the_property_as_proven():
    r = prove(fixed_graph(), no_tool_without_confirmation("issue_refund"))
    assert r.status == "proven" and r.ok
    assert r.method == "reachability"
    assert r.states_explored > 0
    assert "For all reachable paths" in r.claim()
    assert "Limit:" in r.claim()


def test_shipped_properties_hold_on_a_well_guarded_policy():
    g = fixed_graph()
    for factory in (no_write_from_unauthenticated, no_write_without_prior_read,
                    no_cross_tenant_exposure, no_tool_after_revocation):
        r = prove(g, factory())
        assert r.status == "proven", (factory.__name__, r.detail)


def test_a_missing_auth_guard_is_caught():
    g = PolicyGraph(edges=[
        ToolEdge("get_order", action_class="read", requires_auth=False,
                 loads_entity=True),
        # write reachable with no authentication at all
        ToolEdge("issue_refund", action_class="write", requires_auth=False,
                 requires_entity=True)])
    r = prove(g, no_write_from_unauthenticated())
    assert r.status == "counterexample"
    assert "issue_refund" in " ".join(r.path)


def test_cross_tenant_hole_is_caught():
    g = PolicyGraph(edges=[
        ToolEdge("authenticate", requires_auth=False, grants_auth=True),
        ToolEdge("bind_tenant_a", binds_tenant="acme"),
        ToolEdge("read_globex_entity", action_class="read",
                 touches_tenant="globex", loads_entity=True)])
    r = prove(g, no_cross_tenant_exposure())
    assert r.status == "counterexample"


# --- 3. unbounded properties report unbounded, NEVER proven ---------------- #

def test_unbounded_property_reports_unbounded_never_proven():
    r = prove(fixed_graph(), unbounded_rate_property())
    assert r.status == "unbounded"
    assert r.status != "proven"
    assert "No safety claim is made" in r.claim()


def test_unbounded_state_space_reports_unbounded():
    g = fixed_graph()
    g.unbounded = True
    r = prove(g, no_write_from_unauthenticated())
    assert r.status == "unbounded"
    assert "not finite" in r.detail


def test_exceeding_the_exploration_cap_reports_unbounded_not_proven():
    """An incomplete search is not a proof."""
    r = prove(fixed_graph(), no_write_from_unauthenticated(), max_states=1)
    assert r.status == "unbounded"
    assert "incomplete" in r.detail


# --- anti-pattern §7.6: overclaimed proofs --------------------------------- #

def test_the_result_type_stays_four_valued():
    import typing

    from agenttic.verification.formal.prove import ProofStatus
    assert set(typing.get_args(ProofStatus)) == {
        "proven", "counterexample", "unbounded", "not_attempted"}


def test_a_bounded_check_never_returns_proven():
    """z3 runs a BOUNDED check: finding no counterexample within the bound proves
    nothing, so it must report `unbounded`."""
    if not z3_available():
        r = prove(fixed_graph(), no_write_from_unauthenticated(), method="z3")
        assert r.status == "not_attempted"      # honest about not having run
        assert "z3 is not installed" in r.detail
    else:
        r = prove(fixed_graph(), no_write_from_unauthenticated(), method="z3")
        assert r.status != "proven"
        assert r.status == "unbounded"
        assert "bounded check cannot establish a proof" in r.detail
    # and it still refutes a real hole
    r2 = prove(holed_graph(), no_tool_without_confirmation("issue_refund"),
               method="z3")
    assert r2.status in ("counterexample", "not_attempted")


def test_not_attempted_never_reads_as_safe():
    r = ProofResult(property_id="p", status="not_attempted", scope="s",
                    limit="l", description="d", detail="no solver")
    assert not r.ok
    assert "No safety claim is made" in r.claim()


# --- 4. the claim-scope test ------------------------------------------------ #

def test_every_rendered_artifact_carries_its_scope_limitation():
    results = prove_all(fixed_graph(),
                        [no_tool_without_confirmation("issue_refund"),
                         no_write_from_unauthenticated(),
                         unbounded_rate_property()])
    text = render_report(results)          # render_report itself asserts scope
    assert "SCOPE:" in text
    assert "The model itself is NOT verified" in text
    assert text.lower().count("limit:") >= 3
    assert_scoped(text)


@pytest.mark.parametrize("bad", [
    "This agent is proven safe.",
    "The system is certified secure.",
    "We guarantee safety of the agent.",
])
def test_unqualified_claims_are_rejected(bad):
    with pytest.raises(AssertionError):
        assert_scoped(bad)


def test_a_proof_mentioned_without_its_limit_is_rejected():
    with pytest.raises(AssertionError, match="scope limitation"):
        assert_scoped("The property was proven over all reachable paths.")


# --- extraction from the real compiled policy ------------------------------ #

def test_graph_extracts_from_a_real_enforcement_policy():
    policy = EnforcementPolicy(policy_id="p1", agent_id="a1", rules=[
        Rule(rule_id="r1", lane="lane1", action="require_approval",
             matcher={"tool": "issue_refund"}),
        Rule(rule_id="r2", lane="lane1", action="allow",
             matcher={"tool": "get_order"}),
        Rule(rule_id="r3", lane="lane2", action="deny",
             matcher={"tool": "delete_account"}),
    ])
    g = from_enforcement_policy(policy, confirmable=["issue_refund"])
    assert g.tool("issue_refund").requires_confirmation is True   # require_approval
    assert g.tool("delete_account").denied is True                # deny
    assert g.tool("get_order").action_class == "read"
    # the compiled policy discharges the confirmation property
    r = prove(g, no_tool_without_confirmation("issue_refund"))
    assert r.status == "proven"
    # and a denied tool is unreachable
    r2 = prove(g, no_tool_without_confirmation("delete_account"))
    assert r2.status == "proven"


def test_removing_the_approval_rule_reintroduces_the_hole():
    policy = EnforcementPolicy(policy_id="p2", agent_id="a1", rules=[
        Rule(rule_id="r1", lane="lane1", action="allow",
             matcher={"tool": "issue_refund"}),
        Rule(rule_id="r2", lane="lane1", action="allow",
             matcher={"tool": "get_order"}),
    ])
    g = from_enforcement_policy(policy, confirmable=["issue_refund"])
    r = prove(g, no_tool_without_confirmation("issue_refund"))
    assert r.status == "counterexample"
