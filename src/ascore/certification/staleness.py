"""Computed certification status (SPEC-2 T17.1).

A dossier's status is never a stored, hand-set field — it is *computed* from the
registry every time it is read (Hard Rule 14):

* **revoked** — a ``revoked`` dossier_event exists (append-only; wins over all).
* **stale** — any of: the agent's config hash changed (a newer dossier for the
  agent pins a different config, or an explicit current-hash mismatch), a drift
  re-eval was requested after certification, a newer profile version exists, or
  an S1/S2 incident is open.
* **current** — none of the above.

This module is pure over registry reads (plus optional inputs); it never mutates.
"""

from __future__ import annotations

from ascore.registry.sqlite_store import NotFoundError

_OPEN_STATES = {"open", "triaged", "reported"}  # not closed


def _is_revoked(reg, dossier) -> bool:
    for e in reg.list_dossier_events(dossier.dossier_id):
        if e["event_type"] == "revoked":
            return True
    return False


def _config_changed(reg, dossier, current_config_hash: str | None) -> bool:
    if current_config_hash is not None:
        return current_config_hash != dossier.agent_config_hash
    # otherwise: a newer dossier for the same agent+profile with a different
    # config hash means the agent config moved on.
    for row in reg.list_dossiers(dossier.agent_id):
        if row["dossier_id"] == dossier.dossier_id:
            continue
        if row["profile_id"] != dossier.profile_id:
            continue
        other = reg.get_dossier(row["dossier_id"])
        if (other.created_at > dossier.created_at
                and other.agent_config_hash != dossier.agent_config_hash):
            return True
    return False


def _drift_requested(reg, dossier) -> bool:
    try:
        reqs = reg.reeval_requests(dossier.agent_id)
    except Exception:  # noqa: BLE001
        return False
    return bool(reqs)


def _newer_profile(reg, dossier) -> bool:
    try:
        latest = reg.get_profile(dossier.profile_id)  # highest version
    except NotFoundError:
        return False
    return latest.version > dossier.profile_version


def _open_s1_s2(reg, dossier) -> bool:
    from ascore.live.incidents import IncidentManager
    mgr = IncidentManager(reg)
    for row in reg.list_incidents(dossier.agent_id):
        if row["severity"] not in ("S1", "S2"):
            continue
        try:
            state = mgr.current_state(row["incident_id"])
        except Exception:  # noqa: BLE001
            state = "open"
        if state in _OPEN_STATES:
            return True
    return False


def status(reg, dossier, *, cfg: dict | None = None,
           current_config_hash: str | None = None) -> str:
    """Compute a dossier's certification status: current | stale | revoked."""
    if _is_revoked(reg, dossier):
        return "revoked"
    if (_config_changed(reg, dossier, current_config_hash)
            or _drift_requested(reg, dossier)
            or _newer_profile(reg, dossier)
            or _open_s1_s2(reg, dossier)):
        return "stale"
    return "current"


def status_reasons(reg, dossier, *, cfg: dict | None = None,
                   current_config_hash: str | None = None) -> list[str]:
    """The reasons behind a stale/revoked status (for display)."""
    reasons: list[str] = []
    if _is_revoked(reg, dossier):
        reasons.append("revoked")
        return reasons
    if _config_changed(reg, dossier, current_config_hash):
        reasons.append("agent config changed since certification")
    if _drift_requested(reg, dossier):
        reasons.append("drift re-evaluation requested")
    if _newer_profile(reg, dossier):
        reasons.append("a newer profile version exists")
    if _open_s1_s2(reg, dossier):
        reasons.append("an S1/S2 incident is open")
    return reasons
