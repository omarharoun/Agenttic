"""Shared operations layer — the single implementation of every pipeline step.

The CLI (typer commands) and the workflow engine/UI API both call these
functions; neither reimplements pipeline logic. Each long-running op accepts
an optional ``on_progress(event_type, data)`` callback so callers (the UI's
event bus, or nothing for the CLI) can observe per-case progress.

Hard rules stay enforced where they live: the human gate in
``harness.run_suite`` (unapproved suites refuse to run) and judge-model
separation in ``scoring.judge.make_judge`` — no caller can route around them.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Callable, Literal

from ascore.adapters.anthropic_simple import AnthropicSimpleAgent
from ascore.adapters.base import AgentAdapter
from ascore.adapters.blackbox_http import BlackBoxHTTPAgent
from ascore.adapters.managed_agent import ManagedAgentAdapter
from ascore.generator.pipeline import BenchmarkGenerator
from ascore.harness.runner import HarnessConfig, run_suite
from ascore.registry.sqlite_store import Registry
from ascore.reporting.scorecard_report import render_markdown
from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import RunScore, Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.trace import Trace
from ascore.scoring.engine import score_run
from ascore.scoring.judge import make_judge

ProgressFn = Callable[[str, dict], None]

AdapterVariant = Literal["reference", "blackbox", "managed"]


def build_adapter(
    cfg: dict,
    *,
    variant: AdapterVariant,
    agent_id: str,
    url: str = "",
    managed_agent_id: str = "",
    environment_id: str = "",
    client=None,
    system_prompt: str = "",
    model: str = "",
) -> AgentAdapter:
    """Instantiate the adapter for one agent under test. ``system_prompt``
    overrides the reference agent's task instructions and ``model`` overrides
    its model (both are part of the configuration under test and feed the trace
    config hash, so a declared agent that pins them is reproducible)."""
    if variant == "managed":
        if not environment_id:
            environment_id = cfg.get("managed", {}).get("environment_id", "")
        if not managed_agent_id or not environment_id:
            raise ValueError("managed adapter needs managed_agent_id and environment_id")
        kw = {"client": client} if client is not None else {}
        return ManagedAgentAdapter(
            managed_agent_id=managed_agent_id, environment_id=environment_id,
            agent_id=agent_id, **kw)
    if variant == "blackbox":
        if not url:
            raise ValueError("blackbox adapter needs a url")
        allow_private = not cfg.get("security", {}).get("blackbox_block_private", True)
        return BlackBoxHTTPAgent(agent_id=agent_id, url=url,
                                 allow_private_url=allow_private)
    kw = {"client": client} if client is not None else {}
    resolved_model = model or cfg["models"]["agent_default"]
    from ascore.pricing import model_price
    return AnthropicSimpleAgent(model=resolved_model,
                                kb_path="kb.json", agent_id=agent_id,
                                max_steps=cfg["harness"]["max_steps"],
                                pricing_per_mtok=model_price(cfg, resolved_model),
                                system_prompt=system_prompt or None, **kw)


def agent_model_of(adapter: AgentAdapter) -> str:
    """The model string Hard Rule 4 compares judges against. Black-box
    adapters expose no model, so they never collide with a judge tier."""
    return getattr(adapter, "model", None) or f"blackbox:{adapter.agent_id}"


async def run_suite_op(
    cfg: dict,
    reg: Registry,
    adapter: AgentAdapter,
    suite_id: str,
    version: int | None = None,
    on_progress: ProgressFn | None = None,
) -> tuple[TestSuite, list[TestCase], list[Trace]]:
    """Harness step: execute every case of a suite, persisting all traces."""
    suite, cases = reg.get_suite(suite_id, version)
    h = cfg["harness"]
    traces = await run_suite(
        adapter, suite, cases, reg,
        HarnessConfig(timeout_seconds=h["timeout_seconds"],
                      max_parallel=h["max_parallel"],
                      transport_retries=h["transport_retries"]),
        on_event=on_progress,
    )
    return suite, cases, traces


async def score_op(
    cfg: dict,
    reg: Registry,
    traces: list[Trace],
    cases: list[TestCase],
    agent_model: str,
    on_progress: ProgressFn | None = None,
    judge_client=None,
    pass_threshold: float = 0.7,
    rubric_override: Rubric | None = None,
    fi_evaluator=None,
    fi_evaluate_fn=None,
) -> list[RunScore]:
    """Scoring step: deterministic checks + LLM judge (+ FI), one RunScore per
    trace. Partial batch scoring: a case that fails to score becomes an errored
    RunScore (kept, surfaced, excluded from quality aggregates) rather than
    aborting the whole batch — mirroring the harness's per-case resilience."""
    judge = make_judge(cfg, agent_model, client=judge_client)
    if fi_evaluator is None:
        from ascore.scoring.fi_eval import FiEvaluator
        fi_evaluator = FiEvaluator(
            threshold=cfg.get("scoring", {}).get("fi_threshold", 0.5),
            evaluate_fn=fi_evaluate_fn)
    runs: list[RunScore] = []
    total = len(cases)
    for i, (trace, case) in enumerate(zip(traces, cases)):
        rubric = rubric_override or reg.get_rubric(case.rubric_id)
        try:
            rs = await asyncio.to_thread(
                score_run, trace, case, rubric, judge,
                pass_threshold=pass_threshold, fi_evaluator=fi_evaluator)
            runs.append(rs)
            if on_progress:
                on_progress("case_scored", {
                    "index": i, "total": total, "test_id": case.test_id,
                    "passed": rs.passed,
                })
        except Exception as exc:  # noqa: BLE001 — scoring failure is data, not fatal
            err = f"{type(exc).__name__}: {exc}"
            runs.append(RunScore(
                trace_id=trace.trace_id, test_id=case.test_id,
                criterion_scores=[], passed=False,
                cost_usd=trace.total_cost_usd, latency_ms=trace.total_latency_ms,
                steps=trace.total_steps, scoring_error=err))
            if on_progress:
                on_progress("case_error", {
                    "index": i, "total": total, "test_id": case.test_id,
                    "error": err,
                })
    return runs


def aggregate_op(
    reg: Registry,
    *,
    agent_id: str,
    suite: TestSuite,
    rubric: Rubric,
    runs: list[RunScore],
    visibility: str,
) -> Scorecard:
    """Aggregate RunScores into an immutable, persisted Scorecard."""
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=agent_id,
        suite_id=suite.suite_id, suite_version=suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=runs, visibility_tier=visibility)
    reg.save_scorecard(sc)
    # record total spend (execution + scoring) for the daily budget ledger
    reg.record_spend(agent_id, sc.total_cost_usd + sc.total_scoring_cost_usd)
    return sc


async def run_and_score_op(
    cfg: dict,
    reg: Registry,
    adapter: AgentAdapter,
    suite_id: str,
    version: int | None = None,
    on_progress: ProgressFn | None = None,
    judge_client=None,
) -> Scorecard:
    """The full run → score → aggregate chain (CLI `run`/`regress` behavior)."""
    suite, cases, traces = await run_suite_op(
        cfg, reg, adapter, suite_id, version, on_progress)
    runs = await score_op(cfg, reg, traces, cases, agent_model_of(adapter),
                          on_progress, judge_client=judge_client)
    rubric = reg.get_rubric(cases[0].rubric_id)
    return aggregate_op(reg, agent_id=adapter.agent_id, suite=suite,
                        rubric=rubric, runs=runs, visibility=adapter.visibility)


def generate_op(cfg: dict, reg: Registry, business_doc: str, suite_id: str,
                client=None, on_progress: ProgressFn | None = None,
                cases_per_task: int = 5) -> TestSuite:
    """Generator step: business doc → DRAFT suite + review file (human gate)."""
    kw = {"client": client} if client is not None else {}
    gen = BenchmarkGenerator(model=cfg["models"]["generator"], **kw)
    return gen.generate_suite(business_doc, suite_id=suite_id, registry=reg,
                              review_dir=cfg["paths"]["review_dir"],
                              on_progress=on_progress,
                              cases_per_task=cases_per_task)


def deploy_op(spec: dict, env_name: str = "ascore-workflows", client=None) -> dict:
    """Deploy/update a Managed Agents workflow agent. Create-once semantics:
    matching agent name updates (new immutable version); environment reused
    by name."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    env = next((e for e in client.beta.environments.list()
                if getattr(e, "name", "") == env_name), None)
    if env is None:
        env = client.beta.environments.create(
            name=env_name,
            config={"type": "cloud", "networking": {"type": "unrestricted"}})

    existing = next((a for a in client.beta.agents.list()
                     if getattr(a, "name", "") == spec["name"]), None)
    body = dict(
        name=spec["name"],
        model=spec["model"],
        system=spec.get("system", ""),
        tools=spec.get("tools", [{"type": "agent_toolset_20260401"}]),
    )
    if spec.get("skills"):
        body["skills"] = spec["skills"]
    if existing is None:
        agent = client.beta.agents.create(**body)
        action = "created"
    else:
        agent = client.beta.agents.update(existing.id, **body)
        action = "updated"
    return {
        "action": action, "name": spec["name"], "agent_id": agent.id,
        "version": agent.version, "environment_id": env.id,
        "model": spec["model"],
    }


def report_op(reg: Registry, scorecard_id: str) -> str:
    """Render a scorecard to client-ready Markdown (with regression diff)."""
    sc = reg.get_scorecard(scorecard_id)
    rubric = reg.get_rubric(sc.rubric_id, sc.rubric_version)
    history = reg.scorecards_for(sc.agent_id, sc.suite_id)
    previous = next((h for h in reversed(history)
                     if h.scorecard_id != sc.scorecard_id), None)
    return render_markdown(sc, rubric, previous)
