"""Empirical difficulty calibration (SPEC-6 Step 26.2).

Once a suite has been run by >=3 distinct agent configs, band each case by the
pass rate across those configs (Terminal-Bench banding): easy if >=2/3 pass,
hard if <=1/3, medium between. A case every config passes — or every config
fails — has zero discrimination and is flagged for the next suite version
(generalising Step 16's discrimination metric to the case level).
"""

from __future__ import annotations

MIN_CONFIGS = 3
_BANDS = {"easy", "medium", "hard"}


def case_difficulty(reg, suite_id: str, version: int | None = None) -> tuple[dict, str]:
    """Per-case empirical difficulty. Returns ({test_id: {...}}, note); the map is
    empty (with an explanatory note) below MIN_CONFIGS distinct configs — never a
    fabricated band."""
    suite, _ = reg.get_suite(suite_id, version)
    by_agent: dict[str, object] = {}
    for sc in reg.scorecards_in([suite_id]):  # oldest-first => latest per config wins
        if sc.suite_version == suite.version:
            by_agent[sc.agent_id] = sc
    n_configs = len(by_agent)
    if n_configs < MIN_CONFIGS:
        return {}, (f"empirical difficulty needs >={MIN_CONFIGS} agent configs; "
                    f"{n_configs} evaluated")

    verdicts: dict[str, list[bool]] = {}
    for sc in by_agent.values():
        for rs in sc.run_scores:
            if rs.scoring_error is not None:      # infra failure, not an agent verdict
                continue
            verdicts.setdefault(rs.test_id, []).append(rs.passed)

    out: dict[str, dict] = {}
    for tid, v in verdicts.items():
        if len(v) < MIN_CONFIGS:
            continue
        rate = sum(v) / len(v)
        band = "easy" if rate >= 2 / 3 else "hard" if rate <= 1 / 3 else "medium"
        out[tid] = {"pass_rate": round(rate, 4), "n_configs": len(v), "band": band,
                    "zero_discrimination": all(v) or not any(v)}
    return out, ""


def predicted_vs_empirical(cases, difficulty: dict) -> tuple[float | None, str]:
    """Agreement between the generator's predicted difficulty tag (easy/medium/
    hard, when present) and the empirical band. None when no case carries a
    predicted difficulty tag (the generator's calibration is simply unmeasured)."""
    pairs = []
    for c in cases:
        pred = next((t for t in c.tags if t in _BANDS), None)
        emp = difficulty.get(c.test_id, {}).get("band")
        if pred and emp:
            pairs.append((pred, emp))
    if not pairs:
        return None, "no case carries a predicted difficulty tag"
    agree = sum(1 for a, b in pairs if a == b) / len(pairs)
    return round(agree, 4), f"{len(pairs)} cases with a predicted difficulty tag"
