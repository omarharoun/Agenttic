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

import logging
import time
import uuid
from dataclasses import dataclass, field

from agenttic.certification.hashing import sha256_hex
from agenttic.schema.enforcement import (
    Decision,
    EnforcementEvent,
    EnforcementPolicy,
)

log = logging.getLogger("agenttic.enforce.gateway")


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
                           args: dict | None = None,
                           caller_cohort: str | None = None) -> Decision:
        return self._evaluate(session_id, "tool_call", tool_name, args or {},
                              caller_cohort)

    def evaluate_tool_result(self, session_id: str, tool_name: str,
                             result, caller_cohort: str | None = None) -> Decision:
        return self._evaluate(session_id, "tool_result", tool_name, result,
                              caller_cohort)

    def _evaluate(self, session_id: str, phase: str, tool_name: str, data,
                  caller_cohort: str | None = None) -> Decision:
        session = self.get_session(session_id)
        t0 = time.perf_counter()

        action = "allow"
        lane = "lane1"
        evidence: list[str] = []
        action_class = "unknown"
        fail_open = False
        preserved_ref = None

        # stage gate (T28.2): a caller above the agent's promoted stage is denied.
        if caller_cohort is not None:
            gate = None
            origin = "stage_gate"
            try:
                from agenttic.release.ladder import STAGE_GATE_ORIGIN, stage_gate
                origin = STAGE_GATE_ORIGIN
                gate = stage_gate(self.reg, session.agent_id, caller_cohort)
            except Exception as exc:  # noqa: BLE001 — release ladder is optional:
                # if the gate cannot be COMPUTED we never established a violation,
                # so we proceed to the normal lanes rather than blanket-denying an
                # unrelated call. The error is surfaced, not silently swallowed.
                self._surface_enforcement_error(
                    "stage_gate_detect", session, phase, tool_name, exc)
                gate = None
            if gate is not None and not gate.allowed:
                # A CONFIRMED gate violation → deny. The deny is returned OUTSIDE
                # the detection try above so that a failure while building/logging
                # the deny can never fall through to allow (fail CLOSED).
                return self._gated_deny(session, phase, tool_name, t0, [
                    f"{origin}:cohort={caller_cohort}:"
                    f"{gate.caller_stage}>{gate.agent_stage}"])

        from agenttic.enforce.lanes import (
            action_class_of, lane1_evaluate, lane2_evaluate,
        )
        action_class = action_class_of(tool_name, self.cfg)

        # honeypot canaries (T29.1): a trip is a confirmed positive → deny +
        # incident at severity_on_trip, detected in Lane 1.
        #
        # Fail-CLOSED contract: DETECTION (``check``) is optional — if we cannot
        # even check, we could not confirm a trip, so we proceed to the lanes (a
        # detection error must never be upgraded into a forced deny of an
        # unrelated call). But once a trip IS confirmed, the call MUST be denied:
        # opening the incident is best-effort and its failure fails CLOSED (deny
        # anyway, error surfaced) — it must NEVER fall through to allow.
        cm = None
        trip = None
        try:
            from agenttic.enforce.canaries import CanaryManager
            cm = CanaryManager(self.reg, self.cfg)
            trip = cm.check(session.agent_id, phase, tool_name, data)
        except Exception as exc:  # noqa: BLE001 — canary DETECTION optional
            self._surface_enforcement_error(
                "canary_detect", session, phase, tool_name, exc)
            trip = None
        if trip is not None:
            evidence = [f"canary:{trip.canary_id}", trip.call_ref]
            try:
                incident_id = cm.trip(session, trip)
                evidence.append(f"incident:{incident_id}")
            except Exception as exc:  # noqa: BLE001 — FAIL CLOSED on the block
                # path: a confirmed decoy call is denied even if the incident
                # store errors; the failure is surfaced, not swallowed into allow.
                evidence += ["incident:unavailable",
                             f"canary_handler_error:{type(exc).__name__}"]
                self._surface_enforcement_error(
                    "canary_trip", session, phase, tool_name, exc,
                    fail_closed=True)
            return self._gated_deny(session, phase, tool_name, t0, evidence,
                                    origin="canary")

        # Lane 1 — deterministic (T24.1). Errors here apply the fail policy.
        try:
            l1 = lane1_evaluate(session, phase, tool_name, data, self.cfg)
        except Exception:  # noqa: BLE001 — Lane-1 failure → per-class fail policy
            l1 = self._fail_policy(action_class)
        if l1 is not None:
            action, evidence, fail_open = l1.action, l1.evidence, l1.fail_open
            action_class = l1.action_class or action_class
            lane = "lane1"

        # Lane 2 — classifiers (T24.2), only if Lane 1 allowed. Hard timeout +
        # per-class fail policy (write ⇒ closed, read ⇒ open + fail_open logged).
        transformed = None
        if action == "allow":
            l2 = self._run_lane2_with_policy(
                lane2_evaluate, session, phase, tool_name, data, action_class)
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

        # every deny/quarantine feeds the hardening loop (T26.4): the blocked
        # call is a hardening candidate (a future checker-eval seed).
        if action in ("deny", "transform"):
            self._log(EnforcementEvent(
                event_id=_new_id("evt"), session_id=session_id,
                agent_id=session.agent_id, kind="admin", actor="gateway",
                decision_ref=decision.ref(),
                detail={"hardening_candidate": tool_name, "action": action,
                        "phase": phase, "evidence": evidence}))

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

    def _gated_deny(self, session, phase, tool_name, t0, evidence,
                    origin: str = "stage_gate") -> Decision:
        """A deterministic Lane-1 deny (stage gate / canary), fully logged.

        The deny :class:`Decision` is built *before* logging and returned even if
        the append fails: a logging error on a confirmed block must never
        downgrade a deny into an allow (Hard Rule 19 is honoured best-effort, but
        the safe direction on error is CLOSED). A log failure is surfaced
        out-of-band rather than swallowed."""
        latency_ms = (time.perf_counter() - t0) * 1000.0
        decision = Decision(
            decision_id=_new_id("dec"), session_id=session.session_id,
            agent_id=session.agent_id, phase=phase, action="deny", lane="lane1",
            tool_name=tool_name, latency_ms=latency_ms, evidence=evidence,
            policy_hash=session.policy.content_hash)
        try:
            self._log(EnforcementEvent(
                event_id=_new_id("evt"), session_id=session.session_id,
                agent_id=session.agent_id, kind="decision", action="deny",
                actor="gateway", decision_ref=decision.ref(),
                policy_hash=session.policy.content_hash,
                detail={"phase": phase, "tool": tool_name, "lane": "lane1",
                        "evidence": evidence, "origin": origin}))
        except Exception as exc:  # noqa: BLE001 — never downgrade a deny to allow
            self._surface_enforcement_error(
                f"{origin}_deny_log", session, phase, tool_name, exc,
                fail_closed=True)
        return decision

    def _surface_enforcement_error(self, stage: str, session, phase: str,
                                   tool_name: str, exc: Exception, *,
                                   fail_closed: bool = False) -> None:
        """Surface (never swallow) an error hit on the enforcement path: a
        structured stdlib log, a telemetry counter, and a best-effort append-only
        event. This function itself never raises — it is called from the block
        path where a secondary failure must not derail the deny."""
        log.warning(
            "enforcement error on %s: agent=%s session=%s phase=%s tool=%s "
            "err=%s fail_closed=%s", stage, session.agent_id,
            session.session_id, phase, tool_name, exc, fail_closed)
        try:
            from agenttic.server.metrics import record_enforcement_fail_closed
            if fail_closed:
                record_enforcement_fail_closed(stage.split("_")[0])
        except Exception:  # noqa: BLE001 — telemetry is best-effort
            pass
        try:
            self._log(EnforcementEvent(
                event_id=_new_id("evt"), session_id=session.session_id,
                agent_id=session.agent_id, kind="admin", actor="gateway",
                detail={"enforcement_error": stage, "phase": phase,
                        "tool": tool_name, "error": f"{type(exc).__name__}: {exc}",
                        "fail_closed": fail_closed}))
        except Exception:  # noqa: BLE001 — the append-only store may be the very
            # thing that failed; the stdlib log + counter above already surfaced it.
            pass

    def _fail_policy(self, action_class: str):
        """Per-action-class fail policy: write ⇒ closed (deny); read ⇒ open
        (allow) with fail_open logged. Unknown class defaults to closed (safe)."""
        from agenttic.enforce.lanes import LaneResult
        policy = (self.cfg.get("enforcement", {}) or {}).get("fail_policy", {})
        mode = policy.get(action_class, "closed")
        if mode == "open":
            return LaneResult(action="allow", action_class=action_class,
                              fail_open=True, evidence=["fail_open:read"])
        return LaneResult(action="deny", action_class=action_class,
                          evidence=["fail_closed:write"])

    def _run_lane2_with_policy(self, fn, session, phase, tool_name, data,
                               action_class):
        """Run Lane 2 under a hard timeout; on timeout/error apply the fail
        policy for the action class."""
        import concurrent.futures

        lanes = self.cfg.get("enforcement", {}).get("lanes", {})
        budget_ms = float(lanes.get("lane2_budget_ms", 80))
        mult = float(lanes.get("ci_latency_multiplier", 5))
        timeout_s = (budget_ms * mult) / 1000.0
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(fn, session, phase, tool_name, data, self.cfg)
                return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            return self._fail_policy(action_class)
        except Exception:  # noqa: BLE001 — classifier error → fail policy
            return self._fail_policy(action_class)

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
