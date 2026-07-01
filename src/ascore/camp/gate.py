"""
The guardrail.

This encodes the rule the user was firm about: an agent does not get to act on
its own until it has earned it, and even then a human stays in the loop.

Two conditions, both required to promote an agent to production:

  1. HARD FLOOR (non-overridable): the measured accuracy (Wilson lower bound)
     must clear the threshold on enough data. No human can wave this through.
     "Strict guidelines even a human respects."

  2. HUMAN APPROVAL (required): on top of clearing the floor, a human must
     explicitly sign off. If no human approves, the default is DENY.

The gate never auto-approves. Absence of a decision means "no".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .trainer import CampReport


@dataclass
class GateDecision:
    promoted: bool
    reasons: List[str] = field(default_factory=list)

    def summary(self) -> str:
        head = "PROMOTED" if self.promoted else "BLOCKED"
        return f"{head}: " + "; ".join(self.reasons)


# A human-approval callback returns True only on explicit sign-off.
HumanApprover = Callable[[CampReport], bool]


def deny_by_default(_: CampReport) -> bool:
    """Default approver: nobody is here to approve, so the answer is no."""
    return False


class PromotionGate:
    def __init__(self, human_approver: Optional[HumanApprover] = None):
        self.human_approver = human_approver or deny_by_default

    def evaluate(self, report: CampReport) -> GateDecision:
        reasons: List[str] = []

        # Condition 1: the hard floor. Checked first and cannot be overridden.
        if not report.enough_data:
            reasons.append(
                f"insufficient data: {report.episodes} episodes < "
                f"{report.min_episodes_for_gate} required"
            )
            return GateDecision(promoted=False, reasons=reasons)

        if not report.meets_threshold():
            reasons.append(
                f"below accuracy floor: wilson95_low={report.wilson_lower_95:.4f} "
                f"< {report.threshold:.2f}"
            )
            return GateDecision(promoted=False, reasons=reasons)

        reasons.append(
            f"accuracy floor cleared: wilson95_low={report.wilson_lower_95:.4f} "
            f">= {report.threshold:.2f}"
        )

        # Condition 2: required human sign-off, only reachable once the floor is met.
        approved = bool(self.human_approver(report))
        if not approved:
            reasons.append("human approval not granted (default is deny)")
            return GateDecision(promoted=False, reasons=reasons)

        reasons.append("human approval granted")
        return GateDecision(promoted=True, reasons=reasons)
