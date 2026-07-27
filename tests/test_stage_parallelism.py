"""Generation and scoring run their independent work concurrently.

Both stages used to be strict `for` loops: generation made two slow generator
calls per task one task at a time, and scoring made one judge call per judge
criterion one case at a time. Between them that was most of a run's wall clock,
for work with no cross-item dependency at all.

These tests pin the concurrency itself (does work actually overlap?) and the
things it could quietly break: result ordering, per-item error isolation, and
the cost accounting that a lost `+=` across threads would understate.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agenttic import ops
from agenttic.generator.pipeline import BenchmarkGenerator
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import RunScore
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace


# ---- config knobs ---------------------------------------------------------- #

def test_each_stage_paces_itself_separately():
    """One number can't pace three stages hitting three different models."""
    cfg = {"harness": {"max_parallel": 5}, "scoring": {"max_parallel": 9},
           "generator": {"max_parallel": 3}}
    assert ops.scoring_parallelism(cfg) == 9
    assert ops.generator_parallelism(cfg) == 3


def test_a_config_without_the_new_keys_still_gets_a_sane_cap():
    """Deployed config files predate these keys — they must not fall to zero."""
    assert ops.scoring_parallelism({}) == 5
    assert ops.generator_parallelism({}) == 4


@pytest.mark.parametrize("bad", [0, -4, None, "", "lots"])
def test_a_broken_cap_never_produces_a_stalled_stage(bad):
    assert ops.scoring_parallelism({"scoring": {"max_parallel": bad}}) >= 1


# ---- scoring --------------------------------------------------------------- #

def _trace(i: int) -> Trace:
    now = datetime.now(timezone.utc)
    return Trace(
        trace_id=f"tr-{i}", agent_id="a", agent_config_hash="h",
        test_case_id=f"t-{i}",
        spans=[Span(span_id=f"s{i}", kind="llm_call", name="call",
                    start_time=now, end_time=now)],
        visibility="glass_box", final_output="ok", total_cost_usd=0.0,
        total_latency_ms=1.0, total_steps=1)


def _case(i: int) -> TestCase:
    return TestCase(test_id=f"t-{i}", suite_id="s", version=1,
                    task_description="d", input={"q": i},
                    expected={"final_output": "ok"}, rubric_id="r")


RUBRIC = Rubric(rubric_id="r", version=1, criteria=[Criterion(
    criterion_id="c1", description="matches", scorer="code", scale="binary",
    check_ref="final_output_matches_expected")])


class _Reg:
    """Just enough registry for score_op."""
    def get_rubric(self, rubric_id, version=None):
        return RUBRIC


#: The judge is never actually called — score_run is swapped out — but score_op
#: builds one up front, so it needs models and a client that isn't None (a None
#: client would construct a real Anthropic client and demand an API key).
MODELS = {"models": {"agent_default": "agent-model", "judge_executor": "judge-x",
                     "judge_strong": "judge-s"}}


async def _score(n, cfg, score_fn, on_progress=None):
    traces = [_trace(i) for i in range(n)]
    cases = [_case(i) for i in range(n)]
    import agenttic.ops as ops_mod
    real = ops_mod.score_run
    ops_mod.score_run = score_fn
    try:
        return await ops.score_op({**MODELS, **cfg}, _Reg(), traces, cases,
                                  "agent-model", on_progress=on_progress,
                                  judge_client=NS())
    finally:
        ops_mod.score_run = real


def test_cases_are_scored_concurrently_not_one_after_another():
    """Eight cases, four lanes, each 'judge call' 100ms: serial would be 800ms."""
    def slow(trace, case, rubric, judge, **kw):
        time.sleep(0.1)
        return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                        criterion_scores=[], passed=True, cost_usd=0.0,
                        latency_ms=0.0, steps=1)

    t0 = time.monotonic()
    runs = asyncio.run(_score(8, {"scoring": {"max_parallel": 4}}, slow))
    elapsed = time.monotonic() - t0
    assert len(runs) == 8
    assert elapsed < 0.5, f"scoring looks serial ({elapsed:.2f}s for 8 x 100ms)"


def test_results_still_line_up_with_their_traces_when_they_finish_out_of_order():
    """The scorecard maps RunScore[i] to case[i] — order is not cosmetic."""
    def jittered(trace, case, rubric, judge, **kw):
        # later cases finish first
        time.sleep(0.05 * (5 - int(case.test_id.split("-")[1])))
        return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                        criterion_scores=[], passed=True, cost_usd=0.0,
                        latency_ms=0.0, steps=1)

    runs = asyncio.run(_score(5, {"scoring": {"max_parallel": 5}}, jittered))
    assert [r.test_id for r in runs] == [f"t-{i}" for i in range(5)]
    assert [r.trace_id for r in runs] == [f"tr-{i}" for i in range(5)]


def test_one_case_blowing_up_does_not_take_the_batch_with_it():
    def sometimes(trace, case, rubric, judge, **kw):
        if case.test_id == "t-2":
            raise RuntimeError("judge exploded")
        return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                        criterion_scores=[], passed=True, cost_usd=0.0,
                        latency_ms=0.0, steps=1)

    runs = asyncio.run(_score(4, {"scoring": {"max_parallel": 4}}, sometimes))
    assert len(runs) == 4
    assert runs[2].scoring_error and "judge exploded" in runs[2].scoring_error
    assert runs[2].passed is False
    assert all(r.scoring_error is None for i, r in enumerate(runs) if i != 2)


def test_the_cap_is_actually_a_cap():
    live, peak, lock = 0, 0, threading.Lock()

    def watched(trace, case, rubric, judge, **kw):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                        criterion_scores=[], passed=True, cost_usd=0.0,
                        latency_ms=0.0, steps=1)

    asyncio.run(_score(12, {"scoring": {"max_parallel": 3}}, watched))
    assert peak <= 3, f"ran {peak} at once with a cap of 3"


def test_every_case_still_reports_progress_exactly_once():
    seen = []

    def ok(trace, case, rubric, judge, **kw):
        return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                        criterion_scores=[], passed=True, cost_usd=0.0,
                        latency_ms=0.0, steps=1)

    asyncio.run(_score(6, {"scoring": {"max_parallel": 6}}, ok,
                       on_progress=lambda t, d: seen.append((t, d["test_id"]))))
    assert sorted(tid for _, tid in seen) == [f"t-{i}" for i in range(6)]
    assert {t for t, _ in seen} == {"case_scored"}


# ---- generation ------------------------------------------------------------ #

DOC = "Agents that triage tickets and answer policy questions."
TASKS = {"tasks": [
    {"name": f"Task {i}", "slug": f"task{i}", "description": "d"} for i in range(4)]}


def _criteria(slug):
    return {"criteria": [{
        "criterion_id": f"{slug}-c1", "description": "matches", "scorer": "code",
        "scale": "binary", "check_ref": "final_output_matches_expected",
        "anchors": {}, "tags": []}]}


def _cases(slug):
    return {"cases": [{"task_description": f"{slug} case {i}",
                       "input": {"q": i}, "expected": {"final_output": "x"},
                       "tags": ["happy_path"]} for i in range(3)]}


class ConcurrentFakeClient:
    """A client that answers by WHAT was asked, not by call order.

    The ordered script the other generator tests use can't survive tasks running
    at once — task B's criteria call can overtake task A's. Dispatching on the
    prompt is what a real API does anyway."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            if self.delay:
                time.sleep(self.delay)
            prompt = kw["messages"][0]["content"]
            if "BUSINESS DOCUMENT" in prompt or "extract" in prompt.lower()[:200]:
                body = TASKS
            else:
                task = json.loads(prompt.split("TASK: ")[-1].strip())
                body = _cases(task["slug"]) if "cases" in prompt else _criteria(task["slug"])
            return NS(content=[NS(type="text", text=json.dumps(body))],
                      usage=NS(input_tokens=10, output_tokens=20))
        finally:
            with self._lock:
                self.live -= 1


def test_tasks_are_generated_concurrently(tmp_path):
    client = ConcurrentFakeClient(delay=0.1)
    gen = BenchmarkGenerator(model="gen", client=client, max_parallel=4)
    t0 = time.monotonic()
    gen.generate_suite(DOC, suite_id="s1", registry=Registry(tmp_path / "d.sqlite"),
                       review_dir=tmp_path / "review")
    elapsed = time.monotonic() - t0
    # 1 extract + 4 tasks x 2 calls, 100ms each: serial is 0.9s, 4 lanes ~0.3s
    assert elapsed < 0.6, f"generation looks serial ({elapsed:.2f}s)"
    assert client.peak > 1


def test_generation_defaults_to_serial_so_stage_order_stays_predictable(tmp_path):
    client = ConcurrentFakeClient()
    BenchmarkGenerator(model="gen", client=client).generate_suite(
        DOC, suite_id="s2", registry=Registry(tmp_path / "d.sqlite"),
        review_dir=tmp_path / "review")
    assert client.peak == 1


def test_every_task_lands_in_the_suite_and_cases_stay_sorted(tmp_path):
    reg = Registry(tmp_path / "d.sqlite")
    BenchmarkGenerator(model="gen", client=ConcurrentFakeClient(),
                       max_parallel=4).generate_suite(
        DOC, suite_id="s3", registry=reg, review_dir=tmp_path / "review")
    suite, cases = reg.get_suite("s3")
    assert len({c.rubric_id for c in cases}) == 4          # no task dropped
    assert suite.test_ids == sorted(suite.test_ids)        # order is deterministic
    assert suite.approved is False                         # the gate still holds


def test_the_review_file_lists_rubrics_in_task_order(tmp_path):
    """Tasks finish out of order; the document a human reads must not."""
    reg = Registry(tmp_path / "d.sqlite")
    BenchmarkGenerator(model="gen", client=ConcurrentFakeClient(),
                       max_parallel=4).generate_suite(
        DOC, suite_id="s4", registry=reg, review_dir=tmp_path / "review")
    review = (tmp_path / "review" / "s4.md").read_text()
    positions = [review.index(f"`s4-task{i}`") for i in range(4)]
    assert positions == sorted(positions)


def test_token_accounting_survives_concurrent_stages(tmp_path):
    """`self.tokens_in += n` is a read-modify-write; a lost update under threads
    would silently understate what the run cost."""
    gen = BenchmarkGenerator(model="gen", client=ConcurrentFakeClient(),
                             max_parallel=4)
    gen.generate_suite(DOC, suite_id="s5", registry=Registry(tmp_path / "d.sqlite"),
                       review_dir=tmp_path / "review")
    calls = 1 + 2 * len(TASKS["tasks"])
    assert gen.tokens_in == 10 * calls
    assert gen.tokens_out == 20 * calls
