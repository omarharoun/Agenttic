"""Execution harness — runs a suite of test cases against one adapter.

Semantics (SPEC.md Step 3):
* Concurrency via asyncio with a semaphore (``max_parallel``).
* Per-run timeout. A timeout yields a failure Trace (error span), never a drop.
* Retries apply to TRANSPORT errors only. Agent mistakes are data and live
  inside traces; the harness never retries them (Hard Rule 5).
* Every run persists a trace to the store — success, timeout, or infra failure.

Note: adapters are sync and executed via ``asyncio.to_thread``; on timeout the
worker thread is abandoned (acceptable for MVP, documented limitation) — but the
harness now calls ``adapter.abort_run(test_id)`` first, so an adapter that
spawned a process can kill it. The thread leaks; the AGENT does not.

**Adapter concurrency contract (load-bearing, not folklore).** ``run_suite``
holds ONE adapter object and calls ``adapter.run`` from up to ``max_parallel``
worker threads at once. Therefore:

* ``AgentAdapter.run`` must be re-entrant. Anything it writes to ``self`` is
  shared across every in-flight case, so per-run state stored there is a data
  race — the last writer wins and the other runs read a foreign case's state.
  Per-run state belongs in locals, or in the returned ``Trace``.
* ``describe()``/``config_hash()`` are read concurrently and must stay pure.

Two adapters in this repo held per-run state on ``self`` and were unsafe at
``max_parallel > 1``. Both are fixed, and they are recorded here because the two
fixes are DIFFERENT and the difference is the contract:

* ``redteam.honeypot.GuardedHoneypotAgent`` accumulated ``_decisions`` across
  concurrent cases. That state is genuinely per-run, so it moved off ``self``
  into a ``ContextVar`` — ``asyncio.to_thread`` gives each case its own context,
  so each run reads its own list.
* ``adapters.blackbox_http.BlackBoxHTTPAgent`` keeps ``_last_call`` on ``self``
  DELIBERATELY. A rate limiter that each case had a private copy of would not be
  a rate limit. Shared state that is meant to be shared is locked instead of
  copied: the slot is claimed under ``_rate_lock`` and slept for outside it.

So "per-run state on ``self`` is a race" and "no state may live on ``self``" are
not the same rule, and only the first one is true. Both halves are pinned by
``tests/test_harness.py::TestAdapterConcurrencyContract`` — including
``test_shared_rate_limit_clock_still_spaces_concurrent_requests``, which exists
so the contract is not overstated — rather than described only here. See that
test before changing the sharing model.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from agenttic.adapters.base import AgentAdapter
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
    trial: int = 0,       # which repetition of this suite this is (pass^k)
) -> list[Trace]:
    """Run every test case; return traces in test-case order.

    ``on_event(event_type, data)`` is called from the event loop (never from
    worker threads) with ``case_started`` / ``case_finished`` events so a UI
    can show live progress. It must be fast and must not raise.

    ``trial`` identifies WHICH repetition of this suite this call is, and is the
    only thing that makes repeated runs independent while resume stays on. Trial
    ``t`` may resume only the ``t``-th successful persisted trace of a case, so a
    caller asking for k trials owes the agent k executions of every case. See the
    resume block below for why deleting resume was not the alternative."""
    if not suite.approved:
        raise SuiteNotApprovedError(
            f"suite {suite.suite_id} v{suite.version} is not approved; "
            "run `uv run agenttic approve` first (Step 8 human gate)"
        )
    unknown = [tc.test_id for tc in test_cases if tc.suite_id != suite.suite_id]
    if unknown:
        raise ValueError(f"test cases not in suite {suite.suite_id}: {unknown}")

    sem = asyncio.Semaphore(config.max_parallel)
    total = len(test_cases)

    # Resume: map test_case_id -> a prior SUCCESSFUL trace for this exact agent
    # config AND this exact trial. Infra/upstream failures are NOT reused (they
    # get re-run); genuine agent outputs are. Lets a partially-failed run resume
    # without re-spending.
    #
    # THE TRIAL DIMENSION IS LOAD-BEARING, not bookkeeping. This map used to be
    # keyed on test_case_id alone, and `ops.run_suite_op` hard-codes resume on.
    # So when the pass^k runner asked for k repetitions of a suite, trial 1 ran
    # the agent and trials 2..k read trial 1's persisted traces straight back
    # and returned them unchanged: the agent was invoked ONCE, every trial saw
    # byte-identical output, and pass^k was therefore an alias for pass@1 —
    # structurally, for every agent, forever. The workspace databases show it:
    # 17 cases x 4 runs at k=3 left 17 traces per (agent, config_hash), and
    # every recorded pass^k equals its pass@1 exactly.
    #
    # The fix keeps resume and makes it ORDINAL. Successful traces for a case
    # are taken oldest-first (`store.traces` orders by insertion), and trial `t`
    # may only reuse the t-th of them. Deleting resume instead would have traded
    # a false reliability number for a re-spend of the whole suite on any mid-run
    # crash, which is the failure this mechanism exists to prevent. With ordinal
    # resume BOTH hold: a k=3 run that died half-way through trial 1 resumes the
    # cases trial 1 finished, re-runs the rest, and still owes the agent k
    # independent executions of every case — because trial t needs a t-th trace
    # and only its own execution can produce one.
    #
    # `trial=0` (every non-k caller) is the old single-trial behaviour, with one
    # deliberate difference: the OLDEST successful trace wins rather than the
    # newest, so a resumed run is reproducible instead of depending on how many
    # times the suite happened to be run before.
    _FAIL_PREFIXES = ("HARNESS_FAILURE", "UPSTREAM_ERROR", "BLACKBOX_FAILURE")
    done: dict[str, Trace] = {}
    if resume and trial >= 0 and hasattr(store, "traces"):
        cfg_hash = adapter.config_hash()
        try:
            by_case: dict[str, list[Trace]] = {}
            for t in store.traces(adapter.agent_id, mode="batch"):
                if (t.test_case_id and t.agent_config_hash == cfg_hash
                        and not str(t.final_output).startswith(_FAIL_PREFIXES)):
                    by_case.setdefault(t.test_case_id, []).append(t)
            done = {cid: ts[trial] for cid, ts in by_case.items()
                    if len(ts) > trial}
        except Exception:  # noqa: BLE001 — resume is best-effort
            done = {}

    async def one(index: int, tc: TestCase) -> Trace:
        if tc.test_id in done:
            if on_event:
                on_event("case_resumed", {"index": index, "total": total,
                                          "test_id": tc.test_id, "trial": trial,
                                          "trace_id": done[tc.test_id].trace_id})
            return done[tc.test_id]
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
                    # SHARED INSTANCE: this is the same `adapter` object for
                    # every case, entered from up to max_parallel threads at
                    # once. Whatever run() writes to self is visible to — and
                    # overwritten by — the other in-flight cases. See the
                    # adapter concurrency contract in the module docstring;
                    # per-run state belongs in locals or in the Trace.
                    trace = await asyncio.wait_for(
                        asyncio.to_thread(adapter.run, tc.input, test_case_id=tc.test_id),
                        timeout=config.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    # an agent overrunning its budget is data, not a transport blip
                    #
                    # `wait_for` cancels the AWAIT, not the worker thread, so the
                    # adapter keeps going until its own deadline. For an adapter
                    # that spawned an agent that means the child outlives the run
                    # by (adapter timeout - harness timeout) — 780s under the
                    # shipped config, still spending against the user's key on a
                    # case nobody is waiting for. Measured: two agents still
                    # alive ~40 minutes after the suite gave up on them.
                    #
                    # The thread is still abandoned (that limitation stands); what
                    # ends here is the WORK it was doing.
                    try:
                        adapter.abort_run(tc.test_id)
                    except Exception:  # noqa: BLE001 — cleanup never breaks a run
                        pass
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
