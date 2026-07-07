"""Enforcement gateway skeleton (SPEC-2 T23.3).

The gateway runs a **session** over an agent's tool calls/results through the
pipeline:

    load-policy (hash-verified) → Lane 1 → Lane 2 → log decision → enqueue Lane 3

* **Policy load is hash-verified**: the stored ``content_hash`` is recomputed; on
  mismatch the gateway *refuses to serve*, and that refusal is itself an
  append-only event (Hard Rule 19 — no unlogged enforcement).
* Every evaluated call produces a :class:`Decision` and a logged
  :class:`EnforcementEvent`; the async Lane-3 hook is invoked (best-effort) but
  the full judge never runs inline (Hard Rule 22).

Lane 1 / Lane 2 logic is filled in by T24.* (imported lazily so this module is
usable standalone).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ascore.certification.hashing import sha256_hex
from ascore.schema.enforcement import (
    Decision,
    EnforcementEvent,
    EnforcementPolicy,
)


class PolicyIntegrityError(RuntimeError):
    """A policy failed its content-hash verification; the gateway refuses to
    serve under it (a hard, named error — Hard Rule 27)."""


def compute_policy_hash(policy: EnforcementPolicy) -> str:
    return sha256_hex(policy.hashable_content())


@dataclass
class Session:
    session_id: str
    agent_id: str
    policy: EnforcementPolicy
    active: bool = True
    revoked: bool = False
    metadata: dict = field(default_factory=dict)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class EnforcementGateway:
    """In-process enforcement gateway. The proxy mode (T23.4) wraps this so the
    event shape is identical in-process and over HTTP."""

    def __init__(self, reg, cfg: dict, async_enqueue=None):
        self.reg = reg
        self.cfg = cfg or {}
        self._async_enqueue = async_enqueue  # callable(session, decision) -> None
        self._sessions: dict[str, Session] = {}

    # -- policy load (hash-verified) -----------------------------------------

    def verify_policy(self, policy: EnforcementPolicy) -> bool:
        """True iff the policy's stored content_hash matches a fresh recompute."""
        if not policy.content_hash:
            return False
        return compute_policy_hash(policy) == policy.content_hash

    # -- sessions ------------------------------------------------------------

    def start_session(self, agent_id: str, policy: EnforcementPolicy | None = None
                      ) -> Session:
        """Begin an enforcement session. Loads the agent's latest policy (or the
        one provided), verifies its hash, and refuses (logging the refusal) on
        mismatch."""
        if policy is None:
            policy = self.reg.latest_policy(agent_id)
        session_id = _new_id("sess")
        if not self.verify_policy(policy):
            # refusal on mismatch is itself an event
            self._log(EnforcementEvent(
                event_id=_new_id("evt"), session_id=session_id, agent_id=agent_id,
                kind="refusal", actor="gateway", policy_hash=policy.content_hash,
                detail={"reason": "policy content-hash verification failed",
                        "recomputed": compute_policy_hash(policy)}))
            raise PolicyIntegrityError(
                f"policy {policy.policy_id} failed hash verification — "
                f"gateway refuses to serve agent {agent_id}")
        session = Session(session_id=session_id, agent_id=agent_id, policy=policy)
        self._sessions[session_id] = session
        self._log(EnforcementEvent(
            event_id=_new_id("evt"), session_id=session_id, agent_id=agent_id,
            kind="policy_load", actor="gateway", policy_hash=policy.content_hash,
            detail={"policy_id": policy.policy_id, "rules": len(policy.rules)}))
        return session

    def get_session(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise KeyError(f"unknown enforcement session {session_id}")
        return s

    # -- evaluation pipeline -------------------------------------------------

    def evaluate_tool_call(self, session_id: str, tool_name: str,
                           args: dict | None = None) -> Decision:
        return self._evaluate(session_id, "tool_call", tool_name, args or {})

    def evaluate_tool_result(self, session_id: str, tool_name: str,
                             result) -> Decision:
        return self._evaluate(session_id, "tool_result", tool_name, result)

    def _evaluate(self, session_id: str, phase: str, tool_name: str, data) -> Decision:
        session = self.get_session(session_id)
        t0 = time.perf_counter()

        action = "allow"
        lane = "lane1"
        evidence: list[str] = []
        action_class = "unknown"
        fail_open = False
        preserved_ref = None

        # Lane 1 — deterministic (T24.1)
        try:
            from ascore.enforce.lanes import lane1_evaluate, lane2_evaluate
        except Exception:  # noqa: BLE001 — lanes not yet available
            lane1_evaluate = lane2_evaluate = None

        if lane1_evaluate is not None:
            l1 = lane1_evaluate(session, phase, tool_name, data, self.cfg)
            if l1 is not None:
                action, evidence, action_class, fail_open = (
                    l1.action, l1.evidence, l1.action_class, l1.fail_open)
                lane = "lane1"

        # Lane 2 — classifiers (T24.2), only if Lane 1 allowed
        transformed = None
        if action == "allow" and lane2_evaluate is not None:
            l2 = lane2_evaluate(session, phase, tool_name, data, self.cfg)
            if l2 is not None:
                action, evidence, fail_open = l2.action, l2.evidence, l2.fail_open
                action_class = l2.action_class or action_class
                lane = "lane2"
                transformed = l2.transformed
                # preserve the untouched original as its own append-only event;
                # the decision refs it (nothing is silently dropped — Hard Rule 23)
                if l2.preserved_original is not None:
                    preserved_ref = self._preserve_original(
                        session, phase, tool_name, l2.preserved_original)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        decision = Decision(
            decision_id=_new_id("dec"), session_id=session_id,
            agent_id=session.agent_id, phase=phase, action=action, lane=lane,
            tool_name=tool_name, action_class=action_class, latency_ms=latency_ms,
            evidence=evidence, fail_open=fail_open,
            original_preserved_ref=preserved_ref,
            policy_hash=session.policy.content_hash)

        # log the decision (no enforcement without a logged decision)
        detail = {"phase": phase, "tool": tool_name, "lane": lane,
                  "evidence": evidence, "fail_open": fail_open}
        if transformed is not None:
            detail["transformed"] = transformed
            detail["original_preserved_ref"] = preserved_ref
        self._log(EnforcementEvent(
            event_id=_new_id("evt"), session_id=session_id,
            agent_id=session.agent_id, kind="decision", action=action,
            actor="gateway", decision_ref=decision.ref(),
            policy_hash=session.policy.content_hash, detail=detail))

        # terminal actions flip session state
        if action in ("terminate_session", "revoke_access"):
            session.active = False
            if action == "revoke_access":
                session.revoked = True

        # async Lane 3 enqueue (never inline)
        if self._async_enqueue is not None:
            try:
                self._async_enqueue(session, decision)
            except Exception:  # noqa: BLE001 — async is best-effort
                pass

        return decision

    def _preserve_original(self, session, phase, tool_name, original) -> str:
        """Store the untouched original as an append-only 'preserved' event and
        return a ref to it, so a transform/quarantine never loses the original."""
        event_id = _new_id("evt")
        self._log(EnforcementEvent(
            event_id=event_id, session_id=session.session_id,
            agent_id=session.agent_id, kind="preserved", actor="gateway",
            policy_hash=session.policy.content_hash,
            detail={"phase": phase, "tool": tool_name, "original": original}))
        return f"preserved:{event_id}"

    def resolve_preserved(self, session_id: str, preserved_ref: str):
        """Resolve a preserved-original ref back to its stored value."""
        target = preserved_ref.split(":", 1)[1] if ":" in preserved_ref else preserved_ref
        for e in self.reg.list_enforcement_events(session_id):
            if e.get("kind") == "preserved" and e.get("event_id") == target:
                return e.get("detail", {}).get("original")
        return None

    # -- logging -------------------------------------------------------------

    def _log(self, event: EnforcementEvent) -> None:
        self.reg.append_enforcement_event(event)
