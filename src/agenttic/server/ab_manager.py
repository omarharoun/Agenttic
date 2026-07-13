"""Background runner for A/B comparisons.

An A/B run executes two full agent evaluations, so it can take a while — the
HTTP request can't block on it. ``ABManager`` mirrors the spirit of
``ExecutionManager`` but lighter: ``start`` records a 'running' row and launches
an asyncio task; the task runs both variants via :func:`ascore.ab.run_ab_op`
(which persists the finished comparison or marks the row failed), and the UI
polls ``GET /ab/runs/{id}`` for status + the artifact. Live per-variant progress
is kept in memory and surfaced on the same poll.

Durability comes from the row: a comparison that hasn't reached 'succeeded' or
'failed' after a restart simply shows as 'running' with no artifact; the user
re-runs it. (The single-agent path has the same property for in-flight runs.)
"""

from __future__ import annotations

import asyncio
import uuid

from ascore.ab import run_ab_op
from ascore.registry.sqlite_store import Registry
from ascore.schema.ab import ABVariant


class ABManager:
    def __init__(self, cfg: dict, reg: Registry, clients: dict | None = None):
        self.cfg = cfg
        self.reg = reg
        self.clients = clients or {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._progress: dict[str, dict] = {}

    def start(self, suite_id: str, variant_a: ABVariant, variant_b: ABVariant,
              version: int | None = None, clients: dict | None = None) -> str:
        comparison_id = uuid.uuid4().hex[:12]
        self.reg.create_ab_run(comparison_id, suite_id)
        run_clients = clients or self.clients
        task = asyncio.create_task(self._run(
            comparison_id, suite_id, version, variant_a, variant_b, run_clients))
        self._tasks[comparison_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(comparison_id, None))
        return comparison_id

    async def _run(self, comparison_id: str, suite_id: str, version: int | None,
                   variant_a: ABVariant, variant_b: ABVariant,
                   clients: dict) -> None:
        def on_progress(event: str, data: dict) -> None:
            done = data["index"] + 1 if isinstance(data.get("index"), int) else None
            self._progress[comparison_id] = {
                "variant": data.get("variant"), "event": event,
                "done": done, "total": data.get("total"),
                "message": data.get("message")}
        try:
            await run_ab_op(self.cfg, self.reg, suite_id, variant_a, variant_b,
                            version=version, on_progress=on_progress,
                            clients=clients, comparison_id=comparison_id)
        except Exception as exc:  # noqa: BLE001 — surface as a failed run, not a 500
            self.reg.fail_ab_run(comparison_id, f"{type(exc).__name__}: {exc}")
        finally:
            self._progress.pop(comparison_id, None)

    def progress(self, comparison_id: str) -> dict | None:
        return self._progress.get(comparison_id)
