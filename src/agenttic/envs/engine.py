"""In-memory environment engine (SPEC-7 Step 29.1).

Instantiate a fresh state per run from ``Environment.seed_state``; read tools
observe it, write tools mutate it and record every mutation as a
``state_change`` on the tool span. Deterministic: same seed_state + same tool
calls => byte-identical end state (Hard Rule 33 depends on this).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from agenttic.schema.environment import Environment, ToolSpec
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EnvState:
    """A live copy of an environment's entity store for one run."""

    def __init__(self, seed_state: dict):
        self.state: dict = copy.deepcopy(seed_state)
        self.writes: list[str] = []

    def execute(self, tool: ToolSpec, args: dict) -> tuple[object, list[dict]]:
        """Run one tool. Returns (output, state_changes). Read tools return
        data and no changes; write tools mutate and record the before/after."""
        table = self.state.setdefault(tool.entity_type, {})
        eid = args.get("id")
        if tool.op == "get":
            return table.get(eid), []
        if tool.op == "list":
            return list(table.values()), []
        if tool.op == "create":
            before = table.get(eid)
            table[eid] = dict(args.get("fields", {}))
            return {"created": eid}, [{"op": "create", "entity_type": tool.entity_type,
                                       "id": eid, "before": before, "after": dict(table[eid])}]
        if tool.op == "update":
            before = dict(table.get(eid, {}))
            table.setdefault(eid, {}).update(args.get("fields", {}))
            return {"updated": eid}, [{"op": "update", "entity_type": tool.entity_type,
                                       "id": eid, "before": before, "after": dict(table[eid])}]
        if tool.op == "delete":
            before = table.pop(eid, None)
            return {"deleted": eid}, [{"op": "delete", "entity_type": tool.entity_type,
                                       "id": eid, "before": before, "after": None}]
        return None, []

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)


def replay(env: Environment, tool_calls: list[tuple[str, dict]]
           ) -> tuple[dict, list[Span], list[object]]:
    """Execute an ordered tool sequence against a fresh env. Returns
    (end_state, tool_spans, outputs). An unknown tool becomes an error span."""
    st = EnvState(env.seed_state)
    now = _now()
    spans: list[Span] = []
    outputs: list[object] = []
    for i, (name, args) in enumerate(tool_calls):
        tool = env.tool(name)
        if tool is None:
            spans.append(Span(span_id=f"e{i}", kind="tool_call", name=name,
                              start_time=now, end_time=now, error=f"unknown tool {name!r}"))
            continue
        out, changes = st.execute(tool, dict(args))
        if tool.effect == "write":
            st.writes.append(name)
        spans.append(Span(span_id=f"e{i}", kind="tool_call", name=name,
                          start_time=now, end_time=now, input=dict(args),
                          output={"result": out}, state_change=changes or None))
        outputs.append(out)
    return st.snapshot(), spans, outputs


def env_trace(env: Environment, tool_calls: list[tuple[str, dict]], *,
              final_output: str = "", agent_id: str = "env-scripted",
              test_case_id: str | None = None) -> Trace:
    """A trace that ran `tool_calls` through `env`, carrying the end state on its
    final_output span so goal-state checks can verify it deterministically."""
    end_state, spans, _ = replay(env, tool_calls)
    now = _now()
    spans.append(Span(span_id="final", kind="final_output", name="final_output",
                      start_time=now, end_time=now,
                      output={"text": final_output, "end_state": end_state}))
    return Trace(trace_id=f"envtrace-{test_case_id or agent_id}", agent_id=agent_id,
                 agent_config_hash="env", test_case_id=test_case_id, spans=spans,
                 visibility="glass_box", final_output=final_output,
                 total_steps=sum(1 for s in spans if s.kind == "tool_call"),
                 schema_version=SCHEMA_VERSION)


def env_end_state(trace: Trace) -> dict | None:
    """The environment end state a trace recorded, or None if it carries none."""
    for s in reversed(trace.spans):
        if s.kind == "final_output" and isinstance(s.output, dict):
            return s.output.get("end_state")
    return None
