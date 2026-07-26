"""A tool-use hook that turns a coding agent's session into verifiable traces.

`agenttic certify` drives agents through adapters. A coding agent has no adapter —
it runs on its own, in a real repo, on real work — so the only way to verify it is
to receive what it did. A ``PostToolUse`` hook is the cheapest place to capture
that: it sees the tool name **and the arguments**, and for a coding agent the
arguments are where the risk lives (``Bash`` alone tells you nothing).

Emits OTLP-shaped spans so the existing importer
(:mod:`agenttic.ingest.otel` / :mod:`agenttic.ingest.mapping`) reads them
unchanged — no new wire format.

Appends one JSON object per line rather than rewriting a document, so a crashed or
killed session still leaves every span before the crash intact.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from agenttic.hooks.command_risk import classify

#: Where spans land. Overridable so a session can be scoped to one file.
SPOOL_ENV = "AGENTTIC_HOOK_SPOOL"
DEFAULT_SPOOL = "~/.agenttic/hook-spans.jsonl"

#: Tools whose risk class is known from the tool NAME alone. `mutating: False`
#: here is a genuine claim — `Read` really does not mutate — which is different
#: from the silence we emit for an unclassifiable shell command.
_TOOL_RISK: dict[str, dict] = {
    "Read":         {"mutating": False, "irreversible": False},
    "Grep":         {"mutating": False, "irreversible": False},
    "Glob":         {"mutating": False, "irreversible": False},
    "NotebookRead": {"mutating": False, "irreversible": False},
    "TodoWrite":    {"mutating": False, "irreversible": False},
    # writes the model can undo via version control
    "Edit":         {"mutating": True,  "irreversible": False},
    "Write":        {"mutating": True,  "irreversible": False},
    "NotebookEdit": {"mutating": True,  "irreversible": False},
    # network reads: not a repo mutation, but egress — flagged for exfiltration
    "WebFetch":     {"mutating": False, "irreversible": False, "egress": True},
    "WebSearch":    {"mutating": False, "irreversible": False, "egress": True},
}

#: Which agenttic span kind each tool maps to.
_KIND = {
    "Read": "retrieval", "Grep": "retrieval", "Glob": "retrieval",
    "NotebookRead": "retrieval", "WebFetch": "retrieval",
    "WebSearch": "retrieval", "Task": "agent_decision",
    "Agent": "agent_decision",
}

_PATH_KEYS = ("file_path", "path", "notebook_path", "filePath")


def spool_path() -> Path:
    return Path(os.environ.get(SPOOL_ENV) or DEFAULT_SPOOL).expanduser()


def _fingerprint(text: str) -> str:
    """A stable, non-reversible discriminator for a command.

    Enough to tell two commands apart (so repeat-detection works) without
    recording anything that could carry a secret.
    """
    import hashlib
    return "sha256:" + hashlib.sha256(
        text.strip().encode("utf-8")).hexdigest()[:16]


def _entity_of(tool_input: dict) -> str | None:
    for k in _PATH_KEYS:
        v = tool_input.get(k)
        if v:
            return str(v)
    return None


def span_for(payload: dict) -> dict | None:
    """Build one OTLP span from a PostToolUse hook payload.

    Returns ``None`` for payloads with no tool call — nothing is fabricated.
    """
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not tool:
        return None
    tool_input: dict[str, Any] = payload.get("tool_input") or payload.get(
        "toolInput") or {}
    session = (payload.get("session_id") or payload.get("sessionId")
               or "hook-session")

    attrs: dict[str, Any] = {
        "gen_ai.tool.name": tool,
        "agenttic.agent_id": payload.get("agent_id") or "claude-code",
        "agenttic.span_kind": _KIND.get(tool, "tool_call"),
        "agenttic.hook": "PostToolUse",
    }

    entity = _entity_of(tool_input)
    if entity:
        attrs["entity_id"] = entity

    if tool == "Bash":
        # The whole reason this hook exists: only here does the command exist.
        command = str(tool_input.get("command") or "")
        risk = classify(command)
        attrs.update(risk.attributes)
        attrs["agenttic.command.confidence"] = risk.confidence
        # The command itself is NOT recorded — a shell line can contain a token,
        # a connection string or a customer identifier.
        #
        # But a FINGERPRINT must be, and this is not optional: the
        # `never_repeated_identical_tool_call` property keys on
        # `name | json(input)`, so with no input every Bash call collides and four
        # different commands read as one command repeated four times. Recording a
        # digest makes distinct commands distinct without disclosing any of them.
        attrs["gen_ai.tool.call.arguments"] = _fingerprint(command)
        attrs["agenttic.command.head"] = (
            command.strip().split()[0][:40] if command.strip() else "")
    else:
        known = _TOOL_RISK.get(tool)
        if known:
            attrs.update(known)
        else:
            # An unrecognised tool is unknown, never assumed harmless.
            attrs["agenttic.command.risk_reason"] = (
                f"unrecognised tool {tool!r} — risk class not established")
            attrs["agenttic.command.confidence"] = "unknown"

    if payload.get("tool_error") or payload.get("error"):
        attrs["error"] = str(payload.get("tool_error") or payload.get("error"))[:400]

    now_ns = time.time_ns()
    return {
        "traceId": str(session),
        "spanId": f"{now_ns:x}",
        "name": tool,
        "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano": str(now_ns + 1_000_000),
        "attributes": [_attr(k, v) for k, v in attrs.items()],
    }


def _attr(key: str, value: Any) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def record(payload: dict, *, path: Path | None = None) -> bool:
    """Append a span for this hook event. Returns whether one was written.

    Never raises: a hook that breaks the agent it is observing is worse than no
    hook at all.
    """
    try:
        span = span_for(payload)
        if span is None:
            return False
        target = path or spool_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span, separators=(",", ":")) + "\n")
        return True
    except Exception:      # noqa: BLE001 — observation must never break the agent
        return False


def load_spool(path: Path | None = None) -> list[dict]:
    """Read spooled spans, skipping any truncated final line from a hard kill."""
    target = path or spool_path()
    if not target.exists():
        return []
    out: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def as_otlp(spans: list[dict]) -> dict:
    """Wrap spooled spans in the OTLP envelope the importer expects."""
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def main() -> int:
    """Hook entry point: read the event from stdin, append a span, stay quiet."""
    import sys
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:      # noqa: BLE001
        return 0
    record(payload)
    return 0            # never block the tool call
