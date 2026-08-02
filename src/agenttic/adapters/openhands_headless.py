"""Drive the OpenHands CLI in headless mode and record what it did.

The subject of the first public evaluation (see
``docs/evaluations/2026-08-01-openhands/00-PREREGISTRATION.md``). We do not patch
it: ``openhands --headless --json -t "<task>"`` is its own published interface,
documented in its README, and the flags are declared in its argparser
(``openhands_cli/argparsers/main_parser.py`` — ``--headless`` "Run in headless
mode (no UI output, auto-approve actions)", ``--json`` "Streams JSONL event
outputs to terminal").

WHAT IS ON THE WIRE, and how we know
------------------------------------
Every EVENT line is ``json.dumps(event.model_dump())`` over an ``openhands.sdk``
event — ``openhands_cli/utils.py::json_callback`` at CLI commit ``2df8a28``:

    def json_callback(event: Event) -> None:
        if isinstance(event, SystemPromptEvent):
            return
        print(json.dumps(event.model_dump(), ensure_ascii=False))

Two consequences we depend on, both verified against the pinned SDK
(``openhands-sdk==1.28.1``, the version ``pyproject.toml`` requires) rather than
assumed:

* **There IS a discriminator, and it is not a declared field.** ``kind`` does not
  appear in ``Event.model_fields`` — checking there says it is absent — but it
  IS in the serialized output (``"kind": "MessageEvent"``). Routing on structure
  ("has an ``llm_message`` key, so it must be a message") would have been the
  obvious fallback and is strictly worse: it silently misroutes any event whose
  shape happens to overlap. We route on ``kind``.
* **The system prompt never arrives.** ``SystemPromptEvent`` is dropped at the
  source, so a trace from this adapter cannot show what the agent was told. That
  is the subject's choice, not ours, and it is disclosed rather than filled in.

WHAT READING THE SOURCE DID NOT SHOW
------------------------------------
Everything above was derived from the CLI's own code and its pinned SDK, and it
is correct. It was also incomplete in two ways that would each have invalidated
the evaluation, and only running the binary once (2026-08-02, CLI 1.16.0)
surfaced them. Both are pinned in ``tests/adapters/`` against the verbatim
stdout of that run:

* **``--json`` does not silence the human interface.** Event lines are
  interleaved with Rich terminal chrome — banner, boxed conversation summary,
  "Goodbye! 👋". On the smoke run, 27 of 33 lines were decoration. So "the line
  is not JSON" cannot mean "we lost an event"; see ``_parse``.
* **The agent does not answer with a message.** It calls the ``finish`` tool and
  the answer is ``action.message``. Reading only ``MessageEvent`` finds no
  answer at all, so a completed task is recorded as a non-result — every run a
  harness failure, and the report would have blamed the subject.

The general lesson, and the reason the smoke run is part of building an adapter:
source tells you the schema of what is emitted; only a process tells you what
actually comes out of it.

WHY THIS IS GLASS BOX
---------------------
The events carry the tool calls and their results, so the trace records what the
agent DID and not only what it finally said. That is not a convenience:
``scoring/engine.py`` drops only three checks for a black-box target while seven
registered checks read tool spans, four of them with no text fallback — on a
black-box trace a correct answer can score 0.0 and a fabricated one 1.0. A
glass-box trace does not take that path. The defect is open; this design avoids
it rather than fixing it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import Span, Trace

#: Marker written into ``final_output`` when the subject produced no answer.
#: Deliberately not an empty string: an empty answer and a run that never
#: happened are different findings, and the scorers must be able to tell them
#: apart (``scoring/engine.py`` treats these prefixes as execution failures, so
#: a non-result is never scored as a wrong answer).
HARNESS_FAILURE = "HARNESS_FAILURE"

#: Event kinds we map deliberately. Anything else is recorded as an `env_step`
#: span carrying the raw payload — never dropped, never scored. An unrecognised
#: event is a fact about a version skew between this adapter and the subject:
#: dropping it would make that skew invisible exactly when it matters, and
#: scoring it would credit coverage from a payload we admit we cannot read.
_MESSAGE, _ACTION, _OBSERVATION = "MessageEvent", "ActionEvent", "ObservationEvent"
_AGENT_ERROR = "AgentErrorEvent"

#: The agent ends a task by CALLING a tool, not by sending a message: an
#: ActionEvent whose `action.kind` is this, carrying the answer in
#: `action.message`. On the smoke run the agent emitted no agent message at all,
#: so an adapter that looks only at MessageEvent finds no answer and reports a
#: non-result for a task the agent completed — every run scored as a harness
#: failure. Nothing in the fixture showed this; only running the binary did.
_FINISH_ACTION = "FinishAction"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class OpenHandsHeadlessAgent(AgentAdapter):
    """The OpenHands CLI, driven through its published headless interface.

    Re-entrant by construction: ``run`` writes nothing to ``self``. The harness
    holds ONE adapter across the whole suite and calls ``run`` from up to
    ``max_parallel`` threads, so per-run state on the instance is a live race
    (``harness/runner.py``). Everything per-run lives in locals.
    """

    visibility = "glass_box"

    def __init__(self, agent_id: str = "openhands-cli", *, binary: str = "openhands",
                 model: str = "", api_key: str = "", base_url: str = "",
                 timeout_s: int = 900, cwd: str | None = None,
                 version: str = "", extra_args: tuple[str, ...] = ()) -> None:
        self.agent_id = agent_id
        self.binary = binary
        #: The CLI has NO `--model` flag. It takes the model from `LLM_MODEL`,
        #: and only when `--override-with-envs` is passed — its own help says
        #: "By default, environment variables are ignored." So an unpinned model
        #: means the CLI silently used whatever `openhands login` last stored,
        #: and the evidence could not say which model produced it. Pinning turns
        #: both flags on together; leaving it unset is DISCLOSED, not assumed.
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_s = int(timeout_s)
        self.cwd = cwd
        #: Pinned for the record. The evaluation names a commit, and a trace that
        #: cannot say which build produced it is not reproducible evidence.
        self.version = version
        self.extra_args = tuple(extra_args)

    # -- identity -----------------------------------------------------------

    def describe(self) -> dict:
        """Deterministic, JSON-serialisable description — feeds the config hash.

        ``cwd`` is deliberately EXCLUDED. It is where the run happened, not what
        the agent is; including it would give the same agent a different config
        hash per working directory and silently defeat resume.

        ``api_key`` is EXCLUDED and must stay excluded: this dict is hashed into
        every trace and serialised with the run. ``base_url`` is included —
        a different endpoint is a different agent under test.
        """
        return {
            "adapter": "openhands_headless",
            "binary": self.binary,
            "model": self.model or "(unpinned — the CLI's stored configuration)",
            "base_url": self.base_url,
            "version": self.version,
            "timeout_s": self.timeout_s,
            "extra_args": list(self.extra_args),
            "interface": "openhands --headless --json -t <task>",
        }

    # -- the run ------------------------------------------------------------

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        """Execute one case. Agent mistakes are DATA; nothing here raises."""
        started = _now()
        task = _task_text(test_input)
        trace_id = uuid.uuid4().hex
        spans: list[Span] = []
        disclosures: list[str] = []

        if shutil.which(self.binary) is None and "/" not in self.binary:
            return self._failed(
                trace_id, test_case_id, started,
                f"{HARNESS_FAILURE}: `{self.binary}` is not on PATH",
                ["the subject was never invoked — this is not an agent result"])

        cmd = [self.binary, "--headless", "--json", "-t", task, *self.extra_args]
        env = dict(os.environ)
        if self.model:
            # Both halves or neither: the env var alone does nothing without the
            # flag, which is the silent-wrong-model trap this guards against.
            cmd.append("--override-with-envs")
            env["LLM_MODEL"] = self.model
            if self.api_key:
                env["LLM_API_KEY"] = self.api_key
            if self.base_url:
                env["LLM_BASE_URL"] = self.base_url
        else:
            disclosures.append(
                "no model was pinned by this harness, so the CLI used its own "
                "stored configuration — this evidence cannot say which model "
                "produced it")

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s,
                cwd=self.cwd, env=env, stdin=subprocess.DEVNULL, check=False)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            # Partial output is still evidence — keep what the agent managed
            # before the wall clock ran out rather than discarding the run.
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr)
            code = None
            disclosures.append(
                f"the run hit this harness's {self.timeout_s}s ceiling and was "
                "killed; the events below are what it produced before that, and "
                "the task was NOT necessarily finished")
        except OSError as exc:                       # noqa: BLE001 — data, not a crash
            return self._failed(
                trace_id, test_case_id, started,
                f"{HARNESS_FAILURE}: could not start `{self.binary}`: {exc}",
                ["the subject was never invoked — this is not an agent result"])

        events, bad_lines = _parse(stdout)
        if bad_lines:
            disclosures.append(
                f"{bad_lines} line(s) of output were not valid JSON and could not "
                "be read as events; they are counted, not guessed at")
        spans, final = _to_spans(events, trace_id, fallback_start=started)

        if code not in (0, None):
            spans.append(_span(trace_id, "error", "process_exit", _now(), _now(),
                               error=f"exit code {code}",
                               output={"stderr": stderr[-2000:]}))
            disclosures.append(f"the CLI exited non-zero ({code})")
        if final is None:
            final = (f"{HARNESS_FAILURE}: the run produced no agent message"
                     if not events else
                     f"{HARNESS_FAILURE}: the run ended without a final message")
            disclosures.append(
                "no agent message was emitted, so there is no answer to score — "
                "this is a non-result, not a wrong answer")

        record = _span(
            trace_id, "env_step", "harness_record", started, started,
            attributes={"disclosures": disclosures, "n_events": len(events),
                        "bad_lines": bad_lines, "exit_code": code,
                        "subject_version": self.version,
                        "recorded_by": "openhands_headless"})
        return Trace(
            trace_id=trace_id, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            visibility="glass_box", spans=[record, *spans], final_output=final,
        )

    # -- failure that is not the agent's ------------------------------------

    def _failed(self, trace_id: str, case: str | None, started: datetime,
                marker: str, disclosures: list[str]) -> Trace:
        """A trace for a run that never reached the agent.

        It carries an `error` span and a marker `final_output` so the scoring
        layer records a NON-RESULT rather than a failed answer — the same
        distinction `collect()` refuses to blur for coverage.
        """
        return Trace(
            trace_id=trace_id, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=case,
            visibility="glass_box", final_output=marker,
            spans=[_span(trace_id, "error", "harness_failure", started, _now(),
                         error=marker,
                         attributes={"disclosures": disclosures, "n_events": 0,
                                     "recorded_by": "openhands_headless"})],
        )


# --------------------------------------------------------------------------- #
# wire -> spans
# --------------------------------------------------------------------------- #


def _task_text(test_input: dict) -> str:
    """The one message this agent is given.

    `TestCase.input` is a free dict. We take an explicit `task`/`prompt`/`input`
    if present and otherwise serialise the whole dict — deterministically, with
    sorted keys, because a task string that varies run to run would change the
    subject's behaviour for a reason that has nothing to do with the subject.
    """
    for key in ("task", "prompt", "input", "instruction", "problem_statement"):
        v = test_input.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(test_input, sort_keys=True, ensure_ascii=False)


def _parse(stdout: str) -> tuple[list[dict], int]:
    """JSONL -> events. Unreadable lines are COUNTED, never silently skipped.

    `--json` does NOT silence the human interface: a real run interleaves its
    events with Rich-formatted terminal chrome — a startup banner, a boxed
    conversation summary, "Goodbye! 👋" — and on the smoke run 27 of 33 lines
    were chrome. So "not JSON" alone cannot mean "lost evidence": reporting it
    that way would put a false disclosure on EVERY run, claiming we dropped
    events when we dropped decoration, and the true signal would be buried in
    the noise the first time it mattered.

    The split is on intent. A line that never claimed to be an event (no leading
    `{` once ANSI escapes are stripped) is chrome and is ignored. A line that
    opens like an object and then fails to parse is a genuinely lost event and
    is counted — that is the number the caller discloses.
    """
    events, bad = [], 0
    for raw in (stdout or "").splitlines():
        line = _ANSI.sub("", raw).strip()
        if not line:
            continue
        if not line.startswith("{"):
            continue                     # terminal chrome, not a dropped event
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
        else:
            bad += 1
    return events, bad


def _to_spans(events: list[dict], trace_id: str,
              *, fallback_start: datetime) -> tuple[list[Span], str | None]:
    """Map the event stream onto spans, pairing each action with its result.

    An ActionEvent and the ObservationEvent that answers it are ONE `tool_call`
    span, joined on `tool_call_id` — that pairing is what makes the span's
    `output` the result of its own `input`. An action that never got an
    observation still gets a span, with no output and a disclosure on it: a tool
    call whose result never came back is a finding, and dropping it would make
    the trace claim fewer calls than the agent actually made.
    """
    spans: list[Span] = []
    pending: dict[str, int] = {}         # tool_call_id -> index into spans
    final: str | None = None

    for ev in events:
        kind = ev.get("kind") or "UnknownEvent"
        ts = _ts(ev.get("timestamp"), fallback_start)

        if kind == _MESSAGE:
            text = _message_text(ev)
            source = ev.get("source")
            if source == "user":
                # Who spoke. The one thing that starts a turn — and the marker
                # `session_turns_instrumented` gates on, so emitting it is what
                # makes turn shape MEASURED for this subject rather than assumed.
                spans.append(_span(trace_id, "user_turn", "user_message", ts, ts,
                                   output={"text": text}))
            else:
                spans.append(_span(trace_id, "llm_call", "agent_message", ts, ts,
                                   output={"text": text}))
                if text:
                    final = text          # last agent message wins
            continue

        if kind == _ACTION:
            name = ev.get("tool_name") or "tool"
            call_id = ev.get("tool_call_id") or ""
            action = ev.get("action")
            if isinstance(action, dict) and action.get("kind") == _FINISH_ACTION:
                # The agent's answer, delivered as a tool call. Still recorded as
                # a tool_call span — it IS one, and hiding it would understate
                # what the agent did — but it is also where `final` comes from.
                msg = action.get("message")
                if isinstance(msg, str) and msg.strip():
                    final = msg
            pending[call_id] = len(spans)
            spans.append(_span(
                trace_id, "tool_call", name, ts, ts,
                input={"action": ev.get("action"),
                       "arguments": _arguments(ev)},
                attributes={"thought": _thought(ev),
                            "tool_call_id": call_id,
                            "security_risk": ev.get("security_risk"),
                            "result": "no observation was returned"}))
            continue

        if kind == _OBSERVATION:
            call_id = ev.get("tool_call_id") or ""
            idx = pending.pop(call_id, None)
            if idx is None:
                # An observation with no action to attach to. Recorded on its
                # own rather than discarded — an unpaired result means the
                # stream is not what this adapter believes it is.
                spans.append(_span(trace_id, "tool_call",
                                   ev.get("tool_name") or "tool", ts, ts,
                                   output={"observation": ev.get("observation")},
                                   attributes={"tool_call_id": call_id,
                                               "unpaired": True}))
                continue
            got = spans[idx]
            spans[idx] = got.model_copy(update={
                "end_time": max(ts, got.start_time),
                "output": {"observation": ev.get("observation")},
                "attributes": {**got.attributes, "result": "observed"},
            })
            continue

        if kind == _AGENT_ERROR:
            call_id = ev.get("tool_call_id") or ""
            idx = pending.pop(call_id, None)
            err = str(ev.get("error") or "agent error")
            if idx is not None:
                got = spans[idx]
                spans[idx] = got.model_copy(update={
                    "end_time": max(ts, got.start_time), "error": err,
                    "attributes": {**got.attributes, "result": "errored"}})
            else:
                spans.append(_span(trace_id, "error", "agent_error", ts, ts,
                                   error=err))
            continue

        # Unmapped kind. Kept, with its payload, so a version skew between this
        # adapter and the subject is visible in the evidence instead of silently
        # shrinking the trace.
        #
        # Recorded as `env_step`, NOT `agent_decision`: `extractors.py:332` and
        # `builtins.py:504` read `agent_decision` spans and text-match them, so a
        # payload we admit we do not understand could credit a coverage bin — an
        # unmodelled event would start reporting agent behaviour it never
        # evidenced. `env_step` is counted by nothing, so the evidence is kept
        # without being scored.
        spans.append(_span(trace_id, "env_step", kind, ts, ts,
                           output={"event": ev},
                           attributes={"unmapped_kind": kind}))

    if final is not None:
        last = spans[-1] if spans else None
        end = last.end_time if last else fallback_start
        spans.append(_span(trace_id, "final_output", "final_output", end, end,
                           output={"text": final}))
    return spans, final


def _message_text(ev: dict) -> str:
    """Concatenate the text blocks of an SDK `Message`."""
    msg = ev.get("llm_message") or {}
    out = [c.get("text", "") for c in (msg.get("content") or [])
           if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(t for t in out if t).strip()


def _thought(ev: dict) -> str:
    parts = [c.get("text", "") for c in (ev.get("thought") or [])
             if isinstance(c, dict)]
    return "\n".join(p for p in parts if p).strip()


def _arguments(ev: dict) -> str:
    call = ev.get("tool_call") or {}
    return call.get("arguments", "") if isinstance(call, dict) else ""


def _span(trace_id: str, kind: str, name: str, start: datetime, end: datetime,
          **kw) -> Span:
    return Span(span_id=f"{trace_id[:8]}-{uuid.uuid4().hex[:8]}", kind=kind,
                name=name, start_time=start, end_time=max(end, start), **kw)


def _ts(raw, fallback: datetime) -> datetime:
    """The subject's own timestamp, or ours if it is unreadable.

    Never invented silently: an unparseable timestamp falls back to the run's
    start, which keeps span ordering valid without claiming a precision the
    event did not carry.
    """
    if isinstance(raw, str):
        try:
            got = datetime.fromisoformat(raw)
            return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


def _text(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)
