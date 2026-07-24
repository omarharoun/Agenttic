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

from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.adapters.base import AgentAdapter
from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent
from agenttic.adapters.managed_agent import ManagedAgentAdapter
from agenttic.generator.pipeline import BenchmarkGenerator
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import Trace
from agenttic.scoring.engine import score_run
from agenttic.scoring.judge import make_judge

ProgressFn = Callable[[str, dict], None]

AdapterVariant = Literal["reference", "blackbox", "managed"]

#: User-facing message when a managed (Anthropic-hosted) agent is selected
#: without a deployed agent/environment. Shown verbatim in the guided UI.
MANAGED_UNAVAILABLE_MSG = (
    "Anthropic-hosted (managed) agents must be deployed first and aren't "
    "available yet — use the built-in test agent or your own API agent.")


class AgentConfigError(ValueError):
    """A user-facing problem with the agent-under-test configuration (e.g. a
    managed agent without its IDs, or a black-box agent without a URL). Carries
    a message safe to show directly in the UI; HTTP callers map it to a 400."""


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
    cost_per_call_usd: float = 0.0,
    expected_input_tokens: int = 0,
    expected_output_tokens: int = 0,
    headers: dict | None = None,
) -> AgentAdapter:
    """Instantiate the adapter for one agent under test. ``system_prompt``
    overrides the reference agent's task instructions and ``model`` overrides
    its model (both are part of the configuration under test and feed the trace
    config hash, so a declared agent that pins them is reproducible).

    Black-box agents expose no token usage, so their cost is whatever is
    declared: ``cost_per_call_usd`` (flat) or ``expected_*_tokens`` priced at
    ``model`` (or the default rate). Unset => cost stays 0 (unknown)."""
    from agenttic.retry import RetryPolicy
    retry_policy = RetryPolicy.from_cfg(cfg)
    if variant == "managed":
        if not environment_id:
            environment_id = cfg.get("managed", {}).get("environment_id", "")
        if not managed_agent_id or not environment_id:
            raise AgentConfigError(MANAGED_UNAVAILABLE_MSG)
        kw = {"client": client} if client is not None else {}
        return ManagedAgentAdapter(
            managed_agent_id=managed_agent_id, environment_id=environment_id,
            agent_id=agent_id, retry_policy=retry_policy, **kw)
    if variant == "blackbox":
        if not url:
            raise AgentConfigError("Add the HTTP endpoint URL for your API agent.")
        allow_private = not cfg.get("security", {}).get("blackbox_block_private", True)
        return BlackBoxHTTPAgent(
            agent_id=agent_id, url=url, allow_private_url=allow_private,
            headers=headers or None,
            cost_per_call_usd=blackbox_call_cost(
                cfg, cost_per_call_usd=cost_per_call_usd, model=model,
                expected_input_tokens=expected_input_tokens,
                expected_output_tokens=expected_output_tokens))
    kw = {"client": client} if client is not None else {}
    resolved_model = model or cfg["models"]["agent_default"]
    from agenttic.pricing import model_price
    return AnthropicSimpleAgent(model=resolved_model,
                                kb_path="kb.json", agent_id=agent_id,
                                max_steps=cfg["harness"]["max_steps"],
                                pricing_per_mtok=model_price(cfg, resolved_model),
                                system_prompt=system_prompt or None,
                                retry_policy=retry_policy, **kw)


def blackbox_call_cost(cfg: dict, *, cost_per_call_usd: float = 0.0,
                       model: str = "", expected_input_tokens: int = 0,
                       expected_output_tokens: int = 0) -> float:
    """Resolve a black-box agent's per-call cost from its declared hints:
    a flat cost wins; else expected tokens priced at ``model`` (or default);
    else 0 (unknown)."""
    if cost_per_call_usd:
        return float(cost_per_call_usd)
    if expected_input_tokens or expected_output_tokens:
        from agenttic.pricing import token_cost
        return token_cost(cfg, model or None,
                          expected_input_tokens, expected_output_tokens)
    return 0.0


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
    """Harness step: execute every case of a suite, persisting all traces.

    Enforces the spend ceiling: a pre-run estimate gate (raises
    BudgetExceededError before any spend if projected cost breaches a cap,
    unless budget.warn_only) and a RunBudget that aborts remaining cases once
    actual execution cost crosses the per-run cap."""
    from agenttic.budget import RunBudget, check_pre_run
    from agenttic.cost import estimate_for_run

    suite, cases = reg.get_suite(suite_id, version)
    variant = "blackbox" if adapter.visibility == "black_box" else "reference"
    est = estimate_for_run(cfg, reg, suite_id, variant=variant,
                           model=getattr(adapter, "model", None),
                           bb_call_cost=getattr(adapter, "cost_per_call_usd", 0.0),
                           version=version)
    warnings = check_pre_run(cfg, reg, est.projected_usd)  # may raise
    if warnings and on_progress:
        on_progress("budget_warning", {"warnings": warnings,
                                       "projected_usd": est.projected_usd})

    max_run = float(cfg.get("budget", {}).get("max_run_cost_usd", 0) or 0)
    h = cfg["harness"]
    traces = await run_suite(
        adapter, suite, cases, reg,
        HarnessConfig(timeout_seconds=h["timeout_seconds"],
                      max_parallel=h["max_parallel"],
                      transport_retries=h["transport_retries"]),
        on_event=on_progress,
        budget=RunBudget(max_run_usd=max_run) if max_run else None,
        resume=True,  # resilience is mandatory — resume is always on
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
        from agenttic.scoring.fi_eval import FiEvaluator
        fi_evaluator = FiEvaluator(
            threshold=cfg.get("scoring", {}).get("fi_threshold", 0.5),
            evaluate_fn=fi_evaluate_fn)
    from agenttic.scoring.corpus import uncalibrated_criteria
    runs: list[RunScore] = []
    total = len(cases)
    _uncal_cache: dict[str, set[str]] = {}
    for i, (trace, case) in enumerate(zip(traces, cases)):
        rubric = rubric_override or reg.get_rubric(case.rubric_id)
        # Hard Rule 6: mark provisional every criterion whose calibration isn't
        # demonstrated — all judge criteria, plus heuristic checks not proven by
        # the shipped calibration corpus. Computed once per rubric version.
        rkey = f"{rubric.rubric_id}:{rubric.version}"
        uncal = _uncal_cache.get(rkey)
        if uncal is None:
            uncal = uncalibrated_criteria(
                [c.criterion_id for c in rubric.criteria],
                {c.criterion_id: c.scorer for c in rubric.criteria})
            _uncal_cache[rkey] = uncal
        try:
            rs = await asyncio.to_thread(
                score_run, trace, case, rubric, judge, uncalibrated=uncal,
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


def verify_op(traces: list) -> tuple[list, dict]:
    """Run the SPEC-13 verification layer over a batch of traces.

    Deterministic and free: assertions (Step 62) and the baseline coverage model
    (Step 59) make **zero model calls**, so this runs on the normal path for every
    run. It is what lets a report lead with *what was never exercised* instead of
    a pass rate that is silent about everything the suite never tried.

    Returns (assertion_results, coverage_summary)."""
    from agenttic.coverage.collect import Sample, collect
    from agenttic.coverage.models.baseline import BASELINE_LIMITS, baseline_model
    from agenttic.verification.assertions import evaluate, rollup_assertions

    results: list = []
    for t in traces:
        try:
            results.extend(evaluate(t))
        except Exception:  # noqa: BLE001 — verification must never break a run
            continue

    summary: dict = {}
    try:
        report = collect(baseline_model(), [Sample(trace=t) for t in traces])
        summary = {
            "model_ref": report.model_ref,
            "bins_fingerprint": report.bins_fingerprint,
            "baseline": True,
            "limits": BASELINE_LIMITS,
            "trace_closure": round(report.trace_closure, 4),
            "closure_target": report.closure_target,
            "closed": report.closed,
            "per_coverpoint": {
                cp.coverpoint_id: {"closure": round(cp.trace_closure, 4),
                                   "unhit": cp.unhit, "other_hits": cp.other_hits}
                for cp in report.coverpoints.values()},
            "crosses": {x.cross_id: round(x.closure, 4)
                        for x in report.crosses.values()},
            "holes": [{"kind": h.kind, "where": h.where, "what": h.what}
                      for h in report.holes()[:24]],
            "other_drift": report.other_drift(),
        }
    except Exception:  # noqa: BLE001
        summary = {}

    if results:
        summary["assertions"] = rollup_assertions(results)
    return results, summary


def aggregate_op(
    reg: Registry,
    *,
    agent_id: str,
    suite: TestSuite,
    rubric: Rubric,
    runs: list[RunScore],
    visibility: str,
    traces: list | None = None,
) -> Scorecard:
    """Aggregate RunScores into an immutable, persisted Scorecard.

    When ``traces`` are supplied the SPEC-13 verification layer runs too, so the
    scorecard carries coverage + assertion evidence and the report can lead with
    it (Hard Rule 56: closure, not pass rate, is the headline)."""
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=agent_id,
        suite_id=suite.suite_id, suite_version=suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=runs, visibility_tier=visibility)
    if traces is None:
        # Resolve traces from the registry so EVERY caller gets verification —
        # the server run-node (which the console uses) and the red-team paths
        # aggregate from RunScores and never held Trace objects. Without this the
        # verification layer would silently never reach the console.
        traces = []
        for r in runs:
            if not r.trace_id:
                continue
            try:
                traces.append(reg.get_trace(r.trace_id))
            except Exception:  # noqa: BLE001 — a missing trace must not break scoring
                continue
    if traces:
        assertions, coverage = verify_op(traces)
        sc = sc.model_copy(update={
            "assertions": assertions,
            "assertion_set_ref": "assertions:builtin-default@v1",
            "coverage": coverage})
    reg.save_scorecard(sc)
    # record total spend (execution + scoring) for the daily budget ledger
    total_spend = sc.total_cost_usd + sc.total_scoring_cost_usd
    reg.record_spend(agent_id, total_spend)
    try:  # observability counters (best-effort; never block a scorecard)
        from agenttic.server import metrics
        metrics.record_run("errored" if sc.errored_test_ids else "completed")
        metrics.record_cost(total_spend)
    except Exception:  # noqa: BLE001
        pass
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
    from agenttic.server.tracing import span
    with span("run.suite", suite_id=suite_id, agent_id=adapter.agent_id):
        suite, cases, traces = await run_suite_op(
            cfg, reg, adapter, suite_id, version, on_progress)
        runs = await score_op(cfg, reg, traces, cases, agent_model_of(adapter),
                              on_progress, judge_client=judge_client)
        rubric = reg.get_rubric(cases[0].rubric_id)
        return aggregate_op(reg, agent_id=adapter.agent_id, suite=suite,
                            rubric=rubric, runs=runs, visibility=adapter.visibility,
                            traces=traces)


def generate_op(cfg: dict, reg: Registry, business_doc: str, suite_id: str,
                client=None, on_progress: ProgressFn | None = None,
                cases_per_task: int = 8) -> TestSuite:
    """Generator step: business doc → DRAFT suite + review file (human gate).
    ``cases_per_task`` is an upper bound; the generator decides the actual
    count per task within the pipeline's MIN_CASES..bound range."""
    kw = {"client": client} if client is not None else {}
    from agenttic.pricing import model_price
    from agenttic.retry import RetryPolicy
    gen = BenchmarkGenerator(model=cfg["models"]["generator"],
                             retry_policy=RetryPolicy.from_cfg(cfg),
                             pricing_per_mtok=model_price(cfg, cfg["models"]["generator"]),
                             **kw)
    return gen.generate_suite(business_doc, suite_id=suite_id, registry=reg,
                              review_dir=cfg["paths"]["review_dir"],
                              on_progress=on_progress,
                              cases_per_task=cases_per_task)


def deploy_op(spec: dict, env_name: str = "agenttic-workflows", client=None) -> dict:
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


def _scorecard_with_context(reg: Registry, scorecard_id: str):
    sc = reg.get_scorecard(scorecard_id)
    rubric = reg.get_rubric(sc.rubric_id, sc.rubric_version)
    history = reg.scorecards_for(sc.agent_id, sc.suite_id)
    previous = next((h for h in reversed(history)
                     if h.scorecard_id != sc.scorecard_id), None)
    return sc, rubric, previous


def report_op(reg: Registry, scorecard_id: str) -> str:
    """Render a scorecard to client-ready Markdown (with regression diff)."""
    return render_markdown(*_scorecard_with_context(reg, scorecard_id))


def report_pdf_op(reg: Registry, scorecard_id: str) -> bytes:
    """Render a scorecard to a polished, on-brand PDF (same content as Markdown)."""
    from agenttic.reporting.pdf_report import render_pdf
    return render_pdf(*_scorecard_with_context(reg, scorecard_id))


def inspect_log_op(reg: Registry, scorecard_id: str) -> dict:
    """Export a scorecard as an Inspect (``inspect_ai``) ``EvalLog`` JSON dict.

    Pulls the scorecard, its rubric, and every referenced trace from the
    registry and renders them via :func:`agenttic.interop.to_inspect_log`. Missing
    traces are simply omitted (the scores still export). Returns a plain dict
    that validates against ``inspect_ai.log.EvalLog`` — no runtime dependency on
    ``inspect_ai``."""
    from agenttic.interop import to_inspect_log
    from agenttic.registry.sqlite_store import NotFoundError

    sc = reg.get_scorecard(scorecard_id)
    try:
        rubric = reg.get_rubric(sc.rubric_id, sc.rubric_version)
    except NotFoundError:
        rubric = None
    traces: list[Trace] = []
    for run in sc.run_scores:
        try:
            traces.append(reg.get_trace(run.trace_id))
        except NotFoundError:
            continue
    return to_inspect_log(sc, rubric=rubric, traces=traces)


async def run_standard_op(cfg: dict, reg: Registry, *, agent_id: str, k: int = 3,
                          variant: str = "reference", url: str = "",
                          system_prompt: str = "", model: str = "",
                          client=None, judge_client=None, fi_evaluate_fn=None,
                          on_progress=None, persist: bool = True,
                          cache_key: str | None = None) -> dict:
    """Run the canonical suites k times for an agent and persist the full
    Agenttic Index (incl. pass^k + ECE). Seeds the standard suites if absent.
    When ``cache_key`` is given, the completed run is recorded in the result
    cache so an identical re-run is served for free."""
    import json

    from agenttic.metrics.redteam import seed_redteam_injection_suite
    from agenttic.metrics.runner import run_standard
    from agenttic.metrics.standard_suites import seed_standard_suites
    seed_standard_suites(reg)  # ensure the std suites exist (idempotent)
    seed_redteam_injection_suite(reg)  # + the red-team injection probe set
    # + the content-safety suite (PII/secret/profanity/system-prompt + provisional
    # toxicity/bias/unsafe-content judges) — feat/metrics-safety.
    from agenttic.metrics.safety_suite import seed_safety_content_suite
    seed_safety_content_suite(reg)
    adapter = build_adapter(cfg, variant=variant, agent_id=agent_id, url=url,
                            system_prompt=system_prompt, model=model, client=client)
    result = await run_standard(cfg, reg, adapter, k=k,
                                judge_client=judge_client or client,
                                fi_evaluate_fn=fi_evaluate_fn, on_progress=on_progress)
    if persist:
        reg.save_canonical_run(result["run_id"], agent_id, json.dumps(result))
        if cache_key:
            try:
                reg.put_cached_result(cache_key, "canonical", result["run_id"])
            except Exception:  # noqa: BLE001 — caching is best-effort
                pass
    return result


def standard_index_op(reg: Registry) -> list[dict]:
    """Per-agent canonical Agenttic Index over the standard suites.

    Prefers a full canonical run (with pass^k + ECE) when one exists; otherwise
    falls back to a partial index rolled from the latest standard scorecards
    (the rubric-based metrics only — pass^k/ECE reported as missing)."""
    canonical = reg.latest_canonical_runs()
    if canonical:
        return canonical

    from agenttic.metrics.index import compute_index, rollup_metrics_from_means
    from agenttic.metrics.standard_suites import standard_suite_ids

    latest: dict[tuple[str, str], object] = {}
    for sc in reg.scorecards_in(standard_suite_ids()):  # oldest-first => last wins
        latest[(sc.agent_id, sc.suite_id)] = sc

    by_agent: dict[str, list] = {}
    for (agent, _suite), sc in latest.items():
        by_agent.setdefault(agent, []).append(sc)

    out = []
    for agent, scs in by_agent.items():
        acc: dict[str, list[float]] = {}
        for sc in scs:
            for cid, mean in sc.per_criterion_means.items():
                acc.setdefault(cid, []).append(mean)
        means = {cid: sum(v) / len(v) for cid, v in acc.items()}
        idx = compute_index(rollup_metrics_from_means(means))
        out.append({"agent_id": agent, "suites_run": sorted({sc.suite_id for sc in scs}),
                    **idx})
    out.sort(key=lambda r: r["index"], reverse=True)
    return out


def ab_report_op(reg: Registry, comparison_id: str) -> str:
    """Render an A/B comparison to client-ready Markdown."""
    from agenttic.reporting.ab_report import render_ab_markdown
    return render_ab_markdown(reg.get_ab_comparison(comparison_id))


def ab_report_pdf_op(reg: Registry, comparison_id: str) -> bytes:
    """Render an A/B comparison to a polished, on-brand PDF."""
    from agenttic.reporting.ab_report import render_ab_pdf
    return render_ab_pdf(reg.get_ab_comparison(comparison_id))
