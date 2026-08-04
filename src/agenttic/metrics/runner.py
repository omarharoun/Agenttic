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

from agenttic import ops
from agenttic.metrics.calibration import abstention_appropriateness, ece
from agenttic.metrics.faithfulness import (
    aggregate_faithfulness, make_llm_claim_checker, score_faithfulness,
)
from agenttic.metrics.index import compute_index, rollup_metrics_from_means
from agenttic.metrics.reliability import (flakiness, pass_at_1,
                                          pass_at_k, pass_hat_k)
from agenttic.metrics.standard_suites import canonical_suite_ids

MAX_K = 5
FAITHFULNESS_SUITE_ID = "std-faithfulness-v1"
_CONF_RE = re.compile(r"confidence[\"']?\s*[:=]\s*([01](?:\.\d+)?)")


def _build_faithfulness_checker(cfg, judge_client, agent_model):
    """A claim-checker over the judge model (BYO-tenant-key path), or ``None``
    when no client/model is available or the only judge model would coincide with
    the agent under test (Hard Rule 4) — in which case faithfulness degrades to
    'not computed' and is reported as a missing index component, honestly."""
    if judge_client is None:
        return None
    models = (cfg or {}).get("models", {}) or {}
    model = models.get("judge_strong") or models.get("judge_executor")
    if not model or model == agent_model:
        return None
    from agenttic.retry import RetryPolicy
    policy = RetryPolicy.from_cfg(cfg) if cfg else None
    return make_llm_claim_checker(judge_client, model, retry_policy=policy)


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


def verify_run(traces: list, *, cfg: dict | None = None) -> dict:
    """The verification component of the harness.

    Runs the SPEC-13 layer over every trace the harness produced and returns a
    JSON-safe summary: coverage closure per coverpoint, the assertion roll-up,
    and the serialized sign-off. Deterministic and free — zero model calls — so
    it runs on every harness invocation rather than being something a
    certification path has to remember to ask for.

    ``cfg`` reaches the coverage model's ``closure_target``. Without it the model
    falls back to its built-in 0.95 and SAYS SO on stderr — which is how this was
    found: the standard run called this with no config, so a tenant configuring
    `coverage.closure_target` was measured against a number they did not choose
    and were never told about. `config.yaml` also says 0.95, so the defect was
    invisible until someone changed it.

    Returns ``{"status": "not_run", ...}`` when there are no traces, never a
    fabricated pass: a harness run that produced nothing is the absence of
    evidence, not evidence of safety.
    """
    if not traces:
        return {"status": "not_run",
                "note": "no traces were produced, so nothing was verified"}
    try:
        from agenttic.ops import verify_op
        _results, summary = verify_op(traces, cfg=cfg)
    except Exception as exc:      # noqa: BLE001 — verification never breaks a run
        return {"status": "error", "note": f"verification failed: {exc}"}
    if not summary:
        return {"status": "not_run",
                "note": "the coverage model produced no report"}
    summary["status"] = "populated"
    summary["n_traces"] = len(traces)
    return summary


async def run_standard(cfg, reg, adapter, *, k: int = 3, suite_ids=None,
                       judge_client=None, fi_evaluate_fn=None, on_progress=None,
                       faithfulness_checker=None, claim_extractor=None,
                       include_per_case: bool = False) -> dict:
    """Run the canonical suites k times for ``adapter`` and roll up the full
    Agenttic Index. Returns a JSON-safe result dict (also persistable).

    ``include_per_case`` adds a ``per_case`` map (test_id -> [pass]*k) to the
    result — used by the certification elicitation matrix to pair neutral vs
    strong per case. It is off by default so the persisted run shape is unchanged.

    Faithfulness (FActScore / RAGAS atomic-claim) is computed as a run-level
    metric over the ``std-faithfulness-v1`` suite using an LLM claim-checker —
    injected via ``faithfulness_checker`` (tests) or built from the judge model
    and ``judge_client`` (BYO-tenant-key). With no checker it is left out of the
    index and reported as a missing component."""
    k = max(1, min(int(k), MAX_K))
    suite_ids = suite_ids or canonical_suite_ids(reg)
    model = ops.agent_model_of(adapter)
    checker = faithfulness_checker or _build_faithfulness_checker(
        cfg, judge_client, model)

    per_case: dict[str, list[bool]] = {}      # test_id -> [pass]*k
    crit_scores: dict[str, list[float]] = {}  # criterion_id -> scores
    abstention_scores: list[float] = []
    conf_pairs: list[tuple[float, bool]] = []
    faith_results: list = []                  # FaithfulnessResult per faith run
    # Every trace the harness produced, kept for the verification component
    # below. Accumulated across suites AND k, which is the point: coverage over
    # k×suites exercises more of the situation space than any single run, so the
    # closure number here is the strongest one the harness can produce.
    all_traces: list = []
    total_cost = 0.0

    for sid in suite_ids:
        for trial in range(k):
            # `trial` is what makes the k repetitions INDEPENDENT. `run_suite_op`
            # hard-codes resume on (crash resilience), and the harness resume map
            # used to be keyed on test_case_id alone — so trials 2..k read trial
            # 1's persisted traces back and returned them unchanged. The agent
            # was called once, every trial produced identical output, and pass^k
            # below was arithmetically forced to equal pass@1 for every agent.
            # Passing the trial index makes resume ordinal: trial t can only
            # reuse a t-th prior trace, so k trials cost k real executions.
            suite, cases, traces = await ops.run_suite_op(
                cfg, reg, adapter, sid, None, on_progress, trial=trial)
            runs = await ops.score_op(cfg, reg, traces, cases, model, on_progress,
                                      judge_client=judge_client,
                                      fi_evaluate_fn=fi_evaluate_fn)
            all_traces.extend(traces)
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
            if sid == FAITHFULNESS_SUITE_ID and checker is not None:
                case_by_id = {c.test_id: c for c in cases}
                for t in traces:
                    c = case_by_id.get(t.test_case_id)
                    ref = str((c.expected or {}).get("reference_context", "")) if c else ""
                    faith_results.append(score_faithfulness(
                        t.final_output, ref, claim_checker=checker,
                        claim_extractor=claim_extractor))

    means = {cid: sum(v) / len(v) for cid, v in crit_scores.items() if v}
    components = rollup_metrics_from_means(means)

    case_runs = list(per_case.values())
    components["reliability_pass_k"] = round(pass_hat_k(case_runs), 4)

    faith = aggregate_faithfulness(faith_results) if faith_results else None
    if faith and faith.mode == "scored":
        components["faithfulness"] = round(faith.groundedness, 4)

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

    # --- verification: a harness component, not an optional consult ---------
    # The harness is the only place that holds every trace, so this is where
    # verification belongs. Deterministic and FREE (zero model calls), which is
    # why it can run unconditionally on the normal path.
    #
    # The gap this closes: `agenttic certify` -> run_matrix -> run_standard held
    # the traces and threw them away, so the certification path produced a Tier
    # A/B/C with no closure, no action_risk, no assertions and no sign-off —
    # while the certificate path refused the same agent on the same evidence.
    # Two verdicts, two code paths, nothing reconciling them.
    verification = verify_run(all_traces, cfg=cfg)

    idx = compute_index(components)
    return {
        "run_id": uuid.uuid4().hex[:12],
        "agent_id": adapter.agent_id,
        "k": k,
        "suites_run": suite_ids,
        "n_cases": len(per_case),
        "verification": verification,
        "index": idx["index"],
        "components": {m: round(v, 4) for m, v in components.items()},
        "weights_used": idx["weights_used"],
        "missing": idx["missing"],
        "names": idx["names"],
        "calibration_mode": calib_mode,
        "ece": ece_value,
        "faithfulness_mode": (faith.mode if faith else "not_run"),
        "hallucination_rate": (
            round(faith.hallucination_rate, 4)
            if faith and faith.hallucination_rate is not None else None),
        "faithfulness_no_reference_cases": (faith.no_reference_cases if faith else 0),
        "pass_at_1": round(pass_at_1(case_runs), 4),
        # pass@k and pass^k answer DIFFERENT questions and both sources define
        # both: "one success is enough" (a candidate a human reviews) versus
        # "reliable every time" (an agent acting unattended). Reported as
        # top-level keys, never as `components` — `compute_index` intersects
        # with `index_weights()`, so an unweighted key provably cannot move the
        # Index. `flaky_rate` is the gap between them, per case: a case that
        # passed sometimes is NON-DETERMINISTIC, which needs a cause rather than
        # a better agent.
        "pass_at_k": round(pass_at_k(case_runs), 4),
        "flaky_rate": round(flakiness(case_runs), 4),
        "k_runs_cost_usd": round(total_cost, 4),
        **({"per_case": {tid: list(v) for tid, v in per_case.items()}}
           if include_per_case else {}),
    }
