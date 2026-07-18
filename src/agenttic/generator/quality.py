"""Generator scoring (SPEC-3 Step 16) — make generator quality MEASURABLE.

This module ONLY measures. It does not tune, mutate, or optimize the generator
(that is a future Step 17). Measure-before-optimize is the whole point (Hard
Rule 16): you cannot improve what you have not first learned to measure.

For a suite that has completed human review (approved) and been evaluated at
least once, ``compute_generator_report`` reports three orthogonal signals:

* **edit_rate** — how much the human changed the draft during review. High edit
  rate means the generator's first draft is untrustworthy. Read from the recorded
  review diff (snapshot vs approved). None when there's no snapshot (mined /
  pre-Step-16 suites) — reported honestly as "unavailable", never fabricated.
* **discrimination** — how many cases actually separate good agents from bad.
  A case every evaluated agent config passes (or every one fails) discriminates
  nothing. Needs >=2 distinct agent configs to measure; None below that.
* **coverage_balance** — the suite's actual tag distribution vs the generator's
  configured ``TAG_MIX`` proportions (per-tag fraction + signed deltas).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from agenttic.generator.pipeline import TAG_MIX
from agenttic.registry.sqlite_store import Registry


@dataclass
class GeneratorReport:
    """Measured quality of a generated suite (read-only; no mutation)."""

    suite_id: str
    edit_rate: float | None          # (edited + deleted) / generated_count; None if no snapshot
    discrimination: float | None     # fraction of cases with mixed pass/fail; None if <2 configs
    coverage_balance: dict           # {"actual": {...}, "target": {...}, "deltas": {...}}
    n_cases: int
    n_agent_configs: int
    notes: list[str] = field(default_factory=list)


def _target_mix() -> dict[str, float]:
    """The generator's configured coverage proportions, from TAG_MIX."""
    c = Counter(TAG_MIX)
    total = sum(c.values()) or 1
    return {tag: n / total for tag, n in c.items()}


def _coverage_balance(cases) -> dict:
    """Actual per-tag fraction vs the configured TAG_MIX proportions.

    A case with multiple tags counts once per tag it carries (a case is only
    one unit of the mix if single-tagged). Deltas are ``actual - target`` per
    tag across the union of tags seen and targeted; sum(|delta|)/2 is the total
    divergence (how far the mix is off, in fraction-of-suite units)."""
    target = _target_mix()
    tag_counts: Counter[str] = Counter()
    for c in cases:
        for t in c.tags:
            tag_counts[t] += 1
    total = sum(tag_counts.values()) or 1
    actual = {tag: n / total for tag, n in tag_counts.items()}
    tags = set(actual) | set(target)
    deltas = {t: round(actual.get(t, 0.0) - target.get(t, 0.0), 4) for t in tags}
    return {
        "actual": {t: round(actual.get(t, 0.0), 4) for t in sorted(tags)},
        "target": {t: round(target.get(t, 0.0), 4) for t in sorted(tags)},
        "deltas": {t: deltas[t] for t in sorted(tags)},
        "divergence": round(sum(abs(v) for v in deltas.values()) / 2, 4),
    }


def _discrimination(scorecards, cfg=None) -> tuple[float | None, int, list[str]]:
    """Fraction of cases where at least one evaluated agent config PASSED and at
    least one FAILED. A case every config passes (or every config fails)
    contributes 0. Scoring errors are excluded (a scoring-infra failure is not
    an agent verdict). Needs >=2 distinct agent configs; below that, None.

    Distinct agent CONFIGS = distinct scorecard ``agent_id``s for the suite (one
    scorecard per agent config per suite). Returns (fraction, n_configs, notes).
    """
    notes: list[str] = []
    # One scorecard per (agent config) — collapse to the latest per agent_id in
    # case a config was re-run, so we don't double-count the same config.
    by_agent: dict[str, object] = {}
    for sc in scorecards:  # scorecards_in yields oldest-first => last wins (latest)
        by_agent[sc.agent_id] = sc
    n_configs = len(by_agent)
    if n_configs < 2:
        notes.append(
            f"discrimination needs >=2 agent configs; only {n_configs} evaluated")
        return None, n_configs, notes

    # test_id -> list of pass/fail verdicts across configs (errors excluded)
    verdicts: dict[str, list[bool]] = {}
    for sc in by_agent.values():
        for rs in sc.run_scores:
            if rs.scoring_error is not None:
                continue
            verdicts.setdefault(rs.test_id, []).append(rs.passed)

    measurable = {tid: v for tid, v in verdicts.items() if len(v) >= 2}
    if not measurable:
        notes.append("no case has >=2 non-errored verdicts to compare")
        return None, n_configs, notes
    discriminating = sum(1 for v in measurable.values()
                         if any(v) and not all(v))
    return discriminating / len(measurable), n_configs, notes


def compute_generator_report(reg: Registry, suite_id: str, cfg=None
                             ) -> GeneratorReport:
    """Measure the quality of one generated suite (read-only).

    Requires the suite to exist; edit_rate additionally requires the review to
    be complete (a recorded diff) and discrimination requires >=2 agent-config
    evaluations. Missing inputs degrade to None with an explanatory note, never
    to a fabricated number."""
    suite, cases = reg.get_suite(suite_id)
    notes: list[str] = []

    # -- edit_rate (from the recorded review diff) --------------------------
    diff = reg.get_review_diff(suite_id, suite.version)
    if diff is None:
        edit_rate = None
        snap = reg.get_generated_snapshot(suite_id, suite.version)
        if snap is None:
            notes.append("edit_rate unavailable: no generator snapshot "
                         "(mined/imported or pre-Step-16 suite)")
        elif not suite.approved:
            notes.append("edit_rate unavailable: suite not yet approved "
                         "(diff recorded at approval)")
        else:
            notes.append("edit_rate unavailable: no review diff recorded")
    else:
        gen_n = diff.get("generated_count", 0)
        edit_rate = ((diff["edited"] + diff["deleted"]) / gen_n) if gen_n else None
        if gen_n == 0:
            notes.append("edit_rate unavailable: empty generated snapshot")

    # -- discrimination (needs >=2 agent configs) ---------------------------
    scorecards = reg.scorecards_in([suite_id])
    discrimination, n_configs, disc_notes = _discrimination(scorecards, cfg)
    notes += disc_notes

    # -- coverage balance vs configured TAG_MIX -----------------------------
    coverage = _coverage_balance(cases)

    return GeneratorReport(
        suite_id=suite_id,
        edit_rate=edit_rate,
        discrimination=discrimination,
        coverage_balance=coverage,
        n_cases=len(cases),
        n_agent_configs=n_configs,
        notes=notes,
    )
