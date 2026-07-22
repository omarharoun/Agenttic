"""SPEC-9 Step 43 — the compounding library acceptance tests."""

from __future__ import annotations

from agenttic.rubric_engine.discrimination import (
    CriterionDiscrimination, DiscriminationResult)
from agenttic.rubric_engine.library import RubricLibrary
from agenttic.schema.rubric import Criterion, Rubric


def _disc(per_criterion) -> DiscriminationResult:
    return DiscriminationResult(
        members=[], ranking_correct=True, ends_separated=True, strong_id="s",
        null_id="n", per_criterion=per_criterion,
        non_discriminating=[c.criterion_id for c in per_criterion if not c.discriminates],
        passes_gate=True, reason="ok", k=4)


def test_seeds_from_authored_cores_with_provenance():
    lib = RubricLibrary()
    lc = lib.criterion("ct_follows_policy")
    assert lc is not None
    assert lc.provenance.startswith("authored:")
    assert lib.core_version("core-coding-v1") == 1


def test_mined_discriminating_criterion_proposed_and_gated_into_core():
    lib = RubricLibrary()
    new = Criterion(criterion_id="ct_quotes_sla", description="quotes the SLA",
                    scorer="code", scale="binary", check_ref="keyword_containment")
    result = _disc([CriterionDiscrimination("ct_quotes_sla", 0.6, True,
                                            {"strong": 0.9, "null": 0.3})])
    proposals = lib.propose_from_engagement(
        "conversational_transactional", result, engagement="acme-2026",
        draft_criteria=[new])
    assert len(proposals) == 1
    p = proposals[0]
    assert p.provenance == "mined:acme-2026"
    assert p.approved is False                    # human-gated, not auto-merged

    before = lib.core_version("core-conversational_transactional-v1")
    core = lib.approve(p)                          # explicit human approval
    assert isinstance(core, Rubric)
    assert core.version == before + 1              # new core version
    assert "ct_quotes_sla" in {c.criterion_id for c in core.criteria}
    lc = lib.criterion("ct_quotes_sla")
    assert lc.provenance == "mined:acme-2026"      # provenance recorded


def test_only_stable_discriminating_criteria_are_mined():
    lib = RubricLibrary()
    weak = Criterion(criterion_id="ct_weak", description="x", scorer="code",
                     scale="binary", check_ref="keyword_containment")
    # spread below min_spread -> not proposed
    result = _disc([CriterionDiscrimination("ct_weak", 0.05, True, {})])
    proposals = lib.propose_from_engagement(
        "conversational_transactional", result, engagement="e",
        draft_criteria=[weak], min_spread=0.2)
    assert proposals == []


def test_imported_benchmark_registers_as_exemplar_and_seeds_panel():
    class FakeBench:
        def rubric(self):
            return Rubric(rubric_id="bench-r", version=1, criteria=[
                Criterion(criterion_id="bench_tool_ok", description="tool ok",
                          scorer="code", scale="binary",
                          check_ref="tool_selection_accuracy")])

        def load_records(self, full=False):
            from agenttic.schema.testcase import TestCase
            return [TestCase(test_id=f"b{i}", suite_id="bench",
                             task_description="t", rubric_id="bench-r")
                    for i in range(5)]

    lib = RubricLibrary()
    ex = lib.register_exemplar("bfcl", FakeBench())
    assert ex.archetype_id == "workflow_automation"     # mapped
    assert ex.seed_case_count == 5
    # criteria enrich the core (as a human-gated proposal)
    assert any(p.criterion and p.criterion.criterion_id == "bench_tool_ok"
               and p.provenance == "imported:bfcl" for p in lib.proposals)
    # tasks seed a reference panel for the archetype
    assert len(lib.reference_panel_seed("workflow_automation")) == 5


def test_track_record_and_retire_below_floor():
    lib = RubricLibrary()
    cid = "ct_professional_tone"
    for _ in range(4):
        lib.record_discrimination(cid, 0.01)        # consistently non-discriminating
    lib.record_discrimination("ct_follows_policy", 0.6)
    lc = lib.criterion(cid)
    assert lc.observations == 4
    assert lc.mean_discrimination < 0.05
    retire = lib.retire_candidates(floor=0.05, min_n=3)
    assert cid in retire                            # stopped discriminating -> retire
    assert "ct_follows_policy" not in retire        # too few obs + still discriminates


def test_ingest_discrimination_result_folds_all_criteria():
    lib = RubricLibrary()
    result = _disc([
        CriterionDiscrimination("ct_follows_policy", 0.3, True, {}),
        CriterionDiscrimination("ct_task_resolved", 0.4, True, {})])
    lib.ingest_discrimination_result(result)
    assert lib.criterion("ct_follows_policy").track_record == [0.3]
    assert lib.criterion("ct_task_resolved").track_record == [0.4]


def test_recurring_custom_agents_surface_a_new_archetype():
    lib = RubricLibrary()
    descriptions = [
        "An agent that negotiates a contract clause with a counterparty.",
        "A contract negotiation agent that proposes clause edits.",
        "Negotiates contract terms and redlines clauses for the user.",
        "A weather chatbot.",
    ]
    proposals = lib.cluster_custom(descriptions, min_cluster=3)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind == "new_archetype"
    assert "contract" in p.signals or "negotiat" in " ".join(p.signals)
    # promoting it registers a usable archetype
    arch = lib.approve(p)
    assert arch.archetype_id in lib.archetypes
