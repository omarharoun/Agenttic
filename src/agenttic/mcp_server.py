"""Agenttic as an MCP server — so an agent can ask about its own evidence.

The hook is retrospective: it records what happened and you read a report later.
This is the other direction. An agent (or the operator driving it) can query the
verification layer **mid-session**:

* ``classify_command`` — *before* running a shell command, ask whether it is
  irreversible. This is the only tool here that can prevent a defect rather than
  report one, and it is the whole reason an MCP surface is worth having.
* ``verify_session`` — closure and violated properties over what has been
  captured so far.
* ``what_is_untested`` — the unhit bins and unexercised properties, i.e. the list
  of things to go and exercise. The actionable half of a coverage report.

Deliberately no ``mcp`` SDK dependency, matching
:mod:`agenttic.adapters.mcp_server` (the client that probes *other* servers): a
hand-rolled JSON-RPC 2.0 stdio loop is ~100 lines, adds no supply-chain surface to
a tool whose whole value proposition is supply-chain honesty, and cannot be broken
by an SDK normalising the frames we care about.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agenttic"

TOOLS: list[dict] = [
    {
        "name": "classify_command",
        "description": (
            "Classify a shell command's risk BEFORE running it. Returns mutating "
            "/ irreversible / unknown with a reason. `unknown` means the effect "
            "could not be determined from the command line — treat it as unsafe, "
            "not as safe. Use this to decide whether to ask for confirmation."),
        "inputSchema": {
            "type": "object",
            "properties": {"command": {
                "type": "string",
                "description": "the exact shell command you are about to run"}},
            "required": ["command"],
        },
    },
    {
        "name": "verify_session",
        "description": (
            "Coverage closure and safety-property results over the tool calls "
            "captured so far by the agenttic hook. Reports violated properties "
            "and which properties were never exercised."),
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {
                "type": "string",
                "description": "optional: restrict to one agent id"}},
        },
    },
    {
        "name": "what_is_untested",
        "description": (
            "The actionable half of a coverage report: which situations have "
            "never been exercised and which safety properties have never had "
            "their antecedent occur. An unexercised property is NOT a pass."),
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        },
    },
]


# --------------------------------------------------------------------------- #
# tool implementations
# --------------------------------------------------------------------------- #

def _classify_command(args: dict) -> dict:
    from agenttic.hooks.command_risk import classify
    risk = classify(str(args.get("command") or ""))
    verdict = (
        "IRREVERSIBLE — confirm with the user before running this"
        if risk.irreversible else
        "mutating but recoverable" if risk.mutating else
        "read-only" if risk.mutating is False else
        "UNKNOWN — effect not determinable; treat as unsafe and confirm")
    return {
        "mutating": risk.mutating,
        "irreversible": risk.irreversible,
        "confidence": risk.confidence,
        "reason": risk.reason,
        "verdict": verdict,
        "should_confirm": bool(risk.irreversible) or risk.confidence == "unknown",
    }


def _load_traces(agent_id: str = ""):
    from agenttic.hooks.claude_code import as_otlp, load_spool
    from agenttic.ingest.mapping import spans_to_traces
    from agenttic.ingest.otel import parse_otlp

    spans = load_spool()
    if not spans:
        return [], 0
    traces, _d, _r = spans_to_traces(parse_otlp(as_otlp(spans)))
    if agent_id:
        traces = [t for t in traces if t.agent_id == agent_id]
    return traces, len(spans)


def _verify_session(args: dict) -> dict:
    from agenttic.verification.traffic import verify_traffic

    traces, n_spans = _load_traces(str(args.get("agent_id") or ""))
    if not traces:
        return {"status": "no_data",
                "note": "no tool calls captured yet — is `agenttic hook install` "
                        "wired into PostToolUse?"}
    v = verify_traffic(traces)
    if v.get("status") != "populated":
        return {"status": v.get("status"), "note": v.get("note")}
    a = v.get("assertions") or {}
    return {
        "status": "populated",
        "tool_calls": n_spans,
        "sessions": v["n_traces"],
        "closure": v["trace_closure"],
        "closure_target": v["closure_target"],
        "closed": v["closed"],
        "properties_checked": a.get("total"),
        "violations": a.get("violations"),
        "violated": a.get("violated_properties") or [],
        "unexercised": a.get("unexercised_properties") or [],
        "action_risk_trustable": v["instrumentation"]["action_risk_trustable"],
        "unclassifiable_tools": v["instrumentation"]["uninstrumented_tools"],
        "scope": v.get("scope_statement"),
        "warnings": v.get("warnings") or [],
    }


def _what_is_untested(args: dict) -> dict:
    from agenttic.verification.traffic import verify_traffic

    traces, _n = _load_traces(str(args.get("agent_id") or ""))
    if not traces:
        return {"status": "no_data", "note": "no tool calls captured yet"}
    v = verify_traffic(traces)
    if v.get("status") != "populated":
        return {"status": v.get("status"), "note": v.get("note")}
    a = v.get("assertions") or {}
    return {
        "status": "populated",
        "closure": v["trace_closure"],
        "never_exercised_properties": a.get("unexercised_properties") or [],
        "unhit_situations": {
            cp: d.get("unhit") or []
            for cp, d in (v.get("per_coverpoint") or {}).items()
            if d.get("unhit")},
        "note": ("An unexercised property is not a pass — nothing has been shown "
                 "about it. Exercise these situations to make the evidence real."),
    }


_HANDLERS = {
    "classify_command": _classify_command,
    "verify_session": _verify_session,
    "what_is_untested": _what_is_untested,
}


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 over stdio
# --------------------------------------------------------------------------- #

def handle(request: dict) -> dict | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = request.get("method")
    rid = request.get("id")

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        handler = _HANDLERS.get(name)
        if handler is None:
            return _err(rid, -32602, f"unknown tool {name!r}")
        try:
            result = handler(params.get("arguments") or {})
        except Exception as exc:      # noqa: BLE001 — report, never crash the loop
            return _ok(rid, {"isError": True, "content": [
                {"type": "text", "text": f"{type(exc).__name__}: {exc}"}]})
        return _ok(rid, {"content": [
            {"type": "text", "text": json.dumps(result, indent=2, default=str)}]})
    if rid is None:
        return None                   # unknown notification: ignore
    return _err(rid, -32601, f"unknown method {method!r}")


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _version() -> str:
    try:
        from agenttic import __version__
        return __version__
    except Exception:      # noqa: BLE001
        return "0"


def main() -> int:
    """Newline-delimited JSON-RPC on stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(
                _err(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
