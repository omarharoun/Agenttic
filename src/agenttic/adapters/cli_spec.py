"""Declarative CLI adapter — drive a local agent binary from a SPEC, not code.

The third tier of the "no code per agent" ladder, and the one that catches the
agents speaking nothing standard:

1. the agent has an HTTP endpoint  -> ``BlackBoxHTTPAgent`` + ``connect.Mapping``
2. the agent speaks ACP            -> ``adapters/acp_agent.py``
3. the agent is a local binary     -> **this**, described by a spec
4. the agent is none of the above  -> only then write Python

A spec is data. It lives in ``config.yaml`` (or a YAML file) and adding an agent
is a config change reviewable by someone who does not read Python:

.. code-block:: yaml

    agents:
      openhands:
        command: ["openhands", "--headless", "--json", "-t", "{task}"]
        env: {LLM_MODEL: "{model}"}
        extra_args: ["--override-with-envs"]     # only applied when model is set
        output: jsonl                            # jsonl | text
        event_kind_field: kind
        map:
          user_message:  {when: {kind: MessageEvent, source: user}, span: user_turn}
          agent_message: {when: {kind: MessageEvent}, span: llm_call, text: llm_message.content[].text}
          tool_call:     {when: {kind: ActionEvent}, span: tool_call, id: tool_call_id}
          tool_result:   {when: {kind: ObservationEvent}, pairs_with: tool_call, id: tool_call_id}
        final_output: {from: agent_message, take: last}

WHAT THIS DELIBERATELY DOES NOT PROMISE
---------------------------------------
A spec cannot know what a private event format MEANS. It can pair a call with
its result and find the answer; it cannot tell you a tool mutates state, because
nothing in the agent's output says so. That is precisely what ACP declares and
this tier cannot, so an agent driven here gets `action_risk` classified as
`unknown` — never as read-only (``verification/traffic.classify_confidence``).
Reaching for this tier when the agent speaks ACP trades real evidence for
convenience, and the config surfaces which tier produced a trace so the report
can say so.

Everything here is data-driven and re-entrant: nothing per-run is written to
``self``, because the harness enters one adapter from many threads.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import Span, Trace

HARNESS_FAILURE = "HARNESS_FAILURE"

#: Terminal chrome, not lost evidence. A real CLI interleaves JSONL with banners
#: and boxed summaries — on one measured run, 27 of 33 lines were decoration —
#: so "this line is not JSON" cannot mean "we dropped an event". A line that
#: never opened as an object is chrome; one that opened and failed is counted.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_TASK_KEYS = ("task", "prompt", "input", "instruction", "problem_statement")


def task_text(test_input: dict) -> str:
    """The one message the agent is given.

    ``TestCase.input`` is a free dict: take an explicit task field if present,
    otherwise serialise the whole dict with sorted keys. Deterministic on
    purpose — a task string that varied run to run would change the subject's
    behaviour for a reason that has nothing to do with the subject.
    """
    for key in _TASK_KEYS:
        v = test_input.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(test_input, sort_keys=True, ensure_ascii=False)


def _dig(obj: Any, path: str) -> Any:
    """Read a dotted path, with ``[]`` meaning "join every element".

    ``llm_message.content[].text`` -> concatenate ``text`` across the list.
    """
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if part.endswith("[]"):
            key = part[:-2]
            seq = cur.get(key) if isinstance(cur, dict) else None
            if not isinstance(seq, list):
                return None
            cur = seq
            continue
        if isinstance(cur, list):
            cur = [x.get(part) if isinstance(x, dict) else None for x in cur]
            continue
        cur = cur.get(part) if isinstance(cur, dict) else None
    if isinstance(cur, list):
        return "".join(str(x) for x in cur if isinstance(x, (str, int, float)))
    return cur


def _matches(event: dict, when: dict) -> bool:
    """Every condition must hold. Keys may be dotted, so a rule can select on a
    NESTED discriminator — ``action.kind: FinishAction`` — which is what an agent
    that ends its task by calling a "finish" tool requires."""
    for key, want in (when or {}).items():
        got = _dig(event, key) if "." in key else event.get(key)
        if got != want:
            return False
    return True


class CLISpecAgent(AgentAdapter):
    """A local agent binary, described by a spec instead of by a module."""

    visibility = "glass_box"

    def __init__(self, agent_id: str, spec: dict, *, model: str = "",
                 api_key: str = "", cwd: str | None = None, version: str = "",
                 timeout_s: float = 900.0, conversation_id: str = "",
                 workspace_root: str = "", workspace_template: str = "") -> None:
        if not spec.get("command"):
            raise ValueError(
                f"agent spec {agent_id!r} has no `command`: a CLI adapter needs "
                "the argv to run, with {task} where the task text goes")
        self.agent_id = agent_id
        self.spec = spec
        self.model = model
        self.api_key = api_key
        self.cwd = cwd
        self.version = version
        self.timeout_s = float(timeout_s)
        self.conversation_id = conversation_id
        #: A FRESH working directory per run when set. Trials of one case are
        #: only independent if they do not share state: our own pass^2 run gave
        #: both trials one astropy checkout, so trial 2 started on trial 1's
        #: edits — and pass^k is arithmetic over trials assumed independent.
        #: Anthropic's guidance names the failure directly: unnecessary shared
        #: state between runs causes correlated failures.
        self.workspace_root = workspace_root
        #: Copied into each fresh workspace — a pristine repo checkout, say — so
        #: isolation does not mean re-cloning inside the timed run.
        self.workspace_template = workspace_template
        #: Live child processes by case id, so `abort_run` can reach one the
        #: harness has stopped waiting for. Lock-guarded shared state — the one
        #: kind the adapter concurrency contract permits.
        self._live: dict[str, subprocess.Popen] = {}
        self._live_lock = threading.Lock()
        if str(spec.get("visibility", "")).strip() == "black_box":
            # A spec with no event mapping sees only final text; saying so is
            # what keeps the trace honest about what it can support.
            self.visibility = "black_box"

    # -- cancellation ------------------------------------------------------

    def abort_run(self, test_case_id: str | None = None) -> None:
        """Kill the child started for this case. Called by the harness on its
        own timeout, which otherwise abandons this adapter's thread and leaves
        the agent running for (adapter timeout - harness timeout)."""
        with self._live_lock:
            if test_case_id is not None:
                procs = [self._live.pop(str(test_case_id))] \
                    if str(test_case_id) in self._live else []
            else:
                procs = list(self._live.values())
                self._live.clear()
        for p in procs:
            try:
                p.kill()
            except Exception:      # noqa: BLE001 — teardown never breaks a run
                pass

    def _track(self, case: str | None, proc: subprocess.Popen) -> None:
        if case is None:
            return
        with self._live_lock:
            self._live[str(case)] = proc

    def _untrack(self, case: str | None) -> None:
        if case is None:
            return
        with self._live_lock:
            self._live.pop(str(case), None)

    # -- identity ----------------------------------------------------------

    def describe(self) -> dict:
        """Deterministic, JSON-safe, secret-free — hashed into every trace.

        The SPEC is part of the description: two different mappings over the same
        binary are two different measurements, and a resumed run must not serve
        traces recorded under a different spec.
        """
        return {
            "adapter": "cli_spec",
            "command": list(self.spec.get("command") or []),
            "spec": json.loads(json.dumps(self.spec, sort_keys=True, default=str)),
            "model": self.model or "(unpinned — the CLI's stored configuration)",
            "version": self.version,
            "timeout_s": self.timeout_s,
        }

    # -- the run -----------------------------------------------------------

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        started = _now()
        trace_id = uuid.uuid4().hex
        task = task_text(test_input)
        disclosures: list[str] = []

        cwd, workspace_note = self._workspace()
        argv = [self._render(a, task) for a in self.spec["command"]]
        binary = argv[0]
        if shutil.which(binary) is None and "/" not in binary:
            return self._failed(trace_id, test_case_id, started,
                                f"{HARNESS_FAILURE}: `{binary}` is not on PATH",
                                ["the subject was never invoked — this is not "
                                 "an agent result"])

        env = dict(os.environ)
        spec_env = {k: self._render(v, task) for k, v in
                    (self.spec.get("env") or {}).items()}
        if self.model:
            env.update({k: v for k, v in spec_env.items() if v})
            argv += [self._render(a, task) for a in self.spec.get("extra_args") or []]
        elif spec_env:
            disclosures.append(
                "no model was pinned by this harness, so the agent used its own "
                "stored configuration — this evidence cannot say which model "
                "produced it")
        if self.api_key and self.spec.get("api_key_env"):
            env[str(self.spec["api_key_env"])] = self.api_key

        code: int | None = 0
        proc = None
        try:
            # Popen rather than subprocess.run: run() gives no handle, so a
            # harness timeout (which abandons this thread rather than cancelling
            # it) would leave the agent running with nothing able to reach it.
            # See `abort_run`.
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    cwd=cwd, env=env,
                                    stdin=subprocess.DEVNULL)
            self._track(test_case_id, proc)
            stdout, stderr = proc.communicate(timeout=self.timeout_s)
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            proc.kill()                        # our own deadline: kill it now
            try:
                rest_out, rest_err = proc.communicate(timeout=10)
            except Exception:                  # noqa: BLE001
                rest_out, rest_err = "", ""
            stdout = _s(exc.stdout) or _s(rest_out)
            stderr = _s(exc.stderr) or _s(rest_err)
            code = None
            disclosures.append(
                f"the run hit this harness's {self.timeout_s:g}s ceiling and was "
                "killed; what follows is what it produced before that, and the "
                "task was NOT necessarily finished")
        except OSError as exc:      # noqa: BLE001 — data, not a crash
            return self._failed(trace_id, test_case_id, started,
                                f"{HARNESS_FAILURE}: could not start {binary!r}: {exc}",
                                ["the subject was never invoked — this is not "
                                 "an agent result"])
        finally:
            self._untrack(test_case_id)

        events, lost = self._parse(stdout)
        if lost:
            disclosures.append(
                f"{lost} line(s) opened as JSON objects and could not be parsed, "
                "so those events are lost; they are counted, not guessed at")
        spans, final = self._to_spans(events, trace_id, started, stdout)

        if code not in (0, None):
            spans.append(self._span(trace_id, "error", "process_exit", _now(),
                                    error=f"exit code {code}",
                                    output={"stderr": stderr[-2000:]}))
            disclosures.append(f"the CLI exited non-zero ({code})")
        if not final:
            final = f"{HARNESS_FAILURE}: the run produced no answer"
            disclosures.append(
                "no answer was emitted, so there is nothing to score — this is a "
                "non-result, not a wrong answer")

        record = self._span(trace_id, "env_step", "harness_record", started,
                            attributes={"disclosures": disclosures,
                                        "recorded_by": "cli_spec",
                                        "n_events": len(events),
                                        "lost_events": lost, "exit_code": code,
                                        "workspace": cwd or "",
                                        "workspace_isolated": bool(self.workspace_root),
                                        "subject_version": self.version})
        if workspace_note:
            disclosures.append(workspace_note)
        return Trace(trace_id=trace_id, agent_id=self.agent_id,
                     agent_config_hash=self.config_hash(),
                     test_case_id=test_case_id, visibility=self.visibility,
                     spans=[record, *spans], final_output=final,
                     total_steps=sum(1 for s in spans if s.kind == "tool_call"))

    # -- pieces ------------------------------------------------------------

    def _workspace(self) -> tuple[str | None, str]:
        """A fresh directory for this run, or the shared cwd with a disclosure.

        Isolation is opt-in via ``workspace_root`` because most agents do not
        touch a filesystem. When it is OFF and a cwd IS set, the trace SAYS so —
        a pass^k figure computed over runs that shared a directory is not the
        figure it appears to be, and the reader has to be able to see that.
        """
        if not self.workspace_root:
            note = ("" if not self.cwd else
                    "trials of this case shared one working directory, so they "
                    "were not independent; treat any pass^k figure over them "
                    "with that caveat")
            return self.cwd, note
        import tempfile
        root = pathlib.Path(self.workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        ws = pathlib.Path(tempfile.mkdtemp(prefix="run-", dir=str(root)))
        if self.workspace_template:
            shutil.copytree(self.workspace_template, ws / "work", symlinks=True)
            return str(ws / "work"), ""
        return str(ws), ""

    def _render(self, value: Any, task: str) -> str:
        if not isinstance(value, str):
            return str(value)
        return (value.replace("{task}", task)
                     .replace("{model}", self.model)
                     .replace("{conversation_id}", self.conversation_id))

    def _parse(self, stdout: str) -> tuple[list[dict], int]:
        if str(self.spec.get("output", "jsonl")) != "jsonl":
            return [], 0
        events, lost = [], 0
        for raw in (stdout or "").splitlines():
            line = _ANSI.sub("", raw).strip()
            if not line or not line.startswith("{"):
                continue            # terminal chrome, not a dropped event
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                lost += 1
                continue
            if isinstance(obj, dict):
                events.append(obj)
            else:
                lost += 1
        return events, lost

    def _to_spans(self, events: list[dict], trace_id: str, started: datetime,
                  stdout: str) -> tuple[list[Span], str]:
        rules = self.spec.get("map") or {}
        if not rules:
            # No mapping: text only. Honest black-box behaviour rather than a
            # fabricated trajectory.
            return [], _dig({"stdout": stdout}, "stdout") or ""

        spans: list[Span] = []
        pending: dict[str, int] = {}
        answers: list[str] = []
        ts = started

        for ev in events:
            rule_name, rule = self._rule_for(ev, rules)
            if rule is None:
                spans.append(self._span(
                    trace_id, "env_step", str(ev.get(
                        self.spec.get("event_kind_field") or "kind", "event")),
                    ts, output={"event": ev},
                    attributes={"unmapped_event": True}))
                continue
            if rule.get("pairs_with"):
                self._pair(ev, rule, spans, pending, ts)
                continue
            span_kind = rule.get("span") or "agent_decision"
            text = _dig(ev, rule.get("text") or "")
            # The answer may arrive as a TOOL CALL rather than a message: an
            # agentic CLI typically ends by calling `finish`, and reading only
            # messages finds no answer at all — a completed task recorded as a
            # non-result. So `is_answer` is honoured whatever span it lands on.
            if rule.get("is_answer") and isinstance(text, str) and text.strip():
                answers.append(text)
            if span_kind == "tool_call":
                cid = str(_dig(ev, rule.get("id") or "") or "")
                pending[cid] = len(spans)
                spans.append(self._span(
                    trace_id, "tool_call", str(ev.get("tool_name") or rule_name),
                    ts, input={"event": ev},
                    attributes={"tool_call_id": cid,
                                "result": "no result was returned"}))
                continue
            if span_kind == "llm_call" and not rule.get("is_answer"):
                if isinstance(text, str) and text.strip():
                    answers.append(text)
            spans.append(self._span(trace_id, span_kind, rule_name, ts,
                                    output={"text": text} if text else {"event": ev}))

        final_cfg = self.spec.get("final_output") or {}
        final = ""
        if answers:
            final = answers[-1] if final_cfg.get("take", "last") == "last" \
                else answers[0]
        if final:
            end = spans[-1].end_time if spans else started
            spans.append(self._span(trace_id, "final_output", "final_output",
                                    end, output={"text": final}))
        return spans, final

    def _rule_for(self, ev: dict, rules: dict) -> tuple[str, dict | None]:
        for name, rule in rules.items():
            if _matches(ev, rule.get("when") or {}):
                return name, rule
        return "", None

    def _pair(self, ev: dict, rule: dict, spans: list[Span],
              pending: dict[str, int], ts: datetime) -> None:
        cid = str(_dig(ev, rule.get("id") or "") or "")
        idx = pending.pop(cid, None)
        if idx is None:
            spans.append(self._span(
                spans[0].span_id.split("-")[0] if spans else "", "tool_call",
                "tool", ts, output={"event": ev},
                attributes={"tool_call_id": cid, "unpaired": True}))
            return
        got = spans[idx]
        spans[idx] = got.model_copy(update={
            "end_time": max(ts, got.start_time), "output": {"event": ev},
            "attributes": {**(got.attributes or {}), "result": "observed"}})

    def _span(self, trace_id: str, kind: str, name: str, ts: datetime,
              **kw) -> Span:
        attrs = kw.pop("attributes", None) or {}
        if self.conversation_id:
            attrs["gen_ai.conversation.id"] = self.conversation_id
        return Span(span_id=f"{trace_id[:8]}-{uuid.uuid4().hex[:8]}", kind=kind,
                    name=name, start_time=ts, end_time=ts, attributes=attrs, **kw)

    def _failed(self, trace_id: str, case: str | None, started: datetime,
                marker: str, disclosures: list[str]) -> Trace:
        return Trace(
            trace_id=trace_id, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=case,
            visibility=self.visibility, final_output=marker,
            spans=[self._span(trace_id, "error", "harness_failure", started,
                              error=marker,
                              attributes={"disclosures": disclosures,
                                          "recorded_by": "cli_spec"})])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _s(v) -> str:
    if v is None:
        return ""
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
