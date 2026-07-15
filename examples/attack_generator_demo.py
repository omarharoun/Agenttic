"""End-to-end example: the adversarial attack GENERATOR ("sparring partner").

Runs the whole round-1 loop against the built-in reference agent with the
DETERMINISTIC template author — no API key, no credits, no network:

  generate -> run through the real adapter + scorer -> keep the ones that broke
  the agent -> mutate around the winners -> promote them into a versioned
  regression suite (via the existing hardening path).

Run it:

    uv run python examples/attack_generator_demo.py

or, identically, through the CLI:

    uv run agenttic generate --target reference --promote

This is exercised by ``tests/test_redteam_generator.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agenttic.hardening import regression_detail
from agenttic.redteam import (
    build_demo_target,
    reference_descriptor,
    run_generation,
)
from agenttic.registry.sqlite_store import Registry


def main() -> None:
    descriptor = reference_descriptor()
    print(f"Target: {descriptor.agent_id}")
    print(f"  real tools : {descriptor.tool_names()}")
    print(f"  system     : {descriptor.system_prompt!r}")
    print(f"  secret     : {descriptor.primary_secret()}")
    print()

    # The deterministic, no-key stand-in target (reuses the real reference
    # adapter with a scripted client). A real run points at the live agent.
    adapter = build_demo_target(descriptor)

    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(str(Path(tmp) / "attack_demo.db"))
        rep = run_generation(descriptor, adapter, n=12, mutate=True, reg=reg,
                             promote=True)

        print("Generated probes (round 1):")
        for r in rep["results"]:
            verdict = "BROKE   " if r.broke else "survived"
            print(f"  [{verdict}] {r.test_id:32s} "
                  f"oracle={r.probe.test_case.expected} "
                  f"failed={r.failed_criteria}")

        nb, nw = len(rep["results"]), len(rep["winners"])
        print(f"\nRound 1: {nw}/{nb} broke the agent; {nb - nw} survived.")
        print(f"Mutation: {len(rep['mutation_winners'])} neighbour probes also broke.")

        p = rep["promote"]
        print(f"\nPromoted {len(p['added'])} winners into regression suite "
              f"{p['regression_suite_id']} v{p['version']}.")
        det = regression_detail(reg, p["regression_suite_id"])
        print(f"Regression suite now holds {len(det['cases'])} versioned cases "
              "(each with a real, checkable oracle + provenance).")


if __name__ == "__main__":
    main()
