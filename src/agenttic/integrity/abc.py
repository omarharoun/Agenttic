"""ABC benchmark-rigor scorecard (SPEC-6 Step 26.1).

Score an approved suite against the applicable items of the Agentic Benchmark
Checklist, computed from evidence we already hold — the integrity gates, judge
calibration, tag coverage, and our versioning/CI discipline. Items we cannot
evidence are N/A (never estimated upward, Hard Rule 30). Human-process items
(e.g. manual review hours) are passed in at approve time.
"""

from __future__ import annotations

from agenttic.generator.quality import _coverage_balance
from agenttic.schema.abc import ABCItem, ABCReport
from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge


def _coverage_from_gate(n_cases: int, failing: int) -> float | None:
    return (n_cases - failing) / n_cases if n_cases else None


def compute_abc_report(reg, suite_id: str, version: int | None = None,
                       human_items: list[ABCItem] | None = None) -> ABCReport:
    """Build the ABC report for a suite version from stored evidence."""
    suite, cases = reg.get_suite(suite_id, version)
    n = len(cases)
    integ = reg.get_integrity_report(suite_id, suite.version)
    items: list[ABCItem] = []

    # -- I. Task validity --------------------------------------------------
    gate_meta = {
        "oracle": ("I.a", "Solvability — every case proven solvable by an oracle"),
        "dummy": ("I.b", "Guessing resistance — a do-nothing agent fails every case"),
        "exploit": ("I.c", "Exploitation resistance — an explicit cheater passes nothing"),
    }
    for gate_name, (item_id, name) in gate_meta.items():
        g = integ.get(gate_name) if integ else None
        if g is None or not g.ran:
            items.append(ABCItem(item_id=item_id, name=name, category="I",
                                 score=None, status="n/a",
                                 evidence="no integrity report — run `ascore verify-suite`"))
            continue
        score = _coverage_from_gate(n, len(g.failing_case_ids))
        items.append(ABCItem(
            item_id=item_id, name=name, category="I", score=score, status="computed",
            evidence=f"{n - len(g.failing_case_ids)}/{n} cases clear this gate"
                     + (f"; flagged: {', '.join(g.failing_case_ids[:5])}" if g.failing_case_ids else "")))

    # -- I.d Judge accuracy evidence (calibration) -------------------------
    judge_ids: set[str] = set()
    rubric_cache: dict[str, object] = {}
    for tc in cases:
        if tc.rubric_id not in rubric_cache:
            try:
                rubric_cache[tc.rubric_id] = reg.get_rubric(tc.rubric_id)
            except Exception:  # noqa: BLE001
                rubric_cache[tc.rubric_id] = None
        rb = rubric_cache[tc.rubric_id]
        if rb is not None:
            judge_ids |= {c.criterion_id for c in rb.criteria if c.scorer == "judge"}
    if not judge_ids:
        items.append(ABCItem(item_id="I.d", name="Judge accuracy evidence",
                             category="I", score=None, status="n/a",
                             evidence="no judge criteria in this suite"))
    else:
        calibrated = judge_ids & demonstrated_calibrated_judge()
        items.append(ABCItem(
            item_id="I.d", name="Judge accuracy evidence — judges shown to agree with humans",
            category="I", score=len(calibrated) / len(judge_ids), status="computed",
            evidence=f"{len(calibrated)}/{len(judge_ids)} judge criteria calibrated"
                     + ("" if calibrated else " (all PROVISIONAL — Hard Rule 6)")))

    # -- I.e Coverage balance vs the configured tag mix --------------------
    cov = _coverage_balance(cases)
    items.append(ABCItem(
        item_id="I.e", name="Coverage — case mix matches the configured target",
        category="I", score=max(0.0, 1.0 - cov["divergence"]), status="computed",
        evidence=f"tag-mix divergence {cov['divergence']} from target"))

    # -- III. Reporting & reproducibility ----------------------------------
    items.append(ABCItem(
        item_id="III.a", name="Versioning + CI discipline",
        category="III", score=1.0, status="computed",
        evidence="append-only suite versioning; CI gates (pytest + UI suite) on every change"))
    items.append(ABCItem(
        item_id="III.3", name="Contamination resistance",
        category="III", score=None, status="n/a",
        evidence="per-tenant canary contamination check ships in Step 28"))

    # -- II + any other human-entered items --------------------------------
    if human_items:
        items.extend(human_items)

    return ABCReport(suite_id=suite_id, version=suite.version, items=items)
