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

import os
import re

import pytest

# CLI-output assertions (`assert "--store" in result.output`) were passing or
# failing depending on which test ran first. Two causes, both environmental:
# Typer renders errors and help through rich, which (a) inserts colour escapes
# that can land in the middle of the string being searched for, and (b) wraps and
# ELIDES its panels at the detected terminal width — which is how `--store`
# disappeared from a usage message that plainly contains it.
#
# Pinned here, before anything imports agenttic.cli (its module-level `Console()`
# reads the environment once), so these assertions no longer depend on collection
# order or on how wide the terminal happens to be.
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")
os.environ.setdefault("COLUMNS", "200")


#: Strip terminal styling from CLI output before asserting on it. Rich styles
#: numbers and keywords even with colour off, so `"sc-1"` can arrive as
#: `"sc-\x1b[1m1\x1b[0m"` — present to a reader, invisible to `in`.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """CLI output as a human reads it, with the styling removed."""
    return _ANSI.sub("", text)


@pytest.fixture(autouse=True)
def _isolate_agenttic_logger():
    """Undo :func:`server.observability.configure_logging`'s global side effects.

    Building the app installs a handler on the ``agenttic`` logger and sets
    ``propagate = False`` — process-global state that outlives the test that
    caused it. Any later test asserting on an ``agenttic`` log record then finds
    nothing, because pytest's ``caplog`` handler sits on the ROOT logger and
    propagation to it has been switched off. That is how
    ``test_no_target_is_a_logged_no_op`` passes alone and fails after any test
    that builds an app: the assertion is right, the logger was left dirty.

    Snapshot and restore, so log configuration never leaks between tests.
    """
    import logging
    lg = logging.getLogger("agenttic")
    handlers, propagate, level = list(lg.handlers), lg.propagate, lg.level
    yield
    lg.handlers[:] = handlers
    lg.propagate = propagate
    lg.setLevel(level)


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
