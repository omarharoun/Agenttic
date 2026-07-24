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
