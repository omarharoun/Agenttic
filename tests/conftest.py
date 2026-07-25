"""Shared test fixtures.

Test isolation for the login-lockout counter: :mod:`agenttic.server.routes.auth`
keeps a module-global ``_attempts`` dict (failed-login counts per email) that is
only cleared on a successful login. Several test files reuse the same email
(e.g. ``a@b.com``), so failed logins in one test can accumulate and spuriously
lock out an unrelated test later in a full-suite run (with ``login_max_attempts``
as low as 3 in test configs). Reset it before every test so login-lockout
behaviour is deterministic and per-test. Pure test hygiene — no production code
path changes.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_login_lockout():
    try:
        from agenttic.server.routes import auth
        auth._attempts.clear()
    except Exception:  # noqa: BLE001 — never let hygiene break collection
        pass
    yield


def attesting_signoff(*, agent_id: str = "pilot", config_hash: str = "cfg-abc123"):
    """A minimal sign-off that PASSES the signing gate.

    Signing now requires evidence that signs off (``sign_manifest`` raises
    ``SignoffRefused`` otherwise), so any test that needs a signed manifest needs
    one of these. Deliberately the smallest thing that satisfies
    :attr:`VerificationSignoff.signs_off`: closed coverage and clean assertions.
    The formal/convergence/regression legs are absent on purpose — they do not
    gate, and pretending they ran would misrepresent what the gate requires.

    Tests asserting the *refusal* path should build their own negative sign-off
    rather than mutating this one.
    """
    from agenttic.schema.signoff import (
        AssertionLeg, CoverageLeg, VerificationSignoff)
    return VerificationSignoff(
        signoff_id=f"so-{agent_id}", agent_id=agent_id,
        agent_config_hash=config_hash,
        coverage=CoverageLeg(
            status="populated", closed=True, trace_closure=0.97,
            model_ref="cov-test@v1", bins_fingerprint="fp-test"),
        assertions=AssertionLeg(
            status="populated", total=8, violations=0, unexercised=0,
            exercised_ratio=1.0, assertion_set_ref="assertions:builtin-default@v1"))


@pytest.fixture
def signoff():
    """Fixture form of :func:`attesting_signoff`."""
    return attesting_signoff()
