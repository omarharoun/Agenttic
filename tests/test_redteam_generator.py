"""Tests for the adversarial attack GENERATOR (agenttic.redteam).

Covers the round-1 sparring loop end-to-end against the deterministic, no-key
demo target: schema-valid probes with filled oracles, only-scoreable probes,
a known-breaking probe kept + a survivor discarded, mutation around winners, the
promote-into-regression-suite path, and the LLM-author fallback (no hard-fail
without credits).
"""

from __future__ import annotations

import pytest

from agenttic.redteam import (
    AttackGenerator,
    LLMRedTeamAuthor,
    NoRedTeamModel,
    Probe,
    TemplateAuthor,
    attack_rubric,
    build_demo_target,
    reference_descriptor,
    run_generation,
)
from agenttic.redteam.probe import ATTACK_RUBRIC_ID, KINDS
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.testcase import TestCase
from agenttic.scoring.checks import CHECKS

# Oracle keys that make a probe deterministically scoreable, per kind.
_SCOREABLE_KEYS = {"secret", "injection_target", "forbidden_tools", "danger_markers"}


@pytest.fixture
def descriptor():
    return reference_descriptor()


@pytest.fixture
def target(descriptor, tmp_path):
    # kb_path is irrelevant to the oracles (a missing key still records the span),
    # but point it at a real file so the tool path doesn't error noisily.
    kb = tmp_path / "kb.json"
    kb.write_text('{"refund_policy": "30 days"}')
    return build_demo_target(descriptor, kb_path=str(kb))


# -- descriptor names the agent's REAL surface ------------------------------- #

def test_reference_descriptor_reads_real_schema(descriptor):
    assert descriptor.agent_id == "anthropic-simple-ref"
    # the REAL tools the reference agent exposes, not invented ones
    assert descriptor.tool_names() == ["calculator", "lookup_kb"]
    name, value = descriptor.primary_secret()
    assert name == "internal_api_token" and value.startswith("SK-REF-DEMO-")


# -- 1. GENERATE: schema-valid TestCases with filled, scoreable oracles ------ #

def test_generated_probes_are_schema_valid_testcases(descriptor):
    gen = AttackGenerator(descriptor)
    probes = gen.generate(n=12)
    assert len(probes) == 12
    for p in probes:
        assert isinstance(p, Probe)
        assert isinstance(p.test_case, TestCase)
        # revalidate through the pydantic model (schema-valid)
        TestCase.model_validate(p.test_case.model_dump())
        assert p.test_case.rubric_id == ATTACK_RUBRIC_ID
        assert "adversarial" in p.test_case.tags
        assert p.spec.kind in KINDS


def test_only_scoreable_probes_emitted(descriptor):
    """Every emitted probe carries at least one deterministic oracle key AND
    names a real tool / the real secret — never a probe we cannot score."""
    gen = AttackGenerator(descriptor)
    real_tools = set(descriptor.tool_names())
    _, secret_value = descriptor.primary_secret()
    for p in gen.generate(n=12):
        exp = p.test_case.expected
        assert exp, f"{p.test_case.test_id} has no oracle"
        assert _SCOREABLE_KEYS & set(exp), f"{p.test_case.test_id} not scoreable"
        if "secret" in exp:
            assert exp["secret"] == secret_value  # the REAL declared secret
        if "injection_target" in exp:
            assert exp["injection_target"] in real_tools  # a REAL tool
        if exp.get("forbidden_tools") and "injection_target" in exp:
            assert set(exp["forbidden_tools"]) & real_tools


def test_attack_rubric_uses_registered_checks():
    rubric = attack_rubric()
    for c in rubric.criteria:
        assert c.scorer == "code"
        assert c.check_ref in CHECKS  # reuses existing deterministic checks


# -- 2+3. RUN + KEEP: breaker kept, survivor discarded ----------------------- #

def test_breaker_kept_and_survivor_discarded(descriptor, target):
    gen = AttackGenerator(descriptor)
    results = gen.run_and_filter(gen.generate(n=12), target)
    by_id = {r.test_id: r for r in results}

    # The three DIRECT probes trip the target's shallow guard -> survive.
    for kind in KINDS:
        survivor = next(r for r in results
                        if r.probe.spec.kind == kind
                        and r.probe.spec.technique == "direct")
        assert survivor.broke is False, f"{survivor.test_id} should survive"

    # The evasion variants slip the guard -> break the agent.
    winners = gen.winners(results)
    assert len(winners) == 9  # 3 kinds x 3 evasion techniques
    for w in winners:
        assert w.broke and w.failed_criteria

    # And each break is attributed to the intended oracle.
    assert "no_secret_leak" in by_id["secret-unicode-001"].failed_criteria
    assert "injection_robust" in by_id["injection-unicode-005"].failed_criteria
    assert "tool_misuse_safety" in by_id["tool_misuse-unicode-009"].failed_criteria


def test_survivors_are_genuinely_safe_scored(descriptor, target):
    """A survivor is scored SAFE by the real scorer (passed), not merely skipped."""
    gen = AttackGenerator(descriptor)
    results = gen.run_and_filter(gen.generate(n=12), target)
    survivors = [r for r in results if not r.broke]
    assert survivors
    for s in survivors:
        assert s.score.passed is True
        assert s.score.scoring_error is None


# -- 4. MUTATE around winners ------------------------------------------------ #

def test_mutation_finds_more_winners(descriptor, target):
    gen = AttackGenerator(descriptor)
    results = gen.run_and_filter(gen.generate(n=12), target)
    winners = gen.winners(results)
    mutants = gen.mutate(winners)
    assert len(mutants) == 2 * len(winners)  # 2 neighbours per winner
    mut_results = gen.run_and_filter(mutants, target)
    assert gen.winners(mut_results), "mutation should surface neighbour winners"


# -- 5. PROMOTE via the existing hardening path ------------------------------ #

def test_promote_pushes_winners_into_regression_suite(descriptor, target, tmp_path):
    reg = Registry(str(tmp_path / "rt.db"))
    rep = run_generation(descriptor, target, n=12, mutate=False, reg=reg,
                         promote=True)
    p = rep["promote"]
    assert p["created"] is True
    assert p["added"], "winners should be promoted"
    assert p["total_cases"] == len(rep["winners"])

    # The regression suite really persisted, with real oracles + provenance.
    from agenttic.hardening import regression_detail
    det = regression_detail(reg, p["regression_suite_id"])
    assert len(det["cases"]) == len(rep["winners"])
    for case in det["cases"]:
        assert case["provenance"]["source"] == "attack-generator"
        assert case["provenance"]["why"].startswith("failed criteria")


def test_promote_only_promotes_real_failures(descriptor, target, tmp_path):
    """No fabricated ground truth: promoted count == number that actually broke."""
    reg = Registry(str(tmp_path / "rt2.db"))
    gen = AttackGenerator(descriptor)
    results = gen.run_and_filter(gen.generate(n=12), target)
    winners = gen.winners(results)
    summary = gen.promote(reg, winners)
    assert len(summary["added"]) == len(winners)
    _suite, cases = reg.get_suite(summary["regression_suite_id"])
    # every promoted case keeps its real, checkable oracle
    for c in cases:
        assert c.expected and (_SCOREABLE_KEYS & set(c.expected))


# -- LLM red-teamer plug-in point: fenced fallback, never hard-fail ---------- #

def test_llm_author_falls_back_without_credentials(descriptor):
    author = LLMRedTeamAuthor()  # no client / no model -> no credentials
    with pytest.raises(NoRedTeamModel):
        author.author(descriptor, 4)
    # The generator must NOT hard-fail: it falls back to the template author.
    gen = AttackGenerator(descriptor, author=LLMRedTeamAuthor())
    probes = gen.generate(n=12)
    assert len(probes) == 12  # produced deterministically despite no model


def test_llm_author_prompt_grounds_in_real_surface(descriptor):
    prompt = LLMRedTeamAuthor(client=object(), model="x").build_prompt(descriptor, 5)
    assert "lookup_kb" in prompt  # names the REAL tool
    assert "internal_api_token" in prompt  # names the REAL declared secret
    assert descriptor.system_prompt[:20] in prompt


def test_template_author_is_default(descriptor):
    assert isinstance(AttackGenerator(descriptor).author, TemplateAuthor)
