"""Enforcement dashboard metrics (SPEC-2 T27.1).

Aggregates the append-only enforcement log into the numbers the dashboard shows:
decision counts, block rate, lane-2 flags, fail-open count, approval latency, and
the false-positive feedback surface. Pure over registry reads; renders from the
exported JSON alone (empty registry ⇒ zeros).
"""

from __future__ import annotations

from datetime import datetime


def dashboard_metrics(reg, agent_id: str | None = None,
                      session_id: str | None = None) -> dict:
    events = reg.list_enforcement_events(session_id, agent_id)
    decisions = [e for e in events if e.get("kind") == "decision"]
    n = len(decisions)
    by_action: dict[str, int] = {}
    lane2_flags = 0
    fail_open = 0
    for e in decisions:
        act = e.get("action") or "allow"
        by_action[act] = by_action.get(act, 0) + 1
        detail = e.get("detail") or {}
        if detail.get("lane") == "lane2":
            lane2_flags += 1
        if detail.get("fail_open"):
            fail_open += 1
    blocks = by_action.get("deny", 0) + by_action.get("terminate_session", 0) \
        + by_action.get("revoke_access", 0)

    # approval latency: parked → resolved, from approval events
    latencies = _approval_latencies(reg, session_id)

    return {
        "decisions": n,
        "by_action": by_action,
        "block_rate": round(blocks / n, 4) if n else 0.0,
        "lane2_flags": lane2_flags,
        "fail_open_count": fail_open,
        "approval_latency_seconds": latencies,
        "hardening_candidates": sum(
            1 for e in events if e.get("kind") == "admin"
            and "hardening_candidate" in (e.get("detail") or {})),
        "checker_eval_cases": sum(
            1 for e in events if e.get("kind") == "admin"
            and (e.get("detail") or {}).get("checker_eval_case")),
    }


def _approval_latencies(reg, session_id: str | None) -> dict:
    approvals = reg.list_approvals(session_id)
    durations = []
    for a in approvals:
        created = a.get("created_at")
        resolved = a.get("resolved_at")
        if created and resolved:
            try:
                dt = (datetime.fromisoformat(resolved)
                      - datetime.fromisoformat(created)).total_seconds()
                durations.append(dt)
            except Exception:  # noqa: BLE001
                continue
    if not durations:
        return {"count": 0, "mean": None, "max": None}
    return {"count": len(durations),
            "mean": round(sum(durations) / len(durations), 2),
            "max": round(max(durations), 2)}
