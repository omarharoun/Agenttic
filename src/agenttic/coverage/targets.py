"""Where the closure target lives — one definition, supplied by config.

``config.yaml:1`` says "All model names, thresholds, sample rates live here
(Hard Rule 7)". The trace-closure bar the sign-off gates on was written into the
code instead — the same ``0.95`` literal in ``coverage/model.py``,
``coverage/collect.py``, both shipped coverage models and ``schema/signoff.py``.
Five copies of a threshold is five chances for them to disagree, and the one
place an operator would look to change it (``config.yaml``) was not one of them.

The literal survives here as a *fallback*, not as the value. That is deliberate
rather than defensive: a coverage model must be constructible with no config on
disk, because the library API (SPEC-8) is imported into processes that have
never seen this repo's ``config.yaml``.

What this module will not do is *guess* which config it is running under. The
first cut fell back to reading ``config.DEFAULT_PATH`` — the relative literal
``Path("config.yaml")`` — off the process CWD, which is a different question
from "what config did this process load". An operator running
``--config prod.yaml`` had their threshold ignored in silence, and the server,
which gives every workspace its own ``cfg`` (``server/app.py:223``), would have
served one tenant's target to another. A config that was never passed in is now
reported as such.
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

#: The bar closure is measured against when config says nothing. Config wins.
DEFAULT_CLOSURE_TARGET = 0.95

_log = logging.getLogger(__name__)


def closure_target(cfg: dict | None = None) -> float:
    """``coverage.closure_target`` from the config the caller is running under.

    ``cfg`` is the source; there is no other. Never raises: this is called from
    inside coverage-model construction, and a model that cannot be built is a run
    with no coverage at all — strictly worse than a run measured against the
    documented default. Rejecting a *bad* configured value loudly belongs at load
    time (``config._validate_coverage_surface``), where a human is watching.

    Both fallback paths warn, once per distinct call site. A caller with no config
    is legitimate — the SPEC-8 library API runs in processes that have never seen
    this repo — but at this depth it is indistinguishable from a caller that HAS
    a config and forgot to pass it, which is a bug that otherwise reports nothing.
    Naming the call site is what makes the second one findable.
    """
    if cfg is None:
        _warn_once(f"no config supplied ({_call_site()}); trace closure measured "
                   f"against the built-in default {DEFAULT_CLOSURE_TARGET} rather "
                   f"than coverage.closure_target — pass cfg=load_config(...) if "
                   f"this process has one")
        return DEFAULT_CLOSURE_TARGET
    got = _read(cfg)
    if got is None:
        _warn_once(f"config declares no usable coverage.closure_target "
                   f"({_call_site()}); trace closure measured against the "
                   f"built-in default {DEFAULT_CLOSURE_TARGET}")
        return DEFAULT_CLOSURE_TARGET
    return got


def _read(cfg: object) -> float | None:
    """The configured target, or None when absent/unusable."""
    if not isinstance(cfg, dict):
        return None
    block = cfg.get("coverage")
    if not isinstance(block, dict) or "closure_target" not in block:
        return None
    try:
        value = float(block["closure_target"])
    except (TypeError, ValueError):
        return None
    return value if 0.0 < value <= 1.0 else None


@lru_cache(maxsize=None)
def _warn_once(message: str) -> None:
    """One line per distinct message, not one per model construction.

    ``baseline_model()`` is built per request on the capabilities route; an
    un-deduplicated warning there is a log flood, and a log flood is how a real
    warning stops being read.
    """
    _log.warning("coverage closure target: %s", message)


def _call_site(depth: int = 2, frames: int = 2) -> str:
    """``file:line`` of the code that asked, and of *its* caller.

    Two frames because one is not actionable: the immediate caller is always
    ``models/baseline.py`` or ``models/conversational_transactional.py``, and the
    frame that needs fixing is the one above it (``ops.py:304``,
    ``server/routes/capabilities.py:68``).
    """
    out = []
    for d in range(depth, depth + frames):
        try:
            f = sys._getframe(d)
        except (ValueError, AttributeError):
            # ValueError: stack shallower than asked. AttributeError: an
            # interpreter without _getframe — the warning is still worth emitting
            # without the location, so degrade rather than raise from a log line.
            break
        out.append(f"{Path(f.f_code.co_filename).name}:{f.f_lineno}")
    return " <- ".join(out) or "call site unavailable"


@lru_cache(maxsize=8)
def _from_config_file(path: str) -> float | None:
    """Read the target off a config file an operator named explicitly.

    :func:`closure_target` does not call this — a default path resolved against
    the CWD is a guess, and this module does not guess (see the module docstring).
    It stays as the reader for a caller that holds a *path* rather than a loaded
    dict, which is the shape a CLI has before it loads anything.

    Cached because config is process-lifetime in this codebase and a coverage
    model may be constructed per request. The cache is keyed on the path string
    as given, so pass an absolute one — two spellings of the same file are two
    entries, and a relative one is the CWD guess this module exists to refuse.
    """
    try:
        import yaml
        return _read(yaml.safe_load(Path(path).read_text()))
    except Exception:  # noqa: BLE001 — no config on disk is normal for the library
        return None
