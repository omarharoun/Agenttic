"""Standard-benchmark run path — runs the canonical suites k times against an
agent and computes the FULL Agenttic Index, including the two run-level metrics
that need the repeated/instrumented run: reliability pass^k and calibration ECE.

pass^k (tau-bench): a case is reliable only if it passes on ALL k runs — k is
configurable (default 3). NOTE: k runs cost k x the tokens; callers should keep
k bounded and surface the cost (reported as ``k_runs_cost_usd``).

Calibration: ECE (Guo et al. 2017) when the agent emits a confidence signal
(final-output span attribute/field ``confidence`` or a "confidence: X" in the
output); otherwise we fall back to abstention-appropriateness and label which was
used (``calibration_mode``).
"""

from __future__ import annotations

import re
import uuid

from ascore import ops
from ascore.metrics.calibration import abstention_appropriateness, ece
from ascore.metrics.index import compute_index, rollup_metrics_from_means
from ascore.metrics.reliability import pass_at_1, pass_hat_k
from ascore.metrics.standard_suites import canonical_suite_ids

MAX_K = 5
_CONF_RE = re.compile(r"confidence[\"']?\s*[:=]\s*([01](?:\.\d+)?)")


def _confidence_of(trace) -> float | None:
    if trace is None:
        return None
    for s in reversed(trace.spans):
        if s.kind == "final_output":
            c = (s.attributes or {}).get("confidence")
            if c is None:
                c = (s.output or {}).get("confidence")
            if c is not None:
                try:
                    return min(max(float(c), 0.0), 1.0)
                except (TypeError, ValueError):
                    return None
    m = _CONF_RE.search((trace.final_output or "").lower())
    return float(m.group(1)) if m else None


async def run_standard(cfg, reg, adapter, *, k: int = 3, suite_ids=None,
                       judge_client=None, fi_evaluate_fn=None, on_progress=None) -> dict:
    """Run the canonical suites k times for ``adapter`` and roll up the full
    Agenttic Index. Returns a JSON-safe result dict (also persistable)."""
    k = max(1, min(int(k), MAX_K))
    suite_ids = suite_ids or canonical_suite_ids(reg)
    model = ops.agent_model_of(adapter)

    per_case: dict[str, list[bool]] = {}      # test_id -> [pass]*k
    crit_scores: dict[str, list[float]] = {}  # criterion_id -> scores
    abstention_scores: list[float] = []
    conf_pairs: list[tuple[float, bool]] = []
    total_cost = 0.0

    for sid in suite_ids:
        for _ in range(k):
            suite, cases, traces = await ops.run_suite_op(
                cfg, reg, adapter, sid, None, on_progress)
            runs = await ops.score_op(cfg, reg, traces, cases, model, on_progress,
                                      judge_client=judge_client,
                                      fi_evaluate_fn=fi_evaluate_fn)
            tr_by_id = {t.test_case_id: t for t in traces}
            for rs in runs:
                total_cost += rs.cost_usd
                if rs.scoring_error:
                    continue
                per_case.setdefault(rs.test_id, []).append(rs.passed)
                for cs in rs.criterion_scores:
                    crit_scores.setdefault(cs.criterion_id, []).append(cs.score)
                    if cs.criterion_id == "abstention_correct":
                        abstention_scores.append(cs.score)
                conf = _confidence_of(tr_by_id.get(rs.test_id))
                if conf is not None:
                    conf_pairs.append((conf, bool(rs.passed)))

    means = {cid: sum(v) / len(v) for cid, v in crit_scores.items() if v}
    components = rollup_metrics_from_means(means)

    case_runs = list(per_case.values())
    components["reliability_pass_k"] = round(pass_hat_k(case_runs), 4)

    ece_value = None
    if conf_pairs:
        confs = [c for c, _ in conf_pairs]
        oks = [o for _, o in conf_pairs]
        ece_value = round(ece(confs, oks), 4)
        components["calibration_ece"] = round(1.0 - ece_value, 4)  # calibration quality
        calib_mode = "ece"
    else:
        components["calibration_ece"] = round(abstention_appropriateness(abstention_scores), 4)
        calib_mode = "abstention_only"

    idx = compute_index(components)
    return {
        "run_id": uuid.uuid4().hex[:12],
        "agent_id": adapter.agent_id,
        "k": k,
        "suites_run": suite_ids,
        "n_cases": len(per_case),
        "index": idx["index"],
        "components": {m: round(v, 4) for m, v in components.items()},
        "weights_used": idx["weights_used"],
        "missing": idx["missing"],
        "names": idx["names"],
        "calibration_mode": calib_mode,
        "ece": ece_value,
        "pass_at_1": round(pass_at_1(case_runs), 4),
        "k_runs_cost_usd": round(total_cost, 4),
    }
