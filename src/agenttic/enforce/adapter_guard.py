"""Adapter enforcement guard (SPEC-7 Step 36, T36.3).

The optional ``enforce=`` path for the framework adapters. It routes an agent's
run through the SPEC-4 gateway **at the ramp's non-blocking default posture** —
observe/shadow — so turning it on changes nothing an agent's user can see until
the operator deliberately advances the ramp (Step 39 / M21).

Two hard guarantees the acceptance pins:

* **Fail loud, never silent.** ``enforce=`` without a compiled policy (or without
  a registry to load one from) raises :class:`EnforceConfigError` with a clear
  message. It never silently allows and never silently blocks (Hard Rule 31).
* **Default posture never blocks.** M19 only wires the non-blocking postures
  (``observe``/``shadow``). The inline blocking postures (``enforce_reads`` /
  ``enforce_all``) belong to the Step 39 ramp; requesting one here fails loud
  rather than blocking before the ramp exists — preserving the milestone order.
"""
from __future__ import annotations

from agenttic.enforce.gateway import EnforcementGateway
from agenttic.registry.sqlite_store import NotFoundError

# Non-blocking postures the adapter may enable directly. Blocking postures are
# reached only through the Step 39 ramp (M21), never through the adapter.
_NONBLOCKING = {"observe", "shadow"}
_BLOCKING = {"enforce_reads", "enforce_all"}
_DEFAULT_POSTURE = "shadow"


class EnforceConfigError(RuntimeError):
    """enforce= was requested but cannot be honored safely — surfaced loudly."""


def _resolve_mode(enforce) -> str:
    if enforce is True:
        return _DEFAULT_POSTURE
    mode = str(enforce).lower()
    if mode in _NONBLOCKING:
        return mode
    if mode in _BLOCKING:
        raise EnforceConfigError(
            f"enforce='{mode}' is an inline blocking posture reached only via the "
            "Step 39 enforcement ramp (`agenttic enforce mode`), not the tracing "
            "adapter. Enable it deliberately on the ramp after observability is "
            "proven; the adapter only supports the non-blocking observe/shadow "
            "default.")
    raise EnforceConfigError(
        f"unknown enforce posture '{mode}' (want observe|shadow or True)")


class EnforceGuard:
    """Holds a hash-verified gateway session for a run at a non-blocking posture.

    The guard proves a compiled policy exists and is servable; at observe/shadow
    it computes the decision the gateway *would* make but never blocks."""

    def __init__(self, gateway: EnforcementGateway, agent_id: str, mode: str):
        self.gateway = gateway
        self.agent_id = agent_id
        self.mode = mode
        self._session = None

    def begin(self):
        # start_session verifies the policy's content hash and refuses on
        # mismatch (PolicyIntegrityError) — an integrity failure is never masked.
        self._session = self.gateway.start_session(self.agent_id)
        return self

    def end(self):
        self._session = None

    @property
    def session_id(self):
        return getattr(self._session, "session_id", None)

    def evaluate(self, tool_name: str, args: dict | None = None):
        """Compute the would-be decision for a tool call. At observe/shadow the
        decision is returned for logging; the caller does not act on it (the
        adapter never blocks). Returns None if not in a session."""
        if self._session is None:
            return None
        return self.gateway.evaluate_tool_call(self._session.session_id,
                                               tool_name, args or {})

    def blocks(self) -> bool:
        """Non-blocking postures never block — always False in the adapter."""
        return self.mode in _BLOCKING  # unreachable True: _BLOCKING is rejected


def build_enforce_guard(agent_id: str, enforce, *, reg=None, cfg=None) -> EnforceGuard:
    """Validate the enforce request and build a guard, failing loud on any gap."""
    mode = _resolve_mode(enforce)
    if reg is None:
        raise EnforceConfigError(
            "enforce= requires a registry to load the compiled policy; pass "
            "reg= to trace(). Refusing to enforce without a verifiable policy.")
    try:
        reg.latest_policy(agent_id)
    except NotFoundError as e:
        raise EnforceConfigError(
            f"enforce={enforce!r} but no compiled policy exists for agent "
            f"'{agent_id}'. Compile one from certification evidence first "
            "(the gateway will not serve an agent without a policy).") from e
    gateway = EnforcementGateway(reg, cfg or {})
    return EnforceGuard(gateway, agent_id, mode)
