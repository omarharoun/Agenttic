"""Workflow executor: topologically walks the canvas graph and runs nodes.

Nodes on the same topological level run concurrently (independent branches
animate in parallel in the UI). A node runs only when every upstream node
succeeded; otherwise it is skipped. The human-gate node parks the whole
execution in ``waiting_approval`` — durable across server restarts because
every node output is persisted, so ``ExecutionManager.resume`` can re-launch
the executor seeded with prior outputs and the gate re-checks approval.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from ascore.registry.sqlite_store import Registry
from ascore.server.events import EventBus
from ascore.server.nodes import NODE_TYPES, NodeContext
from ascore.server.store import UIStore
from ascore.server.workflow_schema import Workflow, topo_levels, validate_workflow


class WorkflowValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class _Handles:
    task: asyncio.Task | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)


class WorkflowExecutor:
    def __init__(self, cfg: dict, reg: Registry, store: UIStore, bus: EventBus,
                 clients: dict | None = None):
        self.cfg = cfg
        self.reg = reg
        self.store = store
        self.bus = bus
        self.clients = clients or {}

    async def run(self, wf: Workflow, execution_id: str, handles: _Handles,
                  seeded_outputs: dict[str, dict] | None = None) -> None:
        outputs: dict[str, dict] = dict(seeded_outputs or {})
        failed: set[str] = set()
        by_id = {n.node_id: n for n in wf.nodes}
        upstream: dict[str, list] = {n.node_id: [] for n in wf.nodes}
        for e in wf.edges:
            upstream[e.target].append(e)

        self.bus.publish("execution_started", execution_id,
                         data={"workflow_id": wf.workflow_id})
        levels = topo_levels(wf) or []
        status = "succeeded"

        for level in levels:
            if handles.cancelled.is_set():
                status = "cancelled"
                break
            runnable, skipped = [], []
            for nid in level:
                if nid in outputs:  # seeded from a previous (gated) attempt
                    self._set_state(execution_id, nid, "succeeded")
                    continue
                deps = {e.source for e in upstream[nid]}
                if deps & failed:
                    skipped.append(nid)
                else:
                    runnable.append(nid)
            for nid in skipped:
                failed.add(nid)  # propagate skip downstream
                self._set_state(execution_id, nid, "skipped")
                self.bus.publish("node_skipped", execution_id, nid)
            results = await asyncio.gather(
                *(self._run_node(wf, by_id[nid], upstream[nid], outputs,
                                 execution_id, handles) for nid in runnable),
                return_exceptions=True)
            for nid, res in zip(runnable, results):
                if isinstance(res, asyncio.CancelledError):
                    status = "cancelled"
                elif isinstance(res, BaseException):
                    failed.add(nid)
                else:
                    outputs[nid] = res
            if status == "cancelled":
                break

        if status != "cancelled" and failed:
            status = "failed"
        self.store.update_execution(execution_id, status=status,
                                    waiting_node_id=None, finished=True)
        self.bus.publish(f"execution_{status}", execution_id,
                         data={"failed_nodes": sorted(failed)})
        self.bus.close(execution_id)

    async def _run_node(self, wf: Workflow, node, in_edges, outputs: dict,
                        execution_id: str, handles: _Handles) -> dict:
        spec = NODE_TYPES[node.type]
        inputs = {e.target_port: outputs[e.source][e.source_port]
                  for e in in_edges}
        ctx = NodeContext(
            cfg=self.cfg, reg=self.reg, execution_id=execution_id,
            node_id=node.node_id,
            emit=lambda t, d: self.bus.publish(t, execution_id, node.node_id, d),
            wait_for_approval=self._gate_waiter(execution_id, node.node_id, handles),
            cancelled=handles.cancelled, clients=self.clients)
        self._set_state(execution_id, node.node_id, "running")
        self.bus.publish("node_started", execution_id, node.node_id,
                         {"type": node.type, "label": node.label})
        try:
            result = await spec.run(ctx, spec.config_model.model_validate(node.config),
                                    inputs)
        except asyncio.CancelledError:
            self._set_state(execution_id, node.node_id, "failed")
            raise
        except Exception as exc:  # noqa: BLE001 — node failure is workflow data
            self._set_state(execution_id, node.node_id, "failed")
            self.bus.publish("node_failed", execution_id, node.node_id,
                             {"error": f"{type(exc).__name__}: {exc}"})
            raise
        self.store.update_execution(execution_id,
                                    node_output=(node.node_id, result))
        self._set_state(execution_id, node.node_id, "succeeded")
        self.bus.publish("node_completed", execution_id, node.node_id,
                         {"summary": _summarize(result)})
        return result

    def _gate_waiter(self, execution_id: str, node_id: str, handles: _Handles):
        async def wait(suite_id: str, version: int) -> None:
            self._set_state(execution_id, node_id, "waiting")
            self.store.update_execution(execution_id, status="waiting_approval",
                                        waiting_node_id=node_id)
            self.bus.publish("node_waiting", execution_id, node_id,
                             {"suite_id": suite_id, "version": version})
            await handles.gate.wait()
            handles.gate.clear()
            self._set_state(execution_id, node_id, "running")
            self.store.update_execution(execution_id, status="running",
                                        waiting_node_id=None)
        return wait

    def _set_state(self, execution_id: str, node_id: str, state: str) -> None:
        self.store.update_execution(execution_id, node_state=(node_id, state))


def _summarize(result: dict) -> dict:
    """Small, JSON-safe summary for the node_completed event."""
    out = {}
    for port, payload in result.items():
        if isinstance(payload, str):
            out[port] = payload[:160]
        elif isinstance(payload, dict):
            out[port] = {k: v for k, v in payload.items()
                         if isinstance(v, (str, int, float, bool)) or v is None}
        else:
            out[port] = str(payload)[:160]
    return out


class ExecutionManager:
    """Owns running executions: start, cancel, approve-gate, resume.

    Lives in the FastAPI lifespan (single process). Durability comes from
    the store — on startup, orphaned 'running' rows become 'interrupted';
    'waiting_approval' rows stay resumable via resume()."""

    def __init__(self, cfg: dict, reg: Registry, store: UIStore, bus: EventBus,
                 clients: dict | None = None):
        self.cfg = cfg
        self.reg = reg
        self.store = store
        self.bus = bus
        self.clients = clients or {}
        self._handles: dict[str, _Handles] = {}

    def start(self, wf: Workflow) -> str:
        problems = validate_workflow(wf)
        if problems:
            raise WorkflowValidationError(problems)
        execution_id = uuid.uuid4().hex[:12]
        self.store.create_execution(execution_id, wf)
        self._launch(wf, execution_id, seeded=None)
        return execution_id

    def resume(self, execution_id: str) -> None:
        """Relaunch a gated/interrupted execution seeded with persisted node
        outputs; completed nodes replay instantly, the gate re-checks."""
        ex = self.store.get_execution(execution_id)
        if ex["status"] not in ("waiting_approval", "interrupted"):
            raise ValueError(f"execution {execution_id} is {ex['status']}, "
                             "not resumable")
        wf = Workflow.model_validate(ex["workflow"])
        self.store.update_execution(execution_id, status="running",
                                    waiting_node_id=None)
        self._launch(wf, execution_id, seeded=ex["node_outputs"])

    def _launch(self, wf: Workflow, execution_id: str,
                seeded: dict | None) -> None:
        handles = _Handles()
        self.bus.open(execution_id)  # live before the task's first publish
        executor = WorkflowExecutor(self.cfg, self.reg, self.store, self.bus,
                                    self.clients)
        handles.task = asyncio.create_task(
            executor.run(wf, execution_id, handles, seeded_outputs=seeded))
        self._handles[execution_id] = handles
        handles.task.add_done_callback(
            lambda _t: self._handles.pop(execution_id, None))

    def approve_gate(self, execution_id: str) -> bool:
        """Release an in-memory gate; returns False if not in memory (caller
        should resume() instead — e.g. after a server restart)."""
        h = self._handles.get(execution_id)
        if h is None:
            return False
        h.gate.set()
        return True

    async def cancel(self, execution_id: str) -> None:
        h = self._handles.get(execution_id)
        if h is None:
            return
        h.cancelled.set()
        h.gate.set()  # unblock a parked gate so the loop can observe cancel
        if h.task and not h.task.done():
            h.task.cancel()
            try:
                await h.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            ex = self.store.get_execution(execution_id)
            if ex["status"] in ("running", "waiting_approval"):
                self.store.update_execution(execution_id, status="cancelled",
                                            waiting_node_id=None, finished=True)
                self.bus.publish("execution_cancelled", execution_id)
                self.bus.close(execution_id)
        except KeyError:
            pass
