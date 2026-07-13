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

from agenttic.schema.enforcement import EnforcementEvent

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
        # explicit always_block/always_allow directives per pattern (override the
        # softer bandit signal deterministically)
        self._directives: dict[str, str] = {}

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
        if response in ("always_block_pattern", "always_allow_pattern"):
            self._directives[item.pattern] = response
        return feedback_id

    def model_suggestion(self, decision):
        """Optional model enrichment (config-swappable, BYO-key). Returns a
        suggested response or None. The loop runs fine without a model."""
        if self.judge is None:
            return None
        try:
            verdict = self.judge.verdict_fn(decision)
            return "block" if verdict.get("malicious") else "allow"
        except Exception:  # noqa: BLE001 — model is optional enrichment only
            return None

    # -- 3/4. LEARN + SAFE-DEFAULT ADAPTATION --------------------------------

    def _directive(self, pattern: str) -> str:
        """'tighten' | 'loosen' | 'neutral'. Explicit always_* directives win;
        otherwise the bandit recommends."""
        explicit = self._directives.get(pattern)
        if explicit == "always_block_pattern":
            return "tighten"
        if explicit == "always_allow_pattern":
            return "loosen"
        return self.bandit.recommend(pattern)

    def propose_adaptation(self, agent_id: str) -> list[dict]:
        """Compute posture adaptations from accumulated feedback.

        Tightening is compiled + applied automatically (via the tighten_only
        override path). Loosening is emitted ONLY as a proposal requiring an
        explicit human confirmation — never auto-applied (Rule 20/26). Every
        adaptation names the feedback event ids that produced it."""
        from agenttic.registry.sqlite_store import NotFoundError
        try:
            policy = self.reg.latest_policy(agent_id)
        except NotFoundError:
            return []
        proposals: list[dict] = []
        for pattern in sorted(self.bandit.arms):
            directive = self._directive(pattern)
            feedback_ids = list(self.bandit.feedback_ids.get(pattern, []))
            if directive == "tighten":
                applied = self._auto_tighten(policy, agent_id, pattern, feedback_ids)
                if applied is not None:
                    policy = applied["policy"]  # chain further tightens
                    proposals.append({k: v for k, v in applied.items()
                                      if k != "policy"})
            elif directive == "loosen":
                proposals.append(
                    self._propose_loosen(agent_id, pattern, feedback_ids))
        return proposals

    def _tighten_rule(self, pattern: str, feedback_ids: list[str]):
        from agenttic.schema.enforcement import Rule
        tool, _, action_class = pattern.partition(":")
        explicit_block = self._directives.get(pattern) == "always_block_pattern"
        action = "deny" if explicit_block else "require_approval"
        rid = f"oversight-{action}-{tool}".replace(".", "_")
        return Rule(rule_id=rid, lane="lane1", action=action,
                    matcher={"tool": tool} if tool else {"action_class": action_class},
                    origin=f"oversight:tighten:{','.join(feedback_ids)}",
                    description=f"interactive oversight tighten for {pattern}")

    def _auto_tighten(self, policy, agent_id, pattern, feedback_ids) -> dict | None:
        """Add a pattern-specific restriction (a tightening — safe to auto-apply).
        No-op if the rule is already present."""
        from agenttic.enforce.gateway import compute_policy_hash
        from agenttic.schema.enforcement import EnforcementPolicy
        rule = self._tighten_rule(pattern, feedback_ids)
        if any(r.rule_id == rule.rule_id for r in policy.rules):
            return None  # already tightened for this pattern
        rules = sorted(list(policy.rules) + [rule], key=lambda r: r.rule_id)
        new = EnforcementPolicy(
            policy_id="", agent_id=agent_id, rules=rules,
            compiled_from=list(policy.compiled_from) + feedback_ids)
        new.content_hash = compute_policy_hash(new)
        new.policy_id = f"policy-{agent_id}-{new.content_hash[:12]}"
        self.reg.save_policy(new)
        self._log("", agent_id, "oversight", rule.action, {
            "event": "adaptation_applied", "direction": "tighten",
            "pattern": pattern, "rule_id": rule.rule_id,
            "feedback_ids": feedback_ids, "policy_hash": new.content_hash})
        return {"applied": True, "direction": "tighten", "pattern": pattern,
                "rule_id": rule.rule_id, "feedback_ids": feedback_ids,
                "policy_hash": new.content_hash, "policy": new}

    def _propose_loosen(self, agent_id, pattern, feedback_ids) -> dict:
        """Record a loosening PROPOSAL. Does NOT change any policy — a stream of
        allow feedback can never auto-loosen a rule (Rule 20/26)."""
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        self._log("", agent_id, "oversight", None, {
            "event": "loosen_proposal", "proposal_id": proposal_id,
            "pattern": pattern, "feedback_ids": feedback_ids,
            "state": "pending", "requires_confirmation": True})
        return {"applied": False, "direction": "loosen",
                "proposal_id": proposal_id, "pattern": pattern,
                "feedback_ids": feedback_ids, "requires_confirmation": True}

    def confirm_loosening(self, agent_id: str, proposal_id: str, human: str
                          ) -> dict:
        """Apply a loosening ONLY after an explicit human confirmation. The
        confirmation is itself a logged event; without it, no loosening ever
        happens."""
        from agenttic.enforce.gateway import compute_policy_hash
        from agenttic.registry.sqlite_store import NotFoundError
        from agenttic.schema.enforcement import EnforcementPolicy

        proposal = self._find_proposal(agent_id, proposal_id)
        if proposal is None:
            raise ValueError(f"no pending loosen proposal {proposal_id}")
        pattern = proposal["pattern"]
        # log the explicit confirmation FIRST (auditable authorization)
        self._log("", agent_id, "oversight", None, {
            "event": "loosen_confirmed", "proposal_id": proposal_id,
            "human": human, "pattern": pattern,
            "feedback_ids": proposal.get("feedback_ids", [])})
        # now (and only now) apply the loosening: drop the pattern's tighten rule
        try:
            policy = self.reg.latest_policy(agent_id)
        except NotFoundError:
            return {"applied": False, "reason": "no policy"}
        tool = pattern.partition(":")[0]
        drop_ids = {f"oversight-require_approval-{tool}".replace(".", "_"),
                    f"oversight-deny-{tool}".replace(".", "_")}
        remaining = [r for r in policy.rules if r.rule_id not in drop_ids]
        if len(remaining) == len(policy.rules):
            return {"applied": False, "reason": "no oversight rule to loosen"}
        new = EnforcementPolicy(
            policy_id="", agent_id=agent_id, rules=sorted(remaining, key=lambda r: r.rule_id),
            compiled_from=list(policy.compiled_from) + [f"confirmed_by:{human}", proposal_id])
        new.content_hash = compute_policy_hash(new)
        new.policy_id = f"policy-{agent_id}-{new.content_hash[:12]}"
        self.reg.save_policy(new)
        self._log("", agent_id, "oversight", "allow", {
            "event": "adaptation_applied", "direction": "loosen",
            "pattern": pattern, "confirmed_by": human,
            "proposal_id": proposal_id, "policy_hash": new.content_hash})
        return {"applied": True, "direction": "loosen", "pattern": pattern,
                "confirmed_by": human, "policy_hash": new.content_hash}

    def _find_proposal(self, agent_id: str, proposal_id: str) -> dict | None:
        confirmed = set()
        proposal = None
        for e in self.reg.list_enforcement_events(None, agent_id):
            d = e.get("detail") or {}
            if d.get("event") == "loosen_confirmed":
                confirmed.add(d.get("proposal_id"))
            if d.get("event") == "loosen_proposal" and d.get("proposal_id") == proposal_id:
                proposal = d
        if proposal is None or proposal_id in confirmed:
            return None  # unknown or already confirmed+applied
        return proposal

    # -- helpers -------------------------------------------------------------

    def _log(self, session_id, agent_id, kind, action, detail) -> None:
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=_evt_id(), session_id=session_id, agent_id=agent_id,
            kind=kind, action=action, actor="oversight", detail=detail))


def pending_reviews(reg, agent_id: str | None = None) -> list[dict]:
    """Review prompts that have no matching human response yet (stateless — read
    from the append-only log)."""
    events = reg.list_enforcement_events(None, agent_id)
    responded = {(e.get("detail") or {}).get("review_id")
                 for e in events
                 if (e.get("detail") or {}).get("event") == "human_response"}
    out = []
    for e in events:
        d = e.get("detail") or {}
        if d.get("event") == "review_prompt" and d.get("review_id") not in responded:
            out.append(d)
    return out


def pending_loosen_proposals(reg, agent_id: str | None = None) -> list[dict]:
    """Loosen proposals awaiting explicit confirmation (never auto-applied)."""
    events = reg.list_enforcement_events(None, agent_id)
    confirmed = {(e.get("detail") or {}).get("proposal_id")
                 for e in events
                 if (e.get("detail") or {}).get("event") == "loosen_confirmed"}
    out = []
    for e in events:
        d = e.get("detail") or {}
        if d.get("event") == "loosen_proposal" and d.get("proposal_id") not in confirmed:
            out.append(d)
    return out


def _decision_confidence(decision) -> float | None:
    for e in getattr(decision, "evidence", []) or []:
        if isinstance(e, str) and e.startswith("confidence:"):
            try:
                return float(e.split(":", 1)[1])
            except ValueError:
                return None
    return None
