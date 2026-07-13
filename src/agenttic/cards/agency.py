"""Covered-agent (agency) detector (SPEC-2 T20.3).

Decides whether an agent is a *covered agent* — an agentic system that takes
consequential, unattended actions — from trace evidence:

* **True** — ≥3 autonomous (non-approval-gated) tool calls **and** at least one
  write-class action **and** the agent exercised tool choice, with evidence refs.
* **False** — the evidence *contradicts* agency: ample traces but no unattended
  write action (all read-only or all approval-gated).
* **None** — sparse evidence: too little to decide either way (never guessed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenttic.cards.autonomy import _span_is_approval_gated, _write_actions

MIN_AUTONOMOUS_CALLS = 3
MIN_TRACES_FOR_CONTRADICTION = 3


@dataclass
class CoveredAgentResult:
    covered: bool | None
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""


def detect_covered_agent(reg, agent_id: str, cfg: dict) -> CoveredAgentResult:
    try:
        traces = list(reg.traces(agent_id, mode="batch"))
    except Exception:  # noqa: BLE001
        traces = []
    if not traces:
        return CoveredAgentResult(covered=None, reason="no traces — sparse evidence")

    writes = _write_actions(cfg)
    autonomous_calls = 0
    autonomous_writes = 0
    distinct_tools: set[str] = set()
    refs: list[str] = []
    for t in traces:
        cited = False
        for span in t.spans:
            if span.kind != "tool_call":
                continue
            if _span_is_approval_gated(span):
                continue  # not autonomous
            autonomous_calls += 1
            distinct_tools.add(span.name)
            attrs = span.attributes or {}
            if span.name in writes or attrs.get("action_class") == "write":
                autonomous_writes += 1
            if not cited:
                refs.append(f"trace:{t.trace_id}")
                cited = True

    tool_choice = len(distinct_tools) >= 1

    if (autonomous_calls >= MIN_AUTONOMOUS_CALLS and autonomous_writes >= 1
            and tool_choice):
        return CoveredAgentResult(
            covered=True, evidence_refs=refs[:20],
            reason=(f"{autonomous_calls} autonomous tool calls, "
                    f"{autonomous_writes} write action(s), tool choice exercised"))

    # contradiction: enough traces to have shown agency, but none of it is
    # unattended write action → definitively not a covered agent.
    if len(traces) >= MIN_TRACES_FOR_CONTRADICTION and autonomous_writes == 0:
        return CoveredAgentResult(
            covered=False, evidence_refs=refs[:20],
            reason=("ample traces but no unattended write action "
                    "(read-only or approval-gated)"))

    return CoveredAgentResult(
        covered=None,
        reason=(f"sparse: {autonomous_calls} autonomous calls, "
                f"{autonomous_writes} writes over {len(traces)} traces"))
