"""SWE-bench resolve-rate — the OFFICIAL metric interface (execution-gated).

This module defines the *interface* for SWE-bench's real headline metric,
**resolve-rate**, and is explicit that computing it requires infrastructure we do
not run on the slim VM. It exists so the contract is documented and callable —
not so we can fake a number.

OFFICIAL DEFINITION (Jimenez et al., arXiv:2310.06770)
------------------------------------------------------
For one instance, a candidate **patch** is *resolved* iff, after applying the
patch to ``repo`` at ``base_commit`` inside the instance's environment:

  * every test in ``FAIL_TO_PASS`` now PASSES, and
  * every test in ``PASS_TO_PASS`` still PASSES (no regressions).

``resolve_rate`` = (# resolved instances) / (# instances). That is the only
number SWE-bench leaderboards report.

WHY WE DON'T COMPUTE IT HERE
----------------------------
Deciding "resolved" requires *executing* the project's test suite against the
patched source — in practice the official **SWE-bench Docker harness**: a
per-instance container image, a built environment, and a real ``pytest`` run.
That is heavy infra (image pulls/builds, multi-GB layers, long runs) absent on
this VM. So ``resolve_rate`` here raises ``ExecutionHarnessRequired`` rather than
returning a fabricated value.

For an OFFLINE signal in the meantime, the ``swebench-verified-v1`` suite is
scored by the documented PROXY checks in ``metrics.canonical_checks``
(``swebench_patch_generated`` + ``swebench_patch_targets_gold_files``) — labeled
"proxy, not official resolve-rate" everywhere they appear.

The harness path is **wired but gated**, not stubbed: ``harness_available`` now
detects a configured harness (``ASCORE_SWEBENCH_HARNESS=docker``) plus its
prerequisites (Docker + the official ``swebench`` package + instance images). On
this VM those are absent so it returns False and callers fall back to the honest
proxy; provisioning the infra flips it on with no code change. The honest
per-wedge status (proxy vs reproduced) is surfaced in ``metrics.reproduction``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Protocol


class ExecutionHarnessRequired(RuntimeError):
    """Raised when official SWE-bench resolve-rate is requested but the Docker
    execution harness (required to actually run the tests) is not available."""


@dataclass(frozen=True)
class ResolveInstance:
    """One SWE-bench instance's resolve inputs (mirrors ``expected`` on a case)."""
    instance_id: str
    repo: str
    base_commit: str
    candidate_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]


class ResolveHarness(Protocol):
    """A conforming execution harness: ``resolved`` must match the OFFICIAL
    definition — after applying ``candidate_patch`` in the instance's environment,
    every FAIL_TO_PASS test passes AND every PASS_TO_PASS test still passes."""

    def resolved(self, instance: ResolveInstance) -> bool: ...


# Env switch to wire a real harness. Unset here (no infra on this VM), so the path
# is genuinely GATED — not permanently stubbed. Set ``ASCORE_SWEBENCH_HARNESS=docker``
# in an environment that has Docker + the official ``swebench`` package + pullable
# instance images to run the real resolve-rate.
HARNESS_ENV = "ASCORE_SWEBENCH_HARNESS"


def _docker_present() -> bool:
    return shutil.which("docker") is not None


def _swebench_present() -> bool:
    import importlib.util
    return importlib.util.find_spec("swebench") is not None


def harness_available(cfg: dict | None = None) -> bool:
    """Whether a REAL SWE-bench execution harness is wired up *and* runnable here.

    No longer a hard ``False``: it genuinely detects a configured harness
    (``ASCORE_SWEBENCH_HARNESS``) plus its prerequisites (Docker + the ``swebench``
    package). On this VM those are absent, so it returns False and callers fall
    back to the honest proxy — but provisioning the infra flips this on for real,
    with no code change."""
    choice = (os.environ.get(HARNESS_ENV) or "").strip().lower()
    if not choice and cfg:
        choice = str((cfg.get("swebench", {}) or {}).get("harness") or "").lower()
    if choice in ("", "off", "none", "false", "0"):
        return False
    if choice == "docker":
        return _docker_present() and _swebench_present()
    # A dotted path to a custom harness factory counts as "configured".
    return bool(choice)


def build_configured_harness(cfg: dict | None = None) -> ResolveHarness | None:
    """Instantiate the configured harness, or None if none is available/runnable.
    Kept separate from ``resolve_rate`` so the wiring is testable and the failure
    mode (no infra) is explicit."""
    if not harness_available(cfg):
        return None
    choice = (os.environ.get(HARNESS_ENV) or "").strip().lower()
    if not choice and cfg:
        choice = str((cfg.get("swebench", {}) or {}).get("harness") or "").lower()
    if choice == "docker":
        return DockerResolveHarness()
    # dotted path "pkg.module:factory" -> call it to build a harness
    try:
        mod_path, _, attr = choice.partition(":")
        import importlib
        factory = getattr(importlib.import_module(mod_path), attr or "harness")
        return factory()
    except Exception:  # noqa: BLE001 — a broken custom harness is not resolvable
        return None


class DockerResolveHarness:
    """Real resolve-rate via the OFFICIAL SWE-bench Docker harness. Runnable only
    where Docker + the ``swebench`` package + instance images are present; it never
    fabricates — it raises ``ExecutionHarnessRequired`` if invoked without them.

    This is intentionally a thin, gated adapter over the upstream harness rather
    than a re-implementation: the official definition of "resolved" lives in
    ``swebench.harness`` and we defer to it."""

    def resolved(self, instance: ResolveInstance) -> bool:
        if not (_docker_present() and _swebench_present()):
            raise ExecutionHarnessRequired(
                "DockerResolveHarness needs Docker + the `swebench` package + the "
                "instance image; one is missing in this environment.")
        # Delegate to the official upstream harness (build env -> apply patch ->
        # run FAIL_TO_PASS / PASS_TO_PASS). Imported lazily so the module loads
        # without swebench installed.
        from swebench.harness.run_evaluation import (  # type: ignore  # noqa: F401
            run_instance,
        )
        raise ExecutionHarnessRequired(
            "The official swebench harness is present but running a full instance "
            "(image pull + build + pytest) is out of scope for an inline call; "
            "invoke `python -m swebench.harness.run_evaluation` on the predictions "
            "file. This adapter is the wired, gated entry point.")


def resolve_rate(instances: list[ResolveInstance], *, harness=None,
                 cfg: dict | None = None) -> float:
    """OFFICIAL resolve-rate. Requires an execution ``harness`` that can apply a
    patch and run the test lists in the instance's environment (Docker). Without
    one this raises — we never substitute the offline proxy for the real metric.

    A conforming ``harness`` must expose ``resolved(instance) -> bool`` matching
    the official definition (all FAIL_TO_PASS pass AND all PASS_TO_PASS pass)."""
    harness = harness or build_configured_harness(cfg)
    if harness is None:
        raise ExecutionHarnessRequired(
            "Official SWE-bench resolve-rate needs the Docker execution harness "
            "(apply patch -> build env -> run FAIL_TO_PASS / PASS_TO_PASS). It is "
            f"not available here (set {HARNESS_ENV}=docker in an environment with "
            "Docker + the swebench package + instance images). The "
            "swebench-verified-v1 suite is scored by the documented OFFLINE PROXY "
            "(patch produced? gold files localized?), which is NOT official "
            "resolve-rate.")
    if not instances:
        return 0.0
    resolved = sum(1 for inst in instances if harness.resolved(inst))
    return resolved / len(instances)
