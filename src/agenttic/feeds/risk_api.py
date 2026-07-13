"""Underwriter / procurement risk feed (SPEC-2 T34.1).

A single authenticated, versioned JSON view of an agent's safety posture, built
entirely from aggregate signals: tier + certification status, enforcement
posture, incident counts + SLA adherence, block/approval/canary rates, oversight
health, and passport validity. **No traces, payloads, or PII** (Hard Rule 30) —
the feed must agree with what an independent verifier SDK would conclude.
"""

from __future__ import annotations

from agenttic.registry.sqlite_store import NotFoundError

FEED_VERSION = "agenttic-risk-feed/v1"


def risk_feed(reg, cfg: dict, agent_id: str) -> dict:
    from agenttic.certification.staleness import status as cert_status
    from agenttic.enforce.compiler import posture_summary
    from agenttic.enforce.dashboard import dashboard_metrics
    from agenttic.live.incidents import IncidentManager
    from agenttic.oversight.analytics import approval_analytics

    feed: dict = {"feed_version": FEED_VERSION, "agent_id": agent_id}

    # tier + certification status
    try:
        dossier = reg.latest_dossier(agent_id)
        feed["certification"] = {
            "tier": dossier.tier_decision.tier,
            "status": cert_status(reg, dossier),
            "attestation": dossier.attestation.mode,
            "dossier_sha256": dossier.content_sha256,
        }
    except NotFoundError:
        feed["certification"] = None

    # enforcement posture
    try:
        policy = reg.latest_policy(agent_id)
        ps = posture_summary(policy)
        feed["posture"] = {"policy_hash": ps["policy_hash"], "serve": ps["serve"],
                           "approvals": ps["approvals"],
                           "lane3_sampling": ps["lane3_sampling"]}
    except NotFoundError:
        feed["posture"] = None

    # release stage
    try:
        from agenttic.release.ladder import agent_stage
        feed["stage"] = agent_stage(reg, agent_id)
    except Exception:  # noqa: BLE001
        feed["stage"] = None

    # incident counts + SLA adherence
    incidents = IncidentManager(reg).list_with_sla(cfg, agent_id=agent_id)
    by_sev: dict[str, int] = {}
    open_count = overdue = 0
    for i in incidents:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
        if i["state"] != "closed":
            open_count += 1
            if i["overdue"]:
                overdue += 1
    feed["incidents"] = {
        "total": len(incidents), "open": open_count, "by_severity": by_sev,
        "overdue": overdue,
        "sla_adherence": round(1.0 - (overdue / open_count), 4) if open_count else 1.0,
    }

    # enforcement rates
    dash = dashboard_metrics(reg, agent_id)
    feed["enforcement"] = {
        "decisions": dash["decisions"], "block_rate": dash["block_rate"],
        "fail_open_count": dash["fail_open_count"],
        "lane2_flags": dash["lane2_flags"],
    }

    # canary trip rate
    canary_trips = sum(1 for e in reg.list_enforcement_events(None, agent_id)
                       if e.get("kind") == "canary")
    feed["canaries"] = {"trips": canary_trips}

    # oversight health
    oa = approval_analytics(reg, cfg, agent_id)
    feed["oversight"] = {
        "approval_rate": oa["approval_rate"],
        "reflexive_rate": oa["reflexive_rate"],
        "rubber_stamp": oa["rubber_stamp"],
    }

    # passport validity (aggregate — count active vs revoked, no passport bodies)
    passports = reg.list_passports(agent_id)
    feed["passports"] = {
        "total": len(passports),
        "active": sum(1 for p in passports if p["status"] == "active"),
        "revoked": sum(1 for p in passports if p["status"] == "revoked"),
    }

    return feed
