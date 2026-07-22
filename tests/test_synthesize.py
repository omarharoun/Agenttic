"""SPEC-9 Step 41 — synthesis (core + domain delta + matched suite) tests."""

from __future__ import annotations

from agenttic.rubric_engine.classify import ArchetypeMatch
from agenttic.rubric_engine.synthesize import synthesize, synthesize_suite
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase


class FakeGenerator:
    """Mimics BenchmarkGenerator's define_criteria / generate_cases contract
    without any LLM call."""

    def define_criteria(self, task: dict, rubric_id: str) -> Rubric:
        return Rubric(
            rubric_id=rubric_id, version=1,
            criteria=[
                # a genuine domain-specific NEW criterion (check not in any core)
                Criterion(criterion_id="dom_return_window",
                          description="Quotes the 30-day return window correctly.",
                          scorer="code", scale="binary",
                          check_ref="keyword_containment", tags=[]),
                Criterion(criterion_id="dom_correct_product_name",
                          description="Uses the exact product name.",
                          scorer="judge", scale="binary",
                          anchors={"pass": "Exact product name.", "fail": "Wrong name."}),
                # a criterion the core ALREADY covers (same check_ref) -> dropped
                Criterion(criterion_id="dom_no_bad_write",
                          description="No unauthorized write (dup of core).",
                          scorer="code", scale="binary",
                          check_ref="forbidden_tool_not_called", tags=[]),
            ],
        )

    def generate_cases(self, task, *, suite_id, rubric) -> list[TestCase]:
        return [TestCase(test_id=f"{suite_id}-hp-{i}", suite_id=suite_id,
                         task_description="happy path", tags=["happy_path"],
                         rubric_id=rubric.rubric_id) for i in range(2)]


CT = [ArchetypeMatch("conversational_transactional", 0.9, "", "keyword")]


def test_transactional_rubric_is_mostly_reused_with_labeled_delta():
    draft = synthesize(CT, "A returns-and-refunds support agent for ACME store.",
                       generator=FakeGenerator())
    # ≥60% reused (core + ethos) by weight; the delta is the small audited part
    assert draft.reuse_ratio >= 0.6
    # delta present and labeled generated-new
    assert "dom_return_window" in draft.delta_criterion_ids
    assert "dom_correct_product_name" in draft.delta_criterion_ids
    for cid in draft.delta_criterion_ids:
        crit = next(c for c in draft.rubric.criteria if c.criterion_id == cid)
        assert "prov:generated-new" in crit.tags
    # the duplicate-of-core generated criterion was dropped (not double-scored)
    assert "dom_no_bad_write" not in draft.delta_criterion_ids
    # provenance is complete
    assert set(draft.provenance) == {c.criterion_id for c in draft.rubric.criteria}


def test_ethos_attaches_only_applicable_criteria():
    draft = synthesize(CT, "returns agent", generator=None)
    ids = set(draft.ethos_criterion_ids)
    # CT features include should_escalate + pressure_case but NOT fairness_pair
    assert "ethos_escalates_appropriately" in ids     # should_escalate present
    assert "ethos_injection_robust" in ids            # pressure/multi-turn present
    assert "ethos_fairness_consistent" not in ids     # no fairness_pair feature
    # universal ethos always attaches
    assert {"ethos_refuses_harm", "ethos_no_deception",
            "ethos_no_secret_leak"} <= ids


def test_fairness_ethos_attaches_when_feature_present():
    # a decision_support agent whose synthesis we force to include a fairness pair
    from agenttic.rubric_engine.cores import SEED_ARCHETYPES
    import copy
    arch = copy.deepcopy(SEED_ARCHETYPES)
    arch["decision_support"].required_suite_features.append("fairness_pair")
    draft = synthesize([ArchetypeMatch("decision_support", 0.9, "", "keyword")],
                       archetypes=arch)
    assert "ethos_fairness_consistent" in draft.ethos_criterion_ids


def test_no_generator_means_pure_reuse():
    draft = synthesize(CT)
    assert draft.reuse_ratio == 1.0
    assert draft.delta_criterion_ids == []


def test_synthesis_emits_features_and_suite_is_matched_end_to_end():
    gen = FakeGenerator()
    draft = synthesize(CT, "ACME returns agent", generator=gen)
    # synthesis emitted the archetype's required features
    assert set(draft.required_suite_features) >= {
        "policy_doc", "should_escalate", "unauthorized_write", "pressure_case"}
    suite, cases = synthesize_suite(
        draft, suite_id="suite-acme", business_context="ACME returns agent",
        generator=gen)
    # every required feature is exercised by at least one case (Hard Rule 41)
    feature_tags = {t.split(":", 1)[1] for c in cases for t in c.tags
                    if t.startswith("feature:")}
    assert set(draft.required_suite_features) <= feature_tags
    # suite ships UNAPPROVED (Step 8 human gate) and test_ids match the cases
    assert suite.approved is False
    assert set(suite.test_ids) == {c.test_id for c in cases}


def test_hybrid_composes_two_cores_deduped():
    matches = [ArchetypeMatch("research_analysis", 0.7, "", "keyword"),
               ArchetypeMatch("conversational_transactional", 0.65, "", "keyword")]
    draft = synthesize(matches)
    assert set(draft.archetype_ids) == {"research_analysis",
                                        "conversational_transactional"}
    # union of both cores' criteria, de-duplicated by id
    ids = [c.criterion_id for c in draft.rubric.criteria]
    assert len(ids) == len(set(ids))
    # features unioned across both archetypes
    assert {"retrieval_corpus", "policy_doc"} <= set(draft.required_suite_features)


def test_custom_match_yields_empty_core():
    from agenttic.rubric_engine.classify import ArchetypeMatch as AM
    draft = synthesize([AM("custom", 0.2, "", "keyword")])
    assert draft.core_criterion_ids == []
    assert draft.archetype_ids == []
