"""`agenttic doctor` — verify zero-touch OTel setup in one command (SPEC-8 T44.2).

Two checks, either or both:

* :func:`diagnose_payload` — validate a *captured* OTLP span stream offline: does
  it parse, and what does Agenttic capture from it (llm/tool steps) vs not?
* :func:`probe_target` — POST a synthetic probe span to a target ingest endpoint
  and confirm it is reachable and parses spans (0 rejected).

Both are honest about failure: a malformed payload or an unreachable or
mis-encoded endpoint yields a *specific, actionable* problem, never a vague OK.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

from ascore.ingest.emit import SpanEmitter
from ascore.ingest.mapping import spans_to_traces
from ascore.ingest.otel import parse_otlp

_PROBE_AGENT = "agenttic-doctor-probe"


# --- offline: validate a captured span stream ------------------------------
def diagnose_payload(payload: Any) -> dict:
    """Parse a captured OTLP payload and report what Agenttic would capture.
    Never raises — a parse failure is returned as an actionable problem."""
    problems: list[str] = []
    try:
        spans = parse_otlp(payload)
    except Exception as e:  # noqa: BLE001 — report, don't crash
        return {"ok": False, "spans": 0, "traces": 0, "llm_calls": 0,
                "tool_calls": 0, "incomplete": 0, "agents": [],
                "problems": [
                    f"could not parse OTLP payload ({type(e).__name__}: {e}). "
                    "Expected an OTLP/HTTP JSON ExportTraceServiceRequest, i.e. "
                    '{"resourceSpans": [...]}.']}
    if not spans:
        return {"ok": False, "spans": 0, "traces": 0, "llm_calls": 0,
                "tool_calls": 0, "incomplete": 0, "agents": [],
                "problems": [
                    "no spans found in the payload — check the exporter targets "
                    "/v1/traces with JSON encoding and that your app produced "
                    "spans."]}

    traces, _decisions, rep = spans_to_traces(spans)
    llm = sum(1 for t in traces for s in t.spans if s.kind == "llm_call")
    tool = sum(1 for t in traces for s in t.spans if s.kind == "tool_call")
    incomplete = len(rep.get("incomplete_spans", []))
    agents = sorted({t.agent_id for t in traces})

    ok = len(traces) > 0
    if not ok:
        problems.append(
            "spans parsed but produced no traces — they are likely missing "
            "trace ids.")
    if ok and llm == 0 and tool == 0:
        problems.append(
            "spans parsed but none are GenAI llm/tool spans — Agenttic captures "
            "gen_ai.* spans; other spans are kept as partial/incomplete and "
            "stay NOT ASSESSED.")
    return {"ok": ok, "spans": len(spans), "traces": len(traces),
            "llm_calls": llm, "tool_calls": tool, "incomplete": incomplete,
            "agents": agents, "problems": problems}


# --- online: probe a target ingest endpoint --------------------------------
def probe_payload() -> dict:
    """A minimal, valid OTLP/JSON payload with one benign GenAI span."""
    em = SpanEmitter(_PROBE_AGENT, scope_name="agenttic_doctor", sink=[])
    em.emit_llm_call(system="agenttic", model="probe", prompt="doctor probe",
                     completion="ok", input_tokens=1, output_tokens=1)
    em.flush()
    return em.sink[0]


def _urllib_poster(url: str, payload: dict, auth_header: str | None):
    target = url.rstrip("/")
    if not target.endswith("/v1/traces"):
        target = target + "/v1/traces"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        target, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    if auth_header:
        req.add_header("Authorization", auth_header)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8") or "{}"
        try:
            parsed = json.loads(body)
        except Exception:  # noqa: BLE001
            parsed = {"_raw": body}
        return resp.status, parsed


def probe_target(url: str, *, auth_header: str | None = None,
                 poster: Callable[..., tuple[int, Any]] | None = None) -> dict:
    """POST a probe span to ``url`` and confirm it is accepted and parsed.
    ``poster`` is injectable for tests; it returns ``(status_code, body)``."""
    poster = poster or _urllib_poster
    try:
        status, body = poster(url, probe_payload(), auth_header)
    except Exception as e:  # noqa: BLE001 — network failures are expected
        return {"ok": False, "problems": [
            f"could not reach {url} ({type(e).__name__}: {e}). Is the target "
            "URL correct and the server running?"]}

    if status == 415:
        return {"ok": False, "status": status, "problems": [
            "target rejected OTLP/protobuf — Agenttic ingest speaks OTLP/HTTP "
            "JSON; set the exporter encoding to json (OTEL_EXPORTER_OTLP_"
            "TRACES_PROTOCOL=http/json)."]}
    if not (200 <= status < 300):
        return {"ok": False, "status": status, "problems": [
            f"target returned HTTP {status}: {str(body)[:200]}"]}

    rejected = 0
    if isinstance(body, dict):
        rejected = int((body.get("partialSuccess") or {}).get("rejectedSpans", 0) or 0)
    if rejected:
        return {"ok": False, "status": status, "rejected": rejected, "problems": [
            f"target is reachable but rejected {rejected} probe span(s) — the "
            "endpoint accepted the request yet did not parse the spans as "
            "expected."]}
    return {"ok": True, "status": status, "problems": []}
