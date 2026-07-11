"""ascore — the internal implementation package.

Everything under ``ascore.*`` is **internal** and may change without notice.
The supported, semver'd public surface is the top-level :mod:`agenttic`
umbrella, which re-exports a small, deliberate subset of this package
(SPEC-8 Hard Rule 36). Import from ``agenttic``, not ``ascore``.
"""
from __future__ import annotations

#: Single source of truth for the core version. The ``agenttic`` umbrella
#: tracks this exactly (asserted by tests) and the distribution ``version`` in
#: pyproject.toml is kept in lock-step with it.
__version__ = "0.8.0"
