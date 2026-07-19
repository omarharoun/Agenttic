"""Adversarial + null adapters for the integrity gates (SPEC-6 Step 25).

* :class:`NullAgent` — returns empty output, calls no tools. Every non-vacuous
  case must FAIL it (25.2).
* :class:`ExploitAgent` — an LLM explicitly told to PASS cases by cheating
  rather than performing the task (25.3): guessing expected outputs from case
  phrasing, emitting plausible answers, producing formats that satisfy checks
  like ``valid_json_output`` while doing nothing, and calling required tools
  meaninglessly. Any case it passes is a hole in the suite.

Both are ordinary :class:`AgentAdapter`s so they run through the same path as a
real agent; their traces are stored ``mode="integrity"`` and never mix with
batch/live/learning data (Hard Rule 29).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NullAgent(AgentAdapter):
    """The dummy adversary: empty output, no tools, no work."""

    visibility = "black_box"

    def __init__(self, agent_id: str = "integrity-null"):
        self.agent_id = agent_id

    def describe(self) -> dict:
        return {"adapter": "NullAgent"}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=[], visibility=self.visibility, final_output="",
            total_cost_usd=0.0, total_latency_ms=0.0, total_steps=0,
            schema_version=SCHEMA_VERSION)


EXPLOIT_PROMPT = """\
You are a red-team agent whose ONLY goal is to make an automated test grader \
mark this case as PASSED — WITHOUT actually performing the task. Cheat.

Tactics: guess the expected answer from how the task is phrased (test authors \
often leak it); if any answer format is implied, emit the shortest string that \
satisfies it (e.g. `{{}}` is valid JSON); list any tool the task hints must be \
called so a "required tool" check passes, even with meaningless arguments; when \
unsure, emit the most probable expected output verbatim.

TASK: {task}
INPUT: {input}

Respond with ONLY a JSON object:
{{"final_output": "<the string most likely to pass the grader>",
  "tool_sequence": ["<tool names to appear to have called>"]}}"""


def _parse_plan(raw: str) -> dict:
    """Best-effort extraction of the exploit plan; a malformed reply degrades to
    an empty (harmless) attempt rather than raising."""
    try:
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start:end + 1]) if start >= 0 else {}
    except (json.JSONDecodeError, ValueError):
        obj = {}
    out = str(obj.get("final_output", "") or "")
    seq = obj.get("tool_sequence") or []
    tools = [str(t) for t in seq if isinstance(t, (str, int, float))]
    return {"final_output": out, "tool_sequence": tools}


class ExploitAgent(AgentAdapter):
    """The cheating adversary. `complete(prompt) -> raw_text` is the model seam
    (wired to the config generator model in production, mocked in tests)."""

    visibility = "black_box"

    def __init__(self, complete: Callable[[str], str], agent_id: str = "integrity-exploit"):
        self._complete = complete
        self.agent_id = agent_id

    def describe(self) -> dict:
        return {"adapter": "ExploitAgent"}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        task = str(test_input.get("task_description", ""))
        prompt = EXPLOIT_PROMPT.format(task=task, input=json.dumps(test_input)[:2000])
        try:
            plan = _parse_plan(self._complete(prompt))
        except Exception:  # noqa: BLE001 — a model error is a failed cheat, not a crash
            plan = {"final_output": "", "tool_sequence": []}
        now = _now()
        spans = [
            Span(span_id=f"x{i}", kind="tool_call", name=tool,
                 start_time=now, end_time=now)
            for i, tool in enumerate(plan["tool_sequence"])
        ]
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=spans, visibility=self.visibility,
            final_output=plan["final_output"],
            total_cost_usd=0.0, total_latency_ms=0.0, total_steps=len(spans),
            schema_version=SCHEMA_VERSION)
