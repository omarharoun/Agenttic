"""Policy compiler (SPEC-2 T25.1) — pure, config-driven.

``compile_policy(dossier, card, incidents, cfg)`` turns certification evidence
into an :class:`EnforcementPolicy`. **Every mapping lives in config**
(``enforcement.compiler``): tier posture, caps → rule templates, autonomy
scaling, staleness grace-then-tighten, and open-S1/S2 incident pressure. Every
rule's ``origin`` names the mapping that produced it (Hard Rule 20).

The compile is **pure and deterministic**: the same inputs produce a
byte-identical policy (the content hash is its identity). Postures only ever
*tighten* as more pressure is applied.
"""

from __future__ import annotations

from ascore.enforce.gateway import compute_policy_hash
from ascore.schema.enforcement import EnforcementPolicy, Rule

# posture tightening orders
_APPROVALS_ORDER = {"none": 0, "write": 1}
_SERVE_ORDER = {"allow": 0, "deny": 1}


class _Posture:
    """Mutable posture accumulator that only tightens."""

    def __init__(self):
        self.serve = "allow"
        self.serve_origin = ""
        self.approvals = "none"
        self.approvals_origin = ""
        self.lane3_sampling = 0.0
        self.sampling_origin = ""
        self.domain_sampling: dict[str, tuple[float, str]] = {}

    def deny(self, origin: str):
        if _SERVE_ORDER["deny"] > _SERVE_ORDER[self.serve]:
            self.serve, self.serve_origin = "deny", origin

    def require(self, mode: str, origin: str):
        if _APPROVALS_ORDER.get(mode, 0) > _APPROVALS_ORDER[self.approvals]:
            self.approvals, self.approvals_origin = mode, origin

    def sample(self, rate: float, origin: str):
        if rate > self.lane3_sampling:
            self.lane3_sampling, self.sampling_origin = float(rate), origin

    def sample_domain(self, domain: str, rate: float, origin: str):
        cur = self.domain_sampling.get(domain, (0.0, ""))
        if rate > cur[0]:
            self.domain_sampling[domain] = (float(rate), origin)


def _compiler_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("enforcement", {}).get("compiler", {})


def _apply_tier(posture: _Posture, tier: str, ccfg: dict):
    tp = ccfg.get("tier_posture", {}).get(tier, {})
    origin = f"tier_posture:{tier}"
    if tp.get("serve") == "deny":
        posture.deny(origin)
    if tp.get("approvals") == "write":
        posture.require("write", origin)
    if "lane3_sampling" in tp:
        posture.sample(tp["lane3_sampling"], origin)


def _cap_template(ccfg: dict, cap: str) -> tuple[dict, str] | None:
    caps = ccfg.get("caps", {})
    if cap in caps:
        return caps[cap], f"cap:{cap}"
    # wildcard families, e.g. "elicitation_gap:*"
    family = cap.split(":", 1)[0]
    wildcard = f"{family}:*"
    if wildcard in caps:
        return caps[wildcard], f"cap:{cap}"
    return None


def _apply_caps(posture: _Posture, caps_applied: list[str], ccfg: dict):
    for cap in caps_applied:
        tmpl = _cap_template(ccfg, cap)
        if tmpl is None:
            continue
        rule, origin = tmpl
        if rule.get("serve") == "deny":
            posture.deny(origin)
        if rule.get("approvals") == "write":
            posture.require("write", origin)
        if "lane3_sampling_min" in rule:
            posture.sample(rule["lane3_sampling_min"], origin)
        if "lane3_sampling_domain" in rule:
            domain = cap.split(":", 1)[1] if ":" in cap else "all"
            posture.sample_domain(domain, rule["lane3_sampling_domain"], origin)


def _apply_autonomy(posture: _Posture, autonomy_level: str | None, ccfg: dict):
    if not autonomy_level:
        return
    rule = ccfg.get("autonomy", {}).get(autonomy_level)
    if not rule:
        return
    origin = f"autonomy:{autonomy_level}"
    if rule.get("approvals_min") == "write":
        posture.require("write", origin)
    if "lane3_sampling_min" in rule:
        posture.sample(rule["lane3_sampling_min"], origin)


def _apply_staleness(posture: _Posture, status: str | None, ccfg: dict):
    if status == "revoked":
        rvk = ccfg.get("revoked", {})
        if rvk.get("serve") == "deny":
            posture.deny("revoked")
    elif status == "stale":
        then = ccfg.get("staleness", {}).get("then", {})
        origin = "staleness:grace_expired"
        if then.get("approvals") == "write":
            posture.require("write", origin)
        if "lane3_sampling" in then:
            posture.sample(then["lane3_sampling"], origin)


def _apply_incident_pressure(posture: _Posture, open_s1_s2: bool, ccfg: dict):
    if not open_s1_s2:
        return
    rule = ccfg.get("incident_pressure", {}).get("open_s1_s2", {})
    origin = "incident_pressure:open_s1_s2"
    if rule.get("approvals") == "write":
        posture.require("write", origin)
    if "lane3_sampling" in rule:
        posture.sample(rule["lane3_sampling"], origin)


def _materialize_rules(posture: _Posture) -> list[Rule]:
    """Turn the accumulated posture into an ordered, deterministic rule list."""
    rules: list[Rule] = []
    if posture.serve == "deny":
        rules.append(Rule(rule_id="serve-deny", lane="lane1", action="deny",
                          matcher={"all": True}, origin=posture.serve_origin,
                          description="serve:deny"))
    if posture.approvals == "write":
        rules.append(Rule(rule_id="approvals-write", lane="lane1",
                          action="require_approval",
                          matcher={"action_class": "write"},
                          origin=posture.approvals_origin,
                          description="write actions require approval"))
    if posture.lane3_sampling > 0:
        rules.append(Rule(rule_id="lane3-sampling", lane="lane3", action="allow",
                          matcher={"sampling": round(posture.lane3_sampling, 4)},
                          origin=posture.sampling_origin,
                          description="lane-3 async judge sampling rate"))
    for domain in sorted(posture.domain_sampling):
        rate, origin = posture.domain_sampling[domain]
        rules.append(Rule(rule_id=f"lane3-sampling-{domain}", lane="lane3",
                          action="allow",
                          matcher={"domain": domain, "sampling": round(rate, 4)},
                          origin=origin,
                          description=f"lane-3 sampling for {domain}"))
    # deterministic order
    rules.sort(key=lambda r: r.rule_id)
    return rules


def _apply_oversight(posture: _Posture, cfg: dict, rubber_stamp: bool):
    """Oversight-driven tightening (T30.2). Only acts when the config toggle
    ``oversight.posture_toggle`` is on AND sustained rubber-stamping is detected —
    then it TIGHTENS (second approver via write-approvals + raised sampling). With
    the toggle off it is indicator-only (never changes posture). Absent/weak
    oversight may tighten, never relax (Rule 26)."""
    toggle = bool((cfg or {}).get("oversight", {}).get("posture_toggle", False))
    if not (toggle and rubber_stamp):
        return
    origin = "oversight:rubber_stamp"
    posture.require("write", origin)     # require approval (a second approver)
    posture.sample(0.5, origin)          # raise lane-3 sampling


def compile_policy(dossier, card, incidents, cfg: dict, *,
                   status: str | None = None,
                   stage: str | None = None,
                   oversight_rubber_stamp: bool = False) -> EnforcementPolicy:
    """Compile an enforcement policy from certification evidence. ``dossier`` is
    a Dossier, ``card`` an AgentCard or None, ``incidents`` a list of incident
    dicts (with ``severity`` and computed ``state``), ``cfg`` the config.
    ``status`` is the computed certification status (current/stale/revoked)."""
    ccfg = _compiler_cfg(cfg)
    posture = _Posture()

    tier = dossier.tier_decision.tier
    caps = list(dossier.tier_decision.caps_applied or [])
    autonomy_level = _autonomy_from_card(card)
    open_s1_s2 = any(i.get("severity") in ("S1", "S2")
                     and i.get("state", "open") != "closed"
                     for i in (incidents or []))

    _apply_tier(posture, tier, ccfg)
    _apply_caps(posture, caps, ccfg)
    _apply_autonomy(posture, autonomy_level, ccfg)
    _apply_staleness(posture, status, ccfg)
    _apply_incident_pressure(posture, open_s1_s2, ccfg)
    _apply_oversight(posture, cfg, oversight_rubber_stamp)
    # stage dimension: higher-exposure stages are stricter-or-equal (tighten-only)
    if stage:
        from ascore.release.ladder import apply_stage_to_posture
        apply_stage_to_posture(posture, cfg, stage)

    rules = _materialize_rules(posture)
    compiled_from = [dossier.ref()]
    if stage:
        compiled_from.append(f"stage:{stage}")
    if card is not None:
        compiled_from.append(card.ref())
    compiled_from += [f"incident:{i['incident_id']}" for i in (incidents or [])
                      if i.get("severity") in ("S1", "S2")]

    policy = EnforcementPolicy(policy_id="", agent_id=dossier.agent_id,
                              rules=rules, compiled_from=compiled_from)
    policy.content_hash = compute_policy_hash(policy)
    policy.policy_id = f"policy-{dossier.agent_id}-{policy.content_hash[:12]}"
    return policy


def recompile_for_agent(reg, cfg: dict, agent_id: str, *, persist: bool = True):
    """Recompile an agent's enforcement policy from its CURRENT evidence (latest
    dossier + card + open incidents + computed staleness status). Wired to run on
    staleness / evidence change. Idempotent: if the resulting policy hash is
    unchanged, the existing policy is returned and nothing new is written."""
    from ascore.certification.staleness import status as compute_status
    from ascore.registry.sqlite_store import NotFoundError

    dossier = reg.latest_dossier(agent_id)  # raises NotFoundError if none
    try:
        card = reg.get_card(agent_id)
    except NotFoundError:
        card = None
    try:
        from ascore.live.incidents import IncidentManager
        incidents = IncidentManager(reg).list_with_sla(cfg, agent_id=agent_id)
    except Exception:  # noqa: BLE001
        incidents = []
    status = compute_status(reg, dossier)

    # oversight-driven tightening only when the toggle is on (indicator-only off)
    rubber_stamp = False
    if (cfg or {}).get("oversight", {}).get("posture_toggle", False):
        try:
            from ascore.oversight.analytics import approval_analytics
            rubber_stamp = approval_analytics(reg, cfg, agent_id)["rubber_stamp"]
        except Exception:  # noqa: BLE001
            rubber_stamp = False

    policy = compile_policy(dossier, card, incidents, cfg, status=status,
                            oversight_rubber_stamp=rubber_stamp)

    if persist:
        try:
            current = reg.latest_policy(agent_id)
            if current.content_hash == policy.content_hash:
                return current  # unchanged — no-op
        except NotFoundError:
            pass
        reg.save_policy(policy)
        from ascore.schema.enforcement import EnforcementEvent
        import uuid
        reg.append_enforcement_event(EnforcementEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", session_id="",
            agent_id=agent_id, kind="policy_load", actor="compiler",
            policy_hash=policy.content_hash,
            detail={"recompiled": True, "status": status,
                    "compiled_from": policy.compiled_from}))
    return policy


def posture_summary(policy) -> dict:
    """A human/UI-friendly posture summary derived from the policy rules alone —
    what a public verify/card page shows as "enforced under policy <hash>"."""
    p = _posture_from_rules(policy.rules)
    return {
        "policy_hash": policy.content_hash,
        "serve": p.serve,
        "approvals": p.approvals,
        "lane3_sampling": p.lane3_sampling,
        "domain_sampling": {k: v[0] for k, v in p.domain_sampling.items()},
        "compiled_from": list(policy.compiled_from),
    }


class OverrideError(ValueError):
    """A manual override attempted to LOOSEN a policy. The message names the
    exact diff (Hard Rule 20: manual changes only tighten)."""


def _posture_from_rules(rules) -> _Posture:
    p = _Posture()
    for r in rules:
        if r.rule_id == "serve-deny":
            p.serve, p.serve_origin = "deny", r.origin
        elif r.rule_id == "approvals-write":
            p.approvals, p.approvals_origin = "write", r.origin
        elif r.rule_id == "lane3-sampling":
            p.lane3_sampling = float(r.matcher.get("sampling", 0))
            p.sampling_origin = r.origin
        elif r.rule_id.startswith("lane3-sampling-"):
            dom = r.rule_id[len("lane3-sampling-"):]
            p.domain_sampling[dom] = (float(r.matcher.get("sampling", 0)), r.origin)
    return p


def apply_overrides(policy: EnforcementPolicy, overrides: dict,
                    origin: str = "override") -> EnforcementPolicy:
    """Apply a manual override to a compiled policy — **tighten-only**. Any key
    that would loosen the posture is rejected naming the diff. Returns a new
    (rehashed) policy."""
    base = _posture_from_rules(policy.rules)

    if "serve" in overrides:
        if overrides["serve"] == "allow" and base.serve == "deny":
            raise OverrideError(
                "loosening rejected: serve deny -> allow")
        if overrides["serve"] == "deny":
            base.deny(origin)
    if "approvals" in overrides:
        want = overrides["approvals"]
        if _APPROVALS_ORDER.get(want, 0) < _APPROVALS_ORDER[base.approvals]:
            raise OverrideError(
                f"loosening rejected: approvals {base.approvals} -> {want}")
        base.require(want, origin)
    if "lane3_sampling" in overrides:
        want = float(overrides["lane3_sampling"])
        if want < base.lane3_sampling:
            raise OverrideError(
                f"loosening rejected: lane3_sampling {base.lane3_sampling} -> {want}")
        base.sample(want, origin)
    for dom, rate in (overrides.get("domain_sampling") or {}).items():
        cur = base.domain_sampling.get(dom, (0.0, ""))[0]
        if float(rate) < cur:
            raise OverrideError(
                f"loosening rejected: domain_sampling[{dom}] {cur} -> {rate}")
        base.sample_domain(dom, float(rate), origin)

    rules = _materialize_rules(base)
    new = EnforcementPolicy(policy_id="", agent_id=policy.agent_id, rules=rules,
                           compiled_from=policy.compiled_from + [f"{origin}"])
    new.content_hash = compute_policy_hash(new)
    new.policy_id = f"policy-{policy.agent_id}-{new.content_hash[:12]}"
    return new


def _autonomy_from_card(card) -> str | None:
    if card is None:
        return None
    fv = card.fields.get("autonomy_control.autonomy_level_and_planning_depth")
    if fv is None or fv.status != "value_present":
        return None
    # value looks like "L4 (approver)"
    val = str(fv.value)
    return val.split(" ", 1)[0] if val[:1] == "L" else None
