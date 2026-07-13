"""Lane-3 async judge (SPEC-2 T26.1).

The full judge NEVER runs inline (Hard Rule 22). It samples decisions at the
policy's lane-3 rate and, out of band, can:

* **retro-tag** the sampled decision with its verdict,
* **open an incident** on a malicious verdict,
* **enqueue a hardening candidate**,
* **terminate the session** or **revoke access** when the verdict warrants it.

Every downstream event carries the **verdict ref** of the judge event that
produced it. Sampling is seeded so a verdict stream is reproducible.
"""

from __future__ import annotations

import random
import uuid

from agenttic.schema.enforcement import EnforcementEvent


def _evt_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def default_verdict_fn(decision) -> dict:
    """Benign-by-default verdict (the real judge is an LLM, mocked in tests)."""
    return {"malicious": False, "severity": None, "terminate": False,
            "revoke": False, "rationale": "no-op default verdict"}


class AsyncJudge:
    def __init__(self, reg, cfg: dict, verdict_fn=None, seed: int = 1234):
        self.reg = reg
        self.cfg = cfg or {}
        self.verdict_fn = verdict_fn or default_verdict_fn
        self._rng = random.Random(seed)

    # -- sampling ------------------------------------------------------------

    def sampling_rate(self, policy, domain: str | None = None) -> float:
        rate = 0.0
        for r in getattr(policy, "rules", []):
            if r.lane != "lane3":
                continue
            m = r.matcher or {}
            if "domain" in m:
                if domain is not None and m.get("domain") == domain:
                    rate = max(rate, float(m.get("sampling", 0)))
            else:
                rate = max(rate, float(m.get("sampling", 0)))
        return rate

    def should_sample(self, policy, domain: str | None = None) -> bool:
        return self._rng.random() < self.sampling_rate(policy, domain)

    # -- review --------------------------------------------------------------

    def review(self, session, decision, policy=None, *, force: bool = False) -> dict:
        """Review a decision out of band. Returns a summary dict. No-op (returns
        ``sampled=False``) if the decision isn't sampled."""
        policy = policy or session.policy
        domain = getattr(decision, "action_class", None)
        if not force and not self.should_sample(policy, domain):
            return {"sampled": False}

        verdict = self.verdict_fn(decision)
        verdict_event_id = _evt_id()
        verdict_ref = f"event:{verdict_event_id}"
        self._log(EnforcementEvent(
            event_id=verdict_event_id, session_id=session.session_id,
            agent_id=session.agent_id, kind="judge", actor="async_judge",
            decision_ref=decision.ref(), policy_hash=policy.content_hash,
            detail={"verdict": verdict, "decision": decision.decision_id}))

        actions: list[str] = []
        if verdict.get("malicious"):
            # retro-tag
            self._log(EnforcementEvent(
                event_id=_evt_id(), session_id=session.session_id,
                agent_id=session.agent_id, kind="judge", action="transform",
                actor="async_judge", decision_ref=decision.ref(),
                detail={"retro_tag": "malicious", "verdict_ref": verdict_ref}))
            actions.append("retro_tag")

            # open an incident (with the verdict ref)
            incident = self._open_incident(session, decision, verdict, verdict_ref)
            actions.append(f"incident:{incident}")

            # enqueue a hardening candidate
            self._log(EnforcementEvent(
                event_id=_evt_id(), session_id=session.session_id,
                agent_id=session.agent_id, kind="admin", action=None,
                actor="async_judge",
                detail={"hardening_candidate": decision.tool_name,
                        "verdict_ref": verdict_ref}))
            actions.append("hardening")

            if verdict.get("terminate"):
                session.active = False
                self._log(EnforcementEvent(
                    event_id=_evt_id(), session_id=session.session_id,
                    agent_id=session.agent_id, kind="decision",
                    action="terminate_session", actor="async_judge",
                    detail={"verdict_ref": verdict_ref}))
                actions.append("terminate_session")
            if verdict.get("revoke"):
                session.active = False
                session.revoked = True
                self._log(EnforcementEvent(
                    event_id=_evt_id(), session_id=session.session_id,
                    agent_id=session.agent_id, kind="decision",
                    action="revoke_access", actor="async_judge",
                    detail={"verdict_ref": verdict_ref}))
                actions.append("revoke_access")

        return {"sampled": True, "verdict": verdict, "verdict_ref": verdict_ref,
                "actions": actions}

    def _open_incident(self, session, decision, verdict, verdict_ref) -> str:
        from agenttic.live.incidents import open_manual
        sev = verdict.get("severity") or "S3"
        inc = open_manual(
            self.reg, agent_id=session.agent_id, severity=sev,
            title=f"lane-3 verdict: {decision.tool_name}",
            summary=verdict.get("rationale", ""),
            trace_refs=[decision.ref(), verdict_ref])
        return inc.incident_id

    def _log(self, event: EnforcementEvent) -> None:
        self.reg.append_enforcement_event(event)

    # -- gateway hook --------------------------------------------------------

    def enqueue(self, session, decision) -> None:
        """The gateway's async_enqueue hook — reviews (sampled) out of band."""
        try:
            self.review(session, decision)
        except Exception:  # noqa: BLE001 — async best-effort, never breaks inline
            pass
