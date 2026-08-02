"""The declarative CLI adapter — an agent described by a SPEC, not by a module.

These tests are the evidence for the claim that adding an agent needs no source
change. They drive the adapter with the spec that ships in ``config.yaml`` over
the verbatim stdout of a real ``openhands --headless --json`` process, and assert
the same honesty properties a hand-written adapter had to guarantee:

* a tool call and the result that answered it are ONE span, joined on id;
* an unanswered call is kept and marked;
* an event the spec does not describe is kept, and never scored;
* terminal chrome is not reported as lost evidence, but a truncated event is;
* a run that never reached the agent is a NON-RESULT, never a wrong answer.

This file replaced ``test_openhands_headless.py``. That module hard-coded one
agent's event names in Python; every property it pinned is pinned here against
configuration, which is the whole point.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agenttic.adapters.cli_spec import (HARNESS_FAILURE, CLISpecAgent, _dig,
                                        _matches, task_text)
from agenttic.config import load_config

FIX = Path(__file__).parent / "fixtures"
#: The complete stdout of ONE real `openhands --headless --json` run (CLI
#: 1.16.0, 2026-08-02), captured verbatim — terminal chrome and all.
REAL_RUN = FIX / "openhands_cli_run.stdout"
#: Events serialised from the pinned SDK's own event objects.
SDK_EVENTS = FIX / "openhands_headless.jsonl"
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def spec() -> dict:
    """The OpenHands spec exactly as it ships in config.yaml.

    Deliberately the real file: a spec that works in a test and not in the
    product is worth nothing, and this is the artifact a user would copy.
    """
    return load_config(str(REPO_ROOT / "config.yaml"))["agents"]["openhands"]


@pytest.fixture
def agent(spec) -> CLISpecAgent:
    return CLISpecAgent("openhands", spec, version="1.16.0")


def _map(agent: CLISpecAgent, raw: str):
    events, lost = agent._parse(raw)
    spans, final = agent._to_spans(events, "tid00001", NOW, raw)
    return spans, final, lost


class TestTheShippedSpecDrivesTheRealAgent:
    def test_the_config_spec_maps_a_real_run(self, agent):
        """No Python was written for this agent. The spec did all of it."""
        spans, final, lost = _map(agent, REAL_RUN.read_text(encoding="utf-8"))
        assert lost == 0
        assert [s.kind for s in spans] == [
            "user_turn", "tool_call", "tool_call", "final_output"]
        assert "hello.txt" in final

    def test_it_also_maps_the_sdk_captured_events(self, agent):
        """Same spec, a different capture of the same agent's event schema."""
        spans, final, lost = _map(agent, SDK_EVENTS.read_text(encoding="utf-8"))
        assert lost == 0
        assert sum(1 for s in spans if s.kind == "tool_call") == 3
        assert "Fixed the operator" in (final or "")

    def test_the_answer_comes_from_the_finish_TOOL_not_a_message(self, agent):
        """The agent never sends a final message; it calls `finish`.

        A spec that could only read messages would record a COMPLETED task as a
        non-result — every case a harness failure, and the report would blame
        the subject for our defect. `is_answer` on a tool rule is what fixes it,
        and it is configuration rather than a special case in code.
        """
        spans, final, _ = _map(agent, REAL_RUN.read_text(encoding="utf-8"))
        assert final and "hello.txt" in final
        assert any(s.kind == "tool_call" and s.name == "finish" for s in spans), \
            "the finish call must still appear as the tool call it is"

    def test_a_nested_discriminator_selects_the_finish_rule(self):
        """`when: {action.kind: FinishAction}` — a dotted key, so a rule can
        select on a nested field instead of needing code."""
        ev = {"kind": "ActionEvent", "action": {"kind": "FinishAction"}}
        assert _matches(ev, {"kind": "ActionEvent", "action.kind": "FinishAction"})
        assert not _matches(ev, {"action.kind": "SomethingElse"})


class TestPairing:
    def test_results_that_return_out_of_order_find_their_own_call(self, agent):
        """Two calls in flight, answered in reverse.

        The captured run answers each call before making the next, so it cannot
        tell id-matching from "pair with the oldest pending" — a FIFO
        implementation passes every other test here. The moment an agent issues
        two calls before either returns, FIFO attributes each result to the
        wrong call and the trace reads as an agent that ran a command it never
        ran.
        """
        ts = NOW.isoformat()
        stream = "\n".join(json.dumps(e) for e in [
            {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
             "tool_call_id": "A", "timestamp": ts,
             "tool_call": {"arguments": '{"command": "ls"}'}},
            {"kind": "ActionEvent", "source": "agent", "tool_name": "bash",
             "tool_call_id": "B", "timestamp": ts,
             "tool_call": {"arguments": '{"command": "pwd"}'}},
            {"kind": "ObservationEvent", "tool_call_id": "B", "timestamp": ts,
             "observation": {"output": "/repo"}},
            {"kind": "ObservationEvent", "tool_call_id": "A", "timestamp": ts,
             "observation": {"output": "calc.py"}},
        ])
        spans, _, _ = _map(agent, stream)
        by_id = {s.attributes["tool_call_id"]: s for s in spans
                 if s.kind == "tool_call"}
        assert "calc.py" in str(by_id["A"].output), "A got B's result"
        assert "/repo" in str(by_id["B"].output), "B got A's result"

    def test_an_unanswered_call_is_kept_and_marked(self, agent):
        ev = {"kind": "ActionEvent", "tool_name": "bash", "tool_call_id": "lost",
              "timestamp": NOW.isoformat()}
        spans, _, _ = _map(agent, json.dumps(ev))
        call = next(s for s in spans if s.kind == "tool_call")
        assert call.attributes["result"] == "no result was returned"


class TestNothingIsSilentlyDropped:
    def test_terminal_chrome_is_not_reported_as_lost_evidence(self, agent):
        """`--json` does not silence the UI: 27 of 33 lines were decoration.

        Counting those as unreadable events would put "27 events lost" on every
        trace. A false alarm that fires every time is worse than no alarm — it
        is exactly where a real dropped event would hide.
        """
        raw = REAL_RUN.read_text(encoding="utf-8")
        assert len(raw.splitlines()) == 33, "fixture changed; re-derive the count"
        events, lost = agent._parse(raw)
        assert (len(events), lost) == (5, 0)

    def test_a_truncated_event_line_IS_counted_as_lost(self, agent):
        events, lost = agent._parse('{"kind": "MessageEvent"\nGoodbye! 👋\n')
        assert (events, lost) == ([], 1)

    def test_an_event_the_spec_does_not_describe_is_kept_but_not_scored(self, agent):
        """A kind no rule matches means the subject moved.

        Kept with its payload so the skew is visible, as `env_step` so it can
        never credit a coverage bin: `extractors.py` and `builtins.py` text-match
        `agent_decision` spans, and a payload we admit we cannot read must not
        make claims about agent behaviour.
        """
        from agenttic.coverage.extractors import _escalation_act

        ev = {"kind": "SomeFutureEvent",
              "note": "escalate to a human supervisor immediately"}
        spans, _, _ = _map(agent, json.dumps(ev))
        assert len(spans) == 1
        assert spans[0].kind == "env_step"
        assert spans[0].attributes["unmapped_event"] is True
        assert not _escalation_act(spans[0]), \
            "an unreadable payload credited a coverage bin by text match"

    def test_the_condensation_event_a_real_run_emitted_is_kept(self, agent):
        """Found in a live run: OpenHands compacts its own context and emits
        `Condensation`, which no adapter of ours models. It must survive."""
        spans, _, _ = _map(agent, json.dumps({"kind": "Condensation"}))
        assert spans[0].kind == "env_step" and spans[0].name == "Condensation"


class TestAFailureThatIsNotTheAgents:
    def test_a_missing_binary_never_raises_and_is_a_non_result(self, spec):
        a = CLISpecAgent("x", {**spec, "command": ["definitely-not-installed-xyz"]})
        trace = a.run({"task": "anything"}, test_case_id="c1")
        assert trace.final_output.startswith(HARNESS_FAILURE)
        assert trace.spans[0].kind == "error"
        said = " ".join((trace.spans[0].attributes or {}).get("disclosures") or [])
        assert "not an agent result" in said

    def test_a_timeout_keeps_the_partial_evidence(self, agent, monkeypatch):
        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd="x", timeout=1, output=REAL_RUN.read_text(encoding="utf-8"))
        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        trace = agent.run({"task": "x"})
        assert [s for s in trace.spans if s.kind == "tool_call"], \
            "the partial events were discarded"
        assert any("NOT necessarily finished" in d
                   for d in _disclosures(trace))

    def test_a_nonzero_exit_is_disclosed(self, agent, monkeypatch):
        class _P:
            stdout = REAL_RUN.read_text(encoding="utf-8")
            stderr, returncode = "boom", 3
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        trace = agent.run({"task": "x"})
        assert any(s.name == "process_exit" for s in trace.spans)
        assert any("exited non-zero" in d for d in _disclosures(trace))

    def test_disclosures_are_readable_off_the_returned_trace(self, agent, monkeypatch):
        """`Trace` has no `attributes` field and pydantic drops unknown kwargs,
        so `Trace(..., attributes={...})` constructs cleanly and discards every
        word of it. A previous adapter shipped exactly that."""
        class _P:
            stdout = "{oops\n" + REAL_RUN.read_text(encoding="utf-8")
            stderr, returncode = "e", 9
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        said = _disclosures(agent.run({"task": "x"}))
        assert any("lost" in d or "could not be parsed" in d for d in said)
        assert any("exited non-zero" in d for d in said)


class TestTheHarnessContract:
    def test_the_spec_is_part_of_the_identity(self, spec):
        """Two mappings over one binary are two measurements. If the hash
        ignored the spec, a resumed run could serve traces recorded under a
        different mapping."""
        a = CLISpecAgent("x", spec)
        b = CLISpecAgent("x", {**spec, "map": {}})
        assert a.config_hash() != b.config_hash()

    def test_the_api_key_never_reaches_the_trace(self, spec):
        a = CLISpecAgent("x", spec, api_key="sk-ant-REAL-SECRET")
        assert "sk-ant-REAL-SECRET" not in json.dumps(a.describe())

    def test_an_unpinned_model_is_disclosed_not_assumed(self, agent, monkeypatch):
        class _P:
            stdout = REAL_RUN.read_text(encoding="utf-8")
            stderr, returncode = "", 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        assert any("cannot say which model produced it" in d
                   for d in _disclosures(agent.run({"task": "x"})))

    def test_pinning_a_model_adds_the_env_AND_the_flag(self, spec, monkeypatch):
        """The CLI has no `--model` flag; it reads LLM_MODEL, and only when
        `--override-with-envs` is passed. Setting the env without the flag makes
        the agent quietly run a different model than the evidence claims."""
        seen = {}

        def _capture(argv, **kw):
            seen["argv"], seen["env"] = argv, kw.get("env") or {}
            class _P:
                stdout, stderr, returncode = "", "", 0
            return _P()
        monkeypatch.setattr(subprocess, "run", _capture)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        CLISpecAgent("x", spec, model="anthropic/claude-opus-5").run({"task": "t"})
        assert "--override-with-envs" in seen["argv"]
        assert seen["env"]["LLM_MODEL"] == "anthropic/claude-opus-5"
        assert "--model" not in seen["argv"]

    def test_the_task_lands_in_the_command_template(self, spec, monkeypatch):
        seen = {}

        def _capture(argv, **kw):
            seen["argv"] = argv
            class _P:
                stdout, stderr, returncode = "", "", 0
            return _P()
        monkeypatch.setattr(subprocess, "run", _capture)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        CLISpecAgent("x", spec).run({"task": "fix the bug"})
        assert "fix the bug" in seen["argv"]

    def test_run_writes_no_state_to_self(self, agent, monkeypatch):
        """The harness holds ONE adapter and enters it from many threads."""
        class _P:
            stdout = REAL_RUN.read_text(encoding="utf-8")
            stderr, returncode = "", 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/openhands")
        before = dict(vars(agent))
        agent.run({"task": "one"}, test_case_id="a")
        agent.run({"task": "two"}, test_case_id="b")
        assert dict(vars(agent)) == before

    def test_a_spec_with_no_command_is_refused_loudly(self):
        with pytest.raises(ValueError, match="no `command`"):
            CLISpecAgent("x", {"driver": "cli"})

    @pytest.mark.parametrize("payload,expected", [
        ({"task": "do the thing"}, "do the thing"),
        ({"problem_statement": "fix it"}, "fix it"),
        ({"a": 1, "b": 2}, '{"a": 1, "b": 2}'),
    ])
    def test_the_task_text_is_deterministic(self, payload, expected):
        assert task_text(payload) == expected
        assert task_text(payload) == task_text(dict(reversed(list(payload.items()))))


class TestTheSpecLanguage:
    @pytest.mark.parametrize("path,want", [
        ("a.b", 1),
        ("list[].text", "xy"),
        ("missing.deep", None),
    ])
    def test_dotted_paths(self, path, want):
        obj = {"a": {"b": 1}, "list": [{"text": "x"}, {"text": "y"}]}
        assert _dig(obj, path) == want


def _disclosures(trace) -> list[str]:
    out: list[str] = []
    for s in trace.spans:
        out += (s.attributes or {}).get("disclosures") or []
    return out
