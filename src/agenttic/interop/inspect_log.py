"""Inspect interop — convert agenttic scorecards/traces ⇄ Inspect ``EvalLog``.

`Inspect <https://inspect.aisi.org.uk/>`_ (UK AI Safety Institute's
``inspect_ai``) is the de-facto research harness for agent/LLM evals and the
home of the ``inspect_evals`` catalog. Emitting our results as an Inspect
``EvalLog`` means anyone can open them in the Inspect viewer, re-score them, or
compare them against the catalog without trusting our tooling — the biggest
external-credibility lever in docs/RESEARCH_TESTING_SURVEY.md.

We do **not** depend on ``inspect_ai`` at runtime: this module emits and parses
the documented ``EvalLog`` JSON schema directly (a plain ``dict`` that
``inspect_ai.log.EvalLog.model_validate`` accepts and ``read_eval_log`` loads).
The test-suite, if ``inspect_ai`` is installed, validates every produced log
against the real pydantic models and round-trips it through ``write_eval_log`` /
``read_eval_log``; otherwise it validates against the documented shape.

Why the Task/Solver/Scorer split maps 1:1
-----------------------------------------
Inspect models an eval as *a dataset of Samples, solved by a Solver, graded by
Scorers, aggregated into Results*. agenttic uses the same decomposition:

============================  ==============================================
agenttic                      Inspect
============================  ==============================================
``TestSuite`` / ``TestCase``  ``Task`` / dataset of ``Sample``
``Trace`` (one agent run)     a ``Sample``'s ``messages`` + ``output``
``Rubric`` / ``Criterion``    ``Scorer`` (one ``Score`` per criterion)
``RunScore`` / ``CriterionScore``  a ``Sample``'s ``scores`` dict
``Scorecard`` (aggregates)    ``EvalResults`` (metrics)
agent under test (``agent_id``)    ``model`` (the evaluated subject)
============================  ==============================================

What maps 1:1 (round-trips with no loss)
----------------------------------------
For an **agenttic-origin** log (one this module produced), the following recover
their exact values via :func:`from_inspect_log`:

* Scorecard identity & aggregates: ``scorecard_id`` (↔ ``eval.run_id``),
  ``agent_id`` (↔ ``eval.model``), ``suite_id`` (↔ ``eval.task``),
  ``suite_version`` (↔ ``eval.task_version``), ``rubric_id``, ``rubric_version``,
  ``visibility_tier``, ``created_at``, ``task_success_rate``, ``mean_cost_usd``,
  ``total_cost_usd``, ``total_scoring_cost_usd``, ``p95_latency_ms``,
  ``per_criterion_means``, ``errored_test_ids``.
* Per run (``RunScore``): ``test_id``, ``trace_id``, ``passed``, ``cost_usd``,
  ``scoring_cost_usd``, ``latency_ms``, ``steps``, ``scoring_error``.
* Per criterion (``CriterionScore``): ``criterion_id``, ``score``, ``scorer``,
  ``calibrated``, ``judge_rationale``, ``cost_usd``.
* The full ``Trace`` (every ``Span``, timings, IO, tokens, attributes) — and the
  ``Rubric`` if supplied.

The agenttic-specific values that Inspect has no native slot for live under an
``agenttic`` namespace inside the standard ``metadata`` dicts
(``eval.metadata``, ``results.metadata``, ``sample.metadata``). ``metadata`` is
a first-class part of the Inspect schema, so this is faithful, not a side-car.

Lossy edges (documented, by design)
-----------------------------------
* **Native messages are a projection, not the source of truth.** Each sample's
  ``messages``/``output``/``model_usage`` are rendered from the trace so foreign
  Inspect viewers see a real transcript, but a trace's span *tree* (parent/child
  structure, per-span IO and attributes) is flattened. Round-trip stays lossless
  only because the untouched spans are preserved in
  ``sample.metadata.agenttic.spans``; the flattened ``messages`` view alone is
  lossy.
* **Foreign EvalLogs** (not produced by agenttic) recover only the mappable
  subset: ``score`` values are snapped to agenttic's ``{0, 0.5, 1}`` scale
  (Hard Rule 3), aggregates are recomputed via ``Scorecard.aggregate`` rather
  than read back, and Inspect features with no agenttic equivalent (``events``,
  ``store``, ``attachments``, ``sandbox``, ``choices``, score ``reducers``,
  multi-epoch sampling) are dropped. Each dropped item is recorded under the
  reconstructed run's trace ``attributes`` where practical.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace

# Bump when the metadata layout below changes incompatibly.
INTEROP_VERSION = 1

# Inspect EvalLog format version we emit (inspect_ai's current major).
_EVAL_LOG_VERSION = 2

_ALLOWED_SCORES = (0.0, 0.5, 1.0)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# agenttic  ->  Inspect EvalLog
# --------------------------------------------------------------------------- #


def _span_to_messages(span: Span) -> list[dict[str, Any]]:
    """Project one span onto zero or more Inspect chat messages (lossy view —
    the lossless span is kept in sample metadata)."""
    def _text(d: dict) -> str:
        for key in ("completion", "text", "content", "output", "response"):
            v = d.get(key)
            if isinstance(v, str):
                return v
        return json.dumps(d, default=str) if d else ""

    if span.kind == "llm_call":
        return [{"role": "assistant", "content": _text(span.output)}]
    if span.kind in ("tool_call", "retrieval"):
        return [{
            "role": "tool",
            "content": _text(span.output),
            "function": span.name,
            "tool_call_id": span.span_id,
        }]
    if span.kind == "error":
        return [{"role": "assistant",
                 "content": f"[error] {span.error or span.name}"}]
    if span.kind == "agent_decision":
        return [{"role": "assistant", "content": _text(span.output) or span.name}]
    return []  # final_output is carried by the sample's `output`, not a message


def _trace_model_usage(trace: Trace) -> dict[str, Any]:
    tin = sum(s.tokens_in or 0 for s in trace.spans)
    tout = sum(s.tokens_out or 0 for s in trace.spans)
    if not (tin or tout or trace.total_cost_usd):
        return {}
    usage: dict[str, Any] = {}
    if tin:
        usage["input_tokens"] = tin
    if tout:
        usage["output_tokens"] = tout
    if tin or tout:
        usage["total_tokens"] = tin + tout
    if trace.total_cost_usd:
        usage["total_cost"] = trace.total_cost_usd
    return usage


def _sample(run: RunScore, trace: Trace | None, case: TestCase | None,
            epoch: int) -> dict[str, Any]:
    # native (lossy) transcript projection
    messages: list[dict[str, Any]] = []
    output: dict[str, Any] = {}
    model_usage: dict[str, Any] = {}
    if trace is not None:
        for span in sorted(trace.spans, key=lambda s: s.start_time):
            messages.extend(_span_to_messages(span))
        completion = trace.final_output
        output = {
            "model": trace.agent_id,
            "completion": completion,
            "choices": [{
                "message": {"role": "assistant", "content": completion},
                "stop_reason": "stop",
            }],
        }
        usage = _trace_model_usage(trace)
        if usage:
            output["usage"] = usage
            model_usage = {trace.agent_id: usage}
        messages.append({"role": "assistant", "content": completion})

    # one Inspect Score per criterion
    scores: dict[str, Any] = {}
    for cs in run.criterion_scores:
        scores[cs.criterion_id] = {
            "value": cs.score,
            "answer": None,
            "explanation": cs.judge_rationale,
            "metadata": {"scorer": cs.scorer, "calibrated": cs.calibrated,
                         "cost_usd": cs.cost_usd},
        }

    # agenttic-native fields with no Inspect slot -> standard metadata dict
    agenttic_meta: dict[str, Any] = {
        "interop_version": INTEROP_VERSION,
        "trace_id": run.trace_id,
        "test_id": run.test_id,
        "passed": run.passed,
        "cost_usd": run.cost_usd,
        "scoring_cost_usd": run.scoring_cost_usd,
        "latency_ms": run.latency_ms,
        "steps": run.steps,
        "scoring_error": run.scoring_error,
    }
    if trace is not None:
        agenttic_meta["trace"] = {
            "trace_id": trace.trace_id,
            "agent_id": trace.agent_id,
            "agent_config_hash": trace.agent_config_hash,
            "test_case_id": trace.test_case_id,
            "visibility": trace.visibility,
            "final_output": trace.final_output,
            "total_cost_usd": trace.total_cost_usd,
            "total_latency_ms": trace.total_latency_ms,
            "total_steps": trace.total_steps,
            "schema_version": trace.schema_version,
            "spans": [s.model_dump(mode="json") for s in trace.spans],
        }

    sample: dict[str, Any] = {
        "id": run.test_id,
        "epoch": epoch,
        "input": (case.task_description if case else ""),
        "target": (json.dumps(case.expected, default=str)
                   if case and case.expected is not None else ""),
        "messages": messages,
        "output": output,
        "scores": scores,
        "metadata": {"agenttic": agenttic_meta},
        "model_usage": model_usage,
    }
    if case is not None:
        sample["metadata"]["agenttic"]["testcase"] = case.model_dump(mode="json")
    if run.latency_ms:
        sample["total_time"] = run.latency_ms / 1000.0
    if run.scoring_error:
        sample["error"] = {"message": run.scoring_error,
                           "traceback": "", "traceback_ansi": ""}
    return sample


def _criterion_scorer_kind(rubric: Rubric | None, cid: str) -> str:
    if rubric is not None:
        for c in rubric.criteria:
            if c.criterion_id == cid:
                return c.scorer
    return "agenttic"


def to_inspect_log(
    scorecard: Scorecard,
    *,
    rubric: Rubric | None = None,
    traces: list[Trace] | None = None,
    testcases: list[TestCase] | None = None,
    status: str = "success",
) -> dict[str, Any]:
    """Render an agenttic :class:`Scorecard` (plus its traces/rubric/testcases,
    when available) as an Inspect ``EvalLog`` JSON ``dict``.

    The returned dict validates against ``inspect_ai.log.EvalLog`` and can be
    written verbatim with :func:`json.dump` or ``inspect_ai.log.write_eval_log``.
    Pass ``traces``/``testcases``/``rubric`` to enrich the export — they are
    matched to runs by ``trace_id`` / ``test_id`` / ``criterion_id``. Omitting
    them still yields a valid log (scores + aggregates only).
    """
    traces_by_id = {t.trace_id: t for t in (traces or [])}
    cases_by_id = {c.test_id: c for c in (testcases or [])}

    samples: list[dict[str, Any]] = []
    epoch_for: dict[str, int] = {}
    span_starts: list[datetime] = []
    span_ends: list[datetime] = []
    total_usage: dict[str, int] = {}
    for run in scorecard.run_scores:
        epoch_for[run.test_id] = epoch_for.get(run.test_id, 0) + 1
        trace = traces_by_id.get(run.trace_id)
        case = cases_by_id.get(run.test_id)
        samples.append(_sample(run, trace, case, epoch_for[run.test_id]))
        if trace is not None:
            for s in trace.spans:
                span_starts.append(s.start_time)
                span_ends.append(s.end_time)
                if s.tokens_in:
                    total_usage["input_tokens"] = (
                        total_usage.get("input_tokens", 0) + s.tokens_in)
                if s.tokens_out:
                    total_usage["output_tokens"] = (
                        total_usage.get("output_tokens", 0) + s.tokens_out)

    # results.scores: one EvalScore per criterion + an overall success metric.
    eval_scores: list[dict[str, Any]] = []
    for cid, mean in scorecard.per_criterion_means.items():
        eval_scores.append({
            "name": cid,
            "scorer": _criterion_scorer_kind(rubric, cid),
            "metrics": {"mean": {"name": "mean", "value": mean}},
            "metadata": {"agenttic_metric": "per_criterion_mean"},
        })
    eval_scores.append({
        "name": "task_success_rate",
        "scorer": "agenttic",
        "metrics": {"accuracy": {"name": "accuracy",
                                 "value": scorecard.task_success_rate}},
        "metadata": {"agenttic_metric": "task_success_rate"},
    })

    scored = [r for r in scorecard.run_scores if r.scoring_error is None]
    results = {
        "total_samples": len(scorecard.run_scores),
        "completed_samples": len(scored),
        "scores": eval_scores,
        "metadata": {"agenttic": {
            "interop_version": INTEROP_VERSION,
            "scorecard_id": scorecard.scorecard_id,
            "agent_id": scorecard.agent_id,
            "suite_id": scorecard.suite_id,
            "suite_version": scorecard.suite_version,
            "rubric_id": scorecard.rubric_id,
            "rubric_version": scorecard.rubric_version,
            "task_success_rate": scorecard.task_success_rate,
            "mean_cost_usd": scorecard.mean_cost_usd,
            "total_cost_usd": scorecard.total_cost_usd,
            "total_scoring_cost_usd": scorecard.total_scoring_cost_usd,
            "p95_latency_ms": scorecard.p95_latency_ms,
            "per_criterion_means": scorecard.per_criterion_means,
            "errored_test_ids": scorecard.errored_test_ids,
            "visibility_tier": scorecard.visibility_tier,
            "created_at": _iso(scorecard.created_at),
        }},
    }

    started = _iso(min(span_starts)) if span_starts else _iso(scorecard.created_at)
    completed = _iso(max(span_ends)) if span_ends else _iso(scorecard.created_at)
    stats: dict[str, Any] = {"started_at": started, "completed_at": completed}
    if total_usage:
        total_usage["total_tokens"] = (total_usage.get("input_tokens", 0)
                                       + total_usage.get("output_tokens", 0))
        stats["model_usage"] = {scorecard.agent_id: total_usage}

    eval_metadata: dict[str, Any] = {"agenttic": {
        "interop_version": INTEROP_VERSION,
        "exporter": "agenttic",
        "visibility_tier": scorecard.visibility_tier,
    }}
    if rubric is not None:
        eval_metadata["agenttic"]["rubric"] = rubric.model_dump(mode="json")

    spec = {
        "run_id": scorecard.scorecard_id,
        "created": _iso(scorecard.created_at),
        "task": scorecard.suite_id,
        "task_id": scorecard.suite_id,
        "task_version": scorecard.suite_version,
        "solver": scorecard.agent_id,
        "model": scorecard.agent_id,
        "dataset": {
            "name": scorecard.suite_id,
            "samples": len(scorecard.run_scores),
            "sample_ids": [r.test_id for r in scorecard.run_scores],
        },
        "config": {},
        "metadata": eval_metadata,
    }

    return {
        "version": _EVAL_LOG_VERSION,
        "status": status,
        "eval": spec,
        "plan": {"name": "agenttic",
                 "steps": [{"solver": scorecard.agent_id, "params": {}}]},
        "results": results,
        "stats": stats,
        "samples": samples,
    }


# --------------------------------------------------------------------------- #
# Inspect EvalLog  ->  agenttic
# --------------------------------------------------------------------------- #


def _coerce_score(value: Any) -> float:
    """Snap an arbitrary Inspect ``Value`` to agenttic's {0, 0.5, 1} scale
    (Hard Rule 3). Lossy for foreign logs; documented."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        if v >= 0.75:
            return 1.0
        if v <= 0.25:
            return 0.0
        return 0.5
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("c", "correct", "pass", "passed", "true", "yes", "1", "i_pass"):
            return 1.0
        if s in ("p", "partial", "half", "0.5"):
            return 0.5
        return 0.0
    if isinstance(value, (list, dict)):
        return 0.0
    return 0.0


def _trace_from_meta(meta: dict[str, Any]) -> Trace | None:
    t = meta.get("trace")
    if not t:
        return None
    return Trace(
        trace_id=t["trace_id"],
        agent_id=t["agent_id"],
        agent_config_hash=t.get("agent_config_hash", ""),
        test_case_id=t.get("test_case_id"),
        spans=[Span.model_validate(s) for s in t.get("spans", [])],
        visibility=t.get("visibility", "black_box"),
        final_output=t.get("final_output", ""),
        total_cost_usd=t.get("total_cost_usd", 0.0),
        total_latency_ms=t.get("total_latency_ms", 0.0),
        total_steps=t.get("total_steps", 0),
        schema_version=t.get("schema_version", Trace.model_fields[
            "schema_version"].default),
    )


def _trace_from_sample(sample: dict[str, Any], agent_id: str,
                       trace_id: str) -> Trace | None:
    """Best-effort trace for a foreign sample: a black-box trace carrying the
    final output. The span tree is not recoverable from messages."""
    output = sample.get("output") or {}
    final = output.get("completion")
    if final is None:
        msgs = sample.get("messages") or []
        for m in reversed(msgs):
            if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                final = m["content"]
                break
    if final is None:
        return None
    return Trace(
        trace_id=trace_id,
        agent_id=agent_id,
        agent_config_hash="",
        test_case_id=str(sample.get("id")) if sample.get("id") is not None else None,
        spans=[],
        visibility="black_box",
        final_output=str(final),
    )


def _runscore_from_sample(sample: dict[str, Any], default_trace_id: str
                          ) -> tuple[RunScore, dict[str, Any]]:
    meta = ((sample.get("metadata") or {}).get("agenttic")) or {}
    raw_scores = sample.get("scores") or {}

    criterion_scores: list[CriterionScore] = []
    for cid, sc in raw_scores.items():
        sc = sc or {}
        sc_meta = sc.get("metadata") or {}
        criterion_scores.append(CriterionScore(
            criterion_id=cid,
            score=_coerce_score(sc.get("value")),
            scorer=sc_meta.get("scorer", "code")
            if sc_meta.get("scorer") in ("code", "judge", "fi") else "code",
            calibrated=bool(sc_meta.get("calibrated", True)),
            judge_rationale=sc.get("explanation"),
            cost_usd=float(sc_meta.get("cost_usd", 0.0)),
        ))

    if meta:  # agenttic-origin: exact recovery
        passed = bool(meta.get("passed", False))
        trace_id = meta.get("trace_id", default_trace_id)
        run = RunScore(
            trace_id=trace_id,
            test_id=meta.get("test_id", str(sample.get("id"))),
            criterion_scores=criterion_scores,
            passed=passed,
            cost_usd=float(meta.get("cost_usd", 0.0)),
            scoring_cost_usd=float(meta.get("scoring_cost_usd", 0.0)),
            latency_ms=float(meta.get("latency_ms", 0.0)),
            steps=int(meta.get("steps", 0)),
            scoring_error=meta.get("scoring_error"),
        )
        return run, meta

    # foreign log: derive passed from scores (all criteria scoring 1.0 => pass)
    passed = bool(criterion_scores) and all(
        c.score >= 1.0 for c in criterion_scores)
    err = sample.get("error")
    run = RunScore(
        trace_id=default_trace_id,
        test_id=str(sample.get("id")),
        criterion_scores=criterion_scores,
        passed=passed,
        scoring_error=(err.get("message") if isinstance(err, dict) else None),
    )
    return run, meta


def from_inspect_log(log: dict[str, Any]) -> dict[str, Any]:
    """Parse an Inspect ``EvalLog`` ``dict`` back into agenttic structures.

    Returns ``{"scorecard": Scorecard, "traces": list[Trace],
    "rubric": Rubric | None}``. For an **agenttic-origin** log this is the exact
    inverse of :func:`to_inspect_log` (lossless on every field listed in this
    module's docstring). For a **foreign** log it recovers the mappable subset
    best-effort (see *Lossy edges*).
    """
    spec = log.get("eval") or {}
    eval_meta = ((spec.get("metadata") or {}).get("agenttic")) or {}
    results = log.get("results") or {}
    res_meta = ((results.get("metadata") or {}).get("agenttic")) or {}
    samples = log.get("samples") or []

    run_id = spec.get("run_id") or spec.get("eval_id") or "imported-scorecard"
    agent_id = spec.get("model") or "imported-agent"

    run_scores: list[RunScore] = []
    traces: list[Trace] = []
    for i, sample in enumerate(samples):
        default_trace_id = f"{run_id}-{sample.get('id', i)}-{sample.get('epoch', 1)}"
        run, meta = _runscore_from_sample(sample, default_trace_id)
        run_scores.append(run)
        trace = _trace_from_meta(meta) or _trace_from_sample(
            sample, agent_id, run.trace_id)
        if trace is not None:
            traces.append(trace)

    rubric = None
    if eval_meta.get("rubric"):
        rubric = Rubric.model_validate(eval_meta["rubric"])

    if res_meta:  # agenttic-origin: restore exact aggregates
        scorecard = Scorecard(
            scorecard_id=res_meta["scorecard_id"],
            agent_id=res_meta["agent_id"],
            suite_id=res_meta["suite_id"],
            suite_version=res_meta["suite_version"],
            rubric_id=res_meta["rubric_id"],
            rubric_version=res_meta["rubric_version"],
            run_scores=run_scores,
            task_success_rate=res_meta["task_success_rate"],
            mean_cost_usd=res_meta["mean_cost_usd"],
            total_cost_usd=res_meta.get("total_cost_usd", 0.0),
            total_scoring_cost_usd=res_meta.get("total_scoring_cost_usd", 0.0),
            p95_latency_ms=res_meta["p95_latency_ms"],
            per_criterion_means=res_meta.get("per_criterion_means", {}),
            errored_test_ids=res_meta.get("errored_test_ids", []),
            visibility_tier=res_meta["visibility_tier"],
            created_at=datetime.fromisoformat(res_meta["created_at"]),
        )
    elif run_scores:  # foreign: recompute aggregates from the recovered runs
        scorecard = Scorecard.aggregate(
            scorecard_id=run_id,
            agent_id=agent_id,
            suite_id=spec.get("task", "imported-suite"),
            suite_version=int(spec.get("task_version") or 1)
            if str(spec.get("task_version", 1)).isdigit() else 1,
            rubric_id=(rubric.rubric_id if rubric else "imported-rubric"),
            rubric_version=(rubric.version if rubric else 1),
            run_scores=run_scores,
            visibility_tier=eval_meta.get("visibility_tier", "black_box"),
        )
    else:
        raise ValueError("EvalLog has no samples to reconstruct a scorecard from")

    return {"scorecard": scorecard, "traces": traces, "rubric": rubric}
