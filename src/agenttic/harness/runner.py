"""Execution harness — runs a suite of test cases against one adapter.

Semantics (SPEC.md Step 3):
* Concurrency via asyncio with a semaphore (``max_parallel``).
* Per-run timeout. A timeout yields a failure Trace (error span), never a drop.
* Retries apply to TRANSPORT errors only. Agent mistakes are data and live
  inside traces; the harness never retries them (Hard Rule 5).
* Every run persists a trace to the store — success, timeout, or infra failure.

Note: adapters are sync and executed via ``asyncio.to_thread``; on timeout the
worker thread is abandoned (acceptable for MVP, documented limitation).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from agenttic.adapters.base import AgentAdapter, EscalationRequired, HumanChannel
from agenttic.api_errors import TerminalAPIError
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


class SuiteNotApprovedError(RuntimeError):
    """Raised when running a suite that has not passed the human gate."""


class TraceStore(Protocol):
    """Minimal persistence interface; implemented by the registry (Step 6)."""

    def save_trace(self, trace: Trace) -> None: ...


@dataclass(frozen=True)
class HarnessConfig:
    timeout_seconds: float = 120.0
    max_parallel: int = 5
    transport_retries: int = 2


def _failure_trace(adapter: AgentAdapter, tc: TestCase, kind: str, detail: str) -> Trace:
    """Synthesize a valid Trace for a run the adapter could not complete."""
    now = datetime.now(timezone.utc)
    return Trace(
        trace_id=uuid.uuid4().hex,
        agent_id=adapter.agent_id,
        agent_config_hash=adapter.config_hash(),
        test_case_id=tc.test_id,
        spans=[Span(
            span_id=uuid.uuid4().hex[:12], kind="error", name=kind,
            start_time=now, end_time=now, error=detail,
            attributes={"synthesized_by": "harness"},
        )],
        visibility=adapter.visibility,
        final_output=f"HARNESS_FAILURE:{kind}",
        total_cost_usd=0.0,
        total_latency_ms=0.0,
        total_steps=0,
        schema_version=SCHEMA_VERSION,
    )


def _escalation_span(exc: EscalationRequired) -> Span:
    """Build the ``kind="escalation"`` span that records a HITL handoff, chained
    after whatever partial spans the adapter produced before it escalated."""
    now = datetime.now(timezone.utc)
    parent = exc.partial_trace_spans[-1].span_id if exc.partial_trace_spans else None
    return Span(
        span_id=uuid.uuid4().hex[:12], parent_id=parent, kind="escalation",
        name="human_escalation", start_time=now, end_time=now,
        input={"question": exc.question, "context": exc.context},
        attributes={"synthesized_by": "harness"},
    )


def _unresolved_escalation_trace(
    adapter: AgentAdapter, tc: TestCase, exc: EscalationRequired
) -> Trace:
    """Synthesize a persisted (never dropped) trace for an escalation raised with
    NO human channel to resolve it. Keeps the partial work + the escalation
    span, marks the run ``escalated`` and ``final_output=="ESCALATED_UNRESOLVED"``."""
    spans = list(exc.partial_trace_spans) + [_escalation_span(exc)]
    return Trace(
        trace_id=uuid.uuid4().hex,
        agent_id=adapter.agent_id,
        agent_config_hash=adapter.config_hash(),
        test_case_id=tc.test_id,
        spans=spans,
        visibility=adapter.visibility,
        final_output="ESCALATED_UNRESOLVED",
        total_cost_usd=sum(s.cost_usd or 0.0 for s in spans),
        total_latency_ms=0.0,
        total_steps=sum(1 for s in spans if s.kind in ("llm_call", "tool_call")),
        escalated=True,
        schema_version=SCHEMA_VERSION,
    )


def _persist_escalation_feedback(store: TraceStore, adapter: AgentAdapter,
                                 trace_id: str, resp: str) -> None:
    """Record the human's escalation decision as append-only HumanFeedback with
    provenance (``source="escalation"``). Best-effort: a store without
    ``save_feedback`` (e.g. a bare TraceStore in a test) does not break the run."""
    save = getattr(store, "save_feedback", None)
    if save is None:
        return
    from agenttic.schema.feedback import HumanFeedback
    save(HumanFeedback(
        feedback_id=uuid.uuid4().hex,
        trace_id=trace_id,
        agent_id=adapter.agent_id,
        source="escalation",
        kind="escalation_decision",
        rationale=resp,
        created_at=datetime.now(timezone.utc),
    ))


async def run_suite(
    adapter: AgentAdapter,
    suite: TestSuite,
    test_cases: list[TestCase],
    store: TraceStore,
    config: HarnessConfig = HarnessConfig(),
    transport_errors: tuple[type[Exception], ...] = (ConnectionError, OSError),
    on_event: Callable[[str, dict], None] | None = None,
    budget=None,  # optional agenttic.budget.RunBudget — abort remaining cases on cap
    resume: bool = True,  # reuse successful persisted traces (don't re-spend)
    human: HumanChannel | None = None,  # HITL (Step 12): resolves escalations
    trials_per_case: int = 1,  # SPEC-7 31: run each case k times for pass^k
) -> list[Trace]:
    """Run every test case; return traces in test-case order.

    ``on_event(event_type, data)`` is called from the event loop (never from
    worker threads) with ``case_started`` / ``case_finished`` events so a UI
    can show live progress. It must be fast and must not raise.

    ``human`` is an optional :class:`~agenttic.adapters.base.HumanChannel`. When
    an adapter raises ``EscalationRequired`` and a channel is present, the human
    is consulted, their decision persisted as ``HumanFeedback(source="escalation")``,
    and the adapter re-invoked with ``test_input["human_guidance"]`` set; the
    finished trace is marked ``escalated=True``. With no channel the run is
    persisted as ``final_output=="ESCALATED_UNRESOLVED"`` (never dropped)."""
    if not suite.approved:
        raise SuiteNotApprovedError(
            f"suite {suite.suite_id} v{suite.version} is not approved; "
            "run `uv run agenttic approve` first (Step 8 human gate)"
        )
    # SPEC-7 31: reliability as consistency. Run each case k independent times
    # (fresh env per trial; resume off so trials don't reuse each other), and let
    # the scorecard aggregate the trials into a pass^k curve.
    if trials_per_case > 1:
        traces: list[Trace] = []
        for _ in range(trials_per_case):
            traces += await run_suite(
                adapter, suite, test_cases, store, config, transport_errors,
                on_event, budget, resume=False, human=human, trials_per_case=1)
        return traces
    unknown = [tc.test_id for tc in test_cases if tc.suite_id != suite.suite_id]
    if unknown:
        raise ValueError(f"test cases not in suite {suite.suite_id}: {unknown}")

    sem = asyncio.Semaphore(config.max_parallel)
    #: run-level halt latch (terminal upstream error) — see the kill-switch below
    halted: dict = {"reason": None}
    total = len(test_cases)

    # Resume: map test_case_id -> a prior SUCCESSFUL trace for this exact agent
    # config. Infra/upstream failures are NOT reused (they get re-run); genuine
    # agent outputs are. Lets a partially-failed run resume without re-spending.
    _FAIL_PREFIXES = ("HARNESS_FAILURE", "UPSTREAM_ERROR", "BLACKBOX_FAILURE")
    done: dict[str, Trace] = {}
    if resume and hasattr(store, "traces"):
        cfg_hash = adapter.config_hash()
        try:
            for t in store.traces(adapter.agent_id, mode="batch"):
                if (t.test_case_id and t.agent_config_hash == cfg_hash
                        and not str(t.final_output).startswith(_FAIL_PREFIXES)):
                    done[t.test_case_id] = t  # later (newer) trace wins
        except Exception:  # noqa: BLE001 — resume is best-effort
            done = {}

    async def one(index: int, tc: TestCase) -> Trace:
        if tc.test_id in done:
            if on_event:
                on_event("case_resumed", {"index": index, "total": total,
                                          "test_id": tc.test_id,
                                          "trace_id": done[tc.test_id].trace_id})
            return done[tc.test_id]
        async with sem:
            # terminal-API kill-switch (SPEC-8 discovered gap): once ANY case
            # hits an auth/billing/permission/config error, no further call can
            # succeed — short-circuit remaining cases to a clean run_halted
            # trace instead of grinding against a dead API. Everything already
            # completed stays persisted (Hard Rule 5).
            if halted["reason"] is not None:
                trace = _failure_trace(adapter, tc, "run_halted", halted["reason"])
                store.save_trace(trace)
                if on_event:
                    on_event("run_halted", {
                        "index": index, "total": total, "test_id": tc.test_id,
                        "reason": halted["reason"]})
                return trace
            # budget kill-switch: once the per-run cap is hit, don't start new
            # runs — short-circuit to a clean budget_exceeded trace (no spend).
            if budget is not None and budget.exhausted:
                trace = _failure_trace(
                    adapter, tc, "budget_exceeded",
                    f"per-run cost cap ${budget.max_run_usd:.4f} reached "
                    f"(spent ${budget.spent_usd:.4f}); remaining cases skipped")
                store.save_trace(trace)
                if on_event:
                    on_event("budget_exceeded", {
                        "index": index, "total": total, "test_id": tc.test_id,
                        "spent_usd": round(budget.spent_usd, 6)})
                return trace
            if on_event:
                on_event("case_started",
                         {"index": index, "total": total, "test_id": tc.test_id})
            trace: Trace | None = None
            run_input = tc.input
            for attempt in range(config.transport_retries + 1):
                try:
                    trace = await asyncio.wait_for(
                        asyncio.to_thread(adapter.run, run_input, test_case_id=tc.test_id),
                        timeout=config.timeout_seconds,
                    )
                except EscalationRequired as esc:
                    # A structured HITL signal — handled BEFORE any failure path.
                    if human is None:
                        # No human to consult: persist (never drop) as unresolved.
                        trace = _unresolved_escalation_trace(adapter, tc, esc)
                    else:
                        # Consult the human, persist their decision with
                        # provenance, then re-invoke with their guidance folded in.
                        resp = human.respond(esc.question, esc.context)
                        run_input = {**run_input, "human_guidance": resp}
                        try:
                            trace = await asyncio.wait_for(
                                asyncio.to_thread(adapter.run, run_input,
                                                  test_case_id=tc.test_id),
                                timeout=config.timeout_seconds,
                            )
                        except EscalationRequired as esc2:
                            # Human guidance didn't unblock it: persist unresolved.
                            trace = _unresolved_escalation_trace(adapter, tc, esc2)
                        except Exception as exc:  # noqa: BLE001 — post-guidance run failed
                            trace = _failure_trace(
                                adapter, tc, "harness_error",
                                f"{type(exc).__name__}: {exc}")
                        else:
                            # Mark the resolved run escalated + prepend the
                            # escalation span so the handoff is auditable.
                            trace = trace.model_copy(update={
                                "escalated": True,
                                "spans": [_escalation_span(esc), *trace.spans],
                            })
                        _persist_escalation_feedback(store, adapter, trace.trace_id, resp)
                    if on_event:
                        on_event("case_escalated", {
                            "index": index, "total": total, "test_id": tc.test_id,
                            "resolved": human is not None,
                            "trace_id": trace.trace_id})
                except asyncio.TimeoutError:
                    # an agent overrunning its budget is data, not a transport blip
                    trace = _failure_trace(
                        adapter, tc, "timeout",
                        f"run exceeded {config.timeout_seconds}s",
                    )
                except transport_errors as exc:
                    if attempt < config.transport_retries:
                        continue
                    trace = _failure_trace(
                        adapter, tc, "transport_failure",
                        f"{type(exc).__name__}: {exc} (after {attempt + 1} attempts)",
                    )
                except TerminalAPIError as exc:
                    # Auth/billing/permission/config: trip the run-level latch —
                    # this case records the terminal error; every not-yet-started
                    # case short-circuits to run_halted (no further API calls).
                    halted["reason"] = (
                        f"terminal upstream error: {exc}"
                        + (f" (request_id {exc.request_id})" if exc.request_id else ""))
                    trace = _failure_trace(
                        adapter, tc, "terminal_api_error", halted["reason"])
                    if on_event:
                        on_event("run_halted", {
                            "index": index, "total": total, "test_id": tc.test_id,
                            "reason": halted["reason"]})
                except Exception as exc:  # noqa: BLE001 — adapter bug: persist, don't lose the run
                    trace = _failure_trace(
                        adapter, tc, "harness_error", f"{type(exc).__name__}: {exc}"
                    )
                break
            assert trace is not None
            if budget is not None:
                budget.charge(trace.total_cost_usd)
            store.save_trace(trace)
            if on_event:
                on_event("case_finished", {
                    "index": index, "total": total, "test_id": tc.test_id,
                    "trace_id": trace.trace_id,
                    "ok": not any(s.kind == "error" for s in trace.spans),
                })
            return trace

    return list(await asyncio.gather(
        *(one(i, tc) for i, tc in enumerate(test_cases))))
