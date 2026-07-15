"""Self-contained demo of the Evaluator Plugin Interface — no API key needed.

Runs the built-in reference agent through BOTH evaluators (Agenttic's own
generator + Inspect via its deterministic offline strategy), aggregates the
union with a per-source + per-dimension breakdown and a coverage table, signs a
single passport over the union, and verifies it — then tampers one byte to show
verification fails.

    python -m examples.evaluator_union_passport
    # or:  python examples/evaluator_union_passport.py
"""

from __future__ import annotations

import copy


def main() -> None:
    from agenttic.certification.safety_cert import (
        published_public_keys,
        verify_certificate,
    )
    from agenttic.evaluators.base import AgentTarget
    from agenttic.evaluators.orchestrator import discover_adapters, run_evaluation
    from agenttic.evaluators.passport import build_union_passport

    target = AgentTarget.reference()
    adapters = discover_adapters()
    print("Discovered evaluators:")
    for a in adapters:
        print(f"  - {a.id:14s} v={a.version:22s} license={a.license}")

    report = run_evaluation(target, adapters, deployment_mode="self_hosted")

    print("\n" + report.render_headline() + "\n")
    print("Per-source · per-dimension breakdown (Wilson 95% per source):")
    for sr in report.per_source:
        print(f"  {sr.source} (v={sr.source_version}, {sr.source_license}) "
              f"ran={sr.ran} index={sr.source_index}")
        for dim, st in sorted(sr.dimensions.items()):
            iv = (f"[{st.wilson_low},{st.wilson_high}]"
                  if st.wilson_low is not None else "—")
            print(f"      {dim:22s} {st.status:13s} "
                  f"n={st.n_assessed} pass={st.n_pass} fail={st.n_fail} "
                  f"err={st.n_error} rate={st.pass_rate} wilson={iv}")

    overall, breakdown = report.index_with_breakdown()
    print(f"\nUnion index {overall} decomposes to {breakdown['per_source_index']}")
    print(f"Coverage: {report.coverage_summary()}")

    print("\nLicense-gate decisions:")
    for g in report.gate_decisions:
        print(f"  {g.source:14s} {g.source_license:32s} -> {g.decision} "
              f"({g.classification})")

    passport = build_union_passport(report)
    print(f"\nPassport signed with kid={passport.public_key_id}")
    print(f"  agent_version in signed payload : {passport.signed_payload['agent_version']}")
    print(f"  sources in signed payload       : "
          f"{[s['source'] for s in passport.signed_payload['sources']]}")
    print(f"  verify (published key alone)    : {passport.verify()}")

    # Tamper one byte → verification must fail.
    pub = next(e["public_key_b64"] for e in published_public_keys(None)
               if e["kid"] == passport.public_key_id)
    tampered = copy.deepcopy(passport.signed_payload)
    tampered["agent_id"] = "someone-else"
    print(f"  verify after tampering agent_id : "
          f"{verify_certificate(tampered, passport.signature, pub)}")


if __name__ == "__main__":
    main()
