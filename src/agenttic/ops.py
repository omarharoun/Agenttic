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
from dataclasses import dataclass
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


def _parallelism(cfg: dict, section: str, default: int) -> int:
    """A stage's concurrency cap, from config, floored at 1.

    Every stage that fans out reads its own cap rather than sharing
    ``harness.max_parallel``: the harness paces agent runs, scoring paces judge
    calls, and generation paces generator calls. They hit different models with
    different rate limits, so one number can't pace all three."""
    raw = (cfg.get(section) or {}).get("max_parallel", default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def scoring_parallelism(cfg: dict) -> int:
    """How many cases are scored at once (``scoring.max_parallel``)."""
    return _parallelism(cfg, "scoring", 5)


def generator_parallelism(cfg: dict) -> int:
    """How many tasks are generated at once (``generator.max_parallel``)."""
    return _parallelism(cfg, "generator", 4)


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


def _errored_score(trace: Trace, case: TestCase, err: str) -> RunScore:
    """The one shape a case that could not be scored takes. Shared by the async
    batch path and the sequential one so an outage reads identically on both —
    a scoring-infra failure is kept and surfaced, never silently dropped and
    never scored as an agent failure."""
    return RunScore(trace_id=trace.trace_id, test_id=case.test_id,
                    criterion_scores=[], passed=False,
                    cost_usd=trace.total_cost_usd,
                    latency_ms=trace.total_latency_ms, steps=trace.total_steps,
                    scoring_error=err)


def _prepare_scoring(cfg: dict, reg: Registry, traces: list[Trace],
                     cases: list[TestCase], agent_model: str, *,
                     judge_client=None, rubric_override: Rubric | None = None,
                     fi_evaluator=None, fi_evaluate_fn=None):
    """Resolve the judge, the FI evaluator, and per-case (rubric, uncalibrated).

    Pure registry work — no model calls — done FIRST, in one sequential pass, so
    the per-rubric cache stays single-threaded and a missing rubric aborts the
    batch here rather than half-way through a fan-out with some cases already
    scored. Extracted so the concurrent path and the sequential one configure
    scoring identically; two copies of judge selection would be two chances to
    violate Hard Rule 4.
    """
    from agenttic.scoring.corpus import uncalibrated_criteria

    judge = make_judge(cfg, agent_model, client=judge_client)
    if fi_evaluator is None:
        from agenttic.scoring.fi_eval import FiEvaluator
        fi_evaluator = FiEvaluator(
            threshold=cfg.get("scoring", {}).get("fi_threshold", 0.5),
            evaluate_fn=fi_evaluate_fn)
    prepared, cache = [], {}
    for trace, case in zip(traces, cases):
        rubric = rubric_override or reg.get_rubric(case.rubric_id)
        # Hard Rule 6: mark provisional every criterion whose calibration isn't
        # demonstrated — all judge criteria, plus heuristic checks not proven by
        # the shipped calibration corpus. Computed once per rubric version.
        rkey = f"{rubric.rubric_id}:{rubric.version}"
        uncal = cache.get(rkey)
        if uncal is None:
            uncal = uncalibrated_criteria(
                [c.criterion_id for c in rubric.criteria],
                {c.criterion_id: c.scorer for c in rubric.criteria})
            cache[rkey] = uncal
        prepared.append((trace, case, rubric, uncal))
    return judge, fi_evaluator, prepared


def score_traces_sync(cfg: dict, reg: Registry, traces: list[Trace],
                      cases: list[TestCase], agent_model: str, *,
                      judge_client=None, pass_threshold: float = 0.7,
                      rubric_override: Rubric | None = None,
                      fi_evaluator=None, fi_evaluate_fn=None) -> list[RunScore]:
    """Score a batch WITHOUT an event loop. Same configuration, same errored-run
    shape, one after another.

    For the CDV loop this is not a preference, it is a requirement.
    ``run_until_closure`` drives its executor in a plain sequential ``for`` loop
    (``cdv.py:252``), so the async fan-out has nothing to overlap — one trace per
    call — and ``asyncio.run`` per scenario would build and tear down an event
    loop hundreds of times per run. It also cannot run under the network block
    the offline proof depends on: a selector event loop opens a
    ``socket.socketpair`` for its self-pipe, so a fixture that refuses
    ``socket.socket`` refuses the loop, and the scoring leg would silently
    degrade to "judge unavailable" on exactly the runs meant to prove the wiring
    works offline. A sequential batch needs no loop and has no such coupling.
    """
    judge, fi_evaluator, prepared = _prepare_scoring(
        cfg, reg, traces, cases, agent_model, judge_client=judge_client,
        rubric_override=rubric_override, fi_evaluator=fi_evaluator,
        fi_evaluate_fn=fi_evaluate_fn)
    out: list[RunScore] = []
    for trace, case, rubric, uncal in prepared:
        try:
            out.append(score_run(trace, case, rubric, judge, uncalibrated=uncal,
                                 pass_threshold=pass_threshold,
                                 fi_evaluator=fi_evaluator))
        except Exception as exc:  # noqa: BLE001 — scoring failure is data
            out.append(_errored_score(trace, case, f"{type(exc).__name__}: {exc}"))
    return out


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
    judge, fi_evaluator, prepared = _prepare_scoring(
        cfg, reg, traces, cases, agent_model, judge_client=judge_client,
        rubric_override=rubric_override, fi_evaluator=fi_evaluator,
        fi_evaluate_fn=fi_evaluate_fn)
    total = len(cases)

    # Scoring a case costs one judge call per judge criterion, and cases are
    # independent of each other — so run them concurrently instead of one after
    # another (this was the longest stretch of a run's wall clock). Bounded, not
    # an unbounded gather: the judge shares an API rate limit with everything
    # else, and unbounded fan-out only trades slowness for 429s.
    sem = asyncio.Semaphore(scoring_parallelism(cfg))

    async def score_one(i: int, trace, case, rubric, uncal) -> RunScore:
        async with sem:
            try:
                rs = await asyncio.to_thread(
                    score_run, trace, case, rubric, judge, uncalibrated=uncal,
                    pass_threshold=pass_threshold, fi_evaluator=fi_evaluator)
            except Exception as exc:  # noqa: BLE001 — scoring failure is data, not fatal
                err = f"{type(exc).__name__}: {exc}"
                if on_progress:
                    on_progress("case_error", {
                        "index": i, "total": total, "test_id": case.test_id,
                        "error": err,
                    })
                return _errored_score(trace, case, err)
            if on_progress:
                on_progress("case_scored", {
                    "index": i, "total": total, "test_id": case.test_id,
                    "passed": rs.passed,
                })
            return rs

    # gather preserves argument order, so RunScores still line up with traces
    # even though they finish out of order.
    return list(await asyncio.gather(
        *(score_one(i, *p) for i, p in enumerate(prepared))))


def _round4(x: float | None) -> float | None:
    """Round for the wire, keeping "not measured" distinct from zero."""
    return None if x is None else round(x, 4)


def verify_op(traces: list, *, cfg: dict | None = None,
              samples: list | None = None, cdv_result=None) -> tuple[list, dict]:
    """Run the SPEC-13 verification layer over a batch of traces.

    Deterministic and free: assertions (Step 62) and the baseline coverage model
    (Step 59) make **zero model calls**, so this runs on the normal path for every
    run. It is what lets a report lead with *what was never exercised* instead of
    a pass rate that is silent about everything the suite never tried.

    ``cfg`` is the loaded config, threaded through to the coverage model so the
    closure target comes from ``coverage.closure_target`` (Hard Rule 7) rather
    than a literal. Keyword-only and optional, because the SPEC-8 library API
    calls this from processes that have no config at all — but a caller that HAS
    one must pass it: without it the model measures against
    ``DEFAULT_CLOSURE_TARGET`` and says so in the log, and ``coverage/targets.py``
    deliberately will not go looking for a config file to guess with.

    **Non-results are verified against nothing.** A trace the adapter could not
    complete carries an execution-failure marker instead of an answer
    (``HARNESS_FAILURE:timeout``, ``BLACKBOX_FAILURE:ConnectionError`` — see
    ``coverage.collect.nonresult_marker``, which reads the prefix tuple straight
    off the scoring engine so all three subsystems agree on what a non-result is).
    Both legs here refuse them, for the same reason and with the same measured
    consequence:

    * **Coverage.** ``collect`` does the refusing (it is the boundary every caller
      crosses, including the CDV loop) and counts what it refused onto the report,
      which is where the three ``samples*`` keys below come from. A single harness
      failure had been reporting closure 5.2% on its own: the marker string is
      non-empty so it read as ``trajectory=direct_answer``, and a transport error
      saying "404 Not Found" read as ``data_condition=entity_not_found``.
    * **Assertions.** Filtered here, because this loop is the only caller. On a
      batch where every case died in transport, ``never_secret_in_output`` was
      scoring PASS — the marker string contains no secret — so a run that never
      reached the agent reported a property exercised and clean. That is the M40
      vacuity rule (unexercised is not a pass) failing at its own entry point. If
      NOTHING in the batch ran, no assertion result exists and the sign-off's
      assertion leg stays ``not_run``, which is the honest verdict.

    **The stimulus side** (``samples``). ``Sample`` carries three things — the
    trace, the realized scenario, and ``requested``, the abstract point the
    solver drew (``coverage/collect.py:35``) — and ``requested`` is the ONLY
    source of ``stimulus_hits``. This function built its samples as
    ``Sample(trace=t)`` and nothing else, so on every run the product has ever
    performed ``stimulus_closure`` was 0.0 and ``divergence()`` was ``[]``: *what
    we asked to test* versus *what the run exhibited* has never once been
    visible. A caller that HAS the scenarios (``cdv_op``) passes them here, and
    the two new summary keys report the gap. A scenario that requested a timeout
    and got a clean run is a DIVERGENCE, not coverage.

    The coverage MODEL stays ``baseline_model(cfg)`` either way, so the
    scorecard's coverage and the CDV loop's own report are the same computation
    over the same inputs and cannot disagree.

    **``cdv_result`` populates scope, not the gate.** It fills the sign-off's
    convergence and envelope legs, which were permanently ``not_run`` because no
    production caller ever passed one. ``signs_off`` (``schema/signoff.py:202``)
    binds on coverage closed + assertions populated with zero violations + zero
    formal counterexamples + no illegal-bin hits, and ``refusal_reasons`` mirrors
    it condition for condition. Neither leg is in that expression, and passing
    one here does not make the gate stricter — it makes the report true. Framing
    this as tightening certification would be an overclaim.

    Returns (assertion_results, coverage_summary). The coverage summary carries
    the serialized sign-off under ``"signoff"`` — see :func:`signoff_from_run`,
    which is what the signing gate evaluates."""
    from agenttic.coverage.collect import Sample, collect, nonresult_marker
    from agenttic.coverage.models.baseline import BASELINE_LIMITS, baseline_model
    from agenttic.verification.assertions import evaluate, rollup_assertions

    # Partition once, up front, so the two legs cannot disagree about which runs
    # happened. Non-results are still handed to `collect` — it is the component
    # that counts and names them, and routing them around it would drop the
    # disclosure on the floor.
    ran = [t for t in traces if nonresult_marker(t) is None]
    nonresults = [t for t in traces if nonresult_marker(t) is not None]

    results: list = []
    for t in ran:
        try:
            results.extend(evaluate(t))
        except Exception:  # noqa: BLE001 — verification must never break a run
            continue

    report = None
    summary: dict = {}
    try:
        report = collect(baseline_model(cfg=cfg),
                         list(samples) if samples is not None
                         else [Sample(trace=t) for t in traces])
        summary = {
            "model_ref": report.model_ref,
            "bins_fingerprint": report.bins_fingerprint,
            "baseline": True,
            "limits": BASELINE_LIMITS,
            # All three travel together, always. A renderer that shows
            # `trace_closure` without `non_results` is showing a figure over an
            # undisclosed denominator — which is the same over-report the
            # exclusion was made to remove, one layer further out.
            "samples": report.n_samples,
            "samples_submitted": report.n_submitted,
            "non_results": report.n_nonresults,
            "non_result_reasons": dict(sorted(report.nonresult_reasons.items())),
            "trace_closure": round(report.trace_closure, 4),
            # The other half of the two-number story. 0.0 with an empty
            # divergence list is the honest reading for a run whose caller held
            # no scenarios — nothing was requested, so nothing can be reported
            # unrequested. It is NOT a finding, and a renderer must not print it
            # as one.
            "stimulus_closure": round(report.stimulus_closure, 4),
            "stimulus_vs_trace_divergence": report.divergence(),
            "closure_target": report.closure_target,
            "closed": report.closed,
            # `closure` is None for a coverpoint nothing can feed, and the two
            # extra keys say which one and why. Every consumer of this blob — the
            # report, the console, the CLI — must render that as NOT MEASURED;
            # printing 0% would turn "we cannot see this" into "the suite missed
            # it", and printing the bins it happens to match would be the
            # over-report this split exists to remove.
            "not_measurable": report.not_measurable,
            "waived_bins": report.waived_bins(),
            "per_coverpoint": {
                cp.coverpoint_id: {"closure": _round4(cp.trace_closure),
                                   "unhit": cp.unhit, "other_hits": cp.other_hits,
                                   "not_measurable": not cp.measurable,
                                   "not_measurable_reason": cp.not_measurable_reason}
                for cp in report.coverpoints.values()},
            "crosses": {x.cross_id: round(x.closure, 4)
                        for x in report.crosses.values()},
            "holes": [{"kind": h.kind, "where": h.where, "what": h.what}
                      for h in report.holes()[:24]],
            "other_drift": report.other_drift(),
        }
    except Exception:  # noqa: BLE001
        report = None
        summary = {}

    if nonresults and "non_results" not in summary:
        # Coverage collection failed, so the report is not here to carry the
        # count — but the runs still did not happen, and this is the last place
        # that knows. The assertion leg below was filtered on exactly these
        # traces; a summary that stayed silent about them would leave a consumer
        # unable to tell a batch of 8 real runs from a batch of 10 where 2 died.
        counts: dict[str, int] = {}
        for t in nonresults:
            k = nonresult_marker(t) or "unknown"
            counts[k] = counts.get(k, 0) + 1
        summary["samples"] = len(ran)
        summary["samples_submitted"] = len(traces)
        summary["non_results"] = len(nonresults)
        summary["non_result_reasons"] = dict(sorted(counts.items()))

    if results:
        summary["assertions"] = rollup_assertions(results)

    # Build the sign-off here: this is the only place that holds the raw coverage
    # report AND the raw assertion results, which is exactly what build_signoff
    # consumes. Building it anywhere else would mean recomputing, and a sign-off
    # derived from a different computation could disagree with the report.
    try:
        from agenttic.schema.signoff import build_signoff
        agent_id = getattr(traces[0], "agent_id", "") if traces else ""
        cfg_hash = getattr(traces[0], "agent_config_hash", "") if traces else ""
        signoff = build_signoff(
            signoff_id=f"signoff-{cfg_hash[:12] or 'unpinned'}",
            agent_id=agent_id, agent_config_hash=cfg_hash,
            coverage_report=report,
            assertion_results=results or None,
            cdv_result=cdv_result)
        summary["signoff"] = signoff.model_dump(mode="json")
    except Exception:  # noqa: BLE001 — verification must never break a run
        pass
    return results, summary


def signoff_from_run(scorecard) -> "object | None":
    """Rebuild the sign-off a run recorded, for the signing gate to evaluate.

    Certification works from a stored scorecard and never holds the traces, so
    the sign-off has to survive the round-trip rather than be recomputed.
    Returns ``None`` for scorecards written before sign-offs were persisted —
    the caller must refuse rather than invent one.
    """
    from agenttic.schema.signoff import VerificationSignoff
    raw = getattr(scorecard, "signoff", None) or {}
    if not raw:
        raw = (getattr(scorecard, "coverage", None) or {}).get("signoff") or {}
    if not raw:
        return None
    try:
        return VerificationSignoff.model_validate(raw)
    except Exception:  # noqa: BLE001 — a corrupt sign-off must not be trusted
        return None


def aggregate_op(
    reg: Registry,
    *,
    agent_id: str,
    suite: TestSuite,
    rubric: Rubric,
    runs: list[RunScore],
    visibility: str,
    traces: list | None = None,
    cfg: dict | None = None,
    samples: list | None = None,
    cdv_result=None,
) -> Scorecard:
    """Aggregate RunScores into an immutable, persisted Scorecard.

    When ``traces`` are supplied the SPEC-13 verification layer runs too, so the
    scorecard carries coverage + assertion evidence and the report can lead with
    it (Hard Rule 56: closure, not pass rate, is the headline).

    ``cfg`` exists only to reach :func:`verify_op`'s coverage model with the
    configured closure target. Optional so no existing caller breaks; a caller
    that holds a config should pass it, or the run is scored against the built-in
    default while ``config.yaml`` says something else.

    ``samples`` and ``cdv_result`` are pass-throughs to :func:`verify_op` for the
    one caller that holds a stimulus side (:func:`cdv_op`). Both default to
    ``None``, so every existing caller is bit-identical."""
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
        assertions, coverage = verify_op(traces, cfg=cfg, samples=samples,
                                         cdv_result=cdv_result)
        sc = sc.model_copy(update={
            "assertions": assertions,
            "assertion_set_ref": "assertions:builtin-default@v1",
            "coverage": coverage,
            # promoted out of the coverage blob so certification can find it
            # without reaching through a summary dict
            "signoff": coverage.get("signoff") or {}})
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
    """The full run → score → aggregate chain (CLI `run`/`regress` behavior).

    Optionally followed by the honeypot battery — see
    :func:`_run_honeypot_battery`, off unless ``harness.honeypot_battery`` says
    otherwise.
    """
    from agenttic.server.tracing import span
    with span("run.suite", suite_id=suite_id, agent_id=adapter.agent_id):
        suite, cases, traces = await run_suite_op(
            cfg, reg, adapter, suite_id, version, on_progress)
        runs = await score_op(cfg, reg, traces, cases, agent_model_of(adapter),
                              on_progress, judge_client=judge_client)
        rubric = reg.get_rubric(cases[0].rubric_id)
        sc = aggregate_op(reg, agent_id=adapter.agent_id, suite=suite,
                          rubric=rubric, runs=runs, visibility=adapter.visibility,
                          traces=traces, cfg=cfg)
        _run_honeypot_battery(cfg, reg, adapter, sc)
        return sc


def _run_honeypot_battery(cfg: dict, reg: Registry, adapter: AgentAdapter,
                          scorecard) -> None:
    """Tempt the agent under test with decoy tools; file the result on ``sc``.

    P7's last mile. ``redteam/honeypot.py`` has always distinguished ``resisted``
    (a fact about the MODEL) from ``attempted_blocked`` (a fact about the
    HARNESS), and `report_op` has always rendered a stored battery — but nothing
    ran one against a real agent, so the distinction lived in dev tooling and
    never reached a customer's scorecard.

    **Off by default, and silence is the honest off-state.** The battery drives
    real probes against a real agent and spends money every run. When it does not
    run, no battery is stored and the report carries no harness section. That is
    deliberate and is `report_op`'s documented rule: synthesising a NOT MEASURED
    section for a battery that never ran would have to invent a posture and a
    decoy list, and would read as "we tested the harness and it was inconclusive"
    when nothing tested it. NOT MEASURED is the verdict for a battery that RAN
    and reached the enforcement path zero times — a finding, with a fix.

    **Never costs the run.** The scorecard is already aggregated and stored by the
    time this is called. A battery that raises must not retract a run that
    succeeded, so every failure is logged and swallowed — but never silently: an
    adapter the battery cannot instrument (`guarded_twin` accepts only
    `AnthropicSimpleAgent`) is a fact about coverage of the harness, not a crash,
    and it is logged as such.
    """
    if not bool((cfg.get("harness") or {}).get("honeypot_battery", False)):
        return
    import logging

    from agenttic.redteam.descriptor import descriptor_for_adapter
    from agenttic.redteam.honeypot import (
        AgentNotInstrumentable, run_honeypot_harness)
    log = logging.getLogger("agenttic.ops")
    # Read BEFORE the try, and defensively. The failure handlers below name the
    # scorecard, so reading it inside them would let the handler raise the very
    # exception it exists to contain — turning "the battery never costs the run"
    # into a promise that breaks precisely when something has already gone wrong.
    sc_id = getattr(scorecard, "scorecard_id", "<unknown>")
    agent_id = getattr(adapter, "agent_id", "<unknown>")
    try:
        # Discovered from the agent's own `describe()`, not hand-built here: a
        # descriptor written beside the adapter is a note about the agent, and
        # the battery has to plant decoys among the tools it ACTUALLY declares.
        descriptor = descriptor_for_adapter(adapter, agent_id=agent_id)
        report = run_honeypot_harness(descriptor, reg=reg, under_test=adapter)
        reg.save_honeypot_battery(sc_id, report.enforcement_result())
    except AgentNotInstrumentable as exc:
        log.warning(
            "harness battery NOT RUN for %s: %s — the run and its scorecard "
            "stand; the harness was not put on trial and the report says "
            "nothing about it either way", agent_id, exc)
    except Exception as exc:      # noqa: BLE001 — see the docstring
        log.warning(
            "harness battery FAILED for %s (scorecard %s): %s: %s — the run "
            "stands and no harness claim is made",
            agent_id, sc_id, type(exc).__name__, exc)


@dataclass
class CDVOutcome:
    """What one coverage-directed run produced. ``cdv`` is the loop's own result
    (closure, rounds, bug curve, frozen proposals), ``runs`` the per-scenario
    artifacts, ``scorecard`` the persisted evidence."""

    cdv: object
    runs: list
    scorecard: Scorecard
    #: where the frozen-regression PROPOSALS were written, or "" if there were
    #: none. Proposals only — promotion goes through the human gate (HR63).
    regressions_path: str = ""


def cdv_op(cfg: dict, reg: Registry, adapter: AgentAdapter, *, space,
           rubric: Rubric, run_scenario, coverage_model=None, policy=None,
           seed: int = 0, budget=None, judge_client=None, bias: bool = True,
           on_progress: ProgressFn | None = None) -> CDVOutcome:
    """Coverage-directed generation against a real agent (SPEC-13 Step 61).

    Generate a seeded batch from the scenario space, run each scenario against a
    real environment, extract coverage from **scenario + trace**, rank the holes,
    and aim the next batch at them — until the closure target or the budget.
    This is the loop ``verification/cdv.py`` has always implemented and never
    been given an executor for; :func:`agenttic.scenario.runner.harness_executor`
    is the executor.

    ``run_scenario`` is REQUIRED and keyword-only, with no default. A default
    that JSON-dumped ``scenario.text`` into one ``adapter.run()`` call would
    reproduce exactly the defect this exists to remove, and would do it behind a
    ``ConvergenceLeg(status="populated")``.

    ``policy`` defaults to the retail world's :data:`RETAIL_POLICY`, not to a
    bare ``PolicyDoc``: the default document names ``create_exchange`` /
    ``update_account`` / ``delete_account``, three tools no environment in this
    build has, and an expectation whose ``forbidden_tools`` cannot be called is a
    check that can never fail — vacuous, in exactly the sense M40 refuses for
    assertions.

    ``bias=False`` runs plain unbiased random. It is the control arm: "the solver
    aims at holes" is a claim, and the only way to check it is to run the same
    seeds without the aiming.

    **Re-measured after P4, and the number moved for the other reason.** The
    previous entry here said the aiming changed closure by roughly nothing
    because the top-ranked holes were the five ``tool_condition`` fault bins and
    nothing in this build could inject a fault — a fault-injector gap. The
    injector exists now (``scenario/faults.py``), and running the same three
    seeds (5/11/23, 60 scenarios, 6 rounds, ``--surface support``, scripted
    agent) with the injector disabled and enabled says which half that diagnosis
    got right:

    ===============================  ==============  ==============
    arm                              biased          unbiased
    ===============================  ==============  ==============
    injector off (the old build)     0.5846 ×3       0.5846 ×3
    injector on                      0.656/.693/.696 0.700/.742/.715
    ===============================  ==============  ==============

    The injector was worth +7 to +16 points of closure and is what took
    ``tool_condition`` from one creditable bin of six (0.1667, ``all_ok`` alone)
    to four or five, and the ``tool_x_trajectory`` cross from 0.0741 to
    0.24–0.39.

    **The aiming was a separate defect, and it has since been fixed.** It used to
    COST 2–5 points against random on every seed, for a reason in the round
    targets rather than the divergence rows: ``CoverageReport.holes`` gives every
    cross hole the same structural rank (3.0) and breaks the tie alphabetically,
    and ``holes_to_targets`` kept only the components that are stimulus
    dimensions — ``trajectory`` is not one. So the top of the list was
    ``all_ok×budget_exceeded``, ``all_ok×direct_answer``, … — seven cells whose
    only aimable component was ``all_ok``, the bin a suite gets for free. A batch
    of 10 consumed the first 10 holes, so every biased round in every seed aimed
    at ``tool_condition=all_ok`` and ``error_5xx``, spent 39 of 60 scenarios on
    ``all_ok`` (already hit, 36 exhibited), and asked for ``rate_limited`` zero
    times. Unbiased random spread across all six values and closed more.

    ``holes_to_targets`` now demotes components already exhibited, collapses
    duplicate target sets, advances through the ranked list across rounds instead
    of re-consuming the first ``batch_size``, and returns the holes it cannot
    steer as ``unaimable_holes`` rather than dropping them on a bare ``continue``.
    Measured on the same setup, and over 21 seeds because three is not evidence:

    ==================  ==============  ==============  ==============
    arm                 seeds 5/11/23   wins over 21    mean closure
    ==================  ==============  ==============  ==============
    before              .656/.693/.696  3/21            0.6783 (−0.0495)
    after               .745/.707/.754  10/21           0.7342 (+0.0063)
    unbiased control    .700/.742/.715  —               0.7279
    ==================  ==============  ==============  ==============

    Read that honestly: what is fixed is that aiming no longer costs closure. It
    still loses on 9 of 21 seeds and wins by less than a point on the mean, so
    directed generation is not yet a decisive lever, and a report that called it
    one would be claiming more than the table says.

    Provenance, because these numbers have already moved once since: the table
    above was measured with ``stale_data`` still staged on call #1 of
    ``lookup_order``, where a stale read is indistinguishable from a fresh one
    and the bin could not close. Re-running the three acceptance seeds after that
    was fixed (``realize._FAULT_CALL_INDEX``) gives biased .745/.707/.754 against
    unbiased .700/.742/.746 — still 2 of 3, with seed 23's UNBIASED arm gaining
    the difference (.7148 → .7457). The 21-seed means predate the fix and are not
    restated here as though they did not; whoever re-runs them should replace
    this paragraph rather than edit the table around it.

    The remaining ceiling is a PRODUCER gap again, not a targeting one. Under the
    support-surface pairing the solver asked for ``data_condition=ambiguous``
    14–24 times per seed for zero exhibits, and likewise ``contradictory`` — the
    retail world has no tool that answers "multiple matches" or contradicts
    itself, so no amount of aiming can reach them. Those rows are visible in
    ``report.divergence()``, which is the honest place for them; an "unproductive
    aim" rule that stopped pinning them was built, measured WORSE (0.7288 vs
    0.7345), and removed rather than shipped as a tuned constant that loses
    closure.

    **Every scenario it executes is persisted as a scenario run**, one row per
    scenario, by :func:`~agenttic.scenario.runner.persist_scenario_run` inside
    the executor. It is the only thing in ``src/`` besides the single-scenario
    CLI command that ever calls ``Registry.save_scenario_run``, and until it did,
    this loop — the only path that drives real scenarios against a real agent —
    dropped every transcript, fault report, state diff and blocked call on the
    floor. Additive: it changes neither what this returns nor the scorecard, and
    a storage failure is logged rather than raised (see that function).

    The ephemeral ``TestSuite`` this builds is NEVER persisted — it exists
    because ``Scorecard.aggregate`` needs a suite id and version. That does not
    route around the Step 8 human gate, which guards *running a stored suite*
    (``harness/runner.py``); generated CDV stimulus is not a stored suite, and
    Hard Rule 63's gate is on PROMOTION, honoured by writing proposals and
    nothing else.
    """
    import json
    from pathlib import Path

    from agenttic.coverage.models.baseline import baseline_model
    from agenttic.registry.sqlite_store import DuplicateVersionError
    from agenttic.scenario.runner import harness_executor
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.verification.cdv import Budget, run_until_closure

    cdv_cfg = dict(cfg.get("cdv") or {})
    coverage_model = coverage_model or baseline_model(cfg=cfg)
    if budget is None:
        budget = Budget(**{k: v for k, v in cdv_cfg.items()
                           if k in ("max_scenarios", "max_dollars", "max_rounds")})
    suite_id = f"cdv:{space.space_id}"
    # `coverage_model` is the one resolved above, handed to BOTH the executor
    # (which stores each run's exhibited bins and divergence) and
    # `run_until_closure` (which computes the round closure). Two models in one
    # run would be two answers to what a scenario covered.
    execute, runs = harness_executor(
        cfg, reg, adapter, rubric=rubric, run_scenario=run_scenario,
        suite_id=suite_id, judge_client=judge_client, on_progress=on_progress,
        coverage_model=coverage_model)

    res = run_until_closure(
        space, coverage_model, execute, budget, seed=seed,
        batch_size=int(cdv_cfg.get("batch_size", 10)),
        policy=policy if policy is not None else RETAIL_POLICY, bias=bias)

    # Vacuity guard. A flat bug curve produced by a detector that never ran is
    # the same error `unexercised` exists to prevent: it would print "0 distinct
    # failure signatures, curve FLAT" over a batch where nothing was ever
    # decided. The oracle counts as a detector — it is deterministic and needs no
    # judge — so the question is whether ANY verdict was reached, from either
    # source. NOT `frozen_regressions`: a scoring outage marks a run not-passed
    # (deny-by-default) and therefore freezes a proposal with signature
    # "unknown", so counting proposals would let the outage vouch for itself.
    detector_ran = any(
        (r.score is not None and r.score.scoring_error is None) or r.oracle_findings
        for r in runs)
    cdv_result = res if detector_ran else None

    try:
        reg.save_scenario_space(space)      # append-only; a re-run stores nothing
    except DuplicateVersionError:
        pass

    run_id = uuid.uuid4().hex[:12]
    regressions_path = ""
    if res.frozen_regressions:
        review_dir = Path((cfg.get("paths") or {}).get("review_dir") or "review")
        review_dir.mkdir(parents=True, exist_ok=True)
        path = review_dir / f"cdv-{space.space_id}-v{space.version}-{run_id}.json"
        path.write_text(json.dumps(
            {"run_id": run_id, "space_id": space.space_id,
             "space_version": space.version, "agent_id": adapter.agent_id,
             "note": ("PROPOSED regression cases. Nothing here is in a suite: "
                      "promotion is a human decision (Hard Rule 63)."),
             "regressions": [{"scenario": f.scenario, "seed": f.seed,
                              "signature": f.signature, "approved": f.approved}
                             for f in res.frozen_regressions]},
            indent=2, sort_keys=True))
        regressions_path = str(path)

    suite = TestSuite(suite_id=suite_id, version=space.version,
                      business_context=f"CDV stimulus over {space.ref()}",
                      test_ids=[r.scenario.scenario_id for r in runs],
                      approved=False)
    sc = aggregate_op(
        reg, agent_id=adapter.agent_id, suite=suite, rubric=rubric,
        runs=[r.score for r in runs if r.score is not None],
        visibility=adapter.visibility, traces=[r.trace for r in runs],
        samples=[r.sample() for r in runs], cdv_result=cdv_result, cfg=cfg)
    return CDVOutcome(cdv=res, runs=runs, scorecard=sc,
                      regressions_path=regressions_path)


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
                             max_parallel=generator_parallelism(cfg),
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
    """Render a scorecard to client-ready Markdown (with regression diff), plus
    the honeypot harness-enforcement section when a battery was stored for this
    scorecard (``Registry.save_honeypot_battery``).

    **No battery stored ⇒ no section, not a "not measured" section.** The two are
    different claims and blurring them is the honesty defect available here:

    * A stored battery that reached the harness zero times renders NOT MEASURED,
      and that is a *finding* — probes ran, the enforcement path was exercised
      zero times, and the reason names the fix (stronger lures, or a target that
      can be tempted).
    * A scorecard with no battery was never put on trial at all. Synthesising a
      NOT MEASURED section for it would have to invent a posture and a
      planted-decoy list for a battery that never ran, and would read to a
      customer as "we tested the harness and it was inconclusive" when nothing
      tested it.

    Absence of the section is not a pass either: nothing in this report claims
    the harness enforces anything unless the section says so. Whether a battery
    *should* have been run for a given agent is a sign-off question — ``schema.
    signoff`` legs carry an explicit ``not_run`` status for exactly that — not
    something a renderer can infer from a missing row."""
    sc, rubric, previous = _scorecard_with_context(reg, scorecard_id)
    return render_markdown(sc, rubric, previous,
                           harness=reg.find_honeypot_battery(scorecard_id))


def report_pdf_op(reg: Registry, scorecard_id: str) -> bytes:
    """Render a scorecard to a polished, on-brand PDF.

    Same content as the Markdown report **except** the honeypot
    harness-enforcement section: ``pdf_report.render_pdf`` has no ``harness=``
    parameter yet, so a battery stored for this scorecard shows up in
    :func:`report_op` and not here. Forwarding it from this side is not the fix —
    the PDF renders the verification section by borrowing ``_verification_block``
    from ``scorecard_report`` precisely so the two documents cannot drift in
    wording, and the harness section has to arrive the same way (borrowing
    ``_harness_enforcement_block``). Until that lands the PDF is silent about the
    harness rather than disagreeing with the Markdown about it."""
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
