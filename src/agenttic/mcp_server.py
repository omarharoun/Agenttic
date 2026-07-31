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

Every tool here that returns a closure figure returns ``not_measurable`` beside
it, under the same name and the same ``id -> reason`` shape that ``verify_op``,
the scorecard and ``agenttic ingest verify-traffic`` already use. A dimension
nothing can feed has ``unhit: []`` (you cannot fail to exercise what nobody
observes) and sits outside the closure denominator, so a projection that showed
only ``unhit`` answered *"what is untested?"* with a list that silently omitted
it — over-reporting by omission, and worse over MCP than anywhere else in the
product: an LLM reading this JSON has no other surface to cross-check against,
no table to notice a missing row in, and will act on the list as complete.

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
            "and which properties were never exercised. `closure` is a fraction "
            "of the dimensions that CAN be measured: any dimension listed in "
            "`not_measurable` is outside that denominator and the reason says "
            "why."),
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
            "their antecedent occur. An unexercised property is NOT a pass. "
            "Answers in THREE parts, all of them part of the answer: "
            "`unhit_situations` (reachable — go exercise them), "
            "`not_measurable` (nothing emits the evidence, so no run can close "
            "them; absent from unhit_situations and from the closure "
            "denominator), and `other_drift` (reached, but the model had no bin "
            "for what it saw). `limits` names what this coverage model does not "
            "examine at all. Treating `unhit_situations` alone as the whole "
            "answer understates what is untested."),
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
        # Always present, `{}` when everything was measurable — a field that
        # appears only in the interesting case is a field consumers forget to
        # handle, and the interesting case here is the one that qualifies the
        # number directly above it. `closure` is a fraction OF THE MEASURABLE
        # DIMENSIONS; without this key it reads as a fraction of the model, i.e.
        # a better-looking number describing a smaller space.
        "not_measurable": v.get("not_measurable") or {},
        "properties_checked": a.get("total"),
        "violations": a.get("violations"),
        "violated": a.get("violated_properties") or [],
        "unexercised": a.get("unexercised_properties") or [],
        "action_risk_trustable": v["instrumentation"]["action_risk_trustable"],
        "unclassifiable_tools": v["instrumentation"]["uninstrumented_tools"],
        "scope": v.get("scope_statement"),
        # `scope` says which POPULATION the figure covers; these two say which
        # MODEL produced it. `BASELINE_LIMITS` is written to be "the only copy
        # that travels with the number" (baseline.py:35) precisely so a baseline
        # closure is never read as a fitted one — and this projection was the one
        # surface it did not reach, leaving `0.12 of 0.95` looking like a verdict
        # on intent and policy pressure, which this model does not examine at all.
        "model_ref": v.get("model_ref"),
        "limits": v.get("limits"),
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
        # A measurable coverpoint with nothing unhit is genuinely closed and has
        # nothing to say here. A NOT-measurable one also reports `unhit: []`, for
        # the opposite reason, so this filter dropped it into the same silence —
        # see `not_measurable` below, which is why that key exists.
        "unhit_situations": {
            cp: d.get("unhit") or []
            for cp, d in (v.get("per_coverpoint") or {}).items()
            if d.get("unhit")},
        # The second half of the answer to this tool's own question. Same key,
        # same `coverpoint_id -> reason` shape as `ops.verify_op`, the scorecard
        # and `agenttic ingest verify-traffic`: one vocabulary across every
        # surface, because a caller who learns "not_measurable" from the CLI must
        # not have to learn a second word for it here.
        "not_measurable": v.get("not_measurable") or {},
        # coverpoint -> share of samples that landed in its `other` bin. Found by
        # the same sweep and the same defect: on this hook path `agent_steps`
        # drifts at 1.0 — nothing the hook emits is an `llm_call` span, so every
        # session's step count is UNOBSERVABLE — while `unhit_situations` lists
        # `single_step` and `multi_step` as gaps. Without this key the tool sends
        # the caller to exercise two bins its own instrumentation cannot credit.
        # Drift is a finding about the MODEL (a dimension it is missing or cannot
        # read), not a hole in the runs, so it is reported beside the gap list and
        # never inside it.
        "other_drift": v.get("other_drift") or {},
        # What this coverage MODEL does not examine, in its own words. The largest
        # untested surface here is the one no bin list can show: this is the
        # deterministic baseline, and intent, emotional register and policy
        # pressure are outside it entirely. A tool called `what_is_untested` that
        # omits that is answering a narrower question than it was asked.
        "model_ref": v.get("model_ref"),
        "limits": v.get("limits"),
        # Qualifies the gap list directly above: an unhit `action_risk` bin may be
        # unhit because nothing exercised it, or because the tools that DID
        # exercise it carry no risk class and could not be credited. The warning
        # is the only thing that tells those two apart.
        "warnings": v.get("warnings") or [],
        "note": ("An unexercised property is not a pass — nothing has been shown "
                 "about it. Exercise the situations in `unhit_situations` to make "
                 "the evidence real. `not_measurable` is the other half of the "
                 "answer and no amount of exercising closes it: nothing emits the "
                 "evidence those dimensions read, so they carry no unhit bins, "
                 "they are outside the `closure` denominator, and the fix is "
                 "instrumentation rather than more runs. A coverpoint in "
                 "`other_drift` is a third case: it WAS reached, and the model "
                 "could not classify what it saw."),
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
