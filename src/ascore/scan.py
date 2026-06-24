"""Safety scan — the thin convenience layer behind the public "Scan my agent"
flow. It reuses the existing engine end-to-end and adds NO new scoring:

    build adapter (black-box URL or the built-in demo agent)
      → seed the Safety Battery suite (idempotent)
      → run + score + aggregate ONE scorecard (ops.run_and_score_op)
      → grade it with the published certification rubric (certification.py)
      → shape a plain-language result a non-expert can read.

The safety battery is scored entirely by deterministic CODE checks, so a scan
against a user's HTTP endpoint needs NO Anthropic key and NO judge calls — the
agent runs on the user's own infrastructure and the checks read its answers. The
demo (built-in reference) agent is the only path that spends the tenant's key.

The HTTP surface (start/poll, cert issuance, live progress) lives in
``server/routes/scan.py``; this module is reusable and unit-testable on its own.
"""

from __future__ import annotations

from ascore import ops
from ascore import certification as cert
from ascore.adapters.base import AgentAdapter
from ascore.metrics.safety_battery import (
    BATTERY_DIMENSIONS, SAFETY_BATTERY_SUITE_ID, seed_safety_battery,
)

#: A placeholder judge passed to the scoring step so ``make_judge`` never tries
#: to construct a real Anthropic client (which would demand a key). The battery
#: is 100% code-scored, so the judge object is never actually called.
_CODE_ONLY_JUDGE = object()


def battery_dimensions_public() -> list[dict]:
    """The battery's dimensions as plain-language descriptors for the scan UI to
    render BEFORE results exist (so it can animate the checklist)."""
    return [{"criterion_id": d.criterion_id, "label": d.plain_label,
             "critical": cert.DIMENSION_BY_CRITERION[d.criterion_id].critical}
            for d in BATTERY_DIMENSIONS]


def _plain_dimensions(graded: dict, dimension_scores: dict[str, float]) -> list[dict]:
    """Map the certification grade's per-dimension breakdown onto consumer
    language ("Refuses harmful requests ✓"), preserving order + pass/fail."""
    # The cert grades by ``dimension`` id; map back to the battery criterion.
    by_dim = {d["dimension"]: d for d in graded.get("dimensions", [])}
    out = []
    for bd in BATTERY_DIMENSIONS:
        sd = cert.DIMENSION_BY_CRITERION.get(bd.criterion_id)
        if sd is None:
            continue
        info = by_dim.get(sd.dimension)
        if info is None:
            continue  # not measured in this run
        passed = info["status"] == "pass"
        out.append({
            "criterion_id": bd.criterion_id,
            "label": bd.plain_label,
            "score": info["score"],
            "percent": round(100 * info["score"]),
            "status": info["status"],          # pass | warn | fail
            "passed": passed,
            "critical": info["critical"],
            "detail": bd.pass_text if passed else bd.fail_text,
        })
    return out


async def run_safety_scan(cfg: dict, reg, *, adapter: AgentAdapter,
                          judge_client=None, on_progress=None) -> dict:
    """Run the Safety Battery once against ``adapter`` and return a graded,
    plain-language scan result. Reuses ``ops.run_and_score_op`` (run → score →
    aggregate → persist scorecard); grading reuses the certification rubric.

    The returned dict carries the scorecard id (so the caller can issue a
    signed certificate), the letter grade + composite score, any critical-cap
    reason, and the consumer-language per-dimension breakdown."""
    seed_safety_battery(reg)
    sc = await ops.run_and_score_op(
        cfg, reg, adapter, SAFETY_BATTERY_SUITE_ID,
        on_progress=on_progress, judge_client=judge_client or _CODE_ONLY_JUDGE)

    dimension_scores = cert.extract_dimension_scores(sc.per_criterion_means)
    missing = cert.missing_required(dimension_scores)
    graded = cert.compute_grade(dimension_scores) if dimension_scores else {
        "grade": "F", "composite_score": 0.0, "grade_capped": False,
        "cap_reason": "", "dimensions": []}

    return {
        "suite_id": SAFETY_BATTERY_SUITE_ID,
        "scorecard_id": sc.scorecard_id,
        "agent_id": sc.agent_id,
        "grade": graded["grade"],
        "composite_score": graded["composite_score"],
        "grade_capped": graded["grade_capped"],
        "cap_reason": graded["cap_reason"],
        "dimensions": _plain_dimensions(graded, dimension_scores),
        "missing_required": missing,
        "n_cases": len(sc.run_scores),
        "errored": len(sc.errored_test_ids),
        # agent execution cost only (scoring is code-only → $0). Black-box agents
        # report 0 (their cost is on the user's own infra/endpoint).
        "cost_usd": round(sc.total_cost_usd, 4),
    }
