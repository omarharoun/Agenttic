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

from ascore.adapters.base import AgentAdapter
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace


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


async def run_suite(
    adapter: AgentAdapter,
    suite: TestSuite,
    test_cases: list[TestCase],
    store: TraceStore,
    config: HarnessConfig = HarnessConfig(),
    transport_errors: tuple[type[Exception], ...] = (ConnectionError, OSError),
    on_event: Callable[[str, dict], None] | None = None,
    budget=None,  # optional ascore.budget.RunBudget — abort remaining cases on cap
) -> list[Trace]:
    """Run every test case; return traces in test-case order.

    ``on_event(event_type, data)`` is called from the event loop (never from
    worker threads) with ``case_started`` / ``case_finished`` events so a UI
    can show live progress. It must be fast and must not raise."""
    if not suite.approved:
        raise SuiteNotApprovedError(
            f"suite {suite.suite_id} v{suite.version} is not approved; "
            "run `uv run ascore approve` first (Step 8 human gate)"
        )
    unknown = [tc.test_id for tc in test_cases if tc.suite_id != suite.suite_id]
    if unknown:
        raise ValueError(f"test cases not in suite {suite.suite_id}: {unknown}")

    sem = asyncio.Semaphore(config.max_parallel)
    total = len(test_cases)

    async def one(index: int, tc: TestCase) -> Trace:
        async with sem:
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
            for attempt in range(config.transport_retries + 1):
                try:
                    trace = await asyncio.wait_for(
                        asyncio.to_thread(adapter.run, tc.input, test_case_id=tc.test_id),
                        timeout=config.timeout_seconds,
                    )
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
