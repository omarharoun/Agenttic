"""ManagedAgentAdapter: session event stream -> Trace (all API calls mocked).

Covers: trace shape (>=3 spans, tokens/cost populated), event->span mapping
for tool calls and errors, stream-first ordering, terminal idle vs
requires_action, version pinning via agents.retrieve, session archiving,
and Hard Rule 5 (errors are data, never raised).
"""

import json
from types import SimpleNamespace as NS

from ascore.adapters.managed_agent import ManagedAgentAdapter

AGENT = NS(id="agent_01", name="support-triage-workflow",
           model="claude-haiku-4-5-20251001", version=7)


def ev(etype, **kw):
    return NS(type=etype, **kw)


def usage_events(tin=300, tout=50):
    return [
        ev("span.model_request_start", id="mr1"),
        ev("span.model_request_end", model_request_start_id="mr1", is_error=False,
           model_usage=NS(input_tokens=tin, output_tokens=tout)),
    ]


def idle(stop="end_turn"):
    return ev("session.status_idle", stop_reason=NS(type=stop))


class FakeStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *exc):
        return False


class FakeManagedClient:
    def __init__(self, events, session_status="idle"):
        self.sent = []
        self.archived = []
        self.created_sessions = []
        self._events = events
        self._status = session_status
        self.beta = NS(
            agents=NS(retrieve=lambda agent_id: AGENT),
            sessions=NS(
                create=self._create_session,
                retrieve=lambda sid: NS(id=sid, status=self._status),
                archive=lambda sid: self.archived.append(sid),
                events=NS(
                    stream=self._stream,
                    send=lambda session_id, events: self.sent.append(
                        (session_id, events)),
                ),
            ),
        )

    def _create_session(self, **kw):
        self.created_sessions.append(kw)
        return NS(id="sesn_1", status="idle")

    def _stream(self, *, session_id):
        assert self.sent == [], "stream must open before the kickoff is sent"
        return FakeStream(self._events)


def make_adapter(events, **kw):
    client = FakeManagedClient(events)
    defaults = dict(managed_agent_id="agent_01", environment_id="env_01",
                    agent_id="triage-v1", client=client)
    defaults.update(kw)
    return ManagedAgentAdapter(**defaults), client


HAPPY = [
    *usage_events(),
    ev("agent.tool_use", id="tu1", name="grep", input={"pattern": "billing"}),
    ev("agent.tool_result", tool_use_id="tu1", is_error=False,
       content=[NS(type="text", text="billing: payments, charges")]),
    *usage_events(tin=500, tout=10),
    ev("agent.message", content=[NS(type="text", text="billing")]),
    idle(),
]


class TestHappyPath:
    def test_trace_shape_and_usage(self):
        adapter, client = make_adapter(HAPPY)
        trace = adapter.run({"ticket": "charged twice"}, test_case_id="triage-000")
        assert trace.visibility == "glass_box"
        assert trace.final_output == "billing"
        assert len(trace.spans) >= 3
        llm = [s for s in trace.spans if s.kind == "llm_call"]
        assert [s.tokens_in for s in llm] == [300, 500]
        assert trace.total_cost_usd > 0
        assert trace.total_steps == 3  # 2 llm calls + 1 tool call

    def test_tool_span_pairing(self):
        adapter, _ = make_adapter(HAPPY)
        trace = adapter.run({"ticket": "x"})
        (tool,) = [s for s in trace.spans if s.kind == "tool_call"]
        assert tool.name == "grep"
        assert tool.input == {"pattern": "billing"}
        assert tool.output == {"result": "billing: payments, charges"}
        assert tool.error is None

    def test_kickoff_payload_and_session(self):
        adapter, client = make_adapter(HAPPY)
        adapter.run({"ticket": "x"}, test_case_id="t-1")
        session_kw = client.created_sessions[0]
        assert session_kw["agent"] == {"type": "agent", "id": "agent_01", "version": 7}
        assert session_kw["environment_id"] == "env_01"
        (sid, events), = client.sent
        assert sid == "sesn_1"
        assert events[0]["type"] == "user.message"
        assert json.loads(events[0]["content"][0]["text"]) == {"ticket": "x"}

    def test_session_archived_after_run(self):
        adapter, client = make_adapter(HAPPY)
        adapter.run({"ticket": "x"})
        assert client.archived == ["sesn_1"]

    def test_version_pinned_in_config_hash(self):
        adapter, _ = make_adapter(HAPPY)
        assert adapter.model == "claude-haiku-4-5-20251001"  # for Hard Rule 4
        assert adapter.describe()["agent_version"] == 7
        assert adapter.config_hash()  # deterministic, version-bound


class TestErrorsAreData:
    def test_tool_error_becomes_error_span(self):
        events = [
            ev("agent.tool_use", id="tu1", name="web_fetch", input={"url": "x"}),
            ev("agent.tool_result", tool_use_id="tu1", is_error=True,
               content=[NS(type="text", text="403 forbidden")]),
            ev("agent.message", content=[NS(type="text", text="general")]),
            idle(),
        ]
        adapter, _ = make_adapter(events)
        trace = adapter.run({"ticket": "x"})
        (tool,) = [s for s in trace.spans if s.kind == "tool_call"]
        assert tool.error == "403 forbidden"
        assert trace.final_output == "general"  # error didn't crash the run

    def test_session_error_event(self):
        events = [ev("session.error", error=NS(message="mcp auth failed")), idle()]
        adapter, _ = make_adapter(events)
        trace = adapter.run({"ticket": "x"})
        assert any(s.kind == "error" and "mcp auth failed" in s.error
                   for s in trace.spans)

    def test_terminated_without_output(self):
        adapter, _ = make_adapter([ev("session.status_terminated")])
        trace = adapter.run({"ticket": "x"})
        assert trace.final_output == ""
        assert any(s.kind == "error" and s.name == "session_terminated"
                   for s in trace.spans)

    def test_unhandled_custom_tool_breaks_with_error_span(self):
        events = [
            ev("agent.custom_tool_use", id="c1", name="crm_lookup", input={}),
            ev("agent.message", content=[NS(type="text", text="never reached")]),
        ]
        adapter, _ = make_adapter(events)
        trace = adapter.run({"ticket": "x"})
        assert trace.final_output == ""
        assert any(s.name == "unhandled_custom_tool" for s in trace.spans)

    def test_requires_action_idle_does_not_terminate(self):
        events = [
            idle("requires_action"),  # transient — e.g. between parallel tools
            ev("agent.message", content=[NS(type="text", text="billing")]),
            idle("end_turn"),
        ]
        adapter, _ = make_adapter(events)
        assert adapter.run({"ticket": "x"}).final_output == "billing"

    def test_max_events_kill_switch(self):
        events = [ev("agent.thinking")] * 50
        adapter, _ = make_adapter(events, max_events=10)
        trace = adapter.run({"ticket": "x"})
        assert any(s.name == "max_events_kill_switch" for s in trace.spans)

    def test_archive_failure_never_fails_trace(self):
        adapter, client = make_adapter(HAPPY)
        client.beta.sessions.archive = lambda sid: (_ for _ in ()).throw(
            RuntimeError("409 still running"))
        assert adapter.run({"ticket": "x"}).final_output == "billing"
