"""The hardened agent loop — Claude in a tool-use loop with the safety model built in.

``SafeAssistant`` drives a bounded Anthropic tool-use loop over the allowlisted
SAFE tools, enforcing every defense at the structural level:

* **Untrusted-content handling** — tool results are guarded (injections
  neutralized) and fenced as untrusted DATA before they re-enter the model's
  context (:mod:`agenttic.assistant.guard`).
* **Allowlist / default-deny** — only :data:`agenttic.assistant.tools.TOOL_REGISTRY`
  tools run; anything else is refused as data, never executed.
* **Human-in-the-loop gate** — a turn that requests a *sensitive* tool PAUSES:
  the loop persists the pending call and returns ``awaiting_approval``; it only
  resumes when the human approves (run) or denies (skip) via :meth:`approve`.
* **Secret-leak filter** — the assistant's own text and every tool result are
  passed through :func:`agenttic.assistant.guard.redact_secrets`.

The loop is **resumable**: all state lives in a JSON-serializable ``session``
dict (transcript, scratchpad, step log, pending action) so the approval gate can
span separate HTTP requests / a server restart. :meth:`send_message` and
:meth:`approve` both advance the same internal :meth:`_drive` loop.

Agent mistakes are captured as steps, never raised (Hard Rule 5).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agenttic.assistant.guard import redact_secrets
from agenttic.assistant.posture import SYSTEM_PROMPT
from agenttic.assistant.tools import (
    ToolContext, execute_tool, is_allowlisted, is_sensitive, tool_schemas,
)

STATUS_READY = "ready"                    # idle, awaiting the next user message
STATUS_AWAITING_APPROVAL = "awaiting_approval"  # paused on a sensitive tool call


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session(session_id: str | None = None) -> dict:
    """A fresh, empty session state object."""
    sid = session_id or uuid.uuid4().hex[:16]
    return {
        "session_id": sid,
        "status": STATUS_READY,
        "messages": [],     # Anthropic transcript (plain-dict content blocks)
        "notes": {},        # per-session scratchpad
        "steps": [],        # human-/UI-facing event log across all turns
        "pending": None,    # the sensitive turn awaiting approval, if any
        "created_at": _now(),
        "updated_at": _now(),
    }


def _serialize_block(block) -> dict:
    """Convert an Anthropic response content block (SDK object or namespace) to a
    plain dict so it can be persisted and re-sent in the transcript."""
    btype = getattr(block, "type", "")
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {"type": "tool_use", "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": dict(getattr(block, "input", {}) or {})}
    # unknown block kinds are preserved opaquely but carry no executable meaning
    return {"type": btype or "unknown"}


class SafeAssistant:
    """Claude + the SAFE tool set in a bounded, safety-hardened, resumable loop."""

    def __init__(self, client, model: str, *, max_steps: int = 8,
                 max_tokens: int = 1024, extra_secrets: set[str] | None = None):
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        # exact known secret values (platform/tenant keys) masked verbatim in
        # any assistant/tool text, on top of the pattern filter.
        self.extra_secrets = extra_secrets or set()

    # -- public API --------------------------------------------------------- #

    def send_message(self, session: dict, text: str) -> dict:
        """Append a user message and run the loop until it answers or pauses for
        approval. Refuses to start a new message while a turn is awaiting
        approval (resolve the gate first)."""
        if session.get("status") == STATUS_AWAITING_APPROVAL:
            raise ValueError("a sensitive action is awaiting approval; approve or "
                             "deny it before sending another message")
        session["messages"].append({"role": "user", "content": str(text)})
        self._log(session, {"type": "user_message", "text": str(text)})
        return self._drive(session)

    def approve(self, session: dict, approved: bool) -> dict:
        """Resolve the pending sensitive action: run it (approved) or skip it
        (denied), then resume the loop. The denied case feeds the model a clear
        'the user declined' tool result so it can adapt rather than hang."""
        pending = session.get("pending")
        if not pending or session.get("status") != STATUS_AWAITING_APPROVAL:
            raise ValueError("no action is awaiting approval")
        self._log(session, {"type": "approval_resolved", "approved": approved,
                            "tool": pending["calls"][0]["name"] if pending["calls"]
                            else None})
        results = []
        for call in pending["calls"]:
            results.append(self._resolve_call(session, call, approved=approved))
        session["messages"].append({"role": "user", "content": results})
        session["pending"] = None
        session["status"] = STATUS_READY
        return self._drive(session)

    # -- the loop ----------------------------------------------------------- #

    def _drive(self, session: dict) -> dict:
        """Run create→tool cycles from the current transcript until a final
        answer, a sensitive pause, an error, or the step budget is hit."""
        for _ in range(self.max_steps):
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT, tools=tool_schemas(),
                    messages=list(session["messages"]))
            except Exception as exc:  # noqa: BLE001 — upstream failure is data
                self._log(session, {"type": "error",
                                    "text": f"upstream error: {type(exc).__name__}"})
                session["status"] = STATUS_READY
                session["updated_at"] = _now()
                return session

            blocks = [_serialize_block(b) for b in getattr(resp, "content", [])]
            session["messages"].append({"role": "assistant", "content": blocks})
            stop = getattr(resp, "stop_reason", None)
            self._log(session, {"type": "llm_call", "stop_reason": stop})

            # surface any assistant text (secret-filtered) for the UI/trace
            for b in blocks:
                if b["type"] == "text" and b["text"].strip():
                    self._log(session, {"type": "assistant_text",
                                        "text": self._clean(b["text"])})

            if stop != "tool_use":
                answer = self._clean("".join(
                    b["text"] for b in blocks if b["type"] == "text"))
                self._log(session, {"type": "final_answer", "text": answer})
                session["answer"] = answer
                session["status"] = STATUS_READY
                session["updated_at"] = _now()
                return session

            calls = [b for b in blocks if b["type"] == "tool_use"]
            for c in calls:
                self._log(session, {"type": "tool_request", "tool": c["name"],
                                    "input": c["input"],
                                    "sensitive": is_sensitive(c["name"]),
                                    "allowlisted": is_allowlisted(c["name"])})

            # Human-in-the-loop: if the turn requests ANY sensitive tool, pause
            # the whole turn (the API needs a result for every tool_use block, so
            # we defer them all until the gate is resolved).
            if any(is_sensitive(c["name"]) for c in calls):
                session["pending"] = {"calls": calls}
                for c in calls:
                    if is_sensitive(c["name"]):
                        self._log(session, {"type": "awaiting_approval",
                                            "tool": c["name"], "input": c["input"]})
                session["status"] = STATUS_AWAITING_APPROVAL
                session["updated_at"] = _now()
                return session

            # all non-sensitive: run now and continue
            results = [self._resolve_call(session, c, approved=True) for c in calls]
            session["messages"].append({"role": "user", "content": results})

        # step budget exhausted — kill switch
        self._log(session, {"type": "error",
                            "text": f"stopped after {self.max_steps} steps"})
        session["answer"] = "I wasn't able to finish that within my step budget."
        session["status"] = STATUS_READY
        session["updated_at"] = _now()
        return session

    # -- tool execution + filtering ----------------------------------------- #

    def _resolve_call(self, session: dict, call: dict, *, approved: bool) -> dict:
        """Produce the tool_result block for one tool_use call, enforcing the
        allowlist, the approval decision, untrusted-content guarding (the tool
        does its own wrap), and the secret filter."""
        name, args, tuid = call["name"], call.get("input", {}), call["id"]
        ctx = ToolContext(notes=session["notes"])

        if is_sensitive(name) and not approved:
            err = "the user did not approve this sensitive action; it was not run"
            self._log(session, {"type": "tool_result", "tool": name,
                                "ok": False, "error": err, "approved": False})
            return self._tool_result_block(tuid, err, is_error=True)

        if not is_allowlisted(name):
            err = (f"tool {name!r} is not on the allowlist and was refused "
                   "(default-deny)")
            self._log(session, {"type": "tool_result", "tool": name,
                                "ok": False, "error": err})
            return self._tool_result_block(tuid, err, is_error=True)

        output, error = execute_tool(name, dict(args), ctx)
        if error is not None:
            self._log(session, {"type": "tool_result", "tool": name,
                                "ok": False, "error": error})
            return self._tool_result_block(tuid, error, is_error=True)

        injection = isinstance(output, dict) and output.get("injection_blocked")
        content = self._stringify(name, output)
        self._log(session, {"type": "tool_result", "tool": name, "ok": True,
                            **({"injection_blocked": injection} if injection else {})})
        return self._tool_result_block(tuid, content, is_error=False)

    def _stringify(self, name: str, output) -> str:
        """Tool output → the string content fed back to the model. Non-web tools
        return small structured data; we secret-filter everything. web_fetch
        already returns guarded/fenced untrusted content; other tools' free-text
        is wrapped here too as defense in depth."""
        if isinstance(output, dict) and "content" in output and name == "web_fetch":
            # web_fetch already fenced + neutralized the page body
            body = str(output["content"])
            return redact_secrets(body, extra=self.extra_secrets)
        text = output if isinstance(output, str) else _to_text(output)
        return redact_secrets(text, extra=self.extra_secrets)

    @staticmethod
    def _tool_result_block(tool_use_id: str, content: str, *, is_error: bool
                           ) -> dict:
        return {"type": "tool_result", "tool_use_id": tool_use_id,
                "content": content, "is_error": is_error}

    def _clean(self, text: str) -> str:
        return redact_secrets(text, extra=self.extra_secrets)

    @staticmethod
    def _log(session: dict, step: dict) -> None:
        step = {"ts": _now(), **step}
        session["steps"].append(step)


def _to_text(output) -> str:
    import json
    try:
        return json.dumps(output, default=str)
    except Exception:  # noqa: BLE001
        return str(output)
