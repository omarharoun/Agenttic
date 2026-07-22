"""SPEC-9 Step 39 — archetype taxonomy + seed cores acceptance tests."""

from __future__ import annotations

import pytest

from agenttic.rubric_engine.cores import SEED_ARCHETYPES, SEED_CORES
from agenttic.rubric_engine.taxonomy import resolve_core, validate_seed_taxonomy
from agenttic.schema.archetype import SUITE_FEATURES
from agenttic.schema.rubric import Rubric

SEED_IDS = {
    "conversational_transactional", "retrieval_qa", "coding",
    "research_analysis", "workflow_automation", "decision_support",
}


def test_six_seed_archetypes_load_with_valid_cores():
    # the six seed archetypes are all present (support_triage is a child on top)
    assert SEED_IDS <= set(SEED_ARCHETYPES)
    for aid in SEED_IDS:
        arch = SEED_ARCHETYPES[aid]
        core = SEED_CORES[arch.core_rubric_id]
        assert isinstance(core, Rubric)
        assert core.criteria, f"{aid} core has no criteria"
        # weights default-filled to 1.0 for every criterion (schema invariant)
        assert set(core.weights) == {c.criterion_id for c in core.criteria}
        # features are drawn from the shared vocabulary
        assert set(arch.required_suite_features) <= set(SUITE_FEATURES)
        # a MAST-style failure catalogue is present
        assert arch.failure_modes


def test_seed_taxonomy_validates():
    # every archetype resolves, every core is a valid rubric, parents exist
    validate_seed_taxonomy()


def test_cores_pass_schema_integrity():
    # judge criteria carry pass/fail anchors (Hard Rule 2); code criteria carry
    # a check_ref; scales are binary/three_point only (Hard Rule 3).
    from agenttic.scoring.checks import CHECKS
    for core in SEED_CORES.values():
        for c in core.criteria:
            assert c.scale in ("binary", "three_point")
            if c.scorer == "judge":
                assert "pass" in c.anchors and "fail" in c.anchors
            if c.scorer == "code":
                assert c.check_ref in CHECKS, f"unknown check_ref {c.check_ref}"


def test_every_core_criterion_has_provenance():
    for arch in SEED_ARCHETYPES.values():
        core = SEED_CORES[arch.core_rubric_id]
        for c in core.criteria:
            assert "prov:authored" in c.tags
            assert any(t.startswith("arch:") for t in c.tags)


def test_support_triage_reparented_under_conversational_transactional():
    st = SEED_ARCHETYPES["support_triage"]
    assert st.parent_id == "conversational_transactional"

    resolved, record = resolve_core("support_triage")
    # inheritance: child core = parent criteria ∪ own
    parent_core = SEED_CORES["core-conversational_transactional-v1"]
    parent_ids = {c.criterion_id for c in parent_core.criteria}
    own_ids = {c.criterion_id for c in SEED_CORES["core-support_triage-v1"].criteria}
    resolved_ids = {c.criterion_id for c in resolved.criteria}
    assert parent_ids <= resolved_ids            # parent criteria inherited
    assert own_ids <= resolved_ids               # own criteria present
    # lineage recorded root-to-leaf
    assert record.lineage == ["conversational_transactional", "support_triage"]


def test_child_wins_conflict_is_recorded():
    # ct_follows_policy exists in BOTH the parent core and the triage core.
    # After resolution the child's version wins and the override is recorded.
    resolved, record = resolve_core("support_triage")
    assert "ct_follows_policy" in record.overridden
    assert record.criterion_source["ct_follows_policy"] == "support_triage"
    # the winning criterion is the triage-specialized description
    crit = next(c for c in resolved.criteria if c.criterion_id == "ct_follows_policy")
    assert "triage" in crit.description.lower()
    # provenance of the specialization points at support_triage
    assert "arch:support_triage" in crit.tags


def test_resolved_features_are_union_of_lineage():
    _, record = resolve_core("support_triage")
    ct_feats = set(SEED_ARCHETYPES["conversational_transactional"].required_suite_features)
    st_feats = set(SEED_ARCHETYPES["support_triage"].required_suite_features)
    assert set(record.required_suite_features) == ct_feats | st_feats


def test_root_archetype_resolves_to_its_own_core():
    resolved, record = resolve_core("coding")
    assert record.lineage == ["coding"]
    assert not record.overridden
    own = SEED_CORES["core-coding-v1"]
    assert {c.criterion_id for c in resolved.criteria} == {
        c.criterion_id for c in own.criteria}


def test_unknown_archetype_raises():
    with pytest.raises(ValueError):
        resolve_core("does_not_exist")
