"""Black-box adapter so the Safe Reference Assistant can be run through
agenttic's OWN Safety Battery to self-certify it.

The coordinator scans this assistant exactly like any user agent: one prompt in,
one final answer out. :class:`SafeAssistantAgent` wraps :class:`SafeAssistant`
into the :class:`~agenttic.adapters.base.AgentAdapter` interface, producing a
:class:`~agenttic.schema.trace.Trace` whose ``final_output`` is the secret-filtered
answer.

Black-box stance on the human-in-the-loop gate: with no human present, any
sensitive action is automatically **denied** (the assistant proceeds without it).
That is the safe default — the assistant is certified for what it does WITHOUT a
human rubber-stamp — and it means the gate itself becomes a tested behavior.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from agenttic.adapters.base import AgentAdapter
from agenttic.assistant.agent import (
    STATUS_AWAITING_APPROVAL, SafeAssistant, new_session,
)
from agenttic.assistant.posture import SYSTEM_PROMPT
from agenttic.assistant.tools import tool_schemas
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sid() -> str:
    return uuid.uuid4().hex[:12]


def _render_prompt(test_input: dict) -> str:
    """Flatten a battery case (``request`` + optional ``content`` that may hide
    an injection) into one user message — same convention as the Connect flow."""
    from agenttic.connect import render_prompt
    return render_prompt(test_input)


class SafeAssistantAgent(AgentAdapter):
    """The Safe Reference Assistant as a black-box agent under test."""

    visibility = "black_box"

    def __init__(self, *, model: str, client=None,
                 agent_id: str = "safe-reference-assistant",
                 max_steps: int = 8, retry_policy=None):
        if client is None:  # real client only when not injected (tests inject fakes)
            import anthropic
            client = anthropic.Anthropic()
        self.model = model
        self.agent_id = agent_id
        self.max_steps = max_steps
        self.assistant = SafeAssistant(client, model, max_steps=max_steps)

    def describe(self) -> dict:
        return {
            "adapter": "SafeAssistantAgent",
            "model": self.model,
            "system_prompt": SYSTEM_PROMPT,
            "tools": [t["name"] for t in tool_schemas()],
            "max_steps": self.max_steps,
            "sandboxed": True,
        }

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        wall = time.monotonic()
        session = new_session()
        prompt = _render_prompt(test_input)
        run_error: str | None = None
        try:
            session = self.assistant.send_message(session, prompt)
            # no human present: deny every sensitive action and keep going
            guard = 0
            while session["status"] == STATUS_AWAITING_APPROVAL and guard < 16:
                session = self.assistant.approve(session, approved=False)
                guard += 1
        except Exception as exc:  # noqa: BLE001 — an upstream/agent failure is a NON-RESULT
            run_error = f"{type(exc).__name__}: {exc}"
            session.setdefault("steps", []).append(
                {"type": "error", "text": run_error})

        steps = session.get("steps", [])
        spans = self._spans_from_steps(steps)
        answer = session.get("answer", "") or ""
        # A failed run (upstream API error, e.g. the model call raising) or a run
        # that produced NO answer at all is a non-result, NOT a valid empty reply.
        # Surface it with the ``BLACKBOX_FAILURE`` marker (the same convention the
        # HTTP adapter uses) so the scoring engine EXCLUDES it as errored instead
        # of scoring an empty body — which would mis-read as a safety verdict
        # (0% refusal). Honest failure beats a silent blank. The assistant's own
        # loop swallows an upstream failure into an ``error`` step (mistakes are
        # data), so look there for the reason when no exception reached us here.
        if run_error is None:
            run_error = next((s.get("text") for s in steps
                              if s.get("type") == "error"), None)
        if not answer.strip():
            final = f"BLACKBOX_FAILURE:{run_error or 'assistant returned no answer'}"
        else:
            final = answer
        spans.append(Span(span_id=_sid(), kind="final_output", name="final_output",
                          start_time=_now(), end_time=_now(),
                          output={"text": final}))
        return Trace(
            trace_id=uuid.uuid4().hex,
            agent_id=self.agent_id,
            agent_config_hash=self.config_hash(),
            test_case_id=test_case_id,
            spans=spans,
            visibility=self.visibility,
            final_output=final,
            total_cost_usd=0.0,
            total_latency_ms=(time.monotonic() - wall) * 1000.0,
            total_steps=sum(1 for s in spans if s.kind in ("llm_call", "tool_call")),
            schema_version=SCHEMA_VERSION,
        )

    def _spans_from_steps(self, steps: list[dict]) -> list[Span]:
        spans: list[Span] = []
        for st in steps:
            t = st.get("type")
            if t == "llm_call":
                spans.append(Span(span_id=_sid(), kind="llm_call", name=self.model,
                                  start_time=_now(), end_time=_now(),
                                  output={"stop_reason": st.get("stop_reason")}))
            elif t == "tool_result":
                spans.append(Span(
                    span_id=_sid(), kind="tool_call", name=st.get("tool", "tool"),
                    start_time=_now(), end_time=_now(),
                    output={"ok": st.get("ok"),
                            "injection_blocked": st.get("injection_blocked")},
                    error=st.get("error")))
            elif t == "error":
                # a run-level failure (e.g. the upstream model call raised) —
                # record it so the trace shows WHY it errored, not a blank.
                spans.append(Span(
                    span_id=_sid(), kind="error", name="error",
                    start_time=_now(), end_time=_now(), error=st.get("text")))
        return spans
