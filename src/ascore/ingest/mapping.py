"""Map OTel-GenAI spans → Agenttic Trace/Decision (SPEC-7 Step 35, T35.2).

Consumes the normalized :class:`~ascore.ingest.otel.OtelSpan` records from
:mod:`ascore.ingest.otel` and produces:

* a :class:`~ascore.schema.trace.Trace` per OTLP trace id, with tools and I/O
  **hashes** populated from the GenAI span/event attributes, ``source=
  "otel_ingest"`` provenance, and ``agent_config_hash`` preserved when the
  producer set it (never fabricated);
* a :class:`~ascore.schema.enforcement.Decision` for any span that describes a
  gateway decision (the inverse of :mod:`ascore.enforce.export`).

Invariants:

* **Scorecard exclusion** — ingested traces are saved with ``mode="live"`` so the
  batch scorecard path (which reads ``mode="batch"``) can never include them
  (SPEC-1 Step 9). :func:`ingest_spans` enforces this at save time.
* **Graceful degradation** — a span missing GenAI attributes still maps to a
  partial ``Span`` (no fabricated fields) and is recorded as an
  ``incomplete_span`` note; ingest never raises on a producer's malformed span.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ascore.ingest.otel import OtelSpan, parse_otlp
from ascore.schema.enforcement import Decision
from ascore.schema.trace import Span, SpanKind, Trace

# --- GenAI semantic-convention attribute keys we read ----------------------
A_SYSTEM = "gen_ai.system"
A_OPERATION = "gen_ai.operation.name"
A_REQ_MODEL = "gen_ai.request.model"
A_RESP_MODEL = "gen_ai.response.model"
A_TOOL_NAME = "gen_ai.tool.name"
A_IN_TOKENS = "gen_ai.usage.input_tokens"
A_OUT_TOKENS = "gen_ai.usage.output_tokens"
A_IN_TOKENS_ALT = "gen_ai.usage.prompt_tokens"
A_OUT_TOKENS_ALT = "gen_ai.usage.completion_tokens"
# Agenttic-side identity attributes an adapter/exporter attaches.
A_AGENT_ID = "agenttic.agent_id"
A_AGENT_CFG = "agenttic.agent_config_hash"
A_GENAI_AGENT_ID = "gen_ai.agent.id"

# Enforcement export namespace (inverse of ascore.enforce.export.export_otel).
E_ACTION = "enforcement.action"
E_LANE = "enforcement.lane"
E_CLASS = "enforcement.action_class"
E_FAIL_OPEN = "enforcement.fail_open"
E_POLICY_HASH = "enforcement.policy_hash"
E_DECISION_REF = "enforcement.decision_ref"
E_EVIDENCE = "enforcement.evidence"

_TOOL_OPS = {"execute_tool", "tool", "invoke_tool"}
_LLM_OPS = {"chat", "text_completion", "generate_content", "completion", "embeddings"}


def _sha(content: Any) -> str:
    if isinstance(content, (dict, list)):
        blob = json.dumps(content, sort_keys=True, default=str)
    else:
        blob = str(content)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _first(attrs: dict, *keys: str) -> Any:
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def is_decision_span(sp: OtelSpan) -> bool:
    return E_ACTION in sp.attributes


def infer_kind(sp: OtelSpan) -> SpanKind:
    a = sp.attributes
    op = str(a.get(A_OPERATION, "")).lower()
    name = (sp.name or "").lower()
    status_code = sp.status.get("code")
    if is_decision_span(sp) or A_TOOL_NAME in a or op in _TOOL_OPS \
            or name.startswith("execute_tool"):
        return "tool_call"
    if A_REQ_MODEL in a or A_RESP_MODEL in a or op in _LLM_OPS or op == "text_completion":
        return "llm_call"
    if op in {"retrieval", "search"} or "db.system" in a or "retriev" in name:
        return "retrieval"
    if sp.status.get("code") in (2, "STATUS_CODE_ERROR") or status_code == "ERROR":
        return "error"
    if "final" in name:
        return "final_output"
    return "agent_decision"


# Event-name heuristics for locating request/response content.
_REQ_HINTS = ("prompt", "user", "input", "tool.call", "system", "request")
_RESP_HINTS = ("completion", "assistant", "tool.message", "output", "result",
               "choice", "response")


def _collect_io(sp: OtelSpan) -> tuple[list, list]:
    """Gather request-side and response-side content parts from span attributes
    and events, using the GenAI content conventions. Returns (inputs, outputs)."""
    inputs: list = []
    outputs: list = []

    # span-level content attributes (older convention)
    for k in ("gen_ai.prompt", "gen_ai.tool.call.arguments", "gen_ai.request.body"):
        v = sp.attributes.get(k)
        if v not in (None, ""):
            inputs.append(v)
    for k in ("gen_ai.completion", "gen_ai.response.body"):
        v = sp.attributes.get(k)
        if v not in (None, ""):
            outputs.append(v)

    for ev in sp.events:
        ename = str(ev.get("name", "")).lower()
        payload = ev.get("attributes", {}) or {}
        # any content-ish value in the event
        content = None
        for ck in ("content", "gen_ai.tool.message.content", "gen_ai.prompt",
                   "gen_ai.completion", "message", "body", "arguments"):
            for key, val in payload.items():
                if key.endswith(ck) or key == ck:
                    content = val
                    break
            if content is not None:
                break
        if content is None and payload:
            content = payload  # whole event payload as a fallback
        if content is None:
            continue
        if any(h in ename for h in _RESP_HINTS):
            outputs.append(content)
        elif any(h in ename for h in _REQ_HINTS):
            inputs.append(content)
        else:
            # unknown event: attribute to input side (a request-ish default)
            inputs.append(content)
    return inputs, outputs


def map_span(sp: OtelSpan) -> tuple[Span, bool]:
    """Map one OtelSpan → (Span, is_incomplete). Never fabricates fields."""
    kind = infer_kind(sp)
    a = sp.attributes
    tool_name = a.get(A_TOOL_NAME)

    inputs, outputs = _collect_io(sp)
    span_input: dict = {}
    span_output: dict = {}
    if tool_name:
        span_input["tool_name"] = tool_name
    if inputs:
        span_input["content_sha256"] = _sha(inputs)
        span_input["parts"] = len(inputs)
    if outputs:
        span_output["content_sha256"] = _sha(outputs)
        span_output["parts"] = len(outputs)

    tokens_in = _int_or_none(_first(a, A_IN_TOKENS, A_IN_TOKENS_ALT))
    tokens_out = _int_or_none(_first(a, A_OUT_TOKENS, A_OUT_TOKENS_ALT))

    # A span with no GenAI/enforcement attributes and no content is "incomplete":
    # we keep it (partial trace) but flag it and fabricate nothing.
    has_genai = any(k.startswith("gen_ai.") for k in a) or is_decision_span(sp)
    incomplete = not has_genai and not inputs and not outputs

    attributes = dict(a)  # preserve everything the producer sent
    if incomplete:
        attributes["agenttic.ingest.incomplete"] = True

    err = sp.status.get("message") if sp.status.get("code") in (2, "STATUS_CODE_ERROR") else None

    span = Span(
        span_id=sp.span_id,
        parent_id=sp.parent_id,
        kind=kind,
        name=sp.name or (tool_name or kind),
        start_time=sp.start_time(),
        end_time=sp.end_time(),
        input=span_input,
        output=span_output,
        error=err,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=None,
        attributes=attributes,
    )
    return span, incomplete


def map_decision(sp: OtelSpan, agent_id: str) -> Decision | None:
    """Map an enforcement-decision span (inverse of enforce.export) → Decision."""
    a = sp.attributes
    action = a.get(E_ACTION)
    if action not in {"allow", "transform", "require_approval", "deny",
                      "terminate_session", "revoke_access"}:
        return None
    lane = a.get(E_LANE) if a.get(E_LANE) in {"lane1", "lane2", "lane3"} else "lane1"
    action_class = a.get(E_CLASS) if a.get(E_CLASS) in {"read", "write", "unknown"} else "unknown"
    evidence = a.get(E_EVIDENCE)
    if isinstance(evidence, str):
        evidence = [evidence]
    elif not isinstance(evidence, list):
        evidence = []
    op = str(a.get(A_OPERATION, "")).lower()
    phase = "tool_result" if "result" in op or "result" in (sp.name or "").lower() else "tool_call"
    return Decision(
        decision_id=(a.get(E_DECISION_REF) or f"ingested:{sp.span_id}").replace("decision:", ""),
        session_id=sp.trace_id or "otel_ingest",
        agent_id=agent_id,
        phase=phase,
        action=action,
        lane=lane,
        tool_name=str(a.get(A_TOOL_NAME, "")),
        action_class=action_class,
        evidence=[str(e) for e in evidence],
        fail_open=bool(a.get(E_FAIL_OPEN, False)),
        policy_hash=str(a.get(E_POLICY_HASH, "")),
    )


def _agent_identity(spans: list[OtelSpan]) -> tuple[str, str]:
    """Derive (agent_id, agent_config_hash) from the group's attributes. The
    config hash is preserved only when a span actually carries it — never
    invented (an empty string means 'unknown', honestly)."""
    agent_id = ""
    cfg_hash = ""
    for sp in spans:
        merged = {**sp.resource_attributes, **sp.attributes}
        agent_id = agent_id or str(_first(merged, A_AGENT_ID, A_GENAI_AGENT_ID,
                                           "service.name") or "")
        cfg_hash = cfg_hash or str(merged.get(A_AGENT_CFG, "") or "")
    return (agent_id or "otel-ingested-agent", cfg_hash)


def spans_to_traces(spans: list[OtelSpan]) -> tuple[list[Trace], list[Decision], dict]:
    """Group normalized spans by trace id and build Trace + Decision objects.

    Returns (traces, decisions, report) where report carries counts and the
    ``incomplete_spans`` / ``notes`` produced during graceful degradation."""
    groups: dict[str, list[OtelSpan]] = {}
    for sp in spans:
        groups.setdefault(sp.trace_id or sp.span_id, []).append(sp)

    traces: list[Trace] = []
    decisions: list[Decision] = []
    incomplete: list[str] = []
    notes: list[str] = []

    for trace_id, group in groups.items():
        group.sort(key=lambda s: s.start_ns)
        agent_id, cfg_hash = _agent_identity(group)
        built_spans: list[Span] = []
        final_output = ""
        for sp in group:
            try:
                span, is_incomplete = map_span(sp)
            except Exception as e:  # never crash on a producer's span
                notes.append(f"skipped_span:{sp.span_id}:{type(e).__name__}")
                continue
            built_spans.append(span)
            if is_incomplete:
                incomplete.append(sp.span_id)
                notes.append(f"incomplete_span:{sp.span_id}")
            if span.kind == "final_output" and not final_output:
                fo = sp.attributes.get("gen_ai.completion") or sp.name
                final_output = str(fo)
            if is_decision_span(sp):
                dec = map_decision(sp, agent_id)
                if dec is not None:
                    decisions.append(dec)
        if not built_spans:
            notes.append(f"empty_trace:{trace_id}")
            continue
        if not final_output:
            # fall back to the last span's output hash reference, else its name
            last = built_spans[-1]
            final_output = last.output.get("content_sha256", "") or last.name

        trace = Trace(
            trace_id=trace_id,
            agent_id=agent_id,
            agent_config_hash=cfg_hash,
            test_case_id=None,          # live/production trace
            spans=built_spans,
            visibility="glass_box",
            final_output=final_output,
            total_steps=len(built_spans),
            source="otel_ingest",       # provenance (SPEC-7 Step 35)
        )
        traces.append(trace)

    report = {
        "trace_count": len(traces),
        "decision_count": len(decisions),
        "incomplete_spans": incomplete,
        "notes": notes,
    }
    return traces, decisions, report


def ingest_spans(reg, spans: list[OtelSpan], *, save: bool = True) -> dict:
    """Map spans → Traces/Decisions and (by default) persist the traces.

    **Scorecard-exclusion invariant**: traces are saved with ``mode="live"`` so
    they are structurally excluded from batch certification scorecards. Ingested
    Decisions are returned but NOT written into the enforcement log — ingest
    observes; it must not fabricate gateway history (Hard Rule 31)."""
    traces, decisions, report = spans_to_traces(spans)
    saved = []
    if save:
        for t in traces:
            # Defense in depth: an ingested trace is always live-provenanced.
            assert t.source == "otel_ingest"
            reg.save_trace(t, mode="live")
            saved.append(t.trace_id)
    report["saved_trace_ids"] = saved
    report["traces"] = traces
    report["decisions"] = decisions
    return report


def ingest_otlp_payload(reg, payload: dict, *, save: bool = True) -> dict:
    """Parse an OTLP payload and ingest it in one call (endpoint/CLI helper)."""
    return ingest_spans(reg, parse_otlp(payload), save=save)
