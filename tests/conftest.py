"""Shared test fixtures.

Test isolation for the login-lockout counter: :mod:`ascore.server.routes.auth`
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
        from ascore.server.routes import auth
        auth._attempts.clear()
    except Exception:  # noqa: BLE001 — never let hygiene break collection
        pass
    yield
