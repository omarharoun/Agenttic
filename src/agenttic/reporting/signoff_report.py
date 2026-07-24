"""The sign-off report (SPEC-13 Step 64).

The headline order is the deliverable: **closure → assertions → formal →
regression → pass rate (demoted)**. A pass rate reported without a coverage model
renders `unscoped — no coverage model` wherever it appears (Hard Rule 56).

The report refuses to emit an unqualified safety claim, reusing the same guard
the formal layer uses — one honesty check, both surfaces.
"""

from __future__ import annotations

from agenttic.schema.signoff import VerificationSignoff
from agenttic.verification.formal.prove import assert_scoped
from agenttic.verification.vplan import VPlanTrace


def _bar(label: str, value: str) -> str:
    return f"  {label:<26}{value}"


def render(signoff: VerificationSignoff,
           vplan_trace: VPlanTrace | None = None) -> str:
    s = signoff
    lines: list[str] = [
        "VERIFICATION SIGN-OFF",
        "=" * 66,
        _bar("subject", f"{s.agent_id} @ {s.agent_config_hash[:12] or '(unpinned)'}"),
        _bar("verdict", "SIGNS OFF" if s.signs_off else "DOES NOT SIGN OFF"),
        "",
    ]

    # --- 1. coverage — the headline ---------------------------------------
    c = s.coverage
    if c.status == "populated":
        lines.append(_bar("1 · COVERAGE CLOSURE",
                          f"{c.trace_closure:.0%} of target {c.closure_target:.0%}"
                          f"{'  CLOSED' if c.closed else '  NOT CLOSED'}"))
        lines.append(_bar("    stimulus vs trace",
                          f"{c.stimulus_closure:.0%} requested / "
                          f"{c.trace_closure:.0%} exhibited"))
        if c.unhit_bins:
            lines.append(_bar("    never exercised",
                              ", ".join(c.unhit_bins[:8])
                              + (f" (+{len(c.unhit_bins)-8} more)"
                                 if len(c.unhit_bins) > 8 else "")))
        if c.waived_bins:
            for b, why in list(c.waived_bins.items())[:4]:
                lines.append(_bar("    waived", f"{b} — {why}"))
        if c.illegal_hits:
            lines.append(_bar("    ILLEGAL BIN HITS", ", ".join(c.illegal_hits)))
        if c.other_drift:
            lines.append(_bar("    other-bin drift",
                              ", ".join(f"{k} {v:.0%}" for k, v in
                                        list(c.other_drift.items())[:4])))
        if c.provisional_coverpoints:
            lines.append(_bar("    PROVISIONAL",
                              ", ".join(c.provisional_coverpoints)))
    else:
        lines.append(_bar("1 · COVERAGE CLOSURE", "not run — no coverage model"))
    lines.append("")

    # --- 2. assertions -----------------------------------------------------
    a = s.assertions
    if a.status == "populated":
        lines.append(_bar("2 · ASSERTIONS",
                          f"{a.verdict} — {a.violations} violation(s) of {a.total}"))
        lines.append(_bar("    unexercised (vacuous)",
                          f"{a.unexercised} — not evidence of correctness"))
        for p in a.violated_properties[:5]:
            lines.append(f"      ! {p}")
    else:
        lines.append(_bar("2 · ASSERTIONS", "not run"))
    lines.append("")

    # --- 3. formal ---------------------------------------------------------
    f = s.formal
    if f.status == "populated":
        lines.append(_bar("3 · FORMAL",
                          f"{f.proven} proven · {f.counterexample} counterexample · "
                          f"{f.unbounded} unbounded · {f.not_attempted} not attempted"))
        lines.append(_bar("    scope", f"{f.scope} — the model itself is NOT verified"))
        for claim in f.claims[:4]:
            lines.append(f"      · {claim}")
    else:
        lines.append(_bar("3 · FORMAL", "not attempted"))
    lines.append("")

    # --- 4. convergence + regression ---------------------------------------
    cv = s.convergence
    if cv.status == "populated":
        lines.append(_bar("4 · CONVERGENCE",
                          f"{cv.distinct_failure_signatures} distinct failure "
                          f"signature(s) over {cv.scenarios_run} scenarios; "
                          f"{'curve FLAT' if cv.curve_flattened else 'curve STILL RISING'}"))
        lines.append(_bar("    since last new signature",
                          f"{cv.scenarios_since_last_new_signature} scenarios"))
    else:
        lines.append(_bar("4 · CONVERGENCE", "not run"))

    r = s.regression
    lines.append(_bar("5 · REGRESSION",
                      f"pass^{r.k} {r.pass_hat_k:.0%} over {r.frozen_cases} frozen "
                      f"historical bug(s)" if r.status == "populated" else "not run"))
    e = s.envelope
    lines.append(_bar("6 · ENVELOPE",
                      f"mean ${e.mean_cost_usd:.4f}/run · p95 {e.p95_latency_ms:.0f}ms"
                      f" · closure/$ {e.closure_per_dollar:.3f}"
                      if e.status == "populated" else "not run"))
    lines.append("")

    # --- pass rate, demoted -------------------------------------------------
    lines.append(_bar("pass rate (one line)", s.pass_rate_label))
    p = s.provenance
    if p.status == "populated":
        lines.append(_bar("judge/classifier state",
                          ("SOME PROVISIONAL — " if p.any_provisional else "")
                          + f"{len(p.judges)} judge(s), {len(p.classifiers)} classifier(s)"))
    lines.append("")

    # --- vPlan traceability — the line that is the product ------------------
    if vplan_trace is not None:
        lines.append(f"vPLAN TRACEABILITY ({vplan_trace.plan_ref})")
        lines.append(_bar("requirements covered",
                          f"{len(vplan_trace.covered)}/{len(vplan_trace.rows)}"))
        if vplan_trace.unexercised:
            lines.append(_bar("mapped but unexercised",
                              ", ".join(r.requirement_id
                                        for r in vplan_trace.unexercised)))
        if vplan_trace.untested:
            lines.append("")
            lines.append("  *** UNTESTED REQUIREMENTS — NOTHING TESTS THESE ***")
            for row in vplan_trace.untested:
                lines.append(f"      ! {row.requirement_id}: {row.text}")
        else:
            lines.append(_bar("untested requirements", "none"))
        lines.append("")

    lines.append(f"sign-off sha256 {s.content_sha256()[:32]}…")
    if s.missing_legs():
        lines.append(f"INCOMPLETE — legs not run: {', '.join(s.missing_legs())}")

    text = "\n".join(lines)
    assert_scoped(text)          # one honesty guard, shared with the formal layer
    return text


def headline(signoff: VerificationSignoff) -> str:
    """The one-liner that replaces "your agent scored 86%"."""
    c, a, f = signoff.coverage, signoff.assertions, signoff.formal
    if c.status != "populated":
        return (f"pass rate {signoff.pass_rate_label}; no coverage model — "
                "the claim is unscoped")
    return (f"coverage {c.trace_closure:.0%}"
            f"{' CLOSED' if c.closed else ''}, "
            f"{a.violations} assertion violation(s) over "
            f"{signoff.convergence.scenarios_run} scenarios, "
            f"{f.proven} propert{'y' if f.proven == 1 else 'ies'} proven over the "
            f"authorization layer, bug curve "
            f"{'flat' if signoff.convergence.curve_flattened else 'still rising'}")
