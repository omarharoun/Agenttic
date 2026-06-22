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
"proxy, not official resolve-rate" everywhere they appear. Wiring the real Docker
harness is a tracked FUTURE INFRA task.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def harness_available() -> bool:
    """Whether a real SWE-bench execution harness is wired up. Always False here —
    flipping this on is the future infra task (build/run per-instance containers,
    apply the patch, run FAIL_TO_PASS / PASS_TO_PASS)."""
    return False


def resolve_rate(instances: list[ResolveInstance], *, harness=None) -> float:
    """OFFICIAL resolve-rate. Requires an execution ``harness`` that can apply a
    patch and run the test lists in the instance's environment (Docker). Without
    one this raises — we never substitute the offline proxy for the real metric.

    A conforming ``harness`` must expose ``resolved(instance) -> bool`` matching
    the official definition (all FAIL_TO_PASS pass AND all PASS_TO_PASS pass)."""
    if harness is None or not harness_available():
        raise ExecutionHarnessRequired(
            "Official SWE-bench resolve-rate needs the Docker execution harness "
            "(apply patch -> build env -> run FAIL_TO_PASS / PASS_TO_PASS). It is "
            "not available here; the swebench-verified-v1 suite is scored by the "
            "documented OFFLINE PROXY (patch produced? gold files localized?), "
            "which is NOT official resolve-rate. Wiring the harness is a future "
            "infra task.")
    if not instances:
        return 0.0
    resolved = sum(1 for inst in instances if harness.resolved(inst))
    return resolved / len(instances)
