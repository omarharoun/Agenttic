"""Honeypot canaries (SPEC-2 T29.1).

Per-agent versioned canaries: **decoy tools** no legitimate flow ever calls,
**canary credentials** planted in tool results whose reuse trips, and **tripwire
egress domains**. A Lane-1 detection ⇒ **deny + an incident** at
``canaries.severity_on_trip`` (default S1), naming the canary id + the call ref.

Because a canary is never legitimately used, a trip is a **confirmed positive**
(zero false positives). Canaries never appear in certification scorecards, and
rotation preserves the append-only trip history (T29.2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ascore.schema.enforcement import CanarySet, EnforcementEvent


def _evt_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


@dataclass
class CanaryTrip:
    canary_id: str
    kind: str          # decoy_tool | canary_credential | tripwire_domain
    detail: str
    call_ref: str


class CanaryManager:
    def __init__(self, reg, cfg: dict):
        self.reg = reg
        self.cfg = cfg or {}

    def _severity(self) -> str:
        return (self.cfg.get("canaries", {}) or {}).get("severity_on_trip", "S1")

    # -- install / rotate ----------------------------------------------------

    def install(self, agent_id: str, *, decoy_tools=None,
                canary_credentials=None, tripwire_domains=None) -> CanarySet:
        cs = CanarySet(
            agent_id=agent_id, decoy_tools=list(decoy_tools or []),
            canary_credentials=list(canary_credentials or []),
            tripwire_domains=list(tripwire_domains or []))
        self.reg.save_canary_set(cs)
        return self.reg.active_canary_set(agent_id)

    def rotate(self, agent_id: str, *, new_credentials=None) -> CanarySet:
        """Rotate to a fresh version. Preserves the decoy tools + tripwire
        domains; swaps credentials. Trip history is untouched (append-only)."""
        current = self.reg.active_canary_set(agent_id)
        if current is None:
            raise ValueError(f"no canary set to rotate for {agent_id}")
        creds = new_credentials or [f"canary-{uuid.uuid4().hex[:10]}"
                                    for _ in current.canary_credentials or [1]]
        rotated = CanarySet(
            agent_id=agent_id, version=current.version + 1,
            decoy_tools=list(current.decoy_tools),
            canary_credentials=list(creds),
            tripwire_domains=list(current.tripwire_domains))
        self.reg.save_canary_set(rotated)
        return self.reg.active_canary_set(agent_id)

    # -- detection (Lane 1) --------------------------------------------------

    def check(self, agent_id: str, phase: str, tool_name: str, data
              ) -> CanaryTrip | None:
        cs = self.reg.active_canary_set(agent_id)
        if cs is None:
            return None
        call_ref = f"toolcall:{tool_name}"

        # decoy tool called (any use is a trip)
        if tool_name in cs.decoy_tools:
            return CanaryTrip(canary_id=f"{cs.ref()}#decoy:{tool_name}",
                              kind="decoy_tool", detail=tool_name, call_ref=call_ref)

        text = _flatten(data)
        # planted credential reused in an OUTBOUND arg
        if phase == "tool_call":
            for cred in cs.canary_credentials:
                if cred and cred in text:
                    return CanaryTrip(
                        canary_id=f"{cs.ref()}#cred", kind="canary_credential",
                        detail="planted credential reused", call_ref=call_ref)
            for domain in cs.tripwire_domains:
                if domain and domain in text:
                    return CanaryTrip(
                        canary_id=f"{cs.ref()}#domain:{domain}",
                        kind="tripwire_domain", detail=domain, call_ref=call_ref)
        return None

    def trip(self, session, trip: CanaryTrip) -> str:
        """Record a canary trip: deny (handled by caller) + open an incident at
        the configured severity, naming the canary id + call ref. Returns the
        incident id."""
        from ascore.live.incidents import open_manual
        sev = self._severity()
        inc = open_manual(
            self.reg, agent_id=session.agent_id, severity=sev,
            title=f"canary trip: {trip.kind}",
            summary=f"canary {trip.canary_id} tripped ({trip.detail})",
            trace_refs=[trip.canary_id, trip.call_ref], actor="canary",
            origin="canary", cfg=self.cfg)
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=_evt_id(), session_id=session.session_id,
            agent_id=session.agent_id, kind="canary", action="deny",
            actor="canary", detail={
                "canary_id": trip.canary_id, "trip_kind": trip.kind,
                "call_ref": trip.call_ref, "incident_id": inc.incident_id,
                "severity": sev}))
        return inc.incident_id

    def trip_history(self, agent_id: str) -> list[dict]:
        """Append-only canary trip history (survives rotation)."""
        return [e for e in self.reg.list_enforcement_events(None, agent_id)
                if e.get("kind") == "canary"]

    # -- separation + rotation (T29.2) ---------------------------------------

    def separation_ok(self, agent_id: str) -> tuple[bool, list[str]]:
        """Invariant: canaries must NEVER perturb certification scorecards — no
        decoy tool or planted credential may appear in any of the agent's
        scorecards. Returns (ok, offending)."""
        cs = self.reg.active_canary_set(agent_id)
        if cs is None:
            return True, []
        markers = set(cs.decoy_tools) | set(cs.canary_credentials) \
            | set(cs.tripwire_domains)
        offending: list[str] = []
        try:
            scorecards = self.reg.scorecards_for(agent_id)
        except Exception:  # noqa: BLE001
            scorecards = []
        for sc in scorecards:
            blob = sc.model_dump_json()
            for m in markers:
                if m and m in blob:
                    offending.append(f"scorecard:{sc.scorecard_id}~{m}")
        return (not offending), offending

    def needs_rotation(self, agent_id: str, *, now=None) -> bool:
        """True if the active canary set is older than ``canaries.rotation_days``."""
        from datetime import datetime, timezone
        cs = self.reg.active_canary_set(agent_id)
        if cs is None:
            return False
        days = float((self.cfg.get("canaries", {}) or {}).get("rotation_days", 30))
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        created = cs.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (now - created).total_seconds() / 86400.0 >= days


def _flatten(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return " ".join(str(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return " ".join(str(v) for v in data)
    return str(data) if data is not None else ""
