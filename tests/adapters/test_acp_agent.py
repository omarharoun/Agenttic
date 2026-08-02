"""The ACP adapter — one client, any Agent Client Protocol agent, no code.

Driven against a fake agent that speaks the REAL wire protocol (JSON-RPC 2.0
over stdio, methods and field names taken from ``agent-client-protocol`` 0.8.1),
so what is under test is the client's behaviour on the protocol, not a mock
shaped to agree with it. Offline: no network, no API key, no real agent.

What these pin is why ACP is worth preferring over parsing an agent's private
event stream. The protocol DECLARES the things our coverage model otherwise has
to guess:

* ``kind`` on a tool call -> ``mutating`` / ``irreversible``, explicitly;
* ``status: failed`` -> a real tool failure, not a substring match on "error";
* ``usage`` -> a subprocess agent's spend, instead of a reported $0.00;
* ``stopReason: refusal`` -> a refusal as a fact rather than a regex on prose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agenttic.adapters.acp_agent import (HARNESS_FAILURE, TOOL_KIND_RISK,
                                         ACPAgent, _text_of)

FAKE = str(Path(__file__).parent / "fake_acp_agent.py")


def agent_in(mode: str = "normal", **kw) -> ACPAgent:
    import os
    env = {**os.environ, "ACP_FAKE_MODE": mode}
    return ACPAgent(agent_id="fake-acp", command=[sys.executable, FAKE],
                    env=env, timeout_s=kw.pop("timeout_s", 30), **kw)


def _tools(trace):
    return [s for s in trace.spans if s.kind == "tool_call"]


def _disclosures(trace) -> list[str]:
    out: list[str] = []
    for s in trace.spans:
        out += (s.attributes or {}).get("disclosures") or []
    return out


class TestAWholeTurn:
    def test_it_drives_the_protocol_end_to_end(self):
        trace = agent_in().run({"task": "fix the bug"}, test_case_id="c1")
        assert trace.final_output == "Fixed the operator."
        assert trace.visibility == "glass_box"
        assert trace.test_case_id == "c1"
        assert [s.kind for s in trace.spans][-1] == "final_output"

    def test_the_user_turn_is_recorded(self):
        """`session_shape` is a claim about what a run exhibited; the protocol
        states who spoke, so turn shape is measured rather than assumed."""
        trace = agent_in().run({"task": "t"})
        assert any(s.kind == "user_turn" for s in trace.spans)


class TestWhatTheProtocolDeclares:
    def test_tool_kind_becomes_an_explicit_risk_class(self):
        """This is the whole argument for ACP.

        `action_risk` over a bespoke adapter came back 0.0 on a real run because
        nothing could tell that `file_editor` mutates state — the classifier was
        left sniffing tool names. ACP's `kind` is the agent's OWN declaration, so
        the class is `explicit` (see verification/traffic.classify_confidence),
        not inferred.
        """
        from agenttic.verification.traffic import classify_confidence

        trace = agent_in().run({"task": "t"})
        read = next(s for s in _tools(trace) if s.attributes["acp_tool_kind"] == "read")
        edit = next(s for s in _tools(trace) if s.attributes["acp_tool_kind"] == "edit")
        assert read.attributes["mutating"] is False
        assert edit.attributes["mutating"] is True
        assert classify_confidence(edit) == "explicit"
        assert classify_confidence(read) == "explicit"

    def test_an_undefined_tool_kind_is_left_unclassified(self):
        """ACP defines `other`/`switch_mode` as "anything else".

        Claiming to know their risk would be the name-sniffing the table exists
        to replace, so they carry no mutation attributes and are reported
        `unknown` — never credited read-only.
        """
        from agenttic.verification.traffic import classify_confidence
        from agenttic.schema.trace import Span
        from datetime import datetime, timezone

        assert "other" not in TOOL_KIND_RISK and "switch_mode" not in TOOL_KIND_RISK
        now = datetime(2026, 8, 2, tzinfo=timezone.utc)
        span = Span(span_id="s1", kind="tool_call", name="do_something",
                    start_time=now, end_time=now,
                    attributes={"acp_tool_kind": "other"})
        assert classify_confidence(span) == "unknown"

    def test_a_failed_call_is_taken_from_status_not_from_a_substring(self):
        """`tool_condition` over a private event stream was a substring sniff:
        the word "timeout" anywhere in a payload credited a timeout. Here the
        agent STATES that the call failed."""
        trace = agent_in().run({"task": "t"})
        failed = [s for s in _tools(trace) if s.error]
        assert len(failed) == 1
        assert failed[0].attributes["acp_tool_kind"] == "edit"
        assert failed[0].attributes["status"] == "failed"

    def test_token_usage_is_recorded(self):
        """A subprocess agent's spend is invisible to us; the protocol reports
        it, so a scorecard need not print $0.00 about a run that cost money."""
        trace = agent_in().run({"task": "t"})
        rec = next(s for s in trace.spans if s.name == "harness_record")
        assert rec.attributes["tokens_in"] == 120
        assert rec.attributes["tokens_out"] == 45

    def test_a_refusal_is_a_declared_fact(self):
        trace = agent_in("refuse").run({"task": "do something bad"})
        rec = next(s for s in trace.spans if s.name == "harness_record")
        assert rec.attributes["stop_reason"] == "refusal"
        assert any((s.attributes or {}).get("refused") for s in trace.spans)
        assert any("not because it finished" in d for d in _disclosures(trace))


class TestPairingAndPreservation:
    def test_results_that_return_out_of_order_find_their_own_call(self):
        """The fake answers t2 before t1 deliberately: a FIFO implementation
        attributes each result to the wrong call, and the trace then reads as an
        agent that ran a command it never ran."""
        trace = agent_in().run({"task": "t"})
        by_id = {s.attributes["tool_call_id"]: s for s in _tools(trace)}
        assert "def add" in json.dumps(by_id["t1"].output)
        assert "permission denied" in json.dumps(by_id["t2"].output)

    def test_an_unmodelled_update_is_kept_but_never_scored(self):
        """`plan` is a member of the union this adapter does not model."""
        from agenttic.coverage.extractors import _escalation_act

        trace = agent_in().run({"task": "t"})
        kept = [s for s in trace.spans
                if (s.attributes or {}).get("unmapped_update")]
        assert kept and kept[0].kind == "env_step"
        assert not _escalation_act(kept[0])

    def test_unreadable_frames_are_counted_and_disclosed(self):
        trace = agent_in("garbage").run({"task": "t"})
        assert trace.final_output == "Fixed the operator."
        assert any("not valid JSON-RPC" in d for d in _disclosures(trace))


class TestFailuresThatAreNotTheAgents:
    def test_a_missing_binary_is_a_non_result_and_never_raises(self):
        a = ACPAgent(command=["definitely-not-installed-xyz"], timeout_s=5)
        trace = a.run({"task": "t"})
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert any("never invoked" in d for d in _disclosures(trace))

    def test_a_crash_mid_session_keeps_what_arrived(self):
        trace = agent_in("crash").run({"task": "t"})
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert _tools(trace), "the tool call that did arrive was discarded"
        assert any("non-result" in d for d in _disclosures(trace))

    def test_an_unanswered_prompt_times_out_rather_than_hanging(self):
        trace = agent_in("hang", timeout_s=2).run({"task": "t"})
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert "did not answer" in trace.final_output

    def test_the_agent_process_is_killed_even_on_timeout(self):
        """The harness's own timeout abandons the adapter thread without killing
        its child (`harness/runner.py` uses asyncio.to_thread), so an adapter
        that leaks its subprocess leaves an agent running against the user's API
        key. Measured on a real run: two orphans still alive ~40 minutes later.
        """
        a = agent_in("hang", timeout_s=2)
        procs: list = []
        real = __import__("subprocess").Popen

        import subprocess as sp
        def _spy(*args, **kw):
            p = real(*args, **kw)
            procs.append(p)
            return p
        sp.Popen = _spy               # noqa: SLF001 — restored below
        try:
            a.run({"task": "t"})
        finally:
            sp.Popen = real
        assert procs, "no process was started"
        assert procs[0].poll() is not None, "the agent was left running"

    def test_authentication_required_is_reported_as_the_agent_stated_it(self):
        """OpenHands 1.16.0 really does this: its only advertised ACP auth is an
        interactive cloud OAuth flow, so a headless run cannot start a session.
        That is a finding about the subject and must arrive as the subject's own
        message, not as a fabricated failure."""
        trace = agent_in("noauth").run({"task": "t"})
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert "Authentication required" in trace.final_output
        assert any("advertises authentication method" in d
                   for d in _disclosures(trace))

    def test_an_auth_method_is_never_guessed(self):
        """Guessing would hang: the flow is interactive. Absent configuration we
        go on and let session/new fail with the agent's own words."""
        a = agent_in("noauth")
        assert a.auth_method == ""
        trace = a.run({"task": "t"})
        assert "Authentication required" in trace.final_output


class TestPermission:
    def test_a_permission_request_is_answered_and_recorded(self):
        """An agent that ASKED and was refused did something different from one
        that never asked. The trace has to be able to tell them apart."""
        trace = agent_in("permission").run({"task": "t"})
        rec = next(s for s in trace.spans if s.name == "harness_record")
        calls = rec.attributes["client_calls"]
        assert any(c["method"] == "session/request_permission" for c in calls)
        assert any(c.get("decision") == "allow" for c in calls)

    def test_a_policy_can_refuse(self):
        a = agent_in("permission", permission_policy=lambda _tc: "reject")
        trace = a.run({"task": "t"})
        rec = next(s for s in trace.spans if s.name == "harness_record")
        assert any(c.get("decision") == "reject"
                   for c in rec.attributes["client_calls"])
        assert "reject_once" in trace.final_output or "n" in trace.final_output


class TestTheHarnessContract:
    def test_it_advertises_sessions(self):
        """ACP has session/new + repeated session/prompt, so this is the first
        adapter that can hold a real conversation — `multi_turn_state` is in
        UNEXERCISABLE_FEATURES only because nothing could take a second turn."""
        assert ACPAgent.supports_sessions() is True

    def test_describe_is_deterministic_and_secret_free(self):
        a = ACPAgent(command=["x"], model="m", version="1")
        assert json.dumps(a.describe(), sort_keys=True) == \
               json.dumps(a.describe(), sort_keys=True)
        assert "env" not in a.describe()

    def test_cwd_is_excluded_from_the_identity(self):
        """Where the run happened is not what the agent IS; including it gives
        one agent a different hash per directory and defeats resume."""
        assert (ACPAgent(command=["x"], cwd="/tmp/a").config_hash()
                == ACPAgent(command=["x"], cwd="/tmp/b").config_hash())

    def test_the_command_changes_the_identity(self):
        assert (ACPAgent(command=["a"]).config_hash()
                != ACPAgent(command=["b"]).config_hash())

    def test_run_writes_no_state_to_self(self):
        a = agent_in()
        before = dict(vars(a))
        a.run({"task": "one"}, test_case_id="a")
        a.run({"task": "two"}, test_case_id="b")
        assert dict(vars(a)) == before

    def test_the_conversation_id_is_stamped_on_every_span(self):
        """The correlation key: spans the agent exports to OTel carrying this id
        can be joined to THIS run — the zero-adapter-code glass-box path."""
        from agenttic.ingest.correlate import CONVERSATION_ID

        trace = agent_in(conversation_id="conv-42").run({"task": "t"})
        assert all((s.attributes or {}).get(CONVERSATION_ID) == "conv-42"
                   for s in trace.spans)


@pytest.mark.parametrize("content,want", [
    ({"type": "text", "text": "hi"}, "hi"),
    ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "ab"),
    ("plain", "plain"),
    (None, ""),
])
def test_content_block_text_extraction(content, want):
    assert _text_of(content) == want


class TestTheHarnessCanStopIt:
    """The orphan bug, and the reason `abort_run` exists.

    `runner.run_suite` drives adapters through `asyncio.to_thread` and its
    timeout cancels the AWAIT, not the thread. The adapter keeps going until its
    OWN deadline, so a spawned agent outlives the run by
    (adapter timeout - harness timeout) — 780s under the shipped config. On a
    real run two agents were still alive ~40 minutes after the suite gave up,
    still spending against the user's API key.
    """

    def test_a_child_survives_the_harness_timeout_without_abort_run(self):
        """The defect itself, pinned.

        Observed INSIDE the running loop on purpose. `asyncio.run()` waits for
        its default executor on the way out, so checking after it returns hides
        the window — and hides it in exactly the way that matters, because a real
        suite keeps running for hours after one case times out.
        """
        import asyncio
        import subprocess as sp

        a = agent_in("hang", timeout_s=30)
        started: list = []
        real = sp.Popen
        sp.Popen = lambda *x, **k: (lambda p: (started.append(p), p)[1])(real(*x, **k))
        alive_after_giving_up = None
        try:
            async def drive():
                nonlocal alive_after_giving_up
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(a.run, {"task": "t"}), timeout=2)
                except asyncio.TimeoutError:
                    pass
                await asyncio.sleep(1)          # the suite carries on
                alive_after_giving_up = [p for p in started if p.poll() is None]
                a.abort_run()                   # do not leak out of the test
            asyncio.run(drive())
            assert alive_after_giving_up, \
                "the child died on its own; the orphan window has closed elsewhere"
        finally:
            sp.Popen = real
            a.abort_run()
            for p in started:
                if p.poll() is None:
                    p.kill()

    def test_abort_run_kills_the_child_the_harness_gave_up_on(self):
        import asyncio
        import subprocess as sp
        import time

        a = agent_in("hang", timeout_s=30)
        started: list = []
        real = sp.Popen
        sp.Popen = lambda *x, **k: (lambda p: (started.append(p), p)[1])(real(*x, **k))
        try:
            dead = None

            async def drive():
                nonlocal dead
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(a.run, {"task": "t"}, test_case_id="c1"),
                        timeout=2)
                except asyncio.TimeoutError:
                    a.abort_run("c1")          # what the harness now does
                await asyncio.sleep(1)
                dead = started[0].poll() is not None
            asyncio.run(drive())
            assert dead, "the agent was left running"
        finally:
            sp.Popen = real
            for p in started:
                if p.poll() is None:
                    p.kill()

    def test_the_harness_itself_calls_abort_run_on_timeout(self):
        """The hook is only worth having if run_suite actually uses it.

        Asserts the CALL, not the after-state: `asyncio.run` waits for its
        executor on the way out, so by the time run_suite returns the adapter
        has finished its own deadline and cleaned up regardless. An
        after-the-fact assertion passes with the harness call deleted — checked,
        by deleting it.
        """
        import asyncio

        from agenttic.harness.runner import HarnessConfig, run_suite
        from agenttic.schema.testcase import TestCase, TestSuite

        class _Store:
            def save_trace(self, t): pass
            def traces(self, *a, **k): return []

        aborted: list = []
        a = agent_in("hang", timeout_s=8)
        real_abort = a.abort_run

        def spy(case_id=None):
            aborted.append(case_id)
            return real_abort(case_id)
        a.abort_run = spy                       # type: ignore[method-assign]

        suite = TestSuite(suite_id="s", version=1, approved=True, name="s",
                          business_context="abort_run coverage")
        cases = [TestCase(test_id="c1", suite_id="s", version=1,
                          task_description="d", input={"task": "t"}, expected={},
                          rubric_id="r")]
        traces = asyncio.run(run_suite(
            a, suite, cases, _Store(),
            HarnessConfig(timeout_seconds=2, max_parallel=1, transport_retries=0),
            resume=False))
        assert aborted == ["c1"], \
            "the harness timed out and never told the adapter to stop"
        assert traces[0].final_output.startswith("HARNESS_FAILURE")
