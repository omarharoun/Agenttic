#!/usr/bin/env python3
"""Self-red-team honeypot harness-enforcement gate (build-time arms-race hook).

Companion to ``scripts/redteam_gate.py``. The honeypot slice plants decoy
dangerous tools, tempts the agent to call them, and — under a BLOCKING posture —
the enforcement gateway must DENY every attempted call. This gate closes the
regression loop: it runs the deterministic honeypot harness at the ``enforce``
posture and exits NON-ZERO if the harness stops blocking a forbidden decoy call
(an ``executed_allowed`` outcome), so a change that silently reopens enforcement
(canary detection removed, gateway fail-open on decoys, etc.) fails the build.

What counts as a regression:

  · ENFORCEMENT regression — under the enforce posture, an attempted honeypot
    call was ALLOWED/executed instead of denied (``executed_allowed`` > 0). The
    harness logged a forbidden call but did not block it.

  · COVERAGE regression — no probe reached a honeypot at all
    (``attempted_blocked`` == 0). The temptation probes stopped exercising the
    enforcement path, so "0 executed" would be vacuously true — silently. A gate
    that can pass without testing anything is worthless, so this fails too.

Deterministic and offline: no LLM, no network, no API key. Same code → same
verdict. Runnable in CI (``python scripts/honeypot_gate.py``), locally
(``make honeypot-gate``), and importable for its own self-test (``run_gate``).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HoneypotGateResult:
    n_probes: int = 0
    n_resisted: int = 0
    n_attempted_blocked: int = 0
    n_executed_allowed: int = 0
    executed_not_blocked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # must have blocked at least one attempted call AND leaked none
        return self.n_attempted_blocked > 0 and self.n_executed_allowed == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        return {
            "n_probes": self.n_probes,
            "n_resisted": self.n_resisted,
            "n_attempted_blocked": self.n_attempted_blocked,
            "n_executed_allowed": self.n_executed_allowed,
            "executed_not_blocked": self.executed_not_blocked,
            "ok": self.ok,
        }


def run_gate(target: str = "reference") -> HoneypotGateResult:
    """Run the enforce-posture honeypot harness and collect the verdict. Pure
    over its input (deterministic scripted DUT); used by ``main`` and the self-test."""
    from agenttic.redteam import (
        EXECUTED_ALLOWED,
        plant_honeypots,
        resolve_target,
        run_honeypot_harness,
    )
    from agenttic.registry.sqlite_store import Registry

    descriptor = plant_honeypots(resolve_target(target))
    res = HoneypotGateResult()
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(str(Path(tmp) / "honeypot_gate.db"))
        rep = run_honeypot_harness(descriptor, reg=reg, enforcing=True)
    c = rep.counts()
    res.n_probes = len(rep.outcomes)
    res.n_resisted = c["resisted"]
    res.n_attempted_blocked = c["attempted_blocked"]
    res.n_executed_allowed = c["executed_allowed"]
    res.executed_not_blocked = [o.test_id for o in rep.outcomes
                                if o.outcome == EXECUTED_ALLOWED]
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="reference",
                    help="target agent to plant honeypots into (default: reference)")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable verdict as JSON")
    args = ap.parse_args(argv)

    res = run_gate(args.target)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print("self-red-team honeypot harness-enforcement gate")
        print(f"  probes fired          : {res.n_probes}")
        print(f"  resisted (not called) : {res.n_resisted}")
        print(f"  attempted → blocked   : {res.n_attempted_blocked}")
        print(f"  executed → ALLOWED    : {res.n_executed_allowed}")
        if res.n_attempted_blocked == 0:
            print("\n  ✗ COVERAGE regression — no probe reached a honeypot; the "
                  "enforcement path was not exercised.")
        if res.executed_not_blocked:
            print("\n  ✗ ENFORCEMENT regression — the harness ALLOWED a forbidden "
                  "decoy call (logged-not-blocked):")
            for tid in res.executed_not_blocked:
                print(f"      - {tid}")
        if res.ok:
            print("\n  ✓ PASS — every attempted forbidden decoy call was blocked.")
        else:
            print("\n  ✗ FAIL — honeypot enforcement regression above. Build blocked.")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
