"""Risk webhooks (SPEC-2 T34.2).

Fires on ``feeds.webhook_events`` (tier_change, revocation, incident_s1_s2,
stage_demotion). Trigger points ENQUEUE an append-only webhook event (cheap, no
network); a dispatcher delivers pending events to configured URLs (SSRF-checked).
Payloads are aggregate signals only — no traces/payloads/PII (Hard Rule 30).
"""

from __future__ import annotations

import uuid

from agenttic.schema.enforcement import EnforcementEvent

# canonical event types
TIER_CHANGE = "tier_change"
REVOCATION = "revocation"
INCIDENT_S1_S2 = "incident_s1_s2"
STAGE_DEMOTION = "stage_demotion"


def _enabled_events(cfg: dict) -> set[str]:
    return set((cfg or {}).get("feeds", {}).get("webhook_events", []))


def enqueue_webhook(reg, cfg: dict, event_type: str, agent_id: str,
                    detail: dict | None = None) -> str | None:
    """Enqueue a webhook (append-only) if the event type is enabled. Returns the
    event id, or None if the type is disabled."""
    if event_type not in _enabled_events(cfg):
        return None
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    reg.append_enforcement_event(EnforcementEvent(
        event_id=event_id, session_id="", agent_id=agent_id, kind="webhook",
        actor="feeds", detail={"webhook_event": event_type, "state": "pending",
                               **(detail or {})}))
    return event_id


def pending_webhooks(reg, agent_id: str | None = None) -> list[dict]:
    delivered = set()
    pending = {}
    for e in reg.list_enforcement_events(None, agent_id):
        d = e.get("detail") or {}
        if e.get("kind") == "webhook" and d.get("webhook_event"):
            if d.get("state") == "delivered":
                delivered.add(d.get("delivers"))
            elif d.get("state") == "pending":
                pending[e["event_id"]] = e
    return [e for eid, e in pending.items() if eid not in delivered]


def deliver_pending(reg, cfg: dict, sender, agent_id: str | None = None
                    ) -> list[dict]:
    """Deliver pending webhooks via ``sender(url, payload) -> status``. Records a
    delivery event per (webhook, url). URLs come from ``feeds.webhook_urls`` and
    are SSRF-validated. Returns the delivery results."""
    urls = (cfg or {}).get("feeds", {}).get("webhook_urls", [])
    results = []
    for e in pending_webhooks(reg, agent_id):
        d = e["detail"]
        payload = {"event": d["webhook_event"], "agent_id": e["agent_id"],
                   **{k: v for k, v in d.items()
                      if k not in ("webhook_event", "state")}}
        for url in urls:
            ok, reason = _safe_url(url, cfg)
            status = sender(url, payload) if ok else f"blocked:{reason}"
            results.append({"webhook": e["event_id"], "url": url, "status": status})
        # mark delivered (append-only, references the source webhook)
        reg.append_enforcement_event(EnforcementEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", session_id="",
            agent_id=e["agent_id"], kind="webhook", actor="feeds",
            detail={"webhook_event": d["webhook_event"], "state": "delivered",
                    "delivers": e["event_id"]}))
    return results


def _safe_url(url: str, cfg: dict) -> tuple[bool, str]:
    from agenttic.security import validate_blackbox_url
    try:
        validate_blackbox_url(url, cfg=cfg, resolve=False)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
