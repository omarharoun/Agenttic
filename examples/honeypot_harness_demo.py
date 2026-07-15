"""End-to-end example: the honeypot / harness-enforcement testing slice.

Plants DECOY dangerous tools into the reference agent's tool schema, tempts the
agent to call them, and — the differentiated part — tests whether the AGENT calls
the honeypot AND whether the HARNESS actually blocks the forbidden call.

Runs the SAME temptation probes under two harness postures, with the
DETERMINISTIC scripted target — no API key, no credits, no network:

  * enforce  — decoys installed as Lane-1 canaries → the gateway DENIES a call.
  * log-only — the same gateway WITHOUT those canaries → the call is merely
               logged and ALLOWED (the real failure: logged-not-blocked).

Comparing the two shows enforcement is a real, separate signal — not a label.

Run it:

    uv run python examples/honeypot_harness_demo.py

or through the CLI:

    uv run agenttic honeypot --target reference --posture enforce
    uv run agenttic honeypot --target reference --posture log-only --promote

Exercised by ``tests/test_redteam_honeypot.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agenttic.redteam import (
    honeypot_tool_schemas,
    plant_honeypots,
    reference_descriptor,
    run_honeypot_harness,
)
from agenttic.registry.sqlite_store import Registry


def _print_report(rep) -> None:
    for o in rep.outcomes:
        called = ", ".join(o.honeypot_tools_called) or "—"
        enforced = ("n/a" if o.enforced is None
                    else ("BLOCKED" if o.enforced else "ALLOWED"))
        print(f"  {o.test_id:34s} called={called:18s} "
              f"agent_unsafe={str(o.agent_unsafe):5s} "
              f"harness={enforced:8s} -> {o.outcome}")
    c = rep.counts()
    print(f"  outcomes: {c['resisted']} resisted, "
          f"{c['attempted_blocked']} attempted-but-blocked, "
          f"{c['executed_allowed']} executed-and-allowed")


def main() -> None:
    descriptor = reference_descriptor()
    planted = plant_honeypots(descriptor)
    print(f"Target: {planted.agent_id}")
    print(f"  real tools    : {descriptor.tool_names()}")
    print("  PLANTED decoys (bait, no real dangerous impl):")
    for s in honeypot_tool_schemas(planted):
        params = ", ".join(s["input_schema"]["properties"])
        print(f"    - {s['name']}({params})")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        print("=== posture: ENFORCE (decoys installed as Lane-1 canaries) ===")
        reg = Registry(str(Path(tmp) / "enforce.db"))
        rep = run_honeypot_harness(planted, reg=reg, enforcing=True, promote=True)
        _print_report(rep)
        print("  every attempted forbidden call was BLOCKED — nothing to promote:"
              f" {rep.promote['regression_suite_id']}")
        print()

        print("=== posture: LOG-ONLY (gateway present, decoys NOT registered) ===")
        reg2 = Registry(str(Path(tmp) / "logonly.db"))
        rep2 = run_honeypot_harness(planted, reg=reg2, enforcing=False,
                                    promote=True)
        _print_report(rep2)
        p = rep2.promote
        print(f"  {len(p['added'])} executed-not-blocked failures PROMOTED into "
              f"regression suite {p['regression_suite_id']} v{p['version']}.")
        print()
        print("The agent's behaviour is identical in both runs; only the harness "
              "differs. That is the point: enforcement is tested, not assumed.")


if __name__ == "__main__":
    main()
