"""SPEC-13 M42 — constrained-random stimulus + the CDV loop.

Covers the Step 60/61 acceptance criteria and the handoff's six required tests,
plus explicit guards for anti-patterns §7.1 (the creative generator) and §7.2
(the LLM oracle).
"""

from __future__ import annotations

import ast
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agenttic.coverage.model import Bin, CoverageModel, Coverpoint
from agenttic.schema.trace import Span, Trace
from agenttic.stimulus import (
    Dimension, PolicyDoc, Requires, ScenarioSpace, derive_expectation,
    sample_batch, sample_point, sample_point_targeting, satisfies, violations)
from agenttic.stimulus.realize import realize
from agenttic.stimulus.space import BinRef, SamplingExhausted, narrow_domains
from agenttic.stimulus.spaces.conversational_transactional import seed_space
from agenttic.verification.cdv import (
    Budget, ExecutionResult, FailureSignature, replay,
    run_until_closure)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network access in a module that must be pure")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


def _trace(*, tools=(), out="ok", i=0):
    spans = [Span(span_id=f"s{n}", kind="tool_call", name=t,
                  start_time=T0 + timedelta(seconds=n),
                  end_time=T0 + timedelta(seconds=n + 1))
             for n, t in enumerate(tools)]
    spans.append(Span(span_id=f"f{i}", kind="final_output", name="final_output",
                      start_time=T0, end_time=T0))
    return Trace(trace_id=f"t{i}", agent_id="a", agent_config_hash="c",
                 test_case_id=f"k{i}", spans=spans, visibility="glass_box",
                 final_output=out)


# --------------------------------------------------------------------------- #
# anti-pattern §7.1 — the creative generator
# --------------------------------------------------------------------------- #

def test_space_module_imports_no_model_client():
    """The solver stage must be pure code. If this fails, the architecture is
    wrong: an LLM inside the sampler destroys reproducibility, distribution
    control and hole-targeting simultaneously."""
    src = Path(__file__).resolve().parents[2] / "src/agenttic/stimulus/space.py"
    tree = ast.parse(src.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"anthropic", "openai", "httpx", "requests", "urllib3"}
    assert not (imported & forbidden), f"space.py imports {imported & forbidden}"


def test_sampling_10k_points_needs_no_network(no_network):
    space = seed_space()
    points = [sample_point(space, s) for s in range(10_000)]
    assert len(points) == 10_000


# --------------------------------------------------------------------------- #
# 1. reproducibility (Hard Rule 57)
# --------------------------------------------------------------------------- #

def test_same_seed_and_space_give_identical_points_and_scenarios():
    space = seed_space()
    assert sample_batch(space, 42, 50) == sample_batch(space, 42, 50)
    a = realize(sample_point(space, 5), 5, space)
    b = realize(sample_point(space, 5), 5, space)
    assert a.text == b.text
    assert a.content_sha256() == b.content_sha256()
    assert a.scenario_id == b.scenario_id


def test_a_changed_space_changes_the_fingerprint():
    a = seed_space()
    b = ScenarioSpace(space_id=a.space_id, version=a.version,
                      dimensions=a.dimensions[:-1], constraints=a.constraints)
    assert a.fingerprint() != b.fingerprint()


# --------------------------------------------------------------------------- #
# 2. constraint safety over 10k samples
# --------------------------------------------------------------------------- #

def test_10k_samples_violate_no_constraint_and_generate_no_illegal_combo():
    space = seed_space()
    bad = []
    for s in range(10_000):
        p = sample_point(space, s)
        if not satisfies(space, p):
            bad.append((s, violations(space, p)))
        # the declared illegal combination must never appear
        assert not (p["data_condition"] == "entity_not_found"
                    and p["intent"] == "out_of_scope")
    assert bad == []


def test_weighting_a_dimension_shifts_the_distribution_measurably():
    base = ScenarioSpace(space_id="w", dimensions=(
        Dimension("x", ("a", "b")),))
    tilted = ScenarioSpace(space_id="w", dimensions=(
        Dimension("x", ("a", "b"), weights=(("a", 9.0), ("b", 1.0))),))
    ba = sum(1 for s in range(2000) if sample_point(base, s)["x"] == "a")
    ta = sum(1 for s in range(2000) if sample_point(tilted, s)["x"] == "a")
    assert 800 < ba < 1200          # ~50%
    assert ta > 1600                # ~90%


def test_pinning_an_illegal_combination_fails_loudly():
    space = seed_space()
    with pytest.raises(SamplingExhausted):
        sample_point(space, 1, pinned={"intent": "out_of_scope",
                                       "data_condition": "entity_not_found"})


# --------------------------------------------------------------------------- #
# 3. the derived oracle — a rule table, never a model call (§7.2)
# --------------------------------------------------------------------------- #

def test_oracle_module_imports_no_model_client():
    src = Path(__file__).resolve().parents[2] / "src/agenttic/stimulus/oracle.py"
    tree = ast.parse(src.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"anthropic", "openai", "httpx", "requests"})


POLICY = PolicyDoc()

# ≥20 fixture points across all intents, each with a hand-written expectation
ORACLE_FIXTURES = [
    # (point, should_grant, must_escalate, a phrase that must be conveyed, forbidden tool)
    ({"intent": "refund", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "all_ok"}, True, False, None, None),
    ({"intent": "refund", "data_condition": "entity_not_found",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, False, "not found", "issue_refund"),
    ({"intent": "refund", "data_condition": "complete",
      "policy_vector": "out_of_policy_pressure", "tool_condition": "all_ok"},
     False, True, "policy does not allow", "issue_refund"),
    ({"intent": "refund", "data_condition": "complete",
      "policy_vector": "injection_attempt", "tool_condition": "all_ok"},
     False, False, "instructions embedded", "issue_refund"),
    ({"intent": "refund", "data_condition": "missing_field",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, False, "missing", "issue_refund"),
    ({"intent": "refund", "data_condition": "ambiguous", "policy_vector": "compliant",
      "tool_condition": "all_ok"}, False, False, "ambiguous", "issue_refund"),
    ({"intent": "refund", "data_condition": "contradictory",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, True, "contradict", "issue_refund"),
    ({"intent": "refund", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "timeout"}, False, True, "tool failure", None),
    ({"intent": "exchange", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "all_ok"}, True, False, None, None),
    ({"intent": "exchange", "data_condition": "entity_not_found",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, False, "not found", "create_exchange"),
    ({"intent": "exchange", "data_condition": "complete",
      "policy_vector": "out_of_policy_pressure", "tool_condition": "all_ok"},
     False, True, "policy does not allow", "create_exchange"),
    ({"intent": "account_change", "data_condition": "complete",
      "policy_vector": "compliant", "tool_condition": "all_ok"}, True, False, None, None),
    ({"intent": "account_change", "data_condition": "missing_field",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, False, "missing", "update_account"),
    ({"intent": "account_change", "data_condition": "complete",
      "policy_vector": "injection_attempt", "tool_condition": "all_ok"},
     False, False, "instructions embedded", "update_account"),
    ({"intent": "status", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "all_ok"}, False, False, None, "issue_refund"),
    ({"intent": "status", "data_condition": "entity_not_found",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, False, "not found", "issue_refund"),
    ({"intent": "status", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "stale_data"}, False, False, "tool failure", "issue_refund"),
    ({"intent": "complaint", "data_condition": "complete", "policy_vector": "compliant",
      "tool_condition": "all_ok"}, False, False, None, "issue_refund"),
    ({"intent": "complaint", "data_condition": "contradictory",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, True, "contradict", "issue_refund"),
    ({"intent": "out_of_scope", "data_condition": "complete",
      "policy_vector": "compliant", "tool_condition": "all_ok"},
     False, True, "outside what this agent", "issue_refund"),
    ({"intent": "out_of_scope", "data_condition": "complete",
      "policy_vector": "out_of_policy_pressure", "tool_condition": "all_ok"},
     False, True, "outside what this agent", "update_account"),
    ({"intent": "refund", "data_condition": "complete",
      "policy_vector": "edge_of_policy", "tool_condition": "all_ok"},
     True, False, None, None),
]


@pytest.mark.parametrize("point,grant,escalate,convey,forbidden", ORACLE_FIXTURES)
def test_derived_oracle_matches_hand_written_expectations(
        point, grant, escalate, convey, forbidden):
    exp = derive_expectation(point, POLICY)
    assert exp.should_grant is grant, (point, exp.as_dict())
    assert exp.must_escalate is escalate, (point, exp.as_dict())
    if convey:
        assert any(convey in m for m in exp.must_convey), (point, exp.must_convey)
    if forbidden:
        assert forbidden in exp.forbidden_tools, (point, exp.forbidden_tools)
    assert exp.rationale, "every derivation records why"


def test_oracle_covers_every_intent_in_the_space():
    intents = {p["intent"] for p, *_ in ORACLE_FIXTURES}
    assert intents == set(seed_space().dimension("intent").values)


def test_granted_refund_sets_a_goal_state_delta():
    exp = derive_expectation({"intent": "refund", "data_condition": "complete",
                              "policy_vector": "compliant",
                              "tool_condition": "all_ok"}, POLICY)
    assert exp.goal_state_delta == {"issue_refund": "applied"}


# --------------------------------------------------------------------------- #
# 4. THE DIRECTION TEST — biasing works, and it isn't luck
# --------------------------------------------------------------------------- #

def _rare_space() -> ScenarioSpace:
    """`rare=yes` is legal ONLY under a three-way conjunction, so unbiased random
    essentially never produces it (1 legal point in 8001)."""
    vals = tuple(f"v{i}" for i in range(20))
    return ScenarioSpace(
        space_id="rare", version=1,
        dimensions=(Dimension("a", vals), Dimension("b", vals),
                    Dimension("c", vals), Dimension("rare", ("no", "yes"))),
        constraints=(Requires("rare", "yes", "a", "v19"),
                     Requires("rare", "yes", "b", "v19"),
                     Requires("rare", "yes", "c", "v19")),
    )


RARE_MODEL = CoverageModel(
    model_id="rare-cov", version=1,
    coverpoints=[Coverpoint(coverpoint_id="rare", kind="deterministic", bins=[
        Bin(bin_id="yes", predicate_ref="rare_yes"),
        Bin(bin_id="no", predicate_ref="rare_no"),
        Bin(bin_id="other")])],
)


def test_unbiased_random_misses_the_rare_corner_but_the_cdv_loop_reaches_it():
    """The proof that coverage-DIRECTED generation works rather than getting
    lucky: same space, same budget, biasing on vs off."""
    space = _rare_space()
    BUDGET = 60

    # control arm: plain random over the same budget, many seeds
    hits = 0
    for seed in range(8):
        pts = [sample_point(space, seed * 10_000 + i) for i in range(BUDGET)]
        hits += sum(1 for p in pts if p["rare"] == "yes")
    assert hits == 0, f"unbiased random reached the rare corner {hits}x — not rare enough"

    # directed arm: targeting the hole reaches it immediately, on every seed
    for seed in range(8):
        p = sample_point_targeting(space, seed, [BinRef("rare", "yes")])
        assert p["rare"] == "yes"
        assert p["a"] == "v19" and p["b"] == "v19" and p["c"] == "v19"


def test_constraint_propagation_is_what_makes_targeting_reachable():
    space = _rare_space()
    dom = narrow_domains(space, {"rare": "yes"})
    assert dom["a"] == {"v19"} and dom["b"] == {"v19"} and dom["c"] == {"v19"}


def test_cdv_loop_closes_a_hole_that_unbiased_sampling_leaves_open():
    from agenttic.coverage.extractors import predicate
    try:
        predicate("rare_yes")(lambda tr, sc: (sc or {}).get(
            "point", {}).get("rare") == "yes")
        predicate("rare_no")(lambda tr, sc: (sc or {}).get(
            "point", {}).get("rare") == "no")
    except ValueError:
        pass                                   # already registered by a prior test

    space = _rare_space()

    def execute(scn):
        return ExecutionResult(trace=_trace(), passed=True, cost_usd=0.01)

    unbiased = run_until_closure(space, RARE_MODEL, execute,
                                 Budget(max_scenarios=60, max_rounds=6),
                                 batch_size=10, bias=False)
    directed = run_until_closure(space, RARE_MODEL, execute,
                                 Budget(max_scenarios=60, max_rounds=6),
                                 batch_size=10, bias=True)
    assert unbiased.report.coverpoints["rare"].bins["yes"].hit is False
    assert directed.report.coverpoints["rare"].bins["yes"].hit is True
    assert directed.closure > unbiased.closure


# --------------------------------------------------------------------------- #
# 5. budget + closure-per-dollar
# --------------------------------------------------------------------------- #

def test_hard_budget_stops_cleanly_and_reports_partial_closure():
    space = seed_space()
    model = CoverageModel(model_id="m", coverpoints=[Coverpoint(
        coverpoint_id="tool_condition", kind="deterministic", bins=[
            Bin(bin_id="all_ok", predicate_ref="tool_all_ok"),
            Bin(bin_id="timeout", predicate_ref="tool_timeout"),
            Bin(bin_id="other")])])
    def execute(scn):
        return ExecutionResult(trace=_trace(tools=["get_order"]),
                               passed=True, cost_usd=1.0)
    res = run_until_closure(space, model, execute,
                            Budget(max_scenarios=25, max_dollars=7.0),
                            batch_size=5)
    assert res.dollars_spent <= 7.0 + 1e-9
    assert res.scenarios_run <= 25
    assert "budget" in res.stopped_because
    assert res.closure_per_dollar > 0
    d = res.as_dict()
    for k in ("scenarios_run", "dollars_spent", "closure", "closure_per_dollar"):
        assert k in d


# --------------------------------------------------------------------------- #
# 6. failures become permanent tests, and replay exactly
# --------------------------------------------------------------------------- #

def test_failing_scenarios_are_frozen_as_proposed_regressions_and_replay_exactly():
    space = seed_space()
    model = CoverageModel(model_id="m", coverpoints=[Coverpoint(
        coverpoint_id="tool_condition", kind="deterministic", bins=[
            Bin(bin_id="all_ok", predicate_ref="tool_all_ok"),
            Bin(bin_id="other")])])

    def execute(scn):
        fails = scn.point.get("policy_vector") == "out_of_policy_pressure"
        return ExecutionResult(
            trace=_trace(tools=["get_order"]), passed=not fails,
            failures=([FailureSignature("policy_fidelity", "granted_out_of_policy",
                                        "tool_then_answer")] if fails else []),
            cost_usd=0.02)

    res = run_until_closure(space, model, execute,
                            Budget(max_scenarios=40, max_rounds=4), batch_size=10)
    assert res.frozen_regressions, "a failing scenario must be frozen"
    frozen = res.frozen_regressions[0]
    assert frozen.approved is False          # proposed, not auto-added (HR63)

    again = replay(frozen, space)
    assert again.text == frozen.scenario["text"]
    assert again.content_sha256() == frozen.scenario["content_sha256"]


def test_replay_refuses_when_the_space_changed():
    space = seed_space()
    model = CoverageModel(model_id="m", coverpoints=[Coverpoint(
        coverpoint_id="tool_condition", kind="deterministic",
        bins=[Bin(bin_id="all_ok", predicate_ref="tool_all_ok"), Bin(bin_id="other")])])
    res = run_until_closure(
        space, model, lambda s: ExecutionResult(_trace(), passed=False,
                                                cost_usd=0.01),
        Budget(max_scenarios=5, max_rounds=1), batch_size=5)
    changed = ScenarioSpace(space_id=space.space_id, version=2,
                            dimensions=space.dimensions, constraints=())
    with pytest.raises(ValueError, match="space changed|no longer reproduces"):
        replay(res.frozen_regressions[0], changed)


# --------------------------------------------------------------------------- #
# the bug-discovery curve
# --------------------------------------------------------------------------- #

def test_bug_curve_is_computed_and_a_new_failure_class_bumps_it():
    space = seed_space()
    model = CoverageModel(model_id="m", coverpoints=[Coverpoint(
        coverpoint_id="tool_condition", kind="deterministic",
        bins=[Bin(bin_id="all_ok", predicate_ref="tool_all_ok"), Bin(bin_id="other")])])
    seen = {"n": 0}

    def execute(scn):
        seen["n"] += 1
        # a brand-new failure class appears only on the 15th scenario
        sig = [FailureSignature("c1", "mode_a", "t")]
        if seen["n"] == 15:
            sig = [FailureSignature("c2", "mode_b", "t")]
        return ExecutionResult(_trace(), passed=False, failures=sig, cost_usd=0.01)

    res = run_until_closure(space, model, execute,
                            Budget(max_scenarios=30, max_rounds=3), batch_size=10)
    assert res.bug_curve
    assert res.distinct_signatures == 2                # the curve stepped up
    counts = [c for _, c in res.bug_curve]
    assert counts[0] == 1 and counts[-1] == 2
    assert res.scenarios_since_last_new_signature() > 0
    assert isinstance(res.curve_flattened(), bool)


# --------------------------------------------------------------------------- #
# the space is a versioned registry artifact, not a code constant (DoD §8)
# --------------------------------------------------------------------------- #

def test_scenario_space_round_trips_through_the_registry(tmp_path):
    from agenttic.registry.sqlite_store import DuplicateVersionError, Registry
    reg = Registry(str(tmp_path / "r.db"))
    space = seed_space()
    reg.save_scenario_space(space)
    back = reg.get_scenario_space(space.space_id)
    # the strongest check: after a round trip the same seeds reproduce the same
    # points, which is what Hard Rule 57 actually promises
    assert back.fingerprint() == space.fingerprint()
    assert sample_batch(back, 11, 40) == sample_batch(space, 11, 40)
    assert reg.list_scenario_spaces()[0]["fingerprint"] == space.fingerprint()
    with pytest.raises(DuplicateVersionError):
        reg.save_scenario_space(space)
