"""Live-monitor catches as a hardening promotion source.

A below-threshold sampled production trace becomes a promotable *catch*;
promoting it reconstructs a regression case from the trace's input, attaches the
observed failure as provenance, and marks it needs-review (never fabricating the
ground truth a production trace can't carry). De-dupe holds and healthy traces
are not promotable. Covers the pure ops and the HTTP surface.
"""

import uuid
from datetime import datetime, timezone

from agenttic import hardening
from agenttic.hardening import (
    DEFAULT_LIVE_CATCH_THRESHOLD,
    LIVE_SOURCE_SUITE,
    live_test_id,
    promote_live_failures_op,
    regression_suite_id,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

from tests.test_api import client  # noqa: F401 — reuse the app+fakes fixture

NOW = datetime(2026, 6, 20, tzinfo=timezone.utc)
AGENT = "prod-agent"


def _trace(agent=AGENT, *, question="refund my last order", with_input=True,
           visibility="glass_box"):
    span = Span(span_id="s1", kind="final_output", name="final_output",
                start_time=NOW, end_time=NOW,
                input={"question": question} if with_input else {})
    return Trace(trace_id=uuid.uuid4().hex, agent_id=agent, agent_config_hash="h",
                 test_case_id=None, spans=[span], visibility=visibility,
                 final_output="(unhelpful)", schema_version=SCHEMA_VERSION)


def _seed_live(reg, scores: dict, *, agent=AGENT, question="refund my last order",
               with_input=True, visibility="glass_box") -> str:
    """Persist one sampled live trace + its per-criterion scores. Returns id."""
    tr = _trace(agent, question=question, with_input=with_input,
                visibility=visibility)
    reg.save_trace(tr, mode="live")
    reg.save_live_scores(agent, tr.trace_id, scores)
    return tr.trace_id


# -- pure ops ----------------------------------------------------------------

class TestLiveCatchCandidates:
    def test_below_threshold_is_a_catch_healthy_is_not(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        bad = _seed_live(reg, {"helpful": 0.0, "accurate": 0.0})
        _good = _seed_live(reg, {"helpful": 1.0, "accurate": 1.0})

        cands = hardening.live_catch_candidates(reg, AGENT)
        ids = [c["trace_id"] for c in cands]
        assert ids == [bad]                       # only the catch; healthy excluded
        catch = cands[0]
        assert catch["mean_score"] == 0.0
        assert catch["failing_criteria"] == ["accurate", "helpful"]
        assert catch["input_reconstructed"] is True
        assert catch["already_promoted"] is False
        assert catch["regression_suite_id"] == \
            regression_suite_id(AGENT, LIVE_SOURCE_SUITE)

    def test_threshold_is_strict_and_tunable(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        _seed_live(reg, {"helpful": 0.5})          # exactly at default 0.5
        # default 0.5 is strict (mean >= threshold is healthy): not a catch
        assert hardening.live_catch_candidates(reg, AGENT) == []
        # raise the bar and it becomes a catch
        assert len(hardening.live_catch_candidates(reg, AGENT, threshold=0.8)) == 1

    def test_spans_all_agents_when_agent_omitted(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        _seed_live(reg, {"helpful": 0.0}, agent="agent-a")
        _seed_live(reg, {"helpful": 0.0}, agent="agent-b")
        agents = {c["agent_id"] for c in hardening.live_catch_candidates(reg)}
        assert agents == {"agent-a", "agent-b"}


class TestPromoteLive:
    def test_promote_reconstructs_case_with_needs_review_provenance(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        tid = _seed_live(reg, {"helpful": 0.0, "accurate": 0.0})

        res = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        reg_id = regression_suite_id(AGENT, LIVE_SOURCE_SUITE)
        assert res["regression_suite_id"] == reg_id
        assert res["added"] == [tid]
        assert res["created"] is True
        assert res["needs_review"] is True
        assert res["source"] == "live"

        suite, cases = reg.get_suite(reg_id)
        assert suite.approved is False             # human gate: not auto-runnable
        case = cases[0]
        assert case.test_id == live_test_id(tid)
        assert case.input == {"question": "refund my last order"}
        assert case.expected is None               # never fabricate ground truth
        assert "needs-review" in case.tags and "live" in case.tags
        assert case.rubric_id == "r-live"

        detail = hardening.regression_detail(reg, reg_id)
        assert detail["source"] == "live" and detail["needs_review"] is True
        prov = detail["cases"][0]["provenance"]
        assert prov["source"] == "live" and prov["needs_review"] is True
        assert prov["source_trace_id"] == tid
        assert prov["observed_scores"] == {"helpful": 0.0, "accurate": 0.0}
        assert "live catch" in prov["why"]

    def test_missing_input_marks_partial(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        # black-box trace with no usable span input → input can't be reconstructed
        tid = _seed_live(reg, {"helpful": 0.0}, with_input=False,
                         visibility="black_box")
        res = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert res["added"] == [tid]
        _suite, cases = reg.get_suite(res["regression_suite_id"])
        assert cases[0].input == {}
        assert "partial" in cases[0].tags        # clearly flagged, not invented

    def test_dedupe_and_append_version_bump(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        t1 = _seed_live(reg, {"helpful": 0.0})
        r1 = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert r1["version"] == 1 and r1["added"] == [t1]

        # re-promoting the same catch adds nothing and does not bump the version
        r2 = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert r2["added"] == [] and r2["skipped_duplicates"] == [t1]
        assert r2["version"] == 1 and r2["created"] is False

        # a new catch with *distinct* input appends and bumps to v2
        t2 = _seed_live(reg, {"helpful": 0.0}, question="where is my package")
        r3 = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert r3["added"] == [t2] and r3["version"] == 2
        _suite, cases = reg.get_suite(r3["regression_suite_id"])
        assert len(cases) == 2

    def test_distinct_traces_same_input_are_deduped(self, tmp_path):
        # two different production traces that reconstruct to the SAME input are
        # near-identical regression cases — keep one, skip the other
        reg = Registry(tmp_path / "db.sqlite")
        _seed_live(reg, {"helpful": 0.0}, question="same ask")
        _seed_live(reg, {"helpful": 0.0}, question="same ask")
        res = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert len(res["added"]) == 1 and len(res["skipped_duplicates"]) == 1

    def test_healthy_traces_are_not_promotable(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        _seed_live(reg, {"helpful": 1.0})
        res = promote_live_failures_op(reg, AGENT, rubric_id="r-live")
        assert res["added"] == [] and res["total_cases"] == 0

    def test_trace_allowlist_narrows_selection(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        t1 = _seed_live(reg, {"helpful": 0.0})
        _t2 = _seed_live(reg, {"helpful": 0.0})
        res = promote_live_failures_op(reg, AGENT, rubric_id="r-live",
                                       trace_ids=[t1])
        assert res["added"] == [t1]


# -- HTTP surface ------------------------------------------------------------

class TestLiveHardeningAPI:
    def _seed(self, reg, scores):
        return _seed_live(reg, scores)

    def test_live_candidates_then_promote_endpoint(self, client):  # noqa: F811
        tid = self._seed(client.reg, {"helpful": 0.0})

        cands = client.get(
            "/api/hardening/live-candidates",
            params={"agent_id": AGENT}).json()["candidates"]
        assert [c["trace_id"] for c in cands] == [tid]
        assert cands[0]["already_promoted"] is False

        res = client.post("/api/hardening/promote", json={
            "source": "live", "agent_id": AGENT, "rubric_id": "r-live",
        }).json()
        reg_id = regression_suite_id(AGENT, LIVE_SOURCE_SUITE)
        assert res["regression_suite_id"] == reg_id and res["added"] == [tid]
        assert res["needs_review"] is True

        # it now shows up among regression suites, flagged as a live source
        suites = client.get("/api/hardening/suites").json()["suites"]
        live = next(s for s in suites if s["regression_suite_id"] == reg_id)
        assert live["source"] == "live"

        # and the catch reports as already promoted on a re-list
        again = client.get("/api/hardening/live-candidates",
                           params={"agent_id": AGENT}).json()["candidates"]
        assert again[0]["already_promoted"] is True

    def test_live_promote_requires_agent_id(self, client):  # noqa: F811
        r = client.post("/api/hardening/promote", json={"source": "live"})
        assert r.status_code == 422

    def test_scorecard_promote_still_requires_scorecard_id(self, client):  # noqa: F811
        r = client.post("/api/hardening/promote", json={"source": "scorecard"})
        assert r.status_code == 422
