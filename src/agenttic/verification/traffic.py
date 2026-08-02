"""Closure over PRODUCTION TRAFFIC, not just an authored suite.

Nobody hand-writes 95% of a situation space. Against real data, suite closure sits
around 20% and never closes: `timeout`, `rate_limited`, `escalated_to_human`,
`budget_exceeded`, `entity_not_found`, `mutating_irreversible` are things that
happen in production and almost never in a test suite.

Production traffic exercises that space continuously. The OTel ingest already
imports it (``source="otel_ingest"``, stored ``mode="live"``) — it was simply never
verified. This module measures the same coverage model and the same safety
properties over that population instead, which turns a suite's weakness into the
strongest claim the platform can make: *closed over N days of real traffic* beats
*closed over 40 authored cases* for any reader.

**The honesty problem this module refuses to paper over.** Ingested spans usually
come from someone else's instrumentation, so most carry no mutation semantics.
:func:`agenttic.verification.builtins.is_write` falls back to tool-NAME hints, so
an uninstrumented tool called ``process_request`` looks read-only and would be
silently credited to ``action_risk.read_only`` — a coverage credit for a question
that was never actually answered. So every tool span is classified by CONFIDENCE
(borrowed from graphify's EXTRACTED/INFERRED/AMBIGUOUS edge labels), and closure
over traffic is reported together with the fidelity of the instrumentation behind
it. An unknown classification is never a read-only credit.
"""

from __future__ import annotations

from typing import Any

#: How a tool span's risk class was established.
#: ``explicit``  — the producer instrumented ``mutating`` / ``irreversible``
#: ``inferred``  — no attribute; the tool NAME matched a write/irreversible hint
#: ``unknown``   — no attribute and no hint matched. Not evidence of read-only.
Confidence = str

_RISK_ATTRS = ("mutating", "irreversible")


def _is_tool(span: Any) -> bool:
    return getattr(span, "kind", "") == "tool_call"


def classify_confidence(span: Any) -> Confidence:
    """How well do we actually know this span's risk class?"""
    from agenttic.verification.builtins import is_irreversible, is_write

    attrs = getattr(span, "attributes", None) or {}
    if any(attrs.get(k) is not None for k in _RISK_ATTRS):
        return "explicit"
    if is_write(span) or is_irreversible(span):
        return "inferred"          # matched on the tool name alone
    return "unknown"               # silence, not a read-only guarantee


def instrumentation_fidelity(traces: list) -> dict:
    """How much of this traffic can be trusted for action-risk coverage.

    Reported alongside closure so a high ``read_only`` figure over
    badly-instrumented traffic cannot be mistaken for evidence that the agent
    does not mutate anything.
    """
    counts = {"explicit": 0, "inferred": 0, "unknown": 0}
    unknown_tools: dict[str, int] = {}
    tool_spans = 0
    ingested = 0
    incomplete_spans = 0

    for t in traces:
        if getattr(t, "source", "native") == "otel_ingest":
            ingested += 1
        for s in getattr(t, "spans", None) or []:
            attrs = getattr(s, "attributes", None) or {}
            if attrs.get("agenttic.ingest.incomplete"):
                incomplete_spans += 1
            if not _is_tool(s):
                continue
            tool_spans += 1
            conf = classify_confidence(s)
            counts[conf] += 1
            if conf == "unknown":
                name = getattr(s, "name", "") or "<unnamed>"
                unknown_tools[name] = unknown_tools.get(name, 0) + 1

    trusted = counts["explicit"] + counts["inferred"]
    return {
        "n_traces": len(traces),
        "n_ingested": ingested,
        "tool_spans": tool_spans,
        "by_confidence": counts,
        "incomplete_spans": incomplete_spans,
        "action_risk_trustable": (
            round(trusted / tool_spans, 4) if tool_spans else 0.0),
        # the actionable output: instrument THESE and action_risk becomes real
        "uninstrumented_tools": sorted(
            unknown_tools.items(), key=lambda kv: -kv[1])[:20],
        "note": (
            "action_risk is only as good as the mutation semantics on the spans. "
            "Tools listed in uninstrumented_tools carry neither a mutating/"
            "irreversible attribute nor a recognisable name, so nothing here is "
            "evidence that they are read-only."
            if counts["unknown"] else
            "every tool span carried a usable risk class"),
    }


def verify_traffic(traces: list, *, cfg: dict | None = None) -> dict:
    """Run the verification layer over a population of production traces.

    Returns the normal verification summary plus an ``instrumentation`` block and
    a ``scope`` sentence stating what the closure figure is a claim about. Same
    coverage model and same properties as a suite run — only the population
    differs, which is the entire point.
    """
    from agenttic.metrics.runner import verify_run

    out = verify_run(traces, cfg=cfg)
    fidelity = instrumentation_fidelity(traces)
    out["instrumentation"] = fidelity
    out["population"] = "production_traffic"

    if out.get("status") != "populated":
        return out

    # State plainly what the number covers. A closure figure with no stated
    # population is the unscoped claim this platform exists to refuse.
    closure = out.get("trace_closure")
    out["scope_statement"] = (
        f"Closure of {closure:.1%} measured over {fidelity['n_traces']} production "
        f"trace(s) ({fidelity['n_ingested']} ingested from external "
        f"instrumentation), not over an authored suite.")
    if fidelity["by_confidence"]["unknown"]:
        out.setdefault("warnings", []).append(
            f"{fidelity['by_confidence']['unknown']} of {fidelity['tool_spans']} "
            "tool span(s) carry no usable risk class — action_risk coverage over "
            "this traffic is not trustworthy until they are instrumented")
    if fidelity["incomplete_spans"]:
        out.setdefault("warnings", []).append(
            f"{fidelity['incomplete_spans']} span(s) were flagged incomplete at "
            "ingest, so their contribution to closure is weaker than it appears")
    return out


def traffic_window(reg, *, agent_id: str, limit: int | None = None) -> list:
    """The live/ingested traces for an agent — the population to verify.

    Deliberately reads ``mode="live"``: ingested traces are stored live precisely
    so they can never enter batch certification scorecards. Verifying them is a
    different and legitimate use — measuring what the agent has actually been
    observed doing, rather than certifying a suite result.
    """
    traces = reg.traces(agent_id, mode="live")
    if limit is not None:
        traces = traces[-int(limit):]
    return traces
