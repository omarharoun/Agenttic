"""The OpenHands headless adapter — the subject of the first public evaluation.

What is on trial is not that the adapter produces a Trace, but that the Trace is
an honest record of what the subject did:

* a tool call and the result that answered it are ONE span, joined on
  `tool_call_id` — otherwise the span's `output` is somebody else's result;
* an action whose observation never came back still appears, marked — a tool
  call with no result is a finding, and dropping it makes the trace claim fewer
  calls than the agent made;
* an event kind this adapter does not know is KEPT with its payload — an
  unmapped kind is a version skew between us and the subject, and dropping it
  hides the skew exactly when it matters;
* a run that never reached the agent is a NON-RESULT, never a wrong answer.

The fixture is captured from the real pinned SDK (`openhands-sdk==1.28.1`), not
hand-authored: `openhands --headless --json` emits exactly
`json.dumps(event.model_dump())` per line, so dumping the SDK's own event objects
reproduces the wire format. See the module docstring of the adapter.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agenttic.adapters.openhands_headless import (
    HARNESS_FAILURE, OpenHandsHeadlessAgent, _parse, _to_spans)

FIXTURE = Path(__file__).parent / "fixtures" / "openhands_headless.jsonl"
#: The complete stdout of ONE real `openhands --headless --json` process
#: (CLI 1.16.0, 2026-08-02), captured verbatim — terminal chrome and all.
#: The SDK-derived fixture above is a correct account of the event schema and
#: still missed two things that only running the binary showed: `--json` does
#: not silence the human UI, and the agent answers by calling `finish` rather
#: than by sending a message. Both defects are pinned below against THIS file.
REAL_RUN = Path(__file__).parent / "fixtures" / "openhands_cli_run.stdout"
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _spans(raw: str):
    events, bad = _parse(raw)
    spans, final = _to_spans(events, "tid12345", fallback_start=NOW)
    return spans, final, bad


def _captured() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _disclosures(trace) -> list[str]:
    """Every disclosure the adapter attached, from wherever it recorded it.

    Deliberately reads the SPANS rather than a run-level field: `Trace` has no
    `attributes`, and pydantic drops unknown kwargs in silence, so an adapter
    that passed `attributes=...` to the constructor would look correct at the
    call site and disclose nothing. This adapter did exactly that until
    2026-08-02 and the tests below are what caught it.
    """
    out: list[str] = []
    for s in trace.spans:
        out += (s.attributes or {}).get("disclosures", [])
    return out


# --------------------------------------------------------------------------- #
# the captured stream
# --------------------------------------------------------------------------- #


class TestTheCapturedStream:
    def test_the_fixture_is_the_real_wire_format(self):
        """Every line is one JSON object carrying the SDK's own `kind`.

        `kind` is NOT in `Event.model_fields` — looking there says it is absent —
        but it IS in the serialized output. An adapter that routed structurally
        because the field "did not exist" would be routing on coincidence.
        """
        lines = [json.loads(x) for x in _captured().splitlines() if x.strip()]
        assert lines, "fixture is empty"
        assert all("kind" in ev for ev in lines)
        assert {ev["kind"] for ev in lines} >= {
            "MessageEvent", "ActionEvent", "ObservationEvent", "AgentErrorEvent"}

    def test_the_whole_run_maps(self):
        spans, final, bad = _spans(_captured())
        assert bad == 0
        kinds = [s.kind for s in spans]
        assert kinds.count("user_turn") == 1
        assert kinds.count("tool_call") == 3
        assert kinds[-1] == "final_output"
        assert "Fixed the operator" in final

    def test_the_agents_last_message_is_the_answer(self):
        _, final, _ = _spans(_captured())
        assert final.startswith("Fixed the operator in calc.py")


class TestWhatOnlyRunningTheBinaryShowed:
    """Two defects the captured SDK fixture could not have caught.

    Both were live in the adapter, both would have made the whole evaluation
    worthless, and both were found by running the subject once on a trivial
    task. Recorded here as the reason a smoke run against the real binary is
    part of building an adapter, not an optional extra.
    """

    def test_terminal_chrome_is_not_reported_as_lost_evidence(self):
        """`--json` does not silence the UI: 27 of 33 lines were decoration.

        Counting those as unreadable events put a disclosure on every run
        saying we had dropped 27 events. We had dropped none. A false alarm
        that fires every time is worse than no alarm — it is exactly the noise
        a real dropped event would hide in.
        """
        raw = REAL_RUN.read_text(encoding="utf-8")
        assert len(raw.splitlines()) == 33, "fixture changed; re-derive the counts"
        events, lost = _parse(raw)
        assert lost == 0, "terminal chrome was miscounted as dropped events"
        assert len(events) == 5

    def test_a_line_that_opens_like_an_event_and_fails_IS_lost_evidence(self):
        """The distinction has to cut both ways or it is just suppression."""
        events, lost = _parse('{"kind": "MessageEvent"\n' + "Goodbye! 👋\n")
        assert lost == 1, "a truncated event line must still be counted"
        assert events == []

    def test_the_answer_is_taken_from_the_finish_tool_call(self):
        """The agent never sent a message — it called `finish`.

        An adapter that reads only MessageEvent finds no answer and returns a
        HARNESS_FAILURE marker for a task the agent actually completed. Every
        run would have scored as a harness failure, and the evaluation would
        have reported a broken subject rather than a broken adapter.
        """
        spans, final, _ = _spans(REAL_RUN.read_text(encoding="utf-8"))
        assert final is not None, "the completed task was read as a non-result"
        assert "hello.txt" in final
        # It is still a tool call. Hiding it would understate what the agent did.
        assert any(s.kind == "tool_call" and s.name == "finish" for s in spans)
        assert spans[-1].kind == "final_output"

    def test_the_real_run_maps_with_nothing_unrecognised(self):
        """Version-skew canary against the pinned CLI, on real output."""
        spans, _, _ = _spans(REAL_RUN.read_text(encoding="utf-8"))
        assert not [s for s in spans if (s.attributes or {}).get("unmapped_kind")]
        assert [s.kind for s in spans] == [
            "user_turn", "tool_call", "tool_call", "final_output"]


# --------------------------------------------------------------------------- #
# pairing — the property that makes a span's output its own
# --------------------------------------------------------------------------- #


class TestActionsPairWithTheirResults:
    def test_each_result_lands_on_the_call_it_answers(self):
        spans, _, _ = _spans(_captured())
        calls = [s for s in spans if s.kind == "tool_call"]
        # tc_1 read the file; tc_2 edited it. If pairing were positional or by
        # arrival order, a later result could be attributed to an earlier call.
        read = next(s for s in calls if "cat calc.py" in str(s.input))
        assert "a-b" in str(read.output), "the read got someone else's result"
        edit = next(s for s in calls if "sed" in str(s.input))
        assert "a-b" not in str(edit.output)

    def test_results_that_come_back_out_of_order_still_find_their_call(self):
        """Two calls in flight, answered in the reverse order.

        The captured fixture answers each call before making the next, so it
        cannot tell id-matching apart from "pair with the oldest pending" — a
        FIFO implementation passes every other test in this file. This is the
        case that separates them, and it is the realistic one: the moment a
        subject issues two calls before either returns, FIFO attributes each
        result to the wrong call, and the trace reads as an agent that ran a
        command it never ran.
        """
        ts = NOW.isoformat()
        stream = "\n".join(json.dumps(e) for e in [
            {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
             "tool_call_id": "A", "timestamp": ts,
             "tool_call": {"arguments": '{"command": "ls"}'}},
            {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
             "tool_call_id": "B", "timestamp": ts,
             "tool_call": {"arguments": '{"command": "pwd"}'}},
            # B answers first — the reversal FIFO cannot survive.
            {"kind": "ObservationEvent", "source": "environment",
             "tool_name": "bash", "tool_call_id": "B", "timestamp": ts,
             "observation": {"output": "/repo"}},
            {"kind": "ObservationEvent", "source": "environment",
             "tool_name": "bash", "tool_call_id": "A", "timestamp": ts,
             "observation": {"output": "calc.py"}},
        ])
        spans, _, _ = _spans(stream)
        by_id = {s.attributes["tool_call_id"]: s for s in spans}
        assert "calc.py" in str(by_id["A"].output), "A got B's result"
        assert "/repo" in str(by_id["B"].output), "B got A's result"
        assert not any(s.attributes.get("unpaired") for s in spans)

    def test_an_unanswered_call_is_still_recorded_and_marked(self):
        """A tool call whose observation never came back."""
        ev = {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
              "tool_call_id": "tc_lost", "timestamp": NOW.isoformat(),
              "tool_call": {"arguments": '{"command": "sleep 1"}'}}
        spans, _, _ = _spans(json.dumps(ev))
        call = next(s for s in spans if s.kind == "tool_call")
        assert call.output == {}
        assert call.attributes["result"] == "no observation was returned"

    def test_an_observation_with_no_action_is_kept_not_dropped(self):
        ev = {"kind": "ObservationEvent", "source": "environment",
              "tool_name": "bash", "tool_call_id": "tc_orphan",
              "timestamp": NOW.isoformat(), "observation": {"output": "hi"}}
        spans, _, _ = _spans(json.dumps(ev))
        assert len(spans) == 1
        assert spans[0].attributes["unpaired"] is True

    def test_an_error_attaches_to_the_call_it_broke(self):
        """The failing `pytest -q` must carry the error, not a sibling call."""
        spans, _, _ = _spans(_captured())
        errored = [s for s in spans if s.error]
        assert len(errored) == 1
        assert "pytest" in str(errored[0].input)
        assert "timed out" in errored[0].error
        assert errored[0].attributes["result"] == "errored"


# --------------------------------------------------------------------------- #
# absence, skew and non-results
# --------------------------------------------------------------------------- #


class TestNothingIsSilentlyDropped:
    def test_an_unknown_event_kind_is_preserved_with_its_payload(self):
        """A kind this adapter has never seen means the subject moved.

        Dropping it would shrink the trace silently — the failure mode is a
        version skew that looks like an agent doing less work.
        """
        ev = {"kind": "SomeFutureEvent", "source": "agent",
              "timestamp": NOW.isoformat(), "surprise": 42}
        spans, _, _ = _spans(json.dumps(ev))
        assert len(spans) == 1
        assert spans[0].attributes["unmapped_kind"] == "SomeFutureEvent"
        assert spans[0].output["event"]["surprise"] == 42

    def test_an_unknown_event_is_kept_but_never_scored(self):
        """Keeping it must not let it CREDIT anything.

        `extractors.py` and `builtins.py` read `agent_decision` spans and
        text-match them — `is_escalation` on a payload we admit we cannot read
        would credit `traj_escalated_to_human` from a coincidence. `env_step` is
        counted by nothing, which is the only kind that keeps the evidence
        without scoring it.
        """
        from agenttic.coverage.extractors import _escalation_act

        ev = {"kind": "SomeFutureEvent", "source": "agent",
              "timestamp": NOW.isoformat(),
              "note": "escalate to a human supervisor immediately"}
        spans, _, _ = _spans(json.dumps(ev))
        assert spans[0].kind == "env_step"
        assert not _escalation_act(spans[0]), \
            "an unreadable payload credited a coverage bin by text match"

    def test_unreadable_lines_are_counted_not_skipped(self):
        """Only lines that CLAIMED to be events count as lost ones.

        This test asserted 2 until 2026-08-02, when a real run showed that
        "not JSON" mostly means terminal decoration — see
        `TestWhatOnlyRunningTheBinaryShowed`. `not json at all` is prose the CLI
        prints; `{broken` opened an object and failed, and that one is a
        genuinely lost event. The count narrowed because it got more precise,
        and both halves are pinned separately above.
        """
        raw = "not json at all\n" + _captured() + "\n{broken\n"
        spans, final, bad = _spans(raw)
        assert bad == 1
        assert final is not None, "the good events must still map"

    def test_no_agent_message_is_a_non_result_not_an_empty_answer(self):
        """`final` is None when nothing was said; the caller turns that into a
        HARNESS_FAILURE marker rather than an empty string, because an empty
        answer and a run that produced none are different findings."""
        ev = {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
              "tool_call_id": "t", "timestamp": NOW.isoformat()}
        _, final, _ = _spans(json.dumps(ev))
        assert final is None


class TestAFailureThatIsNotTheAgents:
    def test_a_missing_binary_never_raises_and_is_marked(self):
        """Hard Rule 5: agent mistakes are data. A missing binary is not even
        an agent mistake — it must not read as a failed answer."""
        agent = OpenHandsHeadlessAgent(binary="definitely-not-installed-xyz")
        trace = agent.run({"task": "anything"}, test_case_id="c1")
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert trace.spans[0].kind == "error"
        assert "not an agent result" in " ".join(_disclosures(trace))

    def test_a_timeout_keeps_the_partial_evidence(self, monkeypatch):
        """A killed run still produced events; discarding them would throw away
        the only record of how far the agent got."""
        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd="openhands", timeout=1, output=_captured())
        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        agent = OpenHandsHeadlessAgent(timeout_s=1)
        trace = agent.run({"task": "x"})
        assert [s for s in trace.spans if s.kind == "tool_call"], \
            "the partial events were thrown away"
        assert any("was NOT necessarily finished" in d for d in _disclosures(trace))

    def test_a_nonzero_exit_is_disclosed(self, monkeypatch):
        class _P:
            stdout, stderr, returncode = _captured(), "boom", 3
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        trace = OpenHandsHeadlessAgent().run({"task": "x"})
        assert any(s.name == "process_exit" for s in trace.spans)
        assert any("exited non-zero" in d for d in _disclosures(trace))

    def test_every_disclosure_survives_onto_the_trace(self, monkeypatch):
        """The disclosures must be READABLE off the returned Trace.

        `Trace` has no `attributes` field and pydantic ignores unknown kwargs,
        so `Trace(..., attributes={"disclosures": [...]})` constructs cleanly and
        discards every word of it. The adapter shipped that for a day: it
        computed honest disclosures and returned a trace carrying none.
        """
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        class _P:                       # non-zero exit AND an unreadable line
            stdout, stderr, returncode = "{oops\n" + _captured(), "e", 9
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())

        trace = OpenHandsHeadlessAgent().run({"task": "x"})
        said = _disclosures(trace)
        assert any("not valid JSON" in d for d in said)
        assert any("exited non-zero" in d for d in said)

    def test_the_run_record_carries_the_subject_version(self, monkeypatch):
        """Which build produced this evidence — on the trace, not in a log."""
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        class _P:
            stdout, stderr, returncode = _captured(), "", 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())

        trace = OpenHandsHeadlessAgent(version="1.16.0").run({"task": "x"})
        rec = next(s for s in trace.spans if s.name == "harness_record")
        assert rec.attributes["subject_version"] == "1.16.0"
        assert rec.attributes["n_events"] == 8


# --------------------------------------------------------------------------- #
# the harness contract
# --------------------------------------------------------------------------- #


class TestTheHarnessContract:
    def test_describe_is_deterministic_and_json_serialisable(self):
        a = OpenHandsHeadlessAgent(version="1.16.0", model="m")
        assert json.dumps(a.describe(), sort_keys=True) == \
               json.dumps(a.describe(), sort_keys=True)

    def test_describe_excludes_the_working_directory(self):
        """`cwd` is where the run happened, not what the agent IS. Including it
        would give one agent a different config hash per directory and silently
        defeat resume — which is keyed on that hash."""
        here = OpenHandsHeadlessAgent(cwd="/tmp/a", version="1.16.0")
        there = OpenHandsHeadlessAgent(cwd="/tmp/b", version="1.16.0")
        assert here.config_hash() == there.config_hash()

    def test_the_pinned_version_changes_the_config_hash(self):
        """Two builds of the subject are two agents. If the hash ignored the
        version, a resumed run could serve traces from a different build."""
        assert (OpenHandsHeadlessAgent(version="1.16.0").config_hash()
                != OpenHandsHeadlessAgent(version="1.17.0").config_hash())

    def test_pinning_a_model_uses_the_env_route_the_cli_actually_has(self, monkeypatch):
        """OpenHands CLI 1.16.0 has NO `--model` flag.

        It reads `LLM_MODEL`, and ONLY when `--override-with-envs` is passed —
        its own `--help` says environment variables are otherwise ignored. Two
        ways to get this wrong, both silent: pass `--model` and argparse rejects
        the whole command (every case becomes a non-result that reads like the
        subject failing), or set the env var without the flag and the CLI
        quietly runs a different model than the evidence claims.
        """
        seen = {}

        def _capture(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
            class _P:
                stdout, stderr, returncode = _captured(), "", 0
            return _P()
        monkeypatch.setattr(subprocess, "run", _capture)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        OpenHandsHeadlessAgent(model="anthropic/claude-opus-5",
                               api_key="sk-test").run({"task": "x"})
        assert "--model" not in seen["cmd"], "the CLI has no such flag"
        assert "--override-with-envs" in seen["cmd"], \
            "without this flag the CLI ignores LLM_MODEL entirely"
        assert seen["env"]["LLM_MODEL"] == "anthropic/claude-opus-5"

    def test_an_unpinned_model_is_disclosed_not_assumed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        class _P:
            stdout, stderr, returncode = _captured(), "", 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())

        trace = OpenHandsHeadlessAgent(model="").run({"task": "x"})
        assert any("cannot say which model produced it" in d
                   for d in _disclosures(trace))

    def test_the_api_key_never_reaches_the_trace(self):
        """`describe()` is hashed into every trace and serialised with the run."""
        a = OpenHandsHeadlessAgent(model="m", api_key="sk-ant-REAL-SECRET",
                                   base_url="https://api.example")
        assert "sk-ant-REAL-SECRET" not in json.dumps(a.describe())
        assert a.describe()["base_url"] == "https://api.example"

    def test_two_endpoints_are_two_agents(self):
        assert (OpenHandsHeadlessAgent(base_url="https://a").config_hash()
                != OpenHandsHeadlessAgent(base_url="https://b").config_hash())

    def test_it_is_glass_box(self):
        assert OpenHandsHeadlessAgent().visibility == "glass_box"

    def test_run_writes_no_state_to_self(self, monkeypatch):
        """The harness holds ONE adapter and calls run() from up to
        max_parallel threads. Per-run state on self is a live race."""
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")

        class _P:
            stdout, stderr, returncode = _captured(), "", 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())

        agent = OpenHandsHeadlessAgent()
        before = dict(vars(agent))
        agent.run({"task": "one"}, test_case_id="a")
        agent.run({"task": "two"}, test_case_id="b")
        assert dict(vars(agent)) == before

    @pytest.mark.parametrize("payload,expected", [
        ({"task": "do the thing"}, "do the thing"),
        ({"problem_statement": "fix it"}, "fix it"),
        ({"a": 1, "b": 2}, '{"a": 1, "b": 2}'),
    ])
    def test_the_task_text_is_deterministic(self, payload, expected):
        """A task string that varied run to run would change the subject's
        behaviour for a reason that has nothing to do with the subject."""
        from agenttic.adapters.openhands_headless import _task_text
        assert _task_text(payload) == expected
        assert _task_text(payload) == _task_text(dict(reversed(list(payload.items()))))
