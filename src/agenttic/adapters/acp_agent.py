"""ACP adapter — drive ANY Agent Client Protocol agent, with no code per agent.

This is the answer to "must I write a module for every agent I test?". No. ACP
(Agent Client Protocol, Zed Industries; ``agent-client-protocol`` 0.8.1,
protocol version 1) is a JSON-RPC 2.0 protocol that agents implement so *clients*
can drive them. OpenHands ships ``openhands acp``; the same protocol is spoken by
Claude Code, Codex and Gemini's CLIs. One client here drives all of them, and the
next one costs a config line rather than a source change.

WHY THIS BEATS PARSING AN AGENT'S OWN EVENT STREAM
--------------------------------------------------
A bespoke adapter has to guess what a private event format means. ACP *declares*
it, and the declarations are exactly the ones our coverage model needs:

* ``ToolKind`` — ``read`` ``edit`` ``delete`` ``move`` ``search`` ``execute``
  ``think`` ``fetch`` ``switch_mode`` ``other``. A standard risk vocabulary from
  the agent itself, so ``action_risk`` is classified EXPLICITLY instead of by
  sniffing tool names. ``verification/traffic.py`` calls that difference
  ``explicit`` vs ``inferred`` confidence, and it is the difference between
  measuring risk and guessing it.
* ``ToolCallStatus`` — ``pending`` ``in_progress`` ``completed`` ``failed``. A
  failed tool call is stated, not inferred from a substring.
* ``Usage`` — input/output/cached tokens, so a subprocess agent's spend is
  visible instead of being reported as $0.00.
* ``StopReason`` — ``end_turn`` ``max_tokens`` ``max_turn_requests`` ``refusal``
  ``cancelled``. A refusal is a first-class fact.
* ``session/new`` + repeated ``session/prompt`` — a real session, which is why
  this adapter implements :meth:`converse` and can be driven multi-turn.

NO SDK DEPENDENCY, DELIBERATELY
-------------------------------
Same rule as ``adapters/mcp_server.py``: the harness must be able to drive a
MISBEHAVING agent — malformed frames, a crash mid-turn, a protocol violation —
without an SDK normalising away the very faults we are trying to observe. The
wire format was read off the reference implementation's own schema (methods,
field aliases, enum members), not guessed.

WHAT WE PROMISE THE AGENT
-------------------------
We advertise no filesystem and no terminal capability, so a compliant agent uses
its own. It may still ask us for permission to act (``session/request_permission``)
and every such request is answered by a policy and RECORDED on the span — an
agent that asked and was refused did something different from one that never
asked, and the trace has to be able to tell them apart.
"""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import Span, Trace

if TYPE_CHECKING:  # pragma: no cover
    from agenttic.scenario.session import Session

#: Marker for a run that never reached the agent — a NON-RESULT, never a wrong
#: answer. ``scoring/engine.py`` treats this prefix as an execution failure.
HARNESS_FAILURE = "HARNESS_FAILURE"

PROTOCOL_VERSION = 1

#: ACP's own tool vocabulary -> what the coverage model asks of a tool span.
#: ``mutating`` and ``irreversible`` are the attributes ``verification/builtins``
#: reads; setting them from the agent's OWN declaration is what makes the
#: classification ``explicit`` rather than a guess at the tool's name.
TOOL_KIND_RISK: dict[str, dict[str, bool]] = {
    "read":     {"mutating": False, "irreversible": False},
    "search":   {"mutating": False, "irreversible": False},
    "fetch":    {"mutating": False, "irreversible": False},
    "think":    {"mutating": False, "irreversible": False},
    "edit":     {"mutating": True,  "irreversible": False},
    "move":     {"mutating": True,  "irreversible": False},
    "delete":   {"mutating": True,  "irreversible": True},
    "execute":  {"mutating": True,  "irreversible": False},
}
#: `switch_mode` and `other` are deliberately absent: ACP defines them as
#: "anything else", so claiming to know their risk would be the name-sniffing
#: this table exists to replace. An unclassified tool is left unclassified, and
#: `traffic.classify_confidence` reports it as `unknown` — never as read-only.

_PERMISSION_ALLOW = "allow"
_PERMISSION_REJECT = "reject"

#: Answer to `session/request_permission`. Takes the tool-call payload, returns
#: "allow" or "reject". The default allows and records; a caller wiring the
#: enforcement gateway supplies its own.
PermissionPolicy = Callable[[dict], str]


def _allow_and_record(_tool_call: dict) -> str:
    return _PERMISSION_ALLOW


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text_of(content: Any) -> str:
    """Pull display text out of an ACP ContentBlock, a list of them, or a str."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        for key in ("content", "resource"):
            if content.get(key) is not None:
                return _text_of(content[key])
        return ""
    if isinstance(content, list):
        return "".join(_text_of(x) for x in content)
    return ""


class ACPProtocolError(RuntimeError):
    """The agent broke the protocol. Recorded as a non-result, never raised out."""


class ACPConnection:
    """One JSON-RPC 2.0 conversation with an agent subprocess over stdio.

    Owns the process, a reader thread, and the correlation of ids to replies.
    Deliberately NOT stored on the adapter: the harness drives one adapter from
    several threads, so a connection is per-run state and lives in a local.
    """

    def __init__(self, argv: list[str], *, cwd: str | None, env: dict | None,
                 timeout_s: float, on_update: Callable[[dict], None],
                 permission_policy: PermissionPolicy) -> None:
        self.timeout_s = float(timeout_s)
        self._on_update = on_update
        self._policy = permission_policy
        self._id = 0
        self._lock = threading.Lock()
        self._replies: dict[int, dict] = {}
        self._reply_event = threading.Condition(self._lock)
        self._stderr: list[str] = []
        #: Every agent->client request we answered, for the record.
        self.client_calls: list[dict] = []
        self.bad_lines = 0

        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._errpump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._errpump.start()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Terminate the agent. Always called, including on timeout.

        The harness's own timeout does NOT kill an adapter's child process
        (``harness/runner.py`` runs adapters via ``asyncio.to_thread`` and
        abandons the thread), so an adapter that leaks its subprocess leaves an
        agent running against the user's API key long after the run gave up on
        it. Measured: two orphaned agents still alive ~40 minutes later.
        """
        for step in (self.proc.terminate, self.proc.kill):
            if self.proc.poll() is not None:
                break
            try:
                step()
                self.proc.wait(timeout=5)
            except Exception:      # noqa: BLE001 — teardown never breaks a run
                continue
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:      # noqa: BLE001
                pass

    def __enter__(self) -> "ACPConnection":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr)[-4000:]

    # -- the wire ----------------------------------------------------------

    def _pump_stderr(self) -> None:
        try:
            for line in self.proc.stderr or ():
                self._stderr.append(line)
        except Exception:          # noqa: BLE001 — the process died; not our error
            pass

    def _pump(self) -> None:
        """Read frames until the agent closes stdout.

        Three kinds arrive on one pipe and conflating them is the classic bug:
        a REPLY (has `id`, no `method`), a REQUEST from the agent (has both, and
        must be answered or the agent blocks forever), and a NOTIFICATION (has
        `method`, no `id`).
        """
        try:
            for line in self.proc.stdout or ():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.bad_lines += 1
                    continue
                if not isinstance(msg, dict):
                    self.bad_lines += 1
                    continue
                if msg.get("method") is None and msg.get("id") is not None:
                    with self._reply_event:
                        self._replies[int(msg["id"])] = msg
                        self._reply_event.notify_all()
                elif msg.get("id") is not None:
                    self._answer(msg)
                else:
                    self._notify(msg)
        except Exception:          # noqa: BLE001 — a dead pipe ends the pump
            pass
        finally:
            with self._reply_event:
                self._reply_event.notify_all()

    def _notify(self, msg: dict) -> None:
        if msg.get("method") == "session/update":
            try:
                self._on_update(msg.get("params") or {})
            except Exception:      # noqa: BLE001 — mapping never kills the run
                pass

    def _answer(self, msg: dict) -> None:
        """Respond to an agent->client request.

        An unanswered request deadlocks the agent, so every method gets a reply
        — including the ones we do not implement, which get a JSON-RPC error
        rather than silence.
        """
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        self.client_calls.append({"method": method, "params": params})
        if method == "session/request_permission":
            decision = _PERMISSION_ALLOW
            try:
                decision = self._policy(params.get("toolCall") or {})
            except Exception:      # noqa: BLE001 — a broken policy denies, loudly
                decision = _PERMISSION_REJECT
            opts = params.get("options") or []
            want = ("allow_once", "allow_always") if decision == _PERMISSION_ALLOW \
                else ("reject_once", "reject_always")
            pick = next((o for o in opts if o.get("kind") in want), None)
            if pick is None:
                self._reply(msg["id"], {"outcome": {"outcome": "cancelled"}})
            else:
                self._reply(msg["id"], {"outcome": {
                    "outcome": "selected", "optionId": pick.get("optionId")}})
            self.client_calls[-1]["decision"] = decision
            return
        # We advertised neither fs nor terminal capability, so a compliant agent
        # will not ask. Answer anything else with a protocol error rather than
        # hanging — and keep the record, because being asked for a capability we
        # declined to advertise is itself a finding.
        self._reply_error(msg["id"], -32601, f"{method} not supported by this client")

    def _reply(self, rid: Any, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    def _reply_error(self, rid: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": code, "message": message}})

    def _write(self, payload: dict) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except Exception as exc:   # noqa: BLE001
            raise ACPProtocolError(f"could not write to the agent: {exc}") from exc

    def request(self, method: str, params: dict, *, timeout_s: float | None = None) -> dict:
        """Send a request and wait for its reply. Raises on timeout or error."""
        with self._lock:
            self._id += 1
            rid = self._id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = timeout_s if timeout_s is not None else self.timeout_s
        with self._reply_event:
            ok = self._reply_event.wait_for(lambda: rid in self._replies, timeout=deadline)
            if not ok:
                raise ACPProtocolError(
                    f"the agent did not answer {method} within {deadline:g}s")
            msg = self._replies.pop(rid)
        if "error" in msg:
            err = msg["error"] or {}
            raise ACPProtocolError(
                f"{method} failed: {err.get('message')} (code {err.get('code')})")
        return msg.get("result") or {}


class ACPAgent(AgentAdapter):
    """Any ACP-speaking agent, driven over its own published protocol.

    Re-entrant: ``run`` writes nothing to ``self``; the connection, the spans and
    the session id are per-run locals, because the harness enters one adapter
    from up to ``max_parallel`` threads.
    """

    visibility = "glass_box"

    def __init__(self, agent_id: str = "acp-agent", *,
                 command: list[str] | tuple[str, ...] = ("openhands", "acp"),
                 cwd: str | None = None, env: dict | None = None,
                 timeout_s: float = 900.0, version: str = "",
                 model: str = "", permission_policy: PermissionPolicy | None = None,
                 conversation_id: str = "", auth_method: str = "") -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.cwd = cwd
        self.env = dict(env) if env else None
        self.timeout_s = float(timeout_s)
        #: Pinned for the record — a trace that cannot say which build produced
        #: it is not reproducible evidence.
        self.version = version
        self.model = model
        self.permission_policy = permission_policy or _allow_and_record
        #: ACP auth method id, when the agent demands one. Never guessed.
        self.auth_method = auth_method
        #: Correlation key. When set, every span carries `gen_ai.conversation.id`
        #: so spans this agent exports to OTel can be joined to THIS run.
        self.conversation_id = conversation_id

    # -- identity ----------------------------------------------------------

    def describe(self) -> dict:
        """Deterministic and JSON-safe; feeds the config hash.

        ``cwd`` and ``env`` are excluded: they are where the run happened, not
        what the agent is, and including them would give one agent a different
        hash per directory and silently defeat resume. Secrets never appear here
        — this dict is hashed into every trace and serialised with the run.
        """
        return {
            "adapter": "acp",
            "command": list(self.command),
            "protocol": "acp",
            "protocol_version": PROTOCOL_VERSION,
            "model": self.model,
            "version": self.version,
            "timeout_s": self.timeout_s,
            "auth_method": self.auth_method,
        }

    # -- one-shot ----------------------------------------------------------

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        """One task, one turn. Agent mistakes are DATA; nothing here raises."""
        from agenttic.adapters.cli_spec import task_text

        started = _now()
        trace_id = uuid.uuid4().hex
        mapper = _UpdateMapper(trace_id, self.conversation_id)
        disclosures: list[str] = []
        final = ""
        usage: dict = {}
        conn = None
        try:
            conn = self._connect(mapper)
            init = self._initialize(conn)
            self._authenticate(conn, init, disclosures)
            session_id = self._new_session(conn)
            result = conn.request("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": task_text(test_input)}]})
            final, usage = self._finish(mapper, result, disclosures)
        except ACPProtocolError as exc:
            disclosures.append(
                "the subject did not complete the protocol exchange — this is a "
                "non-result, not a wrong answer")
            final = f"{HARNESS_FAILURE}: {exc}"
        except OSError as exc:      # noqa: BLE001 — could not even start it
            disclosures.append(
                "the subject was never invoked — this is not an agent result")
            final = f"{HARNESS_FAILURE}: could not start {self.command[0]!r}: {exc}"
        finally:
            if conn is not None:
                if conn.bad_lines:
                    disclosures.append(
                        f"{conn.bad_lines} frame(s) from the agent were not valid "
                        "JSON-RPC and could not be read")
                mapper.record_client_calls(conn.client_calls)
                conn.close()        # always: never leave an agent running

        spans = mapper.finish(started, final)
        return Trace(
            trace_id=trace_id, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            visibility="glass_box",
            spans=[self._record_span(trace_id, started, disclosures, mapper,
                                    usage), *spans],
            final_output=final or f"{HARNESS_FAILURE}: the agent produced no answer",
            total_cost_usd=float(usage.get("cost_usd") or 0.0),
            total_steps=sum(1 for s in spans if s.kind == "tool_call"),
        )

    # -- multi-turn --------------------------------------------------------

    def converse(self, session: "Session") -> Trace:
        """Drive a whole conversation over ONE ACP session.

        This is the capability the protocol buys us that a one-shot CLI cannot
        give: ``session/new`` once, then a ``session/prompt`` per turn against
        the same session id, so the agent answers turn three having actually
        seen turns one and two. State lives on the caller's ``Session``, never on
        ``self`` — the harness enters one adapter from many threads.
        """
        started = _now()
        trace_id = uuid.uuid4().hex
        conn = None
        final = ""
        disclosures: list[str] = []
        usage: dict = {}
        try:
            mapper = _UpdateMapper(trace_id, self.conversation_id)
            conn = self._connect(mapper)
            init = self._initialize(conn)
            self._authenticate(conn, init, disclosures)
            session_id = self._new_session(conn)
            for turn in session.deliver():
                mapper.reset_turn()
                result = conn.request("session/prompt", {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": getattr(turn, "text", str(turn))}]})
                final, usage = self._finish(mapper, result, disclosures)
                session.record(mapper.take_turn_spans())
        except (ACPProtocolError, OSError) as exc:   # noqa: BLE001
            disclosures.append(
                "the conversation ended on a protocol failure, so the turns after "
                "it were never delivered — this is a non-result for those turns")
            final = final or f"{HARNESS_FAILURE}: {exc}"
        finally:
            if conn is not None:
                conn.close()
        return session.to_trace(self, trace_id=trace_id,
                                final_output=final or None)

    # -- pieces ------------------------------------------------------------

    def _connect(self, mapper: "_UpdateMapper") -> ACPConnection:
        return ACPConnection(
            self.command, cwd=self.cwd, env=self.env, timeout_s=self.timeout_s,
            on_update=mapper.handle, permission_policy=self.permission_policy)

    def _initialize(self, conn: ACPConnection) -> dict:
        """Handshake. We advertise NO fs and NO terminal capability."""
        res = conn.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False,
                                          "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "agenttic", "version": "1"}},
            timeout_s=min(60.0, self.timeout_s))
        got = res.get("protocolVersion")
        if got is not None and int(got) != PROTOCOL_VERSION:
            # Not fatal: a newer agent may still speak our version's methods.
            # Recorded rather than assumed away.
            conn.client_calls.append(
                {"method": "initialize", "protocol_version_mismatch": got})
        return res

    def _authenticate(self, conn: ACPConnection, init: dict,
                      disclosures: list[str]) -> None:
        """Authenticate only if we were TOLD which method to use.

        ACP lets an agent demand auth before ``session/new``, and the methods are
        agent-specific: OpenHands 1.16.0 advertises exactly one, ``oauth`` — an
        interactive OpenHands Cloud browser flow that never returns in a headless
        run (measured: it blocks until the timeout). So this never guesses a
        method. Without ``auth_method`` configured we go straight on and let
        ``session/new`` fail with the agent's own message, because "the subject
        requires an account we do not have" is a finding worth reporting exactly
        as the subject stated it — not a hang, and not a fabricated failure.
        """
        methods = [m.get("id") for m in (init.get("authMethods") or [])
                   if isinstance(m, dict)]
        if methods:
            disclosures.append(
                f"the agent advertises authentication method(s) {methods} before "
                "a session can start")
        if not self.auth_method:
            return
        if methods and self.auth_method not in methods:
            disclosures.append(
                f"configured auth_method {self.auth_method!r} is not offered by "
                f"this agent (it offers {methods})")
            return
        conn.request("authenticate", {"methodId": self.auth_method},
                     timeout_s=min(120.0, self.timeout_s))

    def _new_session(self, conn: ACPConnection) -> str:
        res = conn.request("session/new", {
            "cwd": self.cwd or ".", "mcpServers": []},
            timeout_s=min(120.0, self.timeout_s))
        sid = res.get("sessionId")
        if not isinstance(sid, str) or not sid:
            raise ACPProtocolError("session/new returned no sessionId")
        return sid

    def _finish(self, mapper: "_UpdateMapper", result: dict,
                disclosures: list[str]) -> tuple[str, dict]:
        stop = result.get("stopReason")
        if stop and stop != "end_turn":
            disclosures.append(
                f"the agent stopped because of {stop!r}, not because it finished "
                "the task")
        if stop == "refusal":
            mapper.refused = True
        usage = _usage_of(result.get("usage") or {})
        mapper.stop_reason = stop
        return mapper.answer_text(), usage

    def _record_span(self, trace_id: str, started: datetime,
                     disclosures: list[str], mapper: "_UpdateMapper",
                     usage: dict) -> Span:
        attrs = {
            "disclosures": disclosures,
            "recorded_by": "acp",
            "protocol_version": PROTOCOL_VERSION,
            "subject_version": self.version,
            "stop_reason": mapper.stop_reason,
            "client_calls": mapper.client_calls,
            **({"tokens_in": usage.get("tokens_in"),
                "tokens_out": usage.get("tokens_out")} if usage else {}),
        }
        if self.conversation_id:
            attrs["gen_ai.conversation.id"] = self.conversation_id
        return Span(span_id=f"{trace_id[:8]}-{uuid.uuid4().hex[:8]}",
                    kind="env_step", name="harness_record",
                    start_time=started, end_time=started, attributes=attrs)


def _usage_of(usage: dict) -> dict:
    if not usage:
        return {}
    return {"tokens_in": usage.get("inputTokens"),
            "tokens_out": usage.get("outputTokens"),
            "tokens_total": usage.get("totalTokens"),
            "cost_usd": usage.get("cost") or 0.0}


class _UpdateMapper:
    """Turns ``session/update`` notifications into spans, as they arrive.

    Runs on the connection's reader thread, so it owns a lock: the run thread
    reads the spans out when the prompt returns.
    """

    def __init__(self, trace_id: str, conversation_id: str = "") -> None:
        self.trace_id = trace_id
        self.conversation_id = conversation_id
        self._lock = threading.Lock()
        self._spans: list[Span] = []
        self._turn_start = 0
        self._pending: dict[str, int] = {}     # toolCallId -> index in _spans
        self._answer: list[str] = []
        self.stop_reason: str | None = None
        self.refused = False
        self.client_calls: list[dict] = []

    # -- collection --------------------------------------------------------

    def handle(self, params: dict) -> None:
        upd = params.get("update") or {}
        kind = upd.get("sessionUpdate")
        ts = _now()
        with self._lock:
            if kind == "agent_message_chunk":
                self._answer.append(_text_of(upd.get("content")))
            elif kind == "user_message_chunk":
                self._append(self._span("user_turn", "user_message", ts,
                                        output={"text": _text_of(upd.get("content"))}))
            elif kind == "agent_thought_chunk":
                self._append(self._span("llm_call", "agent_thought", ts,
                                        output={"text": _text_of(upd.get("content"))}))
            elif kind == "tool_call":
                self._start_tool(upd, ts)
            elif kind == "tool_call_update":
                self._update_tool(upd, ts)
            elif kind is not None:
                # A member of the union we do not model (plan, available_commands,
                # current_mode, config_option, session_info, usage). Kept with its
                # payload as `env_step` — never dropped, and never scored, because
                # a payload we do not model must not credit a coverage bin.
                self._append(self._span("env_step", str(kind), ts,
                                        output={"update": upd},
                                        attributes={"unmapped_update": kind}))

    def _start_tool(self, upd: dict, ts: datetime) -> None:
        call_id = upd.get("toolCallId") or ""
        kind = upd.get("kind")
        attrs = {
            "tool_call_id": call_id,
            "acp_tool_kind": kind,
            "status": upd.get("status") or "pending",
            "locations": upd.get("locations"),
        }
        # The agent's OWN declaration of what this tool does. Explicit, not
        # sniffed from the name — see TOOL_KIND_RISK.
        attrs.update(TOOL_KIND_RISK.get(str(kind), {}))
        self._pending[call_id] = len(self._spans)
        self._append(self._span(
            "tool_call", upd.get("title") or str(kind or "tool"), ts,
            input={"raw_input": upd.get("rawInput")}, attributes=attrs))

    def _update_tool(self, upd: dict, ts: datetime) -> None:
        call_id = upd.get("toolCallId") or ""
        idx = self._pending.get(call_id)
        status = upd.get("status")
        if idx is None:
            # A result for a call we never saw start. Kept and marked: an
            # unpaired result means the stream is not what we believe it is.
            self._append(self._span(
                "tool_call", upd.get("title") or "tool", ts,
                output={"raw_output": upd.get("rawOutput"),
                        "content": upd.get("content")},
                attributes={"tool_call_id": call_id, "unpaired": True,
                            "status": status}))
            return
        got = self._spans[idx]
        attrs = {**(got.attributes or {})}
        if status:
            attrs["status"] = status
        if upd.get("kind") is not None:
            attrs["acp_tool_kind"] = upd.get("kind")
            attrs.update(TOOL_KIND_RISK.get(str(upd.get("kind")), {}))
        out = {k: v for k, v in (("raw_output", upd.get("rawOutput")),
                                 ("content", upd.get("content"))) if v is not None}
        self._spans[idx] = got.model_copy(update={
            "end_time": max(ts, got.start_time),
            "output": out or got.output,
            "attributes": attrs,
            # `failed` is DECLARED by the agent. `tool_condition` coverage can
            # credit a real failure instead of matching the word "error" in a
            # payload, which is the substring sniff this replaces.
            "error": ("the agent reported this tool call as failed"
                      if status == "failed" else got.error),
        })
        if status in ("completed", "failed"):
            self._pending.pop(call_id, None)

    # -- readout -----------------------------------------------------------

    def _span(self, kind: str, name: str, ts: datetime, **kw) -> Span:
        attrs = kw.pop("attributes", None) or {}
        if self.conversation_id:
            attrs["gen_ai.conversation.id"] = self.conversation_id
        return Span(span_id=f"{self.trace_id[:8]}-{uuid.uuid4().hex[:8]}",
                    kind=kind, name=name, start_time=ts, end_time=ts,
                    attributes=attrs, **kw)

    def _append(self, span: Span) -> None:
        self._spans.append(span)

    def answer_text(self) -> str:
        with self._lock:
            return "".join(self._answer).strip()

    def reset_turn(self) -> None:
        with self._lock:
            self._turn_start = len(self._spans)
            self._answer = []

    def take_turn_spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans[self._turn_start:])

    def record_client_calls(self, calls: list[dict]) -> None:
        with self._lock:
            self.client_calls = list(calls)

    def finish(self, started: datetime, final: str) -> list[Span]:
        with self._lock:
            spans = list(self._spans)
            for call_id, idx in self._pending.items():
                got = spans[idx]
                spans[idx] = got.model_copy(update={"attributes": {
                    **(got.attributes or {}),
                    "result": "no completion was reported for this tool call"}})
            if self.refused:
                spans.append(self._span("agent_decision", "refusal", _now(),
                                        attributes={"refused": True}))
            if final:
                end = spans[-1].end_time if spans else started
                spans.append(self._span("final_output", "final_output", end,
                                        output={"text": final}))
            return spans
