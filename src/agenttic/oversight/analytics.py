"""Approval-quality analytics (SPEC-2 T30.1).

Computes aggregate process-health signals over the approval + enforcement event
streams. This measures the *oversight process*, not any individual reviewer.
"""

from __future__ import annotations

from datetime import datetime


def _reflexive_seconds(cfg: dict) -> float:
    return float((cfg or {}).get("oversight", {}).get("reflexive_under_seconds", 3))


def _rubber_stamp_threshold(cfg: dict) -> float:
    return float((cfg or {}).get("oversight", {}).get("rubber_stamp_threshold", 0.6))


def approval_analytics(reg, cfg: dict, agent_id: str | None = None) -> dict:
    approvals = reg.list_approvals(None)
    if agent_id is not None:
        approvals = [a for a in approvals if a.get("agent_id") == agent_id]

    resolved = [a for a in approvals if a.get("state") in ("approved", "denied")]
    approved = [a for a in resolved if a["state"] == "approved"]
    denied = [a for a in resolved if a["state"] == "denied"]

    # latency distribution
    latencies = []
    reflexive = 0
    reflexive_s = _reflexive_seconds(cfg)
    for a in resolved:
        created, done = a.get("created_at"), a.get("resolved_at")
        if created and done:
            try:
                dt = (datetime.fromisoformat(done)
                      - datetime.fromisoformat(created)).total_seconds()
                latencies.append(dt)
                if dt < reflexive_s:
                    reflexive += 1
            except Exception:  # noqa: BLE001
                continue

    n = len(resolved)
    approval_rate = round(len(approved) / n, 4) if n else 0.0
    reflexive_rate = round(reflexive / n, 4) if n else 0.0

    # approve-without-viewing (only when a UI "viewed" signal is present)
    without_viewing = sum(1 for a in approved
                          if a.get("resolver_identity") and a.get("viewed") is False)

    # override-of-deny rate: false-positive overrides (reviewer marked a blocked
    # decision benign) relative to blocks
    events = reg.list_enforcement_events(None, agent_id)
    overrides = sum(1 for e in events
                    if (e.get("detail") or {}).get("checker_eval_case"))
    blocks = sum(1 for e in events
                 if e.get("kind") == "decision" and e.get("action") == "deny")
    override_of_deny_rate = round(overrides / blocks, 4) if blocks else 0.0

    # post-approval incident attribution: incidents opened after the last approval
    post_approval_incidents = _post_approval_incidents(reg, cfg, agent_id, approved)

    # rubber-stamp indicator: fast + near-total approval (aggregate)
    threshold = _rubber_stamp_threshold(cfg)
    rubber_stamp = (n > 0 and reflexive_rate >= threshold
                    and approval_rate >= threshold)

    return {
        "n_resolved": n,
        "approval_rate": approval_rate,
        "reflexive_rate": reflexive_rate,
        "reflexive_under_seconds": reflexive_s,
        "latency": {
            "count": len(latencies),
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "approve_without_viewing": without_viewing,
        "override_of_deny_rate": override_of_deny_rate,
        "post_approval_incidents": post_approval_incidents,
        "rubber_stamp": rubber_stamp,
        "rubber_stamp_threshold": threshold,
    }


def _post_approval_incidents(reg, cfg, agent_id, approved) -> int:
    if not approved:
        return 0
    times = [datetime.fromisoformat(a["resolved_at"]) for a in approved
             if a.get("resolved_at")]
    if not times:
        return 0
    earliest = min(times)
    count = 0
    from agenttic.live.incidents import IncidentManager
    for row in IncidentManager(reg).list_with_sla(cfg, agent_id=agent_id):
        try:
            opened = datetime.fromisoformat(row["opened_at"])
        except Exception:  # noqa: BLE001
            continue
        if opened >= earliest:
            count += 1
    return count
