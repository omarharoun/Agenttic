"""Faithfulness / hallucination — atomic-claim groundedness (FActScore / RAGAS).

Covers the metric in isolation (grounded vs hallucinated vs no-reference) and its
wiring into the standard run path + the Agenttic Index."""

import asyncio
import re
import uuid
from types import SimpleNamespace

from agenttic.metrics.catalog import BY_ID, index_weights
from agenttic.metrics.faithfulness import (
    FaithfulnessResult, aggregate_faithfulness, extract_atomic_claims,
    make_llm_claim_checker, score_faithfulness,
)
from agenttic.metrics.index import compute_index
from agenttic.metrics.runner import run_standard
from agenttic.metrics.standard_suites import (
    seed_standard_suites, standard_suite_ids,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.scoring.checks import CHECKS

REF = ("Paris is the capital of France. The Eiffel Tower is located in Paris "
       "and was completed in 1889.")


def _wordwise_checker(claim, reference):
    """Fake LLM claim-checker: a claim is supported iff every content word of it
    appears in the reference context."""
    words = re.findall(r"[a-z0-9]+", claim.lower())
    ref = reference.lower()
    return all(w in ref for w in words)


class TestScoreFaithfulness:
    def test_fully_grounded_scores_one_zero_hallucination(self):
        out = "Paris is the capital of France. The Eiffel Tower was completed in 1889."
        res = score_faithfulness(out, REF, claim_checker=_wordwise_checker)
        assert res.status == "scored"
        assert res.groundedness == 1.0
        assert res.faithfulness == 1.0          # literature alias
        assert res.hallucination_rate == 0.0
        assert res.unsupported_claims == ()

    def test_unsupported_claim_scores_down_with_hallucination(self):
        out = "Paris is the capital of France. The tower is 500 meters tall."
        res = score_faithfulness(out, REF, claim_checker=_wordwise_checker)
        assert res.status == "scored"
        assert res.total == 2 and res.supported == 1
        assert res.groundedness == 0.5
        assert res.hallucination_rate == 0.5    # non-zero -> hallucination detected
        assert any("500 meters" in c for c in res.unsupported_claims)

    def test_no_reference_context_is_labeled(self):
        res = score_faithfulness("anything at all.", "", claim_checker=_wordwise_checker)
        assert res.status == "no_reference"
        assert res.groundedness is None          # unverifiable, not 1.0
        assert res.hallucination_rate is None

    def test_no_claims_is_vacuously_grounded(self):
        res = score_faithfulness("   ", REF, claim_checker=_wordwise_checker)
        assert res.status == "no_claims"
        assert res.groundedness == 1.0 and res.hallucination_rate == 0.0

    def test_missing_checker_raises(self):
        try:
            score_faithfulness("x.", REF)
        except NotImplementedError:
            return
        raise AssertionError("expected NotImplementedError without a claim_checker")

    def test_atomic_claim_decomposition(self):
        claims = extract_atomic_claims("A is true. B is false.\n- C is maybe")
        assert claims == ["A is true", "B is false", "C is maybe"]
        custom = extract_atomic_claims("ignored", extractor=lambda _: ["one", " two "])
        assert custom == ["one", "two"]


class TestAggregate:
    def test_macro_average_excludes_no_reference(self):
        results = [
            FaithfulnessResult(supported=2, total=2),            # 1.0
            FaithfulnessResult(supported=1, total=2),            # 0.5
            FaithfulnessResult(supported=0, total=0, status="no_reference"),
        ]
        agg = aggregate_faithfulness(results)
        assert agg.mode == "scored"
        assert agg.scored_cases == 2 and agg.no_reference_cases == 1
        assert agg.groundedness == 0.75 and agg.hallucination_rate == 0.25

    def test_all_no_reference_degrades(self):
        agg = aggregate_faithfulness(
            [FaithfulnessResult(0, 0, status="no_reference")])
        assert agg.mode == "no_reference"
        assert agg.groundedness is None and agg.hallucination_rate is None


class TestLLMClaimChecker:
    def test_parses_judge_json(self):
        verdicts = iter([True, False])

        def _resp(supported):
            return SimpleNamespace(content=[SimpleNamespace(
                type="text", text=f'{{"supported": {str(supported).lower()}}}')])

        client = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: _resp(next(verdicts))))
        checker = make_llm_claim_checker(client, "judge-model")
        assert checker("claim a", REF) is True
        assert checker("claim b", REF) is False


class TestCatalogAndStandardSuite:
    def test_faithfulness_is_implemented_and_weighted(self):
        m = BY_ID["faithfulness"]
        assert m.status == "implemented" and m.weight > 0
        assert "FActScore" in m.methodology
        # weights normalise to 1 across the implemented, weighted metrics
        w = index_weights()
        assert "faithfulness" in w
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_gate_check_registered(self):
        assert "faithfulness_grounded" in CHECKS

    def test_seed_includes_faithfulness_suite(self, tmp_path):
        reg = Registry(tmp_path / "s.db")
        added = seed_standard_suites(reg)
        assert "std-faithfulness-v1" in added
        assert "std-faithfulness-v1" in standard_suite_ids()
        suite, cases = reg.get_suite("std-faithfulness-v1")
        assert suite.approved is True
        assert any((c.expected or {}).get("reference_context") for c in cases)
        assert any(not (c.expected or {}).get("reference_context") for c in cases)


# -- standard run path: faithfulness in the full Agenttic Index --------------

def _trace(tid, final="grounded answer"):
    return SimpleNamespace(trace_id=uuid.uuid4().hex, test_case_id=tid,
                           final_output=final, spans=[], cost_usd=0.0)


def _patch_all_suites(monkeypatch):
    """Fake every canonical suite so a full run produces all six components."""
    from agenttic import ops
    from agenttic.schema.scorecard import CriterionScore, RunScore

    crit_by_suite = {
        "std-tool-use-v1": "tool_selection_accuracy",
        "std-safety-refusal-v1": "harmful_action_refused",
        "std-safety-injection-v1": "injection_robust",
        "std-faithfulness-v1": "faithfulness_grounded",
    }

    async def fake_run_suite(cfg, reg, adapter, sid, version, on_progress=None):
        tid = f"{sid}-c0"
        expected = {"reference_context": REF} if sid == "std-faithfulness-v1" else {}
        case = SimpleNamespace(test_id=tid, expected=expected)
        return (None, [case], [_trace(tid)])

    async def fake_score(cfg, reg, traces, cases, model, on_progress=None,
                         judge_client=None, fi_evaluate_fn=None):
        out = []
        for c, t in zip(cases, traces):
            sid = c.test_id.rsplit("-c", 1)[0]
            cs = [CriterionScore(criterion_id=crit_by_suite[sid], score=1.0,
                                 scorer="code")]
            out.append(RunScore(trace_id=t.trace_id, test_id=c.test_id,
                                passed=True, criterion_scores=cs))
        return out

    monkeypatch.setattr(ops, "run_suite_op", fake_run_suite)
    monkeypatch.setattr(ops, "score_op", fake_score)


def test_full_index_includes_faithfulness_no_missing(monkeypatch):
    _patch_all_suites(monkeypatch)
    adapter = SimpleNamespace(agent_id="agent-x", model="m", visibility="glass_box")
    res = asyncio.run(run_standard(
        {}, None, adapter, k=1, suite_ids=standard_suite_ids(),
        judge_client=object(),
        faithfulness_checker=lambda claim, ref: True))  # all claims supported
    assert res["components"]["faithfulness"] == 1.0
    assert res["faithfulness_mode"] == "scored"
    assert res["hallucination_rate"] == 0.0
    assert "faithfulness" not in res["missing"]
    # every std-suite index component is present; answer_accuracy is the lone
    # missing one because it comes from the AssistantBench *dataset* suite
    # (ingested separately), not the std-* suites this run patches in.
    assert res["missing"] == ["answer_accuracy"]
    assert 0 <= res["index"] <= 100


def test_faithfulness_hallucination_lowers_index(monkeypatch):
    _patch_all_suites(monkeypatch)
    adapter = SimpleNamespace(agent_id="agent-y", model="m", visibility="glass_box")
    # one claim, marked unsupported -> groundedness 0, hallucination 1
    res = asyncio.run(run_standard(
        {}, None, adapter, k=1, suite_ids=["std-faithfulness-v1"],
        judge_client=object(),
        faithfulness_checker=lambda claim, ref: False))
    assert res["components"]["faithfulness"] == 0.0
    assert res["hallucination_rate"] == 1.0


def test_faithfulness_missing_without_checker(monkeypatch):
    _patch_all_suites(monkeypatch)
    adapter = SimpleNamespace(agent_id="agent-z", model="m", visibility="glass_box")
    # no checker and no judge model in cfg -> faithfulness not computed, reported missing
    res = asyncio.run(run_standard(
        {}, None, adapter, k=1, suite_ids=standard_suite_ids(), judge_client=None))
    assert "faithfulness" not in res["components"]
    assert "faithfulness" in res["missing"]
    assert res["faithfulness_mode"] == "not_run"


def test_no_reference_case_is_excluded_and_labeled(monkeypatch):
    from agenttic import ops
    from agenttic.schema.scorecard import CriterionScore, RunScore

    async def fake_run_suite(cfg, reg, adapter, sid, version, on_progress=None):
        tid = f"{sid}-c0"
        case = SimpleNamespace(test_id=tid, expected={"reference_context": ""})
        return (None, [case], [_trace(tid)])

    async def fake_score(cfg, reg, traces, cases, model, on_progress=None,
                         judge_client=None, fi_evaluate_fn=None):
        return [RunScore(trace_id=t.trace_id, test_id=c.test_id, passed=True,
                         criterion_scores=[CriterionScore(
                             criterion_id="faithfulness_grounded", score=1.0,
                             scorer="code")])
                for c, t in zip(cases, traces)]

    monkeypatch.setattr(ops, "run_suite_op", fake_run_suite)
    monkeypatch.setattr(ops, "score_op", fake_score)
    adapter = SimpleNamespace(agent_id="agent-q", model="m", visibility="glass_box")
    res = asyncio.run(run_standard(
        {}, None, adapter, k=1, suite_ids=["std-faithfulness-v1"],
        judge_client=object(), faithfulness_checker=lambda c, r: True))
    # only a no_reference case -> faithfulness degrades, not scored, reported missing
    assert res["faithfulness_mode"] == "no_reference"
    assert res["faithfulness_no_reference_cases"] == 1
    assert "faithfulness" in res["missing"]
