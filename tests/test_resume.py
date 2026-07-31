"""Checkpoint/resume + partial-cost capture: a mid-operation transient failure
must not discard generated content or re-spend tokens on resume."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.adapters.base import AgentAdapter
from agenttic.generator.pipeline import BenchmarkGenerator
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.retry import RetryPolicy
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

JOB = "Triage tickets and answer policy questions."
TASKS = {"tasks": [
    {"slug": "triage", "name": "Triage", "description": "route tickets"},
    {"slug": "policy_qa", "name": "Policy", "description": "answer policy"},
]}


class _Transient(Exception):
    status_code = 500


def _criteria(slug):
    return {"criteria": [{"criterion_id": f"{slug}_ok", "description": "d",
                          "scorer": "code", "scale": "binary",
                          "check_ref": "final_output_matches_expected",
                          "anchors": {}, "tags": []}]}


def _cases(slug, n=5):
    return {"cases": [{"task_description": f"{slug} {i}", "input": {"q": i},
                       "expected": {"final_output": f"{slug}{i}"}, "tags": []}
                      for i in range(n)]}


class GenClient:
    """Sniffs prompt kind/slug; can fail on a (kind, slug); records calls + usage."""
    def __init__(self, fail_kind=None, fail_slug=None):
        self.fail_kind, self.fail_slug = fail_kind, fail_slug
        self.calls = []
        self.messages = NS(create=self._c)

    def _c(self, **kw):
        p = kw["messages"][0]["content"]
        kind = ("extract" if "extract the discrete" in p
                else "criteria" if "Design scoring criteria" in p else "cases")
        slug = next((s for s in ("triage", "policy_qa") if f'"{s}"' in p), None)
        self.calls.append((kind, slug))
        if kind == self.fail_kind and (self.fail_slug is None or slug == self.fail_slug):
            raise _Transient()
        payload = (TASKS if kind == "extract"
                   else _criteria(slug) if kind == "criteria" else _cases(slug))
        return NS(content=[NS(type="text", text=json.dumps(payload))],
                  usage=NS(input_tokens=1000, output_tokens=500))


def _gen(client):
    return BenchmarkGenerator(model="g", client=client,
                              retry_policy=RetryPolicy(max_attempts=1),
                              pricing_per_mtok={"input": 3.0, "output": 15.0})


class TestGeneratorResume:
    def test_partial_cases_persisted_and_resumed_without_rework(self, tmp_path):
        reg = Registry(tmp_path / "g.db")
        # run 1: fails generating policy_qa cases (after triage is done)
        with pytest.raises(_Transient):
            _gen(GenClient(fail_kind="cases", fail_slug="policy_qa")).generate_suite(
                JOB, suite_id="s1", registry=reg, review_dir=tmp_path / "r")
        # triage's 5 cases were checkpointed; policy_qa's were not
        peek = {c.test_id for c in reg.peek_cases("s1", 1)}
        assert len(peek) == 5 and all("triage" in t for t in peek)

        # run 2 (no failure): resumes — triage skipped entirely
        c2 = GenClient()
        suite = _gen(c2).generate_suite(JOB, suite_id="s1", registry=reg,
                                        review_dir=tmp_path / "r")
        stored, cases = reg.get_suite("s1")
        assert len(cases) == 10 and stored.approved is False
        # no triage rework: run 2 only re-ran extract + policy_qa criteria+cases
        assert ("criteria", "triage") not in c2.calls
        assert ("cases", "triage") not in c2.calls
        assert c2.calls == [("extract", None), ("criteria", "policy_qa"),
                            ("cases", "policy_qa")]

    def test_spend_recorded_even_on_failure(self, tmp_path):
        reg = Registry(tmp_path / "g.db")
        with pytest.raises(_Transient):
            _gen(GenClient(fail_kind="cases", fail_slug="policy_qa")).generate_suite(
                JOB, suite_id="s1", registry=reg, review_dir=tmp_path / "r")
        # 4 successful calls (extract, triage criteria+cases, policy criteria)
        # were billed before the failure → ledger non-zero
        assert reg.spend_today() == pytest.approx((4 * 1000 * 3 + 4 * 500 * 15) / 1e6)


class TestAgentPartialTrace:
    def test_terminal_upstream_error_keeps_partial_cost(self, tmp_path):
        kb = tmp_path / "kb.json"; kb.write_text("{}")
        calls = {"n": 0}

        def create(**kw):
            calls["n"] += 1
            if calls["n"] == 1:  # first step: a tool call (real tokens)
                return NS(stop_reason="tool_use",
                          usage=NS(input_tokens=100, output_tokens=10),
                          content=[NS(type="tool_use", name="calculator",
                                      input={"expression": "1+1"}, id="t1")])
            raise _Transient()  # second step: upstream 500, retries exhausted

        agent = AnthropicSimpleAgent(model="m", kb_path=kb,
                                     client=NS(messages=NS(create=create)),
                                     retry_policy=RetryPolicy(max_attempts=1))
        trace = agent.run({"q": "go"}, test_case_id="c0")
        assert trace.final_output.startswith("UPSTREAM_ERROR")
        assert any(s.kind == "error" and s.name == "upstream_error" for s in trace.spans)
        assert any(s.kind == "llm_call" for s in trace.spans)   # first step kept
        assert trace.total_cost_usd > 0                          # its tokens billed


class _PoisonAdapter(AgentAdapter):
    """Same identity as the real run; run() must not be called (resume should
    reuse the persisted trace)."""
    visibility = "glass_box"
    def __init__(self): self.agent_id = "coster"
    def describe(self): return {"adapter": "coster"}
    def run(self, test_input, *, test_case_id=None):  # pragma: no cover
        raise AssertionError("resume should not re-run an already-successful case")


class _CostingAgent(AgentAdapter):
    visibility = "glass_box"
    def __init__(self): self.agent_id = "coster"
    def describe(self): return {"adapter": "coster"}
    def run(self, test_input, *, test_case_id=None):
        now = datetime.now(timezone.utc)
        return Trace(trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
                     agent_config_hash=self.config_hash(), test_case_id=test_case_id,
                     spans=[Span(span_id="s", kind="final_output", name="f",
                                 start_time=now, end_time=now)],
                     visibility="glass_box", final_output="ok",
                     total_cost_usd=0.01, total_latency_ms=1.0, total_steps=1,
                     schema_version=SCHEMA_VERSION)


def _scripted_kb_agent(kb, answer, *, agent_id="kbagent"):
    """Reference adapter wired to a one-shot fake client that always replies
    ``answer`` — so a re-run is observable in the trace, and a resume is too."""
    def create(**_kw):
        return NS(stop_reason="end_turn",
                  usage=NS(input_tokens=1, output_tokens=1),
                  content=[NS(type="text", text=answer)])
    return AnthropicSimpleAgent(model="m", kb_path=kb, agent_id=agent_id,
                                client=NS(messages=NS(create=create)))


class TestKnowledgeBaseIdentity:
    """The KB is what ``lookup_kb`` answers WITH, so it is part of the agent
    under test. If it doesn't reach ``config_hash()``, the harness's resume map
    treats a re-run against a rewritten KB as already done and serves the old
    traces — a silently stale scorecard."""

    def test_config_hash_tracks_kb_content_not_path(self, tmp_path):
        kb = tmp_path / "kb.json"
        kb.write_text(json.dumps({"refund_policy": "30 days"}))
        agent = _scripted_kb_agent(kb, "x")
        before = agent.config_hash()
        # SAME path, corrected content — the case a path-keyed hash misses
        kb.write_text(json.dumps({"refund_policy": "14 days"}))
        assert agent.config_hash() != before
        assert agent.describe()["kb_sha256"] != "unreadable:FileNotFoundError"

    def test_identical_kb_content_at_a_different_path_hashes_the_same(self, tmp_path):
        body = json.dumps({"refund_policy": "30 days"})
        a_path, b_path = tmp_path / "a" / "kb.json", tmp_path / "b" / "kb.json"
        for p in (a_path, b_path):
            p.parent.mkdir()
            p.write_text(body)
        # content identity, not location: the same KB moved is the same agent
        assert (_scripted_kb_agent(a_path, "x").config_hash()
                == _scripted_kb_agent(b_path, "x").config_hash())

    def test_missing_kb_is_named_deterministically(self, tmp_path):
        agent = _scripted_kb_agent(tmp_path / "gone.json", "x")
        assert agent.describe()["kb_sha256"] == "unreadable:FileNotFoundError"
        assert agent.config_hash() == agent.config_hash()   # stays stable

    def test_rewritten_kb_defeats_resume(self, tmp_path):
        reg = Registry(tmp_path / "kb.db")
        kb = tmp_path / "kb.json"
        kb.write_text(json.dumps({"refund_policy": "30 days"}))
        suite = TestSuite(suite_id="s", business_context="x", approved=True,
                          test_ids=["c0"])
        cases = [TestCase(test_id="c0", suite_id="s", task_description="t",
                          input={"q": "refund_policy"}, rubric_id="r")]
        cfg = HarnessConfig(timeout_seconds=5)

        t1 = asyncio.run(run_suite(_scripted_kb_agent(kb, "old"), suite, cases,
                                   reg, cfg))
        assert t1[0].final_output == "old"
        # unchanged KB: same agent, resumed — the second script never runs
        t2 = asyncio.run(run_suite(_scripted_kb_agent(kb, "new"), suite, cases,
                                   reg, cfg))
        assert t2[0].final_output == "old"
        # KB rewritten in place: a different agent, so the case must re-run
        kb.write_text(json.dumps({"refund_policy": "14 days"}))
        t3 = asyncio.run(run_suite(_scripted_kb_agent(kb, "new"), suite, cases,
                                   reg, cfg))
        assert t3[0].final_output == "new"


class TestRunResume:
    def test_successful_cases_not_rerun(self, tmp_path):
        reg = Registry(tmp_path / "r.db")
        suite = TestSuite(suite_id="s", business_context="x", approved=True,
                          test_ids=[f"c{i}" for i in range(4)])
        cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                          input={}, rubric_id="r") for i in range(4)]
        # run 1: all succeed, traces persisted
        t1 = asyncio.run(run_suite(_CostingAgent(), suite, cases, reg,
                                   HarnessConfig(max_parallel=2, timeout_seconds=5)))
        assert len(t1) == 4 and all(t.final_output == "ok" for t in t1)
        # run 2 with a poison adapter of the SAME config_hash → all resumed
        events = []
        t2 = asyncio.run(run_suite(_PoisonAdapter(), suite, cases, reg,
                                   HarnessConfig(max_parallel=2, timeout_seconds=5),
                                   on_event=lambda e, d: events.append(e)))
        assert len(t2) == 4
        assert events.count("case_resumed") == 4   # every case reused, none re-run
