"""Background runner for prompt-optimization runs.

An optimization runs the suite many times (baseline + N candidates per round on
the train split, plus held-out scoring), so it is far too long for an HTTP
request to block on. ``OptimizerManager`` mirrors ``ABManager``: ``start``
records a 'running' row and launches an asyncio task; the task runs
:func:`agenttic.optimizer.optimize` (which persists the finished run or marks the
row failed), and the UI polls ``GET /optimize/runs/{id}`` for status + the
artifact. Live progress (current round / candidate / cost projection) is kept in
memory and surfaced on the same poll.

Durability comes from the row: a run that hasn't reached 'succeeded'/'failed'
after a restart shows as 'running' with no artifact; the user re-runs it.
"""

from __future__ import annotations

import asyncio
import uuid

from agenttic.optimizer import optimize
from agenttic.registry.sqlite_store import Registry


class OptimizerManager:
    def __init__(self, cfg: dict, reg: Registry, clients: dict | None = None):
        self.cfg = cfg
        self.reg = reg
        self.clients = clients or {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._progress: dict[str, dict] = {}

    def start(self, agent_id: str, suite_id: str, *, rounds: int,
              candidates_per_round: int, heldout_fraction: float, seed: int,
              baseline_prompt: str, model: str, variant: str, url: str,
              version: int | None, max_agent_runs: int,
              clients: dict | None = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        self.reg.create_optimization_run(run_id, agent_id, suite_id)
        run_clients = clients or self.clients
        task = asyncio.create_task(self._run(
            run_id, agent_id, suite_id, rounds, candidates_per_round,
            heldout_fraction, seed, baseline_prompt, model, variant, url,
            version, max_agent_runs, run_clients))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return run_id

    async def _run(self, run_id, agent_id, suite_id, rounds,
                   candidates_per_round, heldout_fraction, seed,
                   baseline_prompt, model, variant, url, version,
                   max_agent_runs, clients) -> None:
        def on_progress(event: str, data: dict) -> None:
            cur = self._progress.get(run_id, {})
            cur.update({"event": event, **data})
            self._progress[run_id] = cur
        try:
            await optimize(
                self.cfg, self.reg, agent_id, suite_id,
                rounds=rounds, candidates_per_round=candidates_per_round,
                heldout_fraction=heldout_fraction, seed=seed,
                baseline_prompt=baseline_prompt, version=version,
                variant=variant, model=model, url=url,
                client=clients.get("agent"),
                judge_client=clients.get("judge") or clients.get("agent"),
                optimizer_client=(clients.get("optimizer")
                                  or clients.get("judge")
                                  or clients.get("agent")),
                max_agent_runs=max_agent_runs, run_id=run_id,
                on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 — surface as a failed run, not a 500
            self.reg.fail_optimization_run(run_id, f"{type(exc).__name__}: {exc}")
        finally:
            self._progress.pop(run_id, None)

    def progress(self, run_id: str) -> dict | None:
        return self._progress.get(run_id)
