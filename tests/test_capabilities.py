"""The verification-surface endpoint must be enumerated from the LIVE registries.

A capability page written as copy drifts from the product within a release and
then it is a claim nobody can verify. These tests pin that the numbers move when
the registries move, and that the surface names its own edges.
"""

from __future__ import annotations

from agenttic.server.routes.capabilities import capabilities


def test_counts_come_from_the_live_registries():
    from agenttic.coverage.models.baseline import baseline_model
    from agenttic.rubric_engine.cores import SEED_ARCHETYPES
    from agenttic.scoring.checks import CHECKS
    from agenttic.verification.assertions import ASSERTIONS
    from agenttic.verification.formal import SHIPPED

    c = capabilities()
    assert c["deterministic_checks"]["total"] == len(CHECKS)
    assert c["assertions"]["total"] == len(ASSERTIONS)
    assert c["formal"]["total"] == len(SHIPPED)
    assert c["archetypes"]["total"] == len(SEED_ARCHETYPES)
    assert ([cp["id"] for cp in c["coverage"]["baseline"]["coverpoints"]]
            == [cp.coverpoint_id for cp in baseline_model().coverpoints])


def test_registering_a_check_changes_the_reported_total():
    """The page cannot go stale: it is computed per request."""
    from agenttic.scoring.checks import CHECKS
    before = capabilities()["deterministic_checks"]["total"]
    CHECKS["__probe_check__"] = lambda t, tc: 1.0
    try:
        assert capabilities()["deterministic_checks"]["total"] == before + 1
    finally:
        CHECKS.pop("__probe_check__", None)
    assert capabilities()["deterministic_checks"]["total"] == before


def test_every_assertion_reports_its_severity_and_property_text():
    for a in capabilities()["assertions"]["items"]:
        assert a["severity"] in ("critical", "high", "standard")
        assert a["property"] and a["id"]


def test_formal_surface_keeps_its_scope_and_four_values():
    f = capabilities()["formal"]
    assert set(f["result_values"]) == {"proven", "counterexample", "unbounded",
                                       "not_attempted"}
    assert "guard layer" in f["scope"] or "authorization" in f["scope"]
    assert "not the model" in f["limit"]


def test_semantic_coverpoints_are_declared_provisional():
    fitted = capabilities()["coverage"]["fitted_example"]
    assert set(fitted["provisional"]) == {"intent", "emotional_register",
                                          "policy_vector"}


def test_the_surface_names_what_it_does_not_cover():
    c = capabilities()
    joined = " ".join(c["not_covered"]).lower()
    assert c["not_covered"], "an honest surface names its edges"
    assert "model" in joined                     # we do not verify the weights
    # memory is now certified (SPEC-12 Step 57) — so the edge it names is the
    # boundary of that battery, not its absence.
    assert "memory" in joined
    assert c["supply_chain"]["memory"]["implemented"] is True


def test_the_surface_names_the_absent_environment_user_and_sessions():
    """The three edges that are properties of the HARNESS, not of a registry.

    Everything else on this page is enumerated, so a missing registry entry is
    self-correcting. These are not: a case is one input dict handed to the agent
    once, and no amount of registering checks changes that. Left undisclosed,
    the page describes the measuring instrument and lets a reader assume the
    experiment. If a harness ever makes one of these false, delete the entry —
    do not soften it."""
    nc = capabilities()["not_covered"]

    def entry(*required: str) -> str:
        hits = [e for e in nc if all(w in e.lower() for w in required)]
        assert len(hits) == 1, f"expected exactly one entry naming {required}: {hits}"
        return hits[0]

    # no environment and no fault injection — nothing here makes a tool misbehave
    env = entry("simulated environment", "fault injection")
    assert "time out" in env

    # no counterparty — the agent's first reply ends the case
    user = entry("simulated user")
    assert "one message" in user and "push back" in user

    # no session is ever resumed by the harness
    sessions = entry("resumed sessions")
    assert "empty context" in sessions


def test_memory_and_catalog_capabilities_come_from_the_live_registries():
    """The surface must enumerate the battery, not restate it — adding a memory
    check has to move this endpoint without anyone editing it."""
    from agenttic.certification.catalog import EntryStatus
    from agenttic.certification.memory_suite import MEMORY_CHECKS

    sc = capabilities()["supply_chain"]
    assert sc["memory"]["checks"] == [c["id"] for c in MEMORY_CHECKS]
    assert "principal_isolation" in sc["memory"]["checks"]

    from typing import get_args
    assert set(sc["catalog"]["statuses"]) == set(get_args(EntryStatus))
    assert any("named approver" in g for g in sc["catalog"]["promotion_gates"])


def test_the_transcribed_batteries_still_match_the_modules_that_declare_them():
    """The MCP and toolset check lists are the only names on this page that are
    hand-written — those batteries emit their ``check_id`` inline instead of
    declaring a ``MEMORY_CHECKS``-style tuple, so there is nothing to enumerate.
    Read the ids straight out of the source and pin the pair, so a check added or
    renamed in the battery breaks here instead of leaving a stale public claim.
    Static on purpose: it needs no server, and the drift being caught is in the
    declarations, not in a run."""
    import ast
    from pathlib import Path

    import agenttic.certification as cert

    def declared(module: str) -> set[str]:
        tree = ast.parse((Path(cert.__file__).parent / f"{module}.py").read_text())
        return {n.args[0].value for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "CheckOutcome"
                and n.args and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)}

    sc = capabilities()["supply_chain"]
    for key, module in (("mcp_server", "mcp_suite"), ("tools", "tool_suite")):
        # the page annotates a check with a parenthetical gloss; the id is the head
        published = {c.split(" (")[0] for c in sc[key]["checks"]}
        assert published == declared(module), key
        # and it says outright that this list is not enumerated
        assert sc[key]["enumerated"] is False
        assert sc[key]["declared_in"].startswith("agenttic.certification.")


def test_the_declared_memory_battery_is_the_one_that_actually_runs():
    """Pins the capability registry to the report: a check added to
    certify_memory without a MEMORY_CHECKS entry (or vice versa) fails here
    rather than leaving a stale public claim."""
    from agenttic.camp.memory import ReferenceMemoryStore
    from agenttic.certification.memory_suite import MEMORY_CHECKS, certify_memory

    rep = certify_memory(ReferenceMemoryStore(capacity=32), declared_capacity=32)
    assert [o.check_id for o in rep.outcomes] == [c["id"] for c in MEMORY_CHECKS]


def test_no_unbounded_safety_claim_anywhere_in_the_surface():
    import json

    from agenttic.schema.attestation import BANNED_CLAIMS
    blob = json.dumps(capabilities()).lower()
    for claim in BANNED_CLAIMS:
        assert claim not in blob, f"capability surface asserts {claim!r}"


def test_endpoint_is_served():
    import yaml
    from pathlib import Path

    from fastapi.testclient import TestClient

    from agenttic.server.app import create_app
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    r = TestClient(create_app(cfg)).get("/api/capabilities")
    assert r.status_code == 200
    assert r.json()["assertions"]["total"] > 0


# --------------------------------------------------------------------------- #
# the projection must not be narrower than the model
# --------------------------------------------------------------------------- #
# A projection that drops a field does not merely omit it — it publishes the
# default. `session_shape` carries `measurable=False` and a waived bin, and a
# projection of id/kind/provisional/bins/description alone re-advertised it as an
# ordinary measured dimension detecting "multi-turn, or resumed against prior
# memory", in the same response whose `not_covered` says no second turn and no
# resumed session is ever exercised. One payload, two opposite claims. These pin
# the fields, so the endpoint cannot silently drop back to the flattering shape.

def _published() -> dict:
    """Every published coverpoint from both models, keyed model -> id."""
    cov = capabilities()["coverage"]
    return {k: {cp["id"]: cp for cp in cov[k]["coverpoints"]}
            for k in ("baseline", "fitted_example")}


def test_the_projection_reports_every_field_the_model_declares():
    """Enumerated, not transcribed — the same standard as the rest of the page."""
    from agenttic.coverage.models.baseline import baseline_model
    from agenttic.coverage.models.conversational_transactional import seed_model

    pub = _published()
    for key, model in (("baseline", baseline_model()),
                       ("fitted_example", seed_model())):
        for cp in model.coverpoints:
            got = pub[key][cp.coverpoint_id]
            assert got["measurable"] is cp.measurable, (key, cp.coverpoint_id)
            assert got["not_measurable_reason"] == cp.not_measurable_reason
            assert got["counts_toward_closure"] is cp.required
            assert got["waived_bins"] == {
                b.bin_id: b.reason for b in cp.bins if b.waived}


def test_a_dimension_nothing_can_measure_is_published_as_not_measured():
    """The specific over-report: `session_shape` reads human turns and nothing
    emits one, so the surface must say so where it lists the dimension — not only
    in the prose further down the same payload."""
    for key, cps in _published().items():
        ss = cps["session_shape"]
        assert ss["measurable"] is False, key
        assert ss["not_measurable_reason"].strip(), (
            "declaring a dimension unmeasurable requires a named reason, and the "
            "reason is the part a reader can act on")
        assert ss["counts_toward_closure"] is False, (
            "a dimension no producer can feed must not move the headline")


def test_no_dimension_claims_a_turn_the_harness_cannot_deliver():
    """Stated as a rule rather than as a fact about one coverpoint.

    `not_covered` says there is no counterparty and no resumed session. Any
    dimension published as measured while offering a multi-turn or resumed bin
    contradicts that in the same response — whatever it ends up being called."""
    turn_bins = {"multi_turn", "resumed_with_memory"}
    for key, cps in _published().items():
        for cp in cps.values():
            if turn_bins & set(cp["bins"]):
                assert cp["measurable"] is False, (key, cp["id"])


def test_a_waived_bin_is_named_with_its_reason_rather_than_hidden():
    """Hard Rule 61 on the public surface: the bin stays listed (deleting it from
    the projection would be the silent hole) and the waiver is stated beside it."""
    for key, cps in _published().items():
        ss = cps["session_shape"]
        assert "resumed_with_memory" in ss["bins"], key
        assert ss["waived_bins"]["resumed_with_memory"].strip()


def test_the_baseline_limits_string_matches_the_model_it_describes():
    """This string is the only copy that travels with the closure number, so it is
    the one place a stale claim reaches a customer attached to a figure."""
    from agenttic.coverage.models.baseline import baseline_model

    limits = capabilities()["coverage"]["baseline"]["limits"]
    model = baseline_model()
    assert "NOT MEASURED" in limits and "ession shape" in limits
    # it must not advertise a dimension the model does not carry, in either
    # direction: the phrase and the coverpoint travel together
    assert ("agent steps" in limits) is (
        model.coverpoint("agent_steps") is not None)
    assert "risk class" in limits and model.coverpoint("action_risk") is not None
    # and it must not claim the semantic dimensions, which need a fitted model
    assert "does NOT cover intent" in limits


def test_the_turn_blind_check_count_is_read_off_the_registry_not_asserted():
    """The disclosure names a count and the count comes from the live registry,
    so the two cannot drift apart. A hardcoded figure here would rot into a false
    disclosure — the exact failure this page exists to avoid."""
    from agenttic.scoring.checks import CHECKS

    scope = capabilities()["deterministic_checks"]["scope"]
    total = (scope["last_message_only"] + scope["reads_the_whole_trace"]
             + scope["undetermined"])
    assert total == len(CHECKS), (scope, len(CHECKS))
    # the whole reason for the disclosure: this is not a rounding error
    assert scope["last_message_only"] > 0


def test_grading_only_the_last_message_is_disclosed():
    """A check that reads final_output and never the spans grades the last
    message. On a single-turn run that is invisible; on a session it is a hole,
    and the day sessions exist is the day it goes live. It is stated before then,
    not after."""
    nc = " ".join(capabilities()["not_covered"]).lower()
    assert "earlier turns" in nc
    assert "last" in nc and "message" in nc
    # and it points at the machine-readable count rather than restating it
    assert "deterministic_checks.scope" in " ".join(capabilities()["not_covered"])
