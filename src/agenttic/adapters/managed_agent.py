"""Glass-box adapter for Anthropic Managed Agents (beta managed-agents-2026-04-01).

Deploy a business-workflow agent once (``ascore deploy``), then run benchmark
suites against it: each test case becomes a session, and the session's event
stream is converted into a standard :class:`~agenttic.schema.trace.Trace` —
LLM calls, tool calls, thinking, and errors all become spans, so the full
glass-box rubric applies even though Anthropic hosts the agent loop and the
sandbox.

Design notes:
* The agent is a pre-created, versioned resource — this adapter NEVER calls
  ``agents.create``; it references the agent by ID and pins the exact version
  it retrieved into ``describe()`` / ``config_hash()`` for reproducibility.
* ``agents.retrieve`` also supplies the agent's model, which ``make_judge``
  uses to enforce Hard Rule 4 against both judge tiers.
* Stream-first: the SSE stream is opened before the kickoff message so no
  early events are lost; the loop breaks on ``session.status_terminated`` or
  a terminal ``session.status_idle`` (anything but ``requires_action``).
* Hard Rule 5: ``session.error`` events and unresolvable states (e.g. a
  custom tool call this adapter cannot answer) become error spans, never
  exceptions.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

_TERMINAL_STOP = {"end_turn", "retries_exhausted"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sid() -> str:
    return uuid.uuid4().hex[:12]


def _text_of(content) -> str:
    """Concatenate text blocks from an event content list."""
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if getattr(block, "type", "") == "text":
            out.append(getattr(block, "text", ""))
    return "".join(out)


class ManagedAgentAdapter(AgentAdapter):
    """Drives a deployed Anthropic Managed Agent: one session per test case."""

    visibility = "glass_box"

    def __init__(
        self,
        *,
        managed_agent_id: str,
        environment_id: str,
        agent_id: str,
        client=None,
        pricing_per_mtok: dict | None = None,
        max_events: int = 500,
        archive_sessions: bool = True,
        retry_policy=None,
    ):
        if client is None:  # real client only when not injected (tests inject a fake)
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        from agenttic.retry import RetryPolicy
        self.retry_policy = retry_policy or RetryPolicy()
        self.managed_agent_id = managed_agent_id
        self.environment_id = environment_id
        self.agent_id = agent_id
        self.pricing = pricing_per_mtok or {"input": 3.0, "output": 15.0}
        self.max_events = max_events
        self.archive_sessions = archive_sessions
        # Pin the exact agent version under test (GET /v1/agents/{id}).
        from agenttic.retry import with_retry
        agent = with_retry(lambda: client.beta.agents.retrieve(managed_agent_id),
                           self.retry_policy, op="managed-retrieve")
        self.model = getattr(agent, "model", None)
        if not isinstance(self.model, str):  # {id, speed} object form
            self.model = getattr(self.model, "id", str(self.model))
        self.agent_version = getattr(agent, "version", None)
        self.agent_name = getattr(agent, "name", managed_agent_id)

    # -- AgentAdapter interface -------------------------------------------

    def describe(self) -> dict:
        return {
            "adapter": "ManagedAgentAdapter",
            "managed_agent_id": self.managed_agent_id,
            "agent_version": self.agent_version,
            "model": self.model,
            "environment_id": self.environment_id,
        }

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        spans: list[Span] = []
        final_text = ""
        t_wall = time.monotonic()

        from agenttic.retry import with_retry
        session = with_retry(lambda: self.client.beta.sessions.create(
            agent={"type": "agent", "id": self.managed_agent_id,
                   **({"version": self.agent_version} if self.agent_version else {})},
            environment_id=self.environment_id,
            title=f"ascore {test_case_id or 'adhoc'}",
        ), self.retry_policy, op="managed-session")

        pending_llm: dict[str, datetime] = {}   # model_request_start id -> t0
        open_tools: dict[str, Span] = {}        # tool_use event id -> span
        n_events = 0

        with self.client.beta.sessions.events.stream(session_id=session.id) as stream:
            self.client.beta.sessions.events.send(
                session_id=session.id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": json.dumps(test_input)}],
                }],
            )
            for event in stream:
                n_events += 1
                if n_events > self.max_events:
                    spans.append(Span(
                        span_id=_sid(), kind="error", name="max_events_kill_switch",
                        start_time=_now(), end_time=_now(),
                        error=f"session exceeded {self.max_events} events",
                    ))
                    break
                etype = getattr(event, "type", "")

                if etype == "span.model_request_start":
                    pending_llm[getattr(event, "id", "")] = _now()
                elif etype == "span.model_request_end":
                    t0 = pending_llm.pop(
                        getattr(event, "model_request_start_id", ""), _now())
                    usage = getattr(event, "model_usage", None)
                    tin = getattr(usage, "input_tokens", None)
                    tout = getattr(usage, "output_tokens", None)
                    spans.append(Span(
                        span_id=_sid(), kind="llm_call", name=self.model or "llm",
                        start_time=t0, end_time=_now(),
                        output={"is_error": bool(getattr(event, "is_error", False))},
                        tokens_in=tin, tokens_out=tout,
                        cost_usd=self._cost(tin, tout),
                    ))
                elif etype in ("agent.tool_use", "agent.mcp_tool_use"):
                    span = Span(
                        span_id=_sid(), kind="tool_call",
                        name=getattr(event, "name", "tool"),
                        start_time=_now(), end_time=_now(),
                        input=dict(getattr(event, "input", None) or {}),
                    )
                    spans.append(span)
                    open_tools[getattr(event, "id", _sid())] = span
                elif etype in ("agent.tool_result", "agent.mcp_tool_result"):
                    span = open_tools.pop(getattr(event, "tool_use_id", ""), None)
                    if span is not None:
                        span.end_time = _now()
                        if getattr(event, "is_error", False):
                            span.error = _text_of(getattr(event, "content", None)) or "tool error"
                        else:
                            span.output = {"result": _text_of(getattr(event, "content", None))}
                elif etype == "agent.custom_tool_use":
                    # MVP: workflow agents must be self-contained; an
                    # unanswerable custom tool call would deadlock the session.
                    spans.append(Span(
                        span_id=_sid(), kind="error", name="unhandled_custom_tool",
                        start_time=_now(), end_time=_now(),
                        input=dict(getattr(event, "input", None) or {}),
                        error=f"agent called custom tool {getattr(event, 'name', '?')!r}"
                              " which this adapter does not execute",
                    ))
                    break
                elif etype == "agent.thinking":
                    spans.append(Span(
                        span_id=_sid(), kind="agent_decision", name="thinking",
                        start_time=_now(), end_time=_now(),
                    ))
                elif etype == "agent.message":
                    text = _text_of(getattr(event, "content", None))
                    if text.strip():
                        final_text = text
                elif etype == "session.error":
                    spans.append(Span(
                        span_id=_sid(), kind="error", name="session_error",
                        start_time=_now(), end_time=_now(),
                        error=str(getattr(getattr(event, "error", None), "message", None)
                                  or getattr(event, "error", "session error")),
                    ))
                elif etype == "session.status_terminated":
                    if not final_text:
                        spans.append(Span(
                            span_id=_sid(), kind="error", name="session_terminated",
                            start_time=_now(), end_time=_now(),
                            error="session terminated before producing output",
                        ))
                    break
                elif etype == "session.status_idle":
                    stop = getattr(getattr(event, "stop_reason", None), "type", "end_turn")
                    if stop == "requires_action":
                        continue  # waiting on us mid-turn (tool confirmation etc.)
                    if stop not in _TERMINAL_STOP and not final_text:
                        spans.append(Span(
                            span_id=_sid(), kind="error", name=f"idle:{stop}",
                            start_time=_now(), end_time=_now(),
                            error=f"session idled with stop_reason {stop!r}",
                        ))
                    break

        self._archive(session.id)

        t2 = _now()
        spans.append(Span(
            span_id=_sid(), kind="final_output", name="final_output",
            start_time=t2, end_time=t2, output={"text": final_text},
            attributes={"session_id": session.id},
        ))

        return Trace(
            trace_id=uuid.uuid4().hex,
            agent_id=self.agent_id,
            agent_config_hash=self.config_hash(),
            test_case_id=test_case_id,
            spans=spans,
            visibility=self.visibility,
            final_output=final_text,
            total_cost_usd=sum(s.cost_usd or 0.0 for s in spans),
            total_latency_ms=(time.monotonic() - t_wall) * 1000.0,
            total_steps=sum(1 for s in spans if s.kind in ("llm_call", "tool_call")),
            schema_version=SCHEMA_VERSION,
        )

    # -- internals ---------------------------------------------------------

    def _cost(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        if tokens_in is None or tokens_out is None:
            return None
        return (
            tokens_in * self.pricing["input"] + tokens_out * self.pricing["output"]
        ) / 1_000_000

    def _archive(self, session_id: str) -> None:
        """Sessions are disposable; archive to free resources. Best-effort —
        a failed archive must never fail the trace. Polls briefly first
        because the idle event can precede the queryable status flip."""
        if not self.archive_sessions:
            return
        try:
            for _ in range(10):
                s = self.client.beta.sessions.retrieve(session_id)
                if getattr(s, "status", "") != "running":
                    break
                time.sleep(0.2)
            self.client.beta.sessions.archive(session_id)
        except Exception:  # noqa: BLE001 — cleanup only
            pass
