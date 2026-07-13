"""The Copilot agent loop — Claude Sonnet 4.6 in a streaming, tool-using loop
where the TOOLS are the real Agenttic API (see :mod:`agenttic.copilot.tools`).

The loop is the standard Anthropic tool-use cycle — model → tool_use → execute →
tool_result → model → … → final answer — with the platform's safety model built
in at the structural level:

* **Read vs write.** READ tools run freely. A turn that requests any WRITE/COST
  tool PAUSES the whole turn (the API needs a result for every ``tool_use`` block
  in a turn), persists it as ``pending``, and emits ``approval_required``. It only
  resumes when the user confirms (:meth:`resume`), and a denied write returns a
  "user declined" tool_result so the model adapts instead of hanging.
* **Credits gate on spend.** Before an approved write executes, the credits gate
  is consulted; a refusal becomes a tool_result, never a silent spend. Usage
  (tokens) and executed write actions are recorded for billing.
* **Untrusted tool results.** Every tool result is injection-neutralized, fenced
  as untrusted DATA, and secret-scrubbed before it re-enters the model context —
  so a tool result can't make the agent take an unapproved action or leak a key.
* **Honesty.** The agent reports only what tools actually return.

State lives in a JSON-serializable ``session`` dict so the confirmation gate spans
separate HTTP requests. :meth:`start_turn` and :meth:`resume` are **generators**
that yield UI events (token / tool / approval_required / usage / final / error);
the route formats them as SSE and persists the session when the generator ends.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

from agenttic.assistant.guard import guard_untrusted, redact_secrets
from agenttic.copilot.credits import check_credits, record_action
from agenttic.copilot.errors import classify
from agenttic.copilot.tools import (
    ToolContext, confirmation_for, get_tool, is_write, tool_schemas,
)

log = logging.getLogger("agenttic.copilot.agent")

STATUS_READY = "ready"
STATUS_AWAITING_APPROVAL = "awaiting_approval"

_SECRET_TAIL = 48  # holdback so a secret split across token deltas is still caught


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session(session_id: str | None = None) -> dict:
    sid = session_id or ("cop_" + uuid.uuid4().hex[:16])
    return {
        "session_id": sid,
        "status": STATUS_READY,
        "messages": [],     # Anthropic transcript (plain-dict content blocks)
        "steps": [],        # UI-facing event log across the whole session
        "pending": None,    # a turn's calls awaiting the user's confirmation
        "created_at": _now(),
        "updated_at": _now(),
    }


def _serialize_block(block) -> dict:
    btype = getattr(block, "type", "")
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {"type": "tool_use", "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": dict(getattr(block, "input", {}) or {})}
    return {"type": btype or "unknown"}


class CopilotAgent:
    def __init__(self, client, model: str, *, max_iters: int = 8,
                 max_tokens: int = 1024, extra_secrets: set[str] | None = None):
        self.client = client
        self.model = model
        self.max_iters = max_iters
        self.max_tokens = max_tokens
        self.extra_secrets = extra_secrets or set()

    # -- public API --------------------------------------------------------- #

    def start_turn(self, session: dict, ctx: ToolContext, text: str
                   ) -> Iterator[dict]:
        if session.get("status") == STATUS_AWAITING_APPROVAL:
            yield {"type": "error", "text": "An action is awaiting your "
                   "confirmation — approve or deny it before sending a new message."}
            return
        session["messages"].append({"role": "user", "content": str(text)})
        self._log(session, {"type": "user_message"})
        yield from self._drive(session, ctx)

    def resume(self, session: dict, ctx: ToolContext, approved: bool
               ) -> Iterator[dict]:
        pending = session.get("pending")
        if not pending or session.get("status") != STATUS_AWAITING_APPROVAL:
            yield {"type": "error", "text": "There's no action awaiting confirmation."}
            return
        self._log(session, {"type": "approval_resolved", "approved": approved})
        results = []
        for call in pending["calls"]:
            block, event = self._resolve_call(session, ctx, call, approved=approved)
            if event:
                yield event
            results.append(block)
        session["messages"].append({"role": "user", "content": results})
        session["pending"] = None
        session["status"] = STATUS_READY
        yield from self._drive(session, ctx)

    # -- the loop ----------------------------------------------------------- #

    def _drive(self, session: dict, ctx: ToolContext) -> Iterator[dict]:
        for _ in range(self.max_iters):
            try:
                blocks, stop, usage = yield from self._stream_model_turn(session)
            except Exception as exc:  # noqa: BLE001 — upstream failure is data, not a 500
                err, diag = classify(exc)
                # Log the REAL underlying error (status + type + request_id + case)
                # so an incident is diagnosable from `docker compose logs` alone.
                # The secret-redaction log filter still scrubs any known key value;
                # exc_info is a standard traceback (no locals) — no secret leak.
                log.error("copilot_upstream_error",
                          extra={"extra_fields": diag}, exc_info=True)
                self._log(session, {"type": "error", "code": err.code})
                session["status"] = STATUS_READY
                session["updated_at"] = _now()
                yield {"type": "error", "code": err.code, "text": err.message,
                       "action": err.action}
                return

            session["messages"].append({"role": "assistant", "content": blocks})
            if usage:
                yield {"type": "usage", **usage}

            if stop != "tool_use":
                answer = self._clean("".join(
                    b.get("text", "") for b in blocks if b["type"] == "text"))
                session["answer"] = answer
                session["status"] = STATUS_READY
                session["updated_at"] = _now()
                self._log(session, {"type": "final_answer"})
                yield {"type": "final", "text": answer}
                return

            calls = [b for b in blocks if b["type"] == "tool_use"]

            # Human-in-the-loop: any WRITE tool pauses the whole turn.
            if any(is_write(c["name"]) for c in calls):
                session["pending"] = {"calls": calls}
                session["status"] = STATUS_AWAITING_APPROVAL
                session["updated_at"] = _now()
                for c in calls:
                    if is_write(c["name"]):
                        card = confirmation_for(c["name"], c.get("input", {})) or {}
                        self._log(session, {"type": "awaiting_approval",
                                            "tool": c["name"]})
                        yield {"type": "approval_required", "tool": c["name"],
                               "input": c.get("input", {}), "card": card}
                return

            # all reads → execute now and continue the loop
            results = []
            for c in calls:
                block, event = self._resolve_call(session, ctx, c, approved=True)
                if event:
                    yield event
                results.append(block)
            session["messages"].append({"role": "user", "content": results})

        # iteration budget exhausted — kill switch
        self._log(session, {"type": "error", "text": "step budget exhausted"})
        session["answer"] = "I wasn't able to finish that within my step budget."
        session["status"] = STATUS_READY
        session["updated_at"] = _now()
        yield {"type": "final", "text": session["answer"]}

    def _stream_model_turn(self, session: dict):
        """Stream one model turn: yield ('token' events) as text arrives; return
        (blocks, stop_reason, usage). Secret patterns are scrubbed from every
        emitted chunk, with a tail holdback for secrets split across deltas."""
        pending = ""
        with self.client.messages.stream(
            model=self.model, max_tokens=self.max_tokens,
            system=_system_prompt(), tools=tool_schemas(),
            messages=list(session["messages"]),
        ) as stream:
            for delta in stream.text_stream:
                if not delta:
                    continue
                pending += delta
                if len(pending) > _SECRET_TAIL:
                    emit, pending = pending[:-_SECRET_TAIL], pending[-_SECRET_TAIL:]
                    yield {"type": "token", "text": self._clean(emit)}
            if pending:
                yield {"type": "token", "text": self._clean(pending)}
            final = stream.get_final_message()
        blocks = [_serialize_block(b) for b in getattr(final, "content", [])]
        stop = getattr(final, "stop_reason", None)
        usage = None
        u = getattr(final, "usage", None)
        if u is not None:
            usage = {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
                     "output_tokens": int(getattr(u, "output_tokens", 0) or 0)}
        return blocks, stop, usage

    # -- tool execution ----------------------------------------------------- #

    def _resolve_call(self, session: dict, ctx: ToolContext, call: dict, *,
                      approved: bool) -> tuple[dict, dict | None]:
        """Execute one tool_use → (tool_result block, optional UI event).
        Enforces: default-deny unknown tools, the confirmation decision for write
        tools, the credits gate on approved writes, and untrusted+secret guarding
        of the result."""
        name, args, tuid = call["name"], call.get("input", {}), call.get("id", "")
        tool = get_tool(name)

        if tool is None:
            err = f"tool {name!r} is not available and was refused (default-deny)"
            self._log(session, {"type": "tool_result", "tool": name, "ok": False})
            return self._result_block(tuid, err, is_error=True), \
                {"type": "tool", "tool": name, "phase": "done", "ok": False,
                 "summary": "refused (unknown tool)"}

        if tool.is_write and not approved:
            err = "the user did not approve this action; it was not run"
            self._log(session, {"type": "tool_result", "tool": name, "ok": False,
                                "approved": False})
            return self._result_block(tuid, err, is_error=True), \
                {"type": "tool", "tool": name, "phase": "done", "ok": False,
                 "summary": "declined by user"}

        # credits gate: spend/mutation is where free-credits/billing plugs in
        if tool.is_write:
            decision = check_credits(ctx.tenant)
            if not decision.allowed:
                err = f"credits check refused this action: {decision.reason}"
                self._log(session, {"type": "tool_result", "tool": name,
                                    "ok": False, "credits_denied": True})
                return self._result_block(tuid, err, is_error=True), \
                    {"type": "tool", "tool": name, "phase": "done", "ok": False,
                     "summary": "blocked (out of credits)"}

        try:
            output = tool.run(ctx, dict(args or {}))
        except Exception as exc:  # noqa: BLE001 — a tool fault is data, not a 500
            err = f"the {name} tool failed: {type(exc).__name__}"
            self._log(session, {"type": "tool_result", "tool": name, "ok": False})
            return self._result_block(tuid, err, is_error=True), \
                {"type": "tool", "tool": name, "phase": "done", "ok": False,
                 "summary": err}

        ok = not (isinstance(output, dict) and output.get("error"))
        self._log(session, {"type": "tool_result", "tool": name, "ok": ok})
        if tool.is_write and ok:
            # record the executed action for billing/audit (no message content)
            record_action(ctx.tenant, self.model, name)
            self._log(session, {"type": "action_executed", "tool": name})

        content = self._stringify_result(name, output)
        summary = (output.get("error") if isinstance(output, dict)
                   and output.get("error") else self._summarize(name, output))
        return self._result_block(tuid, content, is_error=not ok), \
            {"type": "tool", "tool": name, "phase": "done", "ok": ok,
             "summary": summary}

    def _stringify_result(self, name: str, output) -> str:
        """Tool output → the string fed back to the model, guarded as UNTRUSTED
        (injection-neutralized + fenced) and secret-scrubbed."""
        raw = output if isinstance(output, str) else _to_json(output)
        raw = redact_secrets(raw, extra=self.extra_secrets)
        return guard_untrusted(source=name, content=raw).sanitized

    @staticmethod
    def _summarize(name: str, output) -> str:
        if isinstance(output, dict):
            for k in ("count", "job_id", "tier", "status", "valid",
                      "anthropic_key_set", "started", "revoked"):
                if k in output:
                    return f"{name}: {k}={output[k]}"
        return f"{name}: ok"

    @staticmethod
    def _result_block(tool_use_id: str, content: str, *, is_error: bool) -> dict:
        return {"type": "tool_result", "tool_use_id": tool_use_id,
                "content": content, "is_error": is_error}

    def _clean(self, text: str) -> str:
        return redact_secrets(text, extra=self.extra_secrets)

    @staticmethod
    def _log(session: dict, step: dict) -> None:
        session["steps"].append({"ts": _now(), **step})


def _system_prompt() -> str:
    from agenttic.copilot.skill import build_system_prompt
    return build_system_prompt()


def _to_json(output) -> str:
    try:
        return json.dumps(output, default=str)
    except Exception:  # noqa: BLE001
        return str(output)
