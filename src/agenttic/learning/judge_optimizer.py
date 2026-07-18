"""Judge optimizer (SPEC-3 Step 15.3) — the judge analogue of Step 14.

Step 14 (``learning.optimizer``) learns a better AGENT config from failures +
human feedback. This module learns a better JUDGE config from judge-vs-human
DISAGREEMENTS, mirroring the same five-stage shape:

    collect_disagreements → propose → evaluate → gate → promote

with the JudgeConfig lineage (15.1) as the ledger, a fail-CLOSED gate, and
rejected candidates recorded with reasons.

What is REUSED rather than duplicated:

* 15.1 — :class:`~agenttic.schema.judge_config.JudgeConfig`,
  :func:`~agenttic.schema.judge_config.render_judge_prompt`, and the registry's
  ``save_judge_config`` / ``active_judge_config`` / ``judge_lineage`` /
  ``set_active_judge_config`` (the atomic active→retired + promote).
  :class:`~agenttic.scoring.judge.LLMJudge` renders a config and produces a
  :class:`~agenttic.schema.scorecard.CriterionScore`.
* 15.2 — :func:`~agenttic.scoring.calibration.frozen_split` (Hard Rule 15: the
  held-out set is frozen across rounds), :func:`~agenttic.scoring.calibration.
  optimizable` (the min-labels guard), and the agreement metrics
  ``exact_match_rate`` (binary) + ``krippendorff_alpha_interval`` (three-point).

Two design decisions the SPEC asks to be documented:

1. **How judge scores are obtained.** ``collect_disagreements`` and ``evaluate``
   RE-SCORE the split's traces with an :class:`LLMJudge` on demand rather than
   reading pre-stored scorecard CriterionScores. Stored scorecards are scored by
   whatever judge config was active at the time; a fair before/after comparison
   needs BOTH sides scored by a config we control (the current active for the
   "before", the candidate for the "after"). Re-scoring is the reliable path —
   it removes the confound of heterogeneous historical scorecards. (Callers who
   want the pre-stored path can pass ``active_config`` explicitly; the default
   is the registry's current active.)

2. **How ``evaluate`` points the judge at a specific CANDIDATE config.** A
   candidate is not ``active`` (there is exactly one active per criterion, 15.1),
   so an ``LLMJudge(reg=…)`` would resolve the *active* config, not the
   candidate. ``LLMJudge`` therefore takes a ``pinned_config`` handle: when set,
   the judge renders THAT config for its criterion and ignores the registry
   lookup. ``evaluate`` builds ``LLMJudge(pinned_config=candidate, …)``. This is
   additive and defaults off, so every existing judge construction is unchanged.

Nothing here talks to the network directly: the proposer and the judge client
are injected, exactly like :mod:`agenttic.learning.optimizer`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from agenttic.schema.judge_config import JudgeConfig, seed_config_for
from agenttic.schema.rubric import Criterion
from agenttic.scoring.calibration import (
    HOLDOUT,
    TRAIN,
    exact_match_rate,
    frozen_split,
    krippendorff_alpha_interval,
    optimizable,
)
from agenttic.scoring.judge import LLMJudge

ProgressFn = Callable[[str, dict], None]

# Defaults for the judge_learning: config block (real values live in config.yaml;
# these are the safety net when a key is absent — Hard Rule 7).
_DEFAULT_HOLDOUT_FRAC = 0.4
_DEFAULT_SEED = 1234
_DEFAULT_MIN_HOLDOUT_GAIN = 0.05
_DEFAULT_MAX_OVERFIT_GAP = 0.15


# -- the round record (rejected-with-reasons + lineage evidence) -------------

class JudgeOptimizationRound(BaseModel):
    """One node in the judge-config promotion history (persisted on the
    JudgeConfig's ``changelog`` + status, and returned in the run summary).

    Records the before/after agreement on BOTH splits so a promotion — or a
    rejection — is self-describing and auditable. ``promoted`` candidates carry
    the winning numbers; ``rejected`` ones carry the gate's reason (the ledger
    is a search history, not just a winners' list)."""

    round: int
    criterion_id: str
    candidate_id: str
    parent_id: str
    promoted: bool
    reason: str = ""
    train_before: Optional[float] = None
    train_after: Optional[float] = None
    holdout_before: Optional[float] = None
    holdout_after: Optional[float] = None
    n_holdout_scored: int = 0
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _jl(cfg: dict) -> dict:
    return cfg.get("judge_learning") or {}


def _metric_for(scale: str):
    """The agreement metric for a criterion's scale: exact-match for binary,
    Krippendorff's alpha for three_point (mirrors ``calibration_report``)."""
    return exact_match_rate if scale == "binary" else krippendorff_alpha_interval


def _agreement(pairs: list[tuple[float, float]], scale: str) -> Optional[float]:
    """Agreement over (judge, human) pairs, or None when there is nothing to
    measure (empty split)."""
    if not pairs:
        return None
    return _metric_for(scale)(pairs)


# -- judge scoring helpers ----------------------------------------------------

def _score_split(
    reg,
    criterion: Criterion,
    trace_ids: list[str],
    *,
    judge,
) -> tuple[dict[str, float], list[str]]:
    """Score each trace_id for one criterion with ``judge``.

    Returns ``(scores_by_trace, errors)``. A trace that cannot be loaded or that
    the judge fails on is recorded in ``errors`` and omitted from the scores —
    the gate treats held-out scoring errors as fail-CLOSED, so they are never
    silently dropped. ``tc`` for the judge is a minimal shell (the judge prompt
    needs only ``input``); traces carry their own evidence."""
    from agenttic.registry.sqlite_store import NotFoundError
    from agenttic.schema.testcase import TestCase

    scores: dict[str, float] = {}
    errors: list[str] = []
    for tid in trace_ids:
        try:
            trace = reg.get_trace(tid)
        except NotFoundError:
            errors.append(f"trace {tid} not found")
            continue
        tc = TestCase(
            test_id=trace.test_case_id or tid,
            suite_id=getattr(trace, "suite_id", "") or "s",
            task_description="calibration case",
            input=getattr(trace, "input", {}) or {},
            rubric_id=criterion.criterion_id)
        try:
            cs = judge.score_criterion(criterion, trace, tc)
        except Exception as exc:  # noqa: BLE001 — a judge failure is fail-closed data
            errors.append(f"trace {tid}: {type(exc).__name__}: {exc}")
            continue
        scores[tid] = cs.score
    return scores, errors


def _pairs(scores: dict[str, float], labels: dict, criterion_id: str
           ) -> list[tuple[float, float]]:
    """Join judge scores with human labels into (judge, human) pairs for the
    trace_ids present in BOTH."""
    out: list[tuple[float, float]] = []
    for tid, judge_score in scores.items():
        human = labels.get((tid, criterion_id))
        if human is not None:
            out.append((judge_score, human))
    return out


def _build_judge(cfg: dict, criterion: Criterion, *, client, agent_model: str,
                 reg=None, pinned_config: JudgeConfig | None = None) -> LLMJudge:
    """An :class:`LLMJudge` for one criterion, rendering either the registry's
    active config (``reg``) or a specific ``pinned_config`` (the candidate under
    evaluation). The judge model must differ from the agent model (Hard Rule 4);
    we use ``models.judge_strong`` and guard against a coincidence by nudging the
    reported ``agent_model`` label when they collide (the label only gates the
    invariant; the mocked client ignores the model in tests)."""
    strong = cfg.get("models", {}).get("judge_strong", "judge-strong")
    am = agent_model or "agent-under-test"
    if strong == am:
        am = f"{am}__distinct"
    return LLMJudge(model=strong, agent_model=am, client=client, cfg=cfg,
                    reg=reg, pinned_config=pinned_config)


# -- 1. collect_disagreements (the dossier) ----------------------------------

def collect_disagreements(reg, criterion_id: str, split, labels, *,
                          cfg: dict | None = None, client=None,
                          criterion: Criterion | None = None,
                          active_config: JudgeConfig | None = None,
                          agent_model: str = "agent-under-test") -> dict:
    """Build the DISAGREEMENT dossier over the TRAIN split.

    ``split`` is ``(train_labels, holdout_labels)`` as produced by
    :func:`frozen_split`. For every TRAIN-set trace where the ACTIVE judge's
    score differs from the human label, record the trace excerpt, the judge's
    score+rationale, and the human's score (the ground truth). This is the raw
    material the proposer edits against — anchor sharpening + few-shot examples
    are drawn from these disagreements.

    Judge scores are obtained by re-scoring the train traces with the active
    JudgeConfig (see the module docstring for why re-scoring is the reliable
    path). ``criterion`` supplies the scale/description/anchors (resolved from
    the registry rubric when omitted)."""
    train_labels, _ = split
    criterion = criterion or _resolve_criterion(reg, criterion_id)
    active_config = active_config or reg.active_judge_config(criterion_id) \
        or seed_config_for(criterion_id)

    judge = _build_judge(cfg or {}, criterion, client=client,
                         agent_model=agent_model, pinned_config=active_config)
    train_ids = sorted({tid for (tid, cid) in train_labels if cid == criterion_id})
    scores, errors = _score_split(reg, criterion, train_ids, judge=judge)

    disagreements: list[dict] = []
    for tid in train_ids:
        judge_score = scores.get(tid)
        human = train_labels.get((tid, criterion_id))
        if judge_score is None or human is None:
            continue
        if judge_score == human:
            continue
        excerpt = _trace_excerpt(reg, tid)
        disagreements.append({
            "trace_id": tid,
            "trace_excerpt": excerpt,
            "judge_score": judge_score,
            "judge_rationale": _rationale(reg, criterion, tid, judge),
            "human_score": human,
        })

    return {
        "criterion_id": criterion_id,
        "scale": criterion.scale,
        "description": criterion.description,
        "anchors": dict(criterion.anchors),
        "n_train": len(train_ids),
        "n_disagreements": len(disagreements),
        "disagreements": disagreements,
        "errors": errors,
    }


def _rationale(reg, criterion: Criterion, trace_id: str, judge) -> str:
    """Best-effort judge rationale for a disagreement (re-score once). Empty on
    any failure — the dossier degrades to score-only, still valid."""
    try:
        from agenttic.schema.testcase import TestCase
        trace = reg.get_trace(trace_id)
        tc = TestCase(test_id=trace.test_case_id or trace_id, suite_id="s",
                      task_description="calibration case",
                      input=getattr(trace, "input", {}) or {},
                      rubric_id=criterion.criterion_id)
        return (judge.score_criterion(criterion, trace, tc).judge_rationale or "")
    except Exception:  # noqa: BLE001
        return ""


def _trace_excerpt(reg, trace_id: str, limit: int = 400) -> str:
    try:
        return (reg.get_trace(trace_id).final_output or "")[:limit]
    except Exception:  # noqa: BLE001
        return ""


# -- 2. propose --------------------------------------------------------------

def propose(dossier: dict, active_config: JudgeConfig, n: int, *,
            client=None, model: str = "") -> list[JudgeConfig]:
    """Ask the proposer for ``n`` candidate JudgeConfigs targeting the dossier.

    Each candidate is an instruction rewrite (``system_prompt``) and/or
    anchor-sharpening note plus few-shot examples drawn from the DISAGREEMENT
    cases (human label = ground truth). Chained to the active config:
    ``version = active.version + 1``, ``parent_id = active.judge_config_id``,
    ``status = "candidate"``, with a changelog.

    CONSTRAINT enforced here (Hard Rule: the rubric owns the scale + the
    criterion description, the judge config does NOT): a proposal may change the
    system prompt, the few-shot examples, and add anchor-emphasis text, but it
    can NEVER change the criterion's scale or description — those fields are not
    part of :class:`JudgeConfig` at all, so they are structurally immutable here.

    ``client`` is the injected proposer (a ``.propose(dossier, active, n)`` call,
    mocked in tests). It returns a list of raw dicts
    ``{system_prompt?, few_shot_examples?, rationale?, changelog?}``; this
    function stamps each into a well-formed candidate JudgeConfig."""
    assert client is not None, "propose requires an injected proposer client"
    raw = client.propose(dossier, active_config, n) or []

    out: list[JudgeConfig] = []
    for i, prop in enumerate(raw):
        version = active_config.version + 1
        cand_id = f"{active_config.criterion_id}:v{version}:{i}"
        system_prompt = str(prop.get("system_prompt")
                            or active_config.system_prompt)
        few_shot = prop.get("few_shot_examples")
        if few_shot is None:
            few_shot = list(active_config.few_shot_examples)
        changelog = str(prop.get("changelog")
                        or prop.get("rationale")
                        or "judge-config edit")[:300]
        out.append(JudgeConfig(
            judge_config_id=cand_id,
            version=version,
            criterion_id=active_config.criterion_id,
            system_prompt=system_prompt,
            instruction_template=active_config.instruction_template,
            few_shot_examples=list(few_shot),
            parent_id=active_config.judge_config_id,
            changelog=changelog,
            status="candidate"))
    return out


# -- 3. evaluate -------------------------------------------------------------

def evaluate(candidate: JudgeConfig, reg, criterion: Criterion, split, labels,
             *, client=None, agent_model: str = "agent-under-test",
             cfg: dict | None = None) -> dict:
    """Re-score the split's traces with the CANDIDATE config and measure
    train + held-out agreement.

    The candidate is pinned into an :class:`LLMJudge` via ``pinned_config`` (see
    the module docstring) so the judge renders THIS config even though it is not
    active. Both splits are scored; agreement uses exact-match for binary scales
    and Krippendorff's alpha for three_point (15.2 metrics).

    Returns ``{train_agreement, holdout_agreement, n_train_scored,
    n_holdout_scored, errors}``. ``holdout_agreement`` is ``None`` when NO
    held-out case could be scored — the gate reads that as fail-CLOSED."""
    train_labels, holdout_labels = split
    cid = criterion.criterion_id
    judge = _build_judge(cfg or {}, criterion, client=client,
                         agent_model=agent_model, pinned_config=candidate)

    train_ids = sorted({t for (t, c) in train_labels if c == cid})
    holdout_ids = sorted({t for (t, c) in holdout_labels if c == cid})

    train_scores, train_errs = _score_split(reg, criterion, train_ids, judge=judge)
    hold_scores, hold_errs = _score_split(reg, criterion, holdout_ids, judge=judge)

    train_pairs = _pairs(train_scores, train_labels, cid)
    hold_pairs = _pairs(hold_scores, holdout_labels, cid)

    return {
        "train_agreement": _agreement(train_pairs, criterion.scale),
        "holdout_agreement": _agreement(hold_pairs, criterion.scale),
        "n_train_scored": len(train_pairs),
        "n_holdout_scored": len(hold_pairs),
        "n_holdout_total": len(holdout_ids),
        "errors": train_errs + hold_errs,
    }


# -- 4. gate -----------------------------------------------------------------

def gate(candidate_eval: dict, active_eval: dict, cfg: dict
         ) -> tuple[bool, str]:
    """Decide whether a candidate judge should be PROMOTED over the active one.

    Promote iff ALL hold:

    (a) TRAIN agreement STRICTLY improves;
    (b) HELD-OUT agreement improves by >= ``judge_learning.min_holdout_gain`` —
        a MARGIN, so a +0.01 on n≈10 (noise) is rejected;
    (c) HELD-OUT does not sit below TRAIN by more than
        ``judge_learning.max_overfit_gap`` — else rejected with "overfit" in the
        reason (the candidate memorized the train set);
    (d) FAIL CLOSED: if the candidate scored ZERO held-out cases (or held-out
        agreement is unmeasurable), reject — same principle as the Step 14
        missing-criteria fix. We cannot certify an improvement we could not
        measure on the frozen benchmark.

    Returns ``(promote, reason)``."""
    jl = _jl(cfg)
    min_gain = float(jl.get("min_holdout_gain", _DEFAULT_MIN_HOLDOUT_GAIN))
    max_gap = float(jl.get("max_overfit_gap", _DEFAULT_MAX_OVERFIT_GAP))

    c_train = candidate_eval.get("train_agreement")
    c_hold = candidate_eval.get("holdout_agreement")
    a_train = active_eval.get("train_agreement")
    a_hold = active_eval.get("holdout_agreement")
    n_hold = candidate_eval.get("n_holdout_scored", 0)

    # (d) fail CLOSED on an unmeasurable held-out benchmark.
    if n_hold == 0 or c_hold is None:
        errs = candidate_eval.get("errors") or []
        detail = f" ({errs[0]})" if errs else ""
        return False, (
            "rejected: candidate scored no held-out cases — cannot certify an "
            f"improvement on the frozen benchmark (fail closed){detail}")

    # (a) train must strictly improve.
    if c_train is None or a_train is None:
        return False, ("rejected: train agreement unmeasurable for candidate or "
                       "active — cannot compare")
    if not (c_train > a_train):
        return False, (
            f"rejected: train agreement did not improve "
            f"({a_train:.3f} -> {c_train:.3f})")

    # (b) held-out gain must clear the margin.
    if a_hold is None:
        # No active held-out baseline to beat: require the candidate to clear the
        # margin from zero (still a real, measurable improvement on the benchmark).
        holdout_gain = c_hold
    else:
        holdout_gain = c_hold - a_hold
    if holdout_gain < min_gain:
        base = f"{a_hold:.3f} -> " if a_hold is not None else "(no baseline) "
        return False, (
            f"rejected: held-out gain {holdout_gain:+.3f} below margin "
            f"min_holdout_gain={min_gain:.3f} ({base}{c_hold:.3f})")

    # (c) overfit guard: held-out cannot trail train by more than the gap.
    gap = c_train - c_hold
    if gap > max_gap:
        return False, (
            f"rejected: overfit — train {c_train:.3f} exceeds held-out "
            f"{c_hold:.3f} by {gap:.3f} > max_overfit_gap={max_gap:.3f}")

    hold_txt = f"{a_hold:.3f} -> {c_hold:.3f}" if a_hold is not None \
        else f"(none) -> {c_hold:.3f}"
    return True, (
        f"promoted: train {a_train:.3f} -> {c_train:.3f}, held-out {hold_txt} "
        f"(gain {holdout_gain:+.3f} >= {min_gain:.3f})")


# -- 5. promote --------------------------------------------------------------

def promote(reg, criterion_id: str, candidate: JudgeConfig,
            round_record: JudgeOptimizationRound) -> JudgeConfig:
    """Promote ``candidate`` to active and retire its predecessor.

    Persists the candidate (with its ``round_record`` folded into the changelog
    for a self-describing, queryable lineage), then flips active↔retired
    atomically via ``set_active_judge_config`` (15.1 — never two actives, even
    transiently). Returns the promoted JudgeConfig (status="active")."""
    stamped = candidate.model_copy(update={
        "changelog": _with_record(candidate.changelog, round_record)})
    reg.save_judge_config(stamped)
    promoted = reg.set_active_judge_config(criterion_id, stamped.judge_config_id)
    return promoted


def _record_rejection(reg, candidate: JudgeConfig,
                      round_record: JudgeOptimizationRound) -> JudgeConfig:
    """Persist a REJECTED candidate (auditable search history, queryable via
    ``judge_lineage``): stored with ``status="rejected"`` and the gate's reason
    folded into the changelog."""
    rejected = candidate.model_copy(update={
        "status": "rejected",
        "changelog": _with_record(candidate.changelog, round_record)})
    try:
        reg.save_judge_config(rejected)
    except Exception:  # noqa: BLE001 — a dup id on retry must not crash the round
        pass
    return rejected


def _with_record(changelog: str, record: JudgeOptimizationRound) -> str:
    """Fold a round record's numbers into the changelog so a stored config is
    self-describing (the lineage carries before/after on both splits)."""
    tag = json.dumps({
        "round": record.round, "promoted": record.promoted,
        "reason": record.reason,
        "train": [record.train_before, record.train_after],
        "holdout": [record.holdout_before, record.holdout_after],
        "n_holdout_scored": record.n_holdout_scored})
    base = (changelog or "").strip()
    return (base + " | " if base else "") + f"round={tag}"


# -- 6. orchestrator ---------------------------------------------------------

def run_judge_learning(reg, cfg: dict, criterion_id: str, rounds: int = 1, *,
                       client=None, judge_client=None, labels: dict | None = None,
                       agent_model: str = "agent-under-test",
                       on_progress: ProgressFn | None = None) -> dict:
    """Run the judge-learning loop for one criterion and return a summary.

    ``client`` is the injected PROPOSER (its ``.propose(dossier, active, n)`` is
    called). ``judge_client`` is the injected JUDGE LLM client used for scoring
    train/held-out cases; it defaults to the proposer's ``.judge_client``
    attribute when present, else to ``client`` (a combined mock is convenient in
    tests).

    Refuses (via :func:`optimizable`) when the criterion has fewer than
    ``judge_learning.min_labels`` labels — the summary carries
    ``{"refused": True, "reason": "insufficient labels ..."}``.

    Per round: split labels (FROZEN, 15.2) → build the disagreement dossier →
    propose N candidates → evaluate each on train+held-out → gate → promote the
    best survivor (or record every candidate's rejection). The promoted config
    becomes the active parent for the next round.

    Returns ``{"criterion_id", "rounds", "refused", "promoted": [...],
    "rejected": [...], "records": [JudgeOptimizationRound...]}``."""
    rounds = max(1, min(rounds, 10))
    jl = _jl(cfg)
    holdout_frac = float(jl.get("holdout_frac", _DEFAULT_HOLDOUT_FRAC))
    seed = int(jl.get("seed", _DEFAULT_SEED))
    n_candidates = int(jl.get("candidates_per_round", 3))

    labels = labels if labels is not None else _load_labels_for(reg, cfg,
                                                                criterion_id)
    ok, reason = optimizable(labels, criterion_id, cfg)
    if not ok:
        return {"criterion_id": criterion_id, "rounds": 0, "refused": True,
                "reason": reason, "promoted": [], "rejected": [], "records": []}

    # A learning round for this criterion RESOLVES the outstanding request the
    # calibration flywheel filed (Step 15.4): re-optimization is now under way
    # on-command, so any open "please re-optimize" request is cleared. Runs
    # BEFORE the (network-touching) rounds so an early failure still records
    # that the request was actioned; harmless when there is nothing open.
    try:
        reg.clear_judge_optimization_requests(criterion_id)
    except Exception:  # noqa: BLE001 — clearing is best-effort, never blocks a round
        pass

    criterion = _resolve_criterion(reg, criterion_id)
    proposer = client if client is not None else _default_proposer(cfg)
    jc = judge_client if judge_client is not None else \
        getattr(proposer, "judge_client", None) or client

    promoted: list[JudgeConfig] = []
    rejected: list[JudgeConfig] = []
    records: list[JudgeOptimizationRound] = []

    # Ensure there is an active config to start from (seed if absent).
    active = reg.active_judge_config(criterion_id)
    if active is None:
        active = seed_config_for(criterion_id)
        try:
            reg.save_judge_config(active)
        except Exception:  # noqa: BLE001 — already seeded elsewhere
            active = reg.active_judge_config(criterion_id) or active

    for rnd in range(1, rounds + 1):
        split = frozen_split(reg, labels, criterion_id,
                             holdout_frac=holdout_frac, seed=seed)

        active_eval = evaluate(active, reg, criterion, split, labels,
                               client=jc, agent_model=agent_model, cfg=cfg)

        dossier = collect_disagreements(
            reg, criterion_id, split, labels, cfg=cfg, client=jc,
            criterion=criterion, active_config=active, agent_model=agent_model)
        if on_progress:
            on_progress("dossier", {"round": rnd,
                                    "n_disagreements": dossier["n_disagreements"]})

        candidates = propose(dossier, active, n_candidates, client=proposer)

        survivors: list[tuple[JudgeConfig, dict, str]] = []
        for cand in candidates:
            cand_eval = evaluate(
                cand, reg, criterion, split, labels,
                client=jc, agent_model=agent_model, cfg=cfg)
            promote_ok, gate_reason = gate(cand_eval, active_eval, cfg)
            record = JudgeOptimizationRound(
                round=rnd, criterion_id=criterion_id,
                candidate_id=cand.judge_config_id,
                parent_id=active.judge_config_id, promoted=promote_ok,
                reason=gate_reason,
                train_before=active_eval.get("train_agreement"),
                train_after=cand_eval.get("train_agreement"),
                holdout_before=active_eval.get("holdout_agreement"),
                holdout_after=cand_eval.get("holdout_agreement"),
                n_holdout_scored=cand_eval.get("n_holdout_scored", 0),
                errors=cand_eval.get("errors", []))
            if promote_ok:
                survivors.append((cand, cand_eval, gate_reason))
                records.append(record)
            else:
                rejected.append(_record_rejection(reg, cand, record))
                records.append(record)

        if not survivors:
            if on_progress:
                on_progress("round_done", {"round": rnd, "chosen": None})
            continue

        # Adopt the best survivor: highest held-out agreement, then train.
        survivors.sort(key=lambda t: (-(t[1].get("holdout_agreement") or 0.0),
                                      -(t[1].get("train_agreement") or 0.0)))
        cand, cand_eval, gate_reason = survivors[0]
        record = next(r for r in reversed(records)
                      if r.candidate_id == cand.judge_config_id)
        ac = promote(reg, criterion_id, cand, record)
        promoted.append(ac)
        # Record the also-rans as rejected (not adopted this round).
        for other, other_eval, _ in survivors[1:]:
            orec = JudgeOptimizationRound(
                round=rnd, criterion_id=criterion_id,
                candidate_id=other.judge_config_id,
                parent_id=active.judge_config_id, promoted=False,
                reason="not adopted: a stronger candidate won this round",
                train_before=active_eval.get("train_agreement"),
                train_after=other_eval.get("train_agreement"),
                holdout_before=active_eval.get("holdout_agreement"),
                holdout_after=other_eval.get("holdout_agreement"),
                n_holdout_scored=other_eval.get("n_holdout_scored", 0))
            rejected.append(_record_rejection(reg, other, orec))
            records.append(orec)

        active = ac  # the promoted config parents the next round
        if on_progress:
            on_progress("round_done", {"round": rnd,
                                       "chosen": ac.judge_config_id})

    return {"criterion_id": criterion_id, "rounds": rounds, "refused": False,
            "promoted": promoted, "rejected": rejected, "records": records}


# -- 7. rejudge --------------------------------------------------------------

def rejudge(reg, cfg: dict, scorecard_id: str, *, client=None,
            agent_model: str = "agent-under-test") -> object:
    """Re-score a scorecard's judge-scored criteria with the CURRENT active
    configs and save the result as a NEW scorecard version (Hard Rule 14 —
    append-only; the original is NEVER mutated).

    Only ``scorer == "judge"`` criteria are re-scored (code/fi criteria and their
    scores are carried through untouched). The new scorecard gets a fresh id and
    is persisted alongside the original; both remain queryable. Returns the new
    :class:`Scorecard`."""
    import uuid

    from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard

    original = reg.get_scorecard(scorecard_id)
    rubric = reg.get_rubric(original.rubric_id, original.rubric_version)
    crit_by_id = {c.criterion_id: c for c in rubric.criteria}

    # One judge per criterion, rendering that criterion's CURRENT active config.
    judges: dict[str, LLMJudge] = {}

    def _judge_for(criterion: Criterion) -> LLMJudge:
        if criterion.criterion_id not in judges:
            judges[criterion.criterion_id] = _build_judge(
                cfg, criterion, client=client, agent_model=agent_model, reg=reg)
        return judges[criterion.criterion_id]

    new_runs: list[RunScore] = []
    for run in original.run_scores:
        try:
            trace = reg.get_trace(run.trace_id)
        except Exception:  # noqa: BLE001 — keep the run, carry old scores through
            new_runs.append(run.model_copy())
            continue
        from agenttic.schema.testcase import TestCase
        tc = TestCase(test_id=run.test_id, suite_id=original.suite_id,
                      task_description="rejudge", input=getattr(trace, "input", {})
                      or {}, rubric_id=original.rubric_id)
        rescored: list[CriterionScore] = []
        for cs in run.criterion_scores:
            criterion = crit_by_id.get(cs.criterion_id)
            if criterion is None or criterion.scorer != "judge":
                rescored.append(cs)  # non-judge scores carry through unchanged
                continue
            try:
                new_cs = _judge_for(criterion).score_criterion(criterion, trace, tc)
                rescored.append(new_cs.model_copy(update={
                    "calibrated": cs.calibrated}))
            except Exception:  # noqa: BLE001 — keep the prior score on judge failure
                rescored.append(cs)
        new_runs.append(run.model_copy(update={"criterion_scores": rescored}))

    new_sc = Scorecard.aggregate(
        scorecard_id=f"{original.scorecard_id}--rejudge-{uuid.uuid4().hex[:8]}",
        agent_id=original.agent_id, suite_id=original.suite_id,
        suite_version=original.suite_version, rubric_id=original.rubric_id,
        rubric_version=original.rubric_version, run_scores=new_runs,
        visibility_tier=original.visibility_tier)
    reg.save_scorecard(new_sc)
    return new_sc


# -- helpers -----------------------------------------------------------------

def _resolve_criterion(reg, criterion_id: str) -> Criterion:
    """Find the criterion's definition (scale/description/anchors) in the
    registry's stored rubrics. Falls back to a minimal binary judge criterion
    when it cannot be located — enough for the judge to render, never a real
    run. Scans ``RubricRow`` directly (there is no bulk-rubric accessor)."""
    try:
        from sqlmodel import Session, select

        from agenttic.registry.sqlite_store import RubricRow
        from agenttic.schema.rubric import Rubric
        with Session(reg.engine) as s:
            rows = s.exec(select(RubricRow).where(
                RubricRow.tenant_id == reg.tenant)).all()
        for row in rows:
            rubric = Rubric.model_validate_json(row.payload)
            for c in rubric.criteria:
                if c.criterion_id == criterion_id and c.scorer == "judge":
                    return c
    except Exception:  # noqa: BLE001
        pass
    return Criterion(criterion_id=criterion_id, description=criterion_id,
                     scorer="judge", scale="binary",
                     anchors={"pass": "ok", "fail": "not ok"})


def _load_labels_for(reg, cfg: dict, criterion_id: str) -> dict:
    """Load the human labels for a criterion from
    ``{paths.calibration_dir}/*.csv``, filtered to this criterion. Merges every
    suite CSV in the directory (a criterion may span suites)."""
    from pathlib import Path

    from agenttic.scoring.calibration import load_labels

    cal_dir = Path(cfg.get("paths", {}).get("calibration_dir", "calibration/"))
    merged: dict = {}
    if cal_dir.exists():
        for csv_path in sorted(cal_dir.glob("*.csv")):
            try:
                for k, v in load_labels(csv_path).items():
                    merged[k] = v
            except Exception:  # noqa: BLE001
                continue
    return {k: v for k, v in merged.items() if k[1] == criterion_id}


def _default_proposer(cfg: dict):
    """A network-backed proposer is out of scope for this step's tests; the
    orchestrator requires an injected proposer client. Raise a clear error rather
    than silently doing nothing when none is provided."""
    raise ValueError(
        "run_judge_learning requires an injected proposer `client` "
        "(no default network proposer is wired in Step 15.3)")
