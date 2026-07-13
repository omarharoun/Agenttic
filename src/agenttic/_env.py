"""Environment-variable back-compat shim for the ``ascore`` → ``agenttic`` rename.

Every variable that used to be read as ``ASCORE_<NAME>`` is now read
``AGENTTIC_<NAME>``-first, falling back to the legacy ``ASCORE_<NAME>`` (with a
``DeprecationWarning``) so existing deployments keep working unchanged.

This is load-bearing in production: node1's ``.env`` supplies
``ASCORE_CERT_SIGNING_KEY`` and ``ASCORE_PASSPORT_SIGNING_KEY``. If the renamed
code stopped honoring the ``ASCORE_*`` names, cert/passport signing would fail
closed and the app would 502. Do NOT rename those vars on the host; the shim
keeps reading them (while nudging operators toward ``AGENTTIC_*``).

Precedence, for a shimmed name ``X``:

    AGENTTIC_X  >  ASCORE_X (deprecated)  >  default

Only names that already start with ``ASCORE_`` / ``AGENTTIC_`` participate in
the dance; any other name (``ANTHROPIC_API_KEY``, ``FI_API_KEY``, …) is read
verbatim, exactly like ``os.environ.get``.
"""

from __future__ import annotations

import os
import warnings
from typing import Mapping

_NEW_PREFIX = "AGENTTIC_"
_OLD_PREFIX = "ASCORE_"


def _suffix(name: str) -> str | None:
    """The bare suffix of a shimmed var (``ASCORE_DB``/``AGENTTIC_DB`` → ``DB``),
    or ``None`` if ``name`` is not part of the rename shim."""
    if name.startswith(_NEW_PREFIX):
        return name[len(_NEW_PREFIX):]
    if name.startswith(_OLD_PREFIX):
        return name[len(_OLD_PREFIX):]
    return None


def candidate_names(name: str) -> tuple[str, str | None]:
    """Return ``(new_name, old_name)`` for a shimmed var, or ``(name, None)`` for
    a name that does not participate in the rename shim."""
    suf = _suffix(name)
    if suf is None:
        return name, None
    return _NEW_PREFIX + suf, _OLD_PREFIX + suf


def warn_legacy(old: str, new: str, *, stacklevel: int = 3) -> None:
    """Emit the deprecation nudge for reading a legacy ``ASCORE_*`` var."""
    warnings.warn(
        f"Environment variable {old} is deprecated; set {new} instead "
        f"({old} is still honored for backward compatibility).",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def get_env(name: str, default: str | None = None, *,
            environ: Mapping[str, str] | None = None) -> str | None:
    """Drop-in for ``os.environ.get(name, default)`` with rename back-compat.

    For an ``ASCORE_*``/``AGENTTIC_*`` name: prefer ``AGENTTIC_<X>``; else fall
    back to ``ASCORE_<X>`` (emitting a ``DeprecationWarning``); else return
    ``default``. For any other name, behaves exactly like ``os.environ.get``.
    """
    env = os.environ if environ is None else environ
    new, old = candidate_names(name)
    if new in env:
        return env[new]
    if old is not None and old in env:
        warn_legacy(old, new)
        return env[old]
    return default
