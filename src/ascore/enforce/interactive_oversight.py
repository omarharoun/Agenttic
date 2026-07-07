"""Interactive RL oversight loop (opt-in) — SPEC-2 addendum.

An optional mode that monitors enforcement decisions live, surfaces borderline
ones to a human in real time, and adapts posture from that feedback via a
lightweight online contextual bandit — WITHOUT violating the playbook's safety
rules:

* **no unlogged enforcement / no self-exemption** (Rule 19): every review prompt
  and every human response is an :class:`EnforcementEvent`;
* **overrides tighten_only** (Rule 20): tightening feedback may auto-apply;
  loosening is ONLY ever a proposal requiring an explicit human confirmation —
  never auto-applied, never silent;
* **oversight may tighten, never silently relax** (Rule 26).

This module reuses the M13 pieces (async_judge, approvals, hardening feedback) and
the policy compiler; it does not reimplement them. It is DISABLED by default
(``oversight.interactive_loop.enabled``).

Commit split:
* review loop (monitor + interact + bandit recording) — this file;
* bandit adaptation (tighten auto, loosen gated) — appended below.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field

from ascore.schema.enforcement import EnforcementEvent

# the closed set of human responses the human may give at a review
HUMAN_RESPONSES = (
    "allow", "block", "always_allow_pattern", "always_block_pattern", "intervene",
)
_TIGHTEN_RESPONSES = {"block", "always_block_pattern", "intervene"}
_LOOSEN_RESPONSES = {"allow", "always_allow_pattern"}


def _evt_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Contextual bandit (Thompson) — auditable, not a black box.
# --------------------------------------------------------------------------- #


@dataclass
class ContextualBandit:
    """A per-pattern two-armed Thompson-sampling bandit. Arms: ``tighten`` and
    ``loosen``. Human feedback is the reward. Every update records the feedback
    event id that produced it, so a recommendation traces to specific logged
    events. Deterministic under ``seed``."""

    exploration: float = 0.05
    seed: int = 1234
    # pattern -> {"tighten": [a, b], "loosen": [a, b]}
    arms: dict = field(default_factory=dict)
    # pattern -> [feedback_event_id, ...]
    feedback_ids: dict = field(default_factory=dict)

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def _arms(self, pattern: str) -> dict:
        return self.arms.setdefault(
            pattern, {"tighten": [1.0, 1.0], "loosen": [1.0, 1.0]})

    def update(self, pattern: str, response: str, feedback_id: str) -> None:
        arms = self._arms(pattern)
        if response in _TIGHTEN_RESPONSES:
            arms["tighten"][0] += 1      # tighten success
            arms["loosen"][1] += 1       # loosen failure
        elif response in _LOOSEN_RESPONSES:
            arms["loosen"][0] += 1       # loosen success
            arms["tighten"][1] += 1      # tighten failure
        self.feedback_ids.setdefault(pattern, []).append(feedback_id)

    def recommend(self, pattern: str) -> str:
        """'tighten' | 'loosen' | 'neutral' (no evidence)."""
        if pattern not in self.arms:
            return "neutral"
        arms = self.arms[pattern]
        # epsilon exploration keeps a small chance of the non-greedy arm
        if self._rng.random() < self.exploration:
            return self._rng.choice(["tighten", "loosen"])
        t = self._rng.betavariate(*arms["tighten"])
        loo = self._rng.betavariate(*arms["loosen"])
        if t == loo:
            return "neutral"
        return "tighten" if t > loo else "loosen"

    def confidence(self, pattern: str) -> dict:
        arms = self.arms.get(pattern, {"tighten": [1.0, 1.0], "loosen": [1.0, 1.0]})
        return {
            "tighten_mean": arms["tighten"][0] / sum(arms["tighten"]),
            "loosen_mean": arms["loosen"][0] / sum(arms["loosen"]),
            "n": len(self.feedback_ids.get(pattern, [])),
        }


# --------------------------------------------------------------------------- #
# The loop.
# --------------------------------------------------------------------------- #


@dataclass
class ReviewItem:
    review_id: str
    session_id: str
    agent_id: str
    decision_ref: str
    pattern: str
    tool_name: str
    action_class: str
    reasons: list[str]


class InteractiveOversightLoop:
    """Monitors decisions, surfaces borderline ones, records human feedback, and
    (in the adaptation half) proposes posture changes."""

    def __init__(self, reg, cfg: dict, judge=None):
        self.reg = reg
        self.cfg = cfg or {}
        self.judge = judge  # optional async_judge for model enrichment (BYO-key)
        icfg = self._icfg()
        bcfg = icfg.get("bandit", {})
        self.bandit = ContextualBandit(
            exploration=float(bcfg.get("exploration", 0.05)),
            seed=int(bcfg.get("seed", 1234)))
        self._seen_patterns: set[str] = set()

    # -- config --------------------------------------------------------------

    def _icfg(self) -> dict:
        return (self.cfg.get("oversight", {}) or {}).get("interactive_loop", {})

    @property
    def enabled(self) -> bool:
        return bool(self._icfg().get("enabled", False))

    @staticmethod
    def pattern_of(decision) -> str:
        return f"{getattr(decision, 'tool_name', '')}:{getattr(decision, 'action_class', 'unknown')}"

    # -- 1. MONITOR: select borderline / high-uncertainty decisions ----------

    def select_for_review(self, decision) -> tuple[bool, list[str]]:
        """Decide whether a decision is borderline enough to surface. Returns
        (selected, reasons)."""
        sel = self._icfg().get("review_selectors", {})
        reasons: list[str] = []

        lanes = (self.cfg.get("enforcement", {}) or {}).get("lanes", {})
        lane1_budget = float(lanes.get("lane1_budget_ms", 10))
        near = float(sel.get("near_budget_ms", 2))
        if getattr(decision, "latency_ms", 0) >= (lane1_budget - near):
            reasons.append("near_budget")

        # low-confidence lane-2 classifier (confidence carried on evidence, else
        # any lane-2 transform is treated as reviewable at the configured floor)
        conf_below = sel.get("lane2_confidence_below")
        if conf_below is not None and getattr(decision, "lane", "") == "lane2":
            conf = _decision_confidence(decision)
            if conf is None or conf < float(conf_below):
                reasons.append("lane2_low_confidence")

        pattern = self.pattern_of(decision)
        if sel.get("first_seen_pattern") and pattern not in self._seen_patterns:
            reasons.append("first_seen_pattern")
        self._seen_patterns.add(pattern)

        return (len(reasons) > 0), reasons

    # -- 2. INTERACT: surface + record --------------------------------------

    def present_for_review(self, session, decision, reasons: list[str]) -> ReviewItem:
        """Surface a decision for human review. Records the prompt as an event
        (no unlogged enforcement)."""
        review_id = f"rev-{uuid.uuid4().hex[:12]}"
        pattern = self.pattern_of(decision)
        self._log(session.session_id, session.agent_id, "oversight", None, {
            "event": "review_prompt", "review_id": review_id,
            "decision_ref": decision.ref(), "pattern": pattern,
            "tool": decision.tool_name, "action_class": decision.action_class,
            "reasons": reasons,
            "options": list(HUMAN_RESPONSES),
        })
        return ReviewItem(
            review_id=review_id, session_id=session.session_id,
            agent_id=session.agent_id, decision_ref=decision.ref(),
            pattern=pattern, tool_name=decision.tool_name,
            action_class=decision.action_class, reasons=list(reasons))

    def record_human_response(self, item: ReviewItem, response: str,
                              human: str) -> str:
        """Record a human response as a feedback event and update the bandit.
        Returns the feedback event id (the reward reference)."""
        if response not in HUMAN_RESPONSES:
            raise ValueError(f"unknown oversight response {response!r}")
        feedback_id = _evt_id()
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=feedback_id, session_id=item.session_id,
            agent_id=item.agent_id, kind="oversight", actor=human,
            decision_ref=item.decision_ref, detail={
                "event": "human_response", "review_id": item.review_id,
                "response": response, "pattern": item.pattern,
                "human": human}))
        self.bandit.update(item.pattern, response, f"event:{feedback_id}")
        return feedback_id

    # -- helpers -------------------------------------------------------------

    def _log(self, session_id, agent_id, kind, action, detail) -> None:
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=_evt_id(), session_id=session_id, agent_id=agent_id,
            kind=kind, action=action, actor="oversight", detail=detail))


def _decision_confidence(decision) -> float | None:
    for e in getattr(decision, "evidence", []) or []:
        if isinstance(e, str) and e.startswith("confidence:"):
            try:
                return float(e.split(":", 1)[1])
            except ValueError:
                return None
    return None
