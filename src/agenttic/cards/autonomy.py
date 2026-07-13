"""Autonomy classifier (SPEC-2 T20.2).

Classifies an agent's autonomy on the L1–L5 ladder (config
``cards.autonomy.levels``: operator / collaborator / consultant / approver /
autonomous) **conservatively** from trace evidence:

* no trace evidence ⇒ **None** (never guess — unclassifiable is not a level);
* tool use gated by approval ⇒ **≤ L3** (consultant);
* unattended write-class actions ⇒ **≥ L4** (approver / autonomous).

Every classification carries the evidence refs that justify it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AutonomyClassification:
    level: str | None            # "L1".."L5" or None (unclassifiable)
    label: str | None            # human name from config, or None
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""

    def to_field_value(self):
        """As a measured FieldValue for the card's autonomy field (or None)."""
        from ascore.cards.autofill import FieldValue
        key = "autonomy_control.autonomy_level_and_planning_depth"
        if self.level is None:
            return None
        return FieldValue.measured(key, f"{self.level} ({self.label})",
                                   self.evidence_refs)


def _write_actions(cfg: dict) -> set[str]:
    return set((cfg or {}).get("enforcement", {})
               .get("action_classes", {}).get("write", []))


def _levels(cfg: dict) -> dict[str, str]:
    return dict((cfg or {}).get("cards", {}).get("autonomy", {}).get("levels", {}))


def _span_is_approval_gated(span) -> bool:
    attrs = span.attributes or {}
    return bool(attrs.get("requires_approval") or attrs.get("approval")
                or attrs.get("approved") is not None
                or attrs.get("human_in_loop"))


def classify_autonomy(reg, agent_id: str, cfg: dict) -> AutonomyClassification:
    """Classify ``agent_id`` from its batch traces. Conservative + evidence-based."""
    try:
        traces = list(reg.traces(agent_id, mode="batch"))
    except Exception:  # noqa: BLE001
        traces = []
    if not traces:
        return AutonomyClassification(level=None, label=None,
                                      reason="no trace evidence — unclassifiable")

    writes = _write_actions(cfg)
    levels = _levels(cfg)
    tool_calls = 0
    write_calls = 0
    approval_gated = False
    refs: list[str] = []
    for t in traces:
        cited = False
        for span in t.spans:
            if span.kind != "tool_call":
                continue
            tool_calls += 1
            if not cited:
                refs.append(f"trace:{t.trace_id}")
                cited = True
            attrs = span.attributes or {}
            is_write = (span.name in writes
                        or attrs.get("action_class") == "write")
            if is_write:
                write_calls += 1
            if _span_is_approval_gated(span):
                approval_gated = True

    if tool_calls == 0:
        level = "L2"  # collaborator: converses, no autonomous tool use
        reason = "no autonomous tool calls observed"
    elif approval_gated:
        level = "L3"  # consultant: actions gated by approval
        reason = "tool use is approval-gated (≤ L3)"
    elif write_calls >= 3:
        level = "L5"  # autonomous: sustained unattended write actions
        reason = f"{write_calls} unattended write-class actions (≥ L4 → autonomous)"
    elif write_calls >= 1:
        level = "L4"  # approver: unattended write action(s)
        reason = f"{write_calls} unattended write-class action(s) (≥ L4)"
    else:
        level = "L3"  # consultant: read-only tool use
        reason = "read-only tool use, no write actions"

    return AutonomyClassification(level=level, label=levels.get(level),
                                  evidence_refs=refs[:20], reason=reason)
