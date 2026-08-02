"""Join a DRIVEN run to the spans the agent exported about itself.

This is the piece that makes glass-box evidence possible with **no adapter code
at all**, and it was the one thing missing from the general path.

The two halves already existed and could not meet:

* driving — ``BlackBoxHTTPAgent`` + ``connect.Mapping`` runs any HTTP agent from
  config, and sees only the reply text. ``scoring/engine.py`` drops three checks
  for a black-box target while seven registered checks read tool spans, four of
  them with no text fallback, so a correct answer can score 0.0.
* observing — ``ingest/mapping.py`` already speaks the OpenTelemetry GenAI
  conventions and preserves every attribute a producer sends. But it observes
  traffic in the past tense; nothing tied a span to the CASE that caused it.

The missing link is one attribute. ``gen_ai.conversation.id`` is the semantic
convention's own correlation key: the harness mints one per case, hands it to
the agent, and the agent stamps it on the spans it exports. Then the run and the
telemetry are the same event seen twice, and this module joins them.

The honest part
---------------
A join that finds nothing must SAY nothing was found. The failure mode this
guards against is an upgrade-by-default: a black-box trace relabelled glass-box
because correlation was switched on, with no spans behind it — a trajectory
claim resting on evidence that never arrived. So:

* visibility is upgraded ONLY when observed spans actually landed;
* every attached span is marked ``observed_via="otel"``, so a reader can always
  tell what the harness saw from what the agent said about itself;
* the outcome is reported either way, including "the agent exported nothing".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agenttic.schema.trace import Span, Trace

#: OpenTelemetry GenAI semantic convention. The agent under test must stamp this
#: on the spans it exports; the harness supplies the value per case.
CONVERSATION_ID = "gen_ai.conversation.id"

#: Marks a span the AGENT reported about itself, as opposed to one the harness
#: observed directly. Never merge the two silently — provenance is the product.
OBSERVED_VIA = "observed_via"


def new_conversation_id(test_case_id: str | None = None) -> str:
    """Mint a correlation id for one case.

    Random rather than derived from the case id: two runs of the same case must
    not collide, or trial 2 would attach trial 1's telemetry and pass^k would be
    measuring one execution twice.
    """
    return uuid.uuid4().hex


def conversation_id_of(obj) -> str:
    """Read the correlation id off a Trace or a Span. ``""`` when absent."""
    attrs = getattr(obj, "attributes", None)
    if isinstance(attrs, dict) and attrs.get(CONVERSATION_ID):
        return str(attrs[CONVERSATION_ID])
    for span in getattr(obj, "spans", None) or ():
        sa = getattr(span, "attributes", None) or {}
        if sa.get(CONVERSATION_ID):
            return str(sa[CONVERSATION_ID])
    return ""


@dataclass
class CorrelationResult:
    """What the join actually found — reported whether or not it found anything."""

    trace: Trace
    conversation_id: str = ""
    attached_spans: int = 0
    attached_from_traces: int = 0
    upgraded_visibility: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def observed(self) -> bool:
        return self.attached_spans > 0

    def as_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "attached_spans": self.attached_spans,
            "attached_from_traces": self.attached_from_traces,
            "upgraded_visibility": self.upgraded_visibility,
            "observed": self.observed,
            "notes": list(self.notes),
        }


def observed_spans_for(conversation_id: str, candidates: list[Trace]) -> list[Span]:
    """Every span from ``candidates`` carrying this conversation id.

    Matches per SPAN, not per trace: an exporter may batch several conversations
    into one OTel trace, and taking the whole trace would attach another case's
    work to this one.
    """
    if not conversation_id:
        return []
    out: list[Span] = []
    for tr in candidates or ():
        for sp in getattr(tr, "spans", None) or ():
            attrs = getattr(sp, "attributes", None) or {}
            if str(attrs.get(CONVERSATION_ID) or "") == conversation_id:
                out.append(sp)
    return out


def correlate(driven: Trace, candidates: list[Trace]) -> CorrelationResult:
    """Attach the agent's own exported spans to the run that caused them."""
    cid = conversation_id_of(driven)
    res = CorrelationResult(trace=driven, conversation_id=cid)

    if not cid:
        res.notes.append(
            "this run carried no gen_ai.conversation.id, so nothing could be "
            "correlated to it — the adapter must stamp one to enable this")
        return res

    matched = observed_spans_for(cid, candidates)
    if not matched:
        res.notes.append(
            f"no exported spans carried gen_ai.conversation.id={cid}: either the "
            "agent is not instrumented, or its telemetry has not been ingested "
            "yet. The trace is unchanged and its visibility is NOT upgraded.")
        return res

    have = {s.span_id for s in driven.spans}
    fresh = []
    for sp in matched:
        if sp.span_id in have:
            continue                       # already the harness's own record
        fresh.append(sp.model_copy(update={
            "attributes": {**(sp.attributes or {}), OBSERVED_VIA: "otel"}}))
    if not fresh:
        res.notes.append(
            "every exported span was already present in the driven trace; "
            "nothing was added")
        return res

    n_traces = sum(1 for tr in candidates
                   if any(str((getattr(s, "attributes", None) or {}).get(
                       CONVERSATION_ID) or "") == cid
                       for s in getattr(tr, "spans", None) or ()))

    spans = sorted([*driven.spans, *fresh], key=lambda s: s.start_time)
    upgrade = driven.visibility == "black_box" and any(
        s.kind in ("tool_call", "retrieval") for s in fresh)

    res.trace = driven.model_copy(update={
        "spans": spans,
        "visibility": "glass_box" if upgrade else driven.visibility,
        "total_steps": sum(1 for s in spans if s.kind == "tool_call"),
    })
    res.attached_spans = len(fresh)
    res.attached_from_traces = n_traces
    res.upgraded_visibility = upgrade
    if upgrade:
        res.notes.append(
            f"{len(fresh)} exported span(s) carried tool calls, so this trace is "
            "now glass-box: the trajectory checks that have no text fallback can "
            "read real evidence instead of scoring a black box")
    else:
        res.notes.append(f"{len(fresh)} exported span(s) attached")
    return res


def correlate_all(driven: list[Trace], candidates: list[Trace]) -> tuple[list[Trace], dict]:
    """Correlate a whole run. Returns (traces, a reportable summary)."""
    out, results = [], []
    for tr in driven:
        r = correlate(tr, candidates)
        out.append(r.trace)
        results.append(r)
    observed = sum(1 for r in results if r.observed)
    return out, {
        "correlated": observed,
        "of_traces": len(results),
        "attached_spans": sum(r.attached_spans for r in results),
        "upgraded_to_glass_box": sum(1 for r in results if r.upgraded_visibility),
        "uncorrelated": [r.conversation_id or "(none)"
                         for r in results if not r.observed][:20],
        "note": (
            "no runs were correlated because none were supplied — this is the "
            "absence of evidence, not evidence that nothing was exported"
            if not results else
            "every driven trace was matched to the agent's own exported spans"
            if observed == len(results) else
            f"{len(results) - observed} of {len(results)} run(s) had no exported "
            "spans to attach; their trajectory evidence is only what the harness "
            "saw directly"),
    }
