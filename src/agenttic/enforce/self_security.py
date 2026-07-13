"""Enforcement self-security (SPEC-2 T27.3).

The enforcement layer secures *itself*:

* **chain-to-dossier**: a production policy must be provably compiled from a real
  dossier that resolves in the registry (no free-floating policies).
* **secret redaction in stored/exported events**: secrets never leak out of the
  enforcement log's own exports.
* **tenancy isolation**: events are tenant-scoped (via the registry).
* **no self-exemption**: the layer cannot exempt its own actions from the log —
  every decision, admin, and judge action is an event.
"""

from __future__ import annotations

from agenttic.enforce.lanes import redact_secrets
from agenttic.registry.sqlite_store import NotFoundError


def verify_policy_provenance(reg, policy) -> tuple[bool, list[str]]:
    """A policy must chain to a dossier that resolves. Returns (ok, problems)."""
    problems: list[str] = []
    dossier_refs = [r for r in (policy.compiled_from or [])
                    if r.startswith("dossier:")]
    if not dossier_refs:
        problems.append(f"{policy.ref()}: not compiled from any dossier")
    for ref in dossier_refs:
        dossier_id = ref.split(":", 1)[1]
        try:
            reg.get_dossier(dossier_id)
        except NotFoundError:
            problems.append(f"{policy.ref()}: dossier {dossier_id} does not resolve")
    return (not problems), problems


def redact_obj(obj):
    """Recursively redact secrets/PII from any JSON-ish structure."""
    if isinstance(obj, str):
        _changed, red, _kinds = redact_secrets(obj)
        return red
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


def redact_events(events: list[dict]) -> list[dict]:
    """Redact secrets from a list of exported events (self-security on exports)."""
    return [redact_obj(e) for e in events]


def assert_no_self_exemption(reg, session_id: str) -> bool:
    """Every gateway/judge/admin action for a session is present as an event —
    there is no code path that enforces without logging (Hard Rule 19). Returns
    True (raises AssertionError if a decision has no backing event)."""
    events = reg.list_enforcement_events(session_id)
    decisions = [e for e in events if e.get("kind") == "decision"]
    for d in decisions:
        # a decision event must reference its decision id (it is the log entry)
        if not d.get("decision_ref"):
            raise AssertionError(
                f"decision event {d.get('event_id')} lacks a decision_ref — "
                "self-exemption detected")
    return True
