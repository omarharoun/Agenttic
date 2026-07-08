"""Progressive enforcement ramp (SPEC-7 Step 39, T39.1).

A per-agent enforcement **mode** layered on top of the SPEC-4 gateway. The mode
selects *how much* of the compiled policy is actually enforced, strictly ordered:

    observe        → log only; nothing is evaluated for blocking
    shadow         → compute the decision the gateway *would* make, log it, but
                     let everything through (non-blocking)
    enforce_reads  → block only read-class calls per policy; write-class stays
                     shadowed
    enforce_all    → full policy, including write approvals

The mode is config-and-API driven and its changes are **append-only events with
actor identity**. Advancing is deliberate; stepping down (…→ observe) is always
permitted as a safety valve. Crucially, a mode change **never loosens the
compiled policy** — the policy is immutable evidence-compiled state; the ramp only
chooses how much of it bites (Hard Rule 35, and SPEC-4 Rule 20 still governs the
policy itself). Shadow evaluation + the would-be-block report live in
:mod:`ascore.enforce.ramp` too (T39.2).
"""
from __future__ import annotations

import uuid

from ascore.schema.enforcement import EnforcementEvent

# Strict order — advancing raises the posture, stepping down lowers it.
MODES = ["observe", "shadow", "enforce_reads", "enforce_all"]
_ORDER = {m: i for i, m in enumerate(MODES)}
DEFAULT_MODE = "observe"

# A decision is "blocking" when it would stop or gate the call. allow/transform
# let the (possibly modified) call proceed.
_NONBLOCKING_ACTIONS = {"allow", "transform"}

_RAMP_EVENT = "ramp_mode"


class RampError(ValueError):
    """Invalid ramp operation (unknown mode, etc.)."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def is_blocking(action: str) -> bool:
    return action not in _NONBLOCKING_ACTIONS


def current_mode(reg, agent_id: str) -> str:
    """The agent's current enforcement mode (latest logged change; default observe)."""
    latest = None
    for e in reg.list_enforcement_events(None, agent_id):
        if e.get("kind") == _RAMP_EVENT:
            to = (e.get("detail") or {}).get("to")
            if to in _ORDER:
                latest = to  # events are returned in append order → last wins
    return latest or DEFAULT_MODE


def mode_history(reg, agent_id: str) -> list[dict]:
    """Append-only history of mode changes (actor + from/to + timestamp)."""
    out = []
    for e in reg.list_enforcement_events(None, agent_id):
        if e.get("kind") == _RAMP_EVENT:
            d = e.get("detail") or {}
            out.append({"event_id": e.get("event_id"), "actor": e.get("actor"),
                        "from": d.get("from"), "to": d.get("to"),
                        "created_at": e.get("created_at")})
    return out


def set_mode(reg, agent_id: str, mode: str, actor: str) -> dict:
    """Change the agent's enforcement mode. Append-only, actor-stamped.

    Any transition is allowed — including skipping straight to ``enforce_all`` by
    explicit action, and stepping all the way down to ``observe`` (the safety
    valve). This records posture only; it does not touch the compiled policy, so
    it can never loosen it (see :func:`assert_policy_unchanged`)."""
    if mode not in _ORDER:
        raise RampError(f"unknown enforcement mode '{mode}' (want one of {MODES})")
    if not actor:
        raise RampError("a mode change must carry an actor identity")
    prev = current_mode(reg, agent_id)
    direction = ("advance" if _ORDER[mode] > _ORDER[prev]
                 else "step_down" if _ORDER[mode] < _ORDER[prev] else "noop")
    event_id = _new_id("evt")
    reg.append_enforcement_event(EnforcementEvent(
        event_id=event_id, session_id="ramp", agent_id=agent_id,
        kind=_RAMP_EVENT, actor=actor,
        detail={"from": prev, "to": mode, "direction": direction}))
    return {"event_id": event_id, "agent_id": agent_id, "from": prev,
            "to": mode, "direction": direction, "actor": actor}


def effective_action(action: str, action_class: str, mode: str) -> dict:
    """Given the gateway's decision + the agent's mode, what actually happens.

    Returns ``enforced`` (is this class enforced in this mode), ``blocked`` (was
    the call actually stopped), and ``would_block`` (the policy would have
    blocked but the mode let it through — the shadow signal)."""
    if mode not in _ORDER:
        raise RampError(f"unknown enforcement mode '{mode}'")
    blocking = is_blocking(action)
    if mode in ("observe", "shadow"):
        enforced = False
    elif mode == "enforce_reads":
        enforced = action_class == "read"
    else:  # enforce_all
        enforced = True
    blocked = blocking and enforced
    would_block = blocking and not blocked
    return {
        "mode": mode,
        "enforced": enforced,
        "blocking": blocking,
        "blocked": blocked,
        "would_block": would_block,
        "effective_action": action if blocked else "allow",
    }


def assert_policy_unchanged(reg, agent_id: str, before_hash: str) -> None:
    """Invariant guard: a mode change must never alter the compiled policy. Call
    with the policy hash captured before the change; raises if it moved."""
    from ascore.registry.sqlite_store import NotFoundError
    try:
        after = reg.latest_policy(agent_id).content_hash
    except NotFoundError:
        after = ""
    if before_hash and after and before_hash != after:
        raise RampError(
            f"mode change altered the compiled policy for {agent_id} "
            f"({before_hash[:12]} → {after[:12]}); ramp must never touch policy")
