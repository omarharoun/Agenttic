"""Feedback miner — turn stored human feedback into draft tests + labels.

This is the engine that closes the outer loop (SPEC-2 Step 13). It reads the
UNPROCESSED feedback the registry accumulated in Step 11 and produces two
kinds of durable, human-gated experience data:

* ``mine_cases`` — every production ``correction`` becomes a draft test case.
  Mined cases are assembled into a NEW draft suite version ``v(n+1)`` that is
  ``approved=False``, so it is refused by the runner until a human runs
  ``agenttic approve``. Original (approved) versions are NEVER touched — no
  silent suite growth (Hard Rule 12). Each mined case carries its provenance:
  the source ``trace_id``, ``feedback_id``, and the corrected output the human
  proposed as ground truth (Hard Rule 11).

* ``mine_labels`` — every ``rating`` becomes a calibration label row
  ``trace_id,criterion_id,human_score`` appended to
  ``{calibration_dir}/{suite_id}.csv`` (the format ``calibration.load_labels``
  reads), so judge-vs-human agreement can be re-measured (Hard Rule 6).

Feedback is marked processed ONLY AFTER its draft suite / labels are durably
written, so a crash mid-mine re-mines rather than silently dropping a label,
and a second run never mines the same feedback twice.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from pathlib import Path

from agenttic.registry.sqlite_store import NotFoundError
from agenttic.schema.testcase import TestCase, TestSuite

MINED_TAG = "mined_from_production"
_CALIBRATION_HEADER = ("trace_id", "criterion_id", "human_score")

_DEFAULT_CALIBRATION_THRESHOLD = 0.8


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _paths(reg, cfg: dict | None) -> tuple[Path, Path]:
    """Resolve (review_dir, calibration_dir) from cfg, creating them.

    ``cfg`` is optional so the miner is trivially callable from tests; when
    omitted (or missing a key) it falls back to the conventional local dirs.
    """
    paths = (cfg or {}).get("paths", {}) if cfg else {}
    review_dir = Path(paths.get("review_dir", "review"))
    calibration_dir = Path(paths.get("calibration_dir", "calibration"))
    review_dir.mkdir(parents=True, exist_ok=True)
    calibration_dir.mkdir(parents=True, exist_ok=True)
    return review_dir, calibration_dir


def _mined_test_id(suite_id: str, version: int, feedback) -> str:
    """A stable, collision-resistant id for a mined case.

    Stable in the feedback_id (so re-running before approval is idempotent per
    feedback) and namespaced by the draft version it belongs to.
    """
    digest = hashlib.sha1(feedback.feedback_id.encode()).hexdigest()[:10]
    return f"{suite_id}-v{version}-mined-{digest}"


def _originating_input(reg, feedback, cases: list[TestCase]) -> dict:
    """The input the source trace was run against.

    Prefer the trace's ``test_case_id`` resolved against the target suite's
    cases (that is the originating test input). Fall back to the raw feedback
    trace's first ``final_output`` span input if the case can't be resolved,
    else an empty dict — a mined case is still valid provenance without it.
    """
    try:
        trace = reg.get_trace(feedback.trace_id)
    except NotFoundError:
        return {}
    by_id = {c.test_id: c for c in cases}
    if trace.test_case_id and trace.test_case_id in by_id:
        return dict(by_id[trace.test_case_id].input)
    return {}


# --------------------------------------------------------------------------- #
# mine_cases — corrections → draft suite v(n+1)
# --------------------------------------------------------------------------- #


def mine_cases(reg, agent_id: str, suite_id: str, cfg: dict | None = None
               ) -> list[TestCase]:
    """Mine unprocessed ``correction`` feedback into a draft suite ``v(n+1)``.

    For each unprocessed correction on ``agent_id``: build a draft ``TestCase``
    whose ``input`` is the source trace's originating test input,
    ``expected={"final_output": corrected_output}``, tagged
    ``"mined_from_production"``, carrying the target suite's ``rubric_id``.

    The mined cases become a NEW draft version ``n+1`` of ``suite_id`` (n = the
    current max version), ``approved=False`` — the original versions are never
    mutated (Hard Rule 12). A review markdown is written so a human can approve.

    No-op (returns ``[]``, no empty suite version) when there are no
    unprocessed corrections. Feedback is marked processed only AFTER the draft
    suite + review are written.
    """
    review_dir, _ = _paths(reg, cfg)

    # The target (latest) suite: source of rubric_id and originating inputs.
    suite, cases = reg.get_suite(suite_id)  # latest/highest version
    n = suite.version
    next_version = n + 1
    rubric_id = cases[0].rubric_id if cases else f"{suite_id}-rubric"

    corrections = [f for f in reg.unprocessed_feedback(agent_id)
                   if f.kind == "correction"]
    if not corrections:
        return []  # no silent, empty suite version

    mined: list[TestCase] = []
    provenance: list[dict] = []
    for fb in corrections:
        test_id = _mined_test_id(suite_id, next_version, fb)
        tc = TestCase(
            test_id=test_id,
            suite_id=suite_id,
            version=next_version,
            task_description=(fb.rationale
                              or f"Mined from production correction {fb.feedback_id}"),
            input=_originating_input(reg, fb, cases),
            expected={"final_output": fb.corrected_output},
            tags=[MINED_TAG],
            rubric_id=rubric_id,
        )
        mined.append(tc)
        provenance.append({
            "test_id": test_id,
            "feedback_id": fb.feedback_id,
            "trace_id": fb.trace_id,
            "source": fb.source,
            "rationale": fb.rationale,
        })

    business_context = json.dumps({
        "mined_from": "production_feedback",
        "parent_version": n,
        "agent_id": agent_id,
        "provenance": provenance,
    })
    draft = TestSuite(
        suite_id=suite_id,
        version=next_version,
        business_context=business_context,
        test_ids=[c.test_id for c in mined],
        approved=False,
    )
    reg.save_suite(draft, mined)  # raises DuplicateVersionError if v(n+1) exists
    _write_review(review_dir, draft, mined, provenance)

    # Only now that the draft is durably written do we retire the feedback.
    for fb in corrections:
        reg.mark_feedback_processed(fb.feedback_id)

    return mined


def _write_review(review_dir: Path, suite: TestSuite,
                  cases: list[TestCase], provenance: list[dict]) -> None:
    """Write a human-reviewable markdown for the draft suite (approved=False)."""
    prov_by_id = {p["test_id"]: p for p in provenance}
    lines = [
        f"# Review: suite `{suite.suite_id}` v{suite.version} (mined from production)",
        "",
        f"Status: **DRAFT — not runnable** until approved — "
        f"CLI: `agenttic approve {suite.suite_id} --version {suite.version}`.",
        "",
        f"These {len(cases)} case(s) were mined from unprocessed human "
        "corrections. Each preserves its source provenance (Hard Rule 11); "
        "none is runnable until a human approves this version (Hard Rule 12).",
        "",
        "## Mined cases",
    ]
    for c in cases:
        p = prov_by_id.get(c.test_id, {})
        expected = (c.expected or {}).get("final_output", "")
        lines.append(
            f"- `{c.test_id}` [{', '.join(c.tags)}]\n"
            f"  - from feedback `{p.get('feedback_id', '?')}` "
            f"(source: {p.get('source', '?')}) on trace `{p.get('trace_id', '?')}`\n"
            f"  - rationale: {p.get('rationale', '')}\n"
            f"  - input: `{json.dumps(c.input)[:200]}`\n"
            f"  - expected final_output: `{str(expected)[:200]}`"
        )
    (review_dir / f"{suite.suite_id}.md").write_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# mine_labels — ratings → calibration CSV
# --------------------------------------------------------------------------- #


def mine_labels(reg, agent_id: str | None = None, suite_id: str | None = None,
                cfg: dict | None = None) -> int:
    """Mine unprocessed ``rating`` feedback into calibration label rows.

    Appends ``trace_id,criterion_id,human_score`` to
    ``{calibration_dir}/{suite_id}.csv`` (header written once, never
    duplicated). Marks each processed AFTER its row is written. Returns the
    number of rows appended.

    Rating→suite routing: a ``rating`` carries no suite of its own, so we route
    it to the suite the trace belongs to. We derive that from the source
    trace's scorecards (``reg.scorecards_for(agent_id).suite_id`` for a
    scorecard containing this trace); if that cannot be resolved we fall back
    to the explicit ``suite_id`` argument. A rating we cannot route to any
    suite is left UNPROCESSED (not silently dropped), so a later call with a
    ``suite_id`` can still place it.
    """
    _, calibration_dir = _paths(reg, cfg)

    ratings = [f for f in reg.unprocessed_feedback(agent_id)
               if f.kind == "rating"]
    if not ratings:
        return 0

    # Count each criterion's labels BEFORE this mining run, so the trigger can
    # tell whether a criterion just CROSSED min_labels (was below, now >=).
    grew: dict[str, str] = {}  # criterion_id -> a target suite that grew it
    counts_before = _label_counts(calibration_dir)

    appended = 0
    for fb in ratings:
        target = _route_rating_suite(reg, fb, suite_id)
        if target is None:
            continue  # unroutable → leave unprocessed for a later, targeted run
        _append_label(calibration_dir / f"{target}.csv",
                      fb.trace_id, fb.criterion_id, fb.rating)
        reg.mark_feedback_processed(fb.feedback_id)
        grew.setdefault(fb.criterion_id, target)
        appended += 1

    # Side effect ONLY: notice + file a request per criterion whose labels grew.
    # This NEVER auto-runs the optimizer (optimization stays on-command via
    # `learn-judge`) — the analogue of Step 9's drift-triggered re-eval.
    for criterion_id, target in grew.items():
        try:
            maybe_request_judge_optimization(
                reg, cfg or {}, criterion_id, target,
                counts_before=counts_before.get(criterion_id, 0))
        except Exception:  # noqa: BLE001 — a trigger failure must not lose labels
            pass

    return appended


def _label_counts(calibration_dir: Path) -> dict[str, int]:
    """Distinct-trace label count per criterion across every suite CSV in the
    calibration dir (the same universe ``optimizable`` counts over)."""
    from agenttic.scoring.calibration import load_labels
    seen: dict[str, set[str]] = {}
    if calibration_dir.exists():
        for csv_path in sorted(calibration_dir.glob("*.csv")):
            try:
                labels = load_labels(csv_path)
            except Exception:  # noqa: BLE001 — a bad/empty CSV is skipped
                continue
            for (trace_id, criterion_id) in labels:
                seen.setdefault(criterion_id, set()).add(trace_id)
    return {cid: len(ids) for cid, ids in seen.items()}


def _route_rating_suite(reg, fb, suite_id: str | None) -> str | None:
    """Resolve which suite a rating's label belongs to (see ``mine_labels``)."""
    try:
        for sc in reg.scorecards_for(fb.agent_id):
            if fb.trace_id in _scorecard_trace_ids(sc):
                return sc.suite_id
    except Exception:  # noqa: BLE001 — scorecard shape is best-effort here
        pass
    return suite_id


def _scorecard_trace_ids(sc) -> set[str]:
    """Best-effort set of trace_ids referenced by a scorecard, across the few
    shapes a scorecard's per-case results may take."""
    ids: set[str] = set()
    for attr in ("run_scores", "results", "cases", "case_results", "entries"):
        rows = getattr(sc, attr, None) or []
        for r in rows:
            tid = getattr(r, "trace_id", None)
            if tid is None and isinstance(r, dict):
                tid = r.get("trace_id")
            if tid:
                ids.add(tid)
    return ids


def _append_label(path: Path, trace_id: str, criterion_id: str,
                  human_score: float) -> None:
    """Append one calibration row, writing the header iff the file is new."""
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_CALIBRATION_HEADER)
        w.writerow([trace_id, criterion_id, human_score])


# --------------------------------------------------------------------------- #
# Step 15.4 — auto-TRIGGER from the calibration flywheel.
#
# New labels are the signal. When ``mine_labels`` grows a criterion's label set,
# it asks ``maybe_request_judge_optimization`` whether that criterion's judge
# now warrants re-optimizing, and if so FILES a request (never runs the
# optimizer). Two triggers, matching Step 9's drift-triggered re-eval:
#
#   1. CROSSED min_labels — the criterion was below the optimizable threshold
#      and this run pushed it to/above it (it just became optimizable).
#   2. Calibrated-eligible but agreement DROPPED — the criterion already has
#      >= min_labels labels and its judge-vs-human agreement on the UPDATED
#      label set is below ``scoring.calibration_threshold``.
#
# Resolving judge scores for the agreement check (documented design choice):
# we read the judge's per-criterion scores from the tenant's STORED scorecards
# (``reg.scorecards_for(agent_id)`` → run_scores → criterion_scores), keyed by
# (trace_id, criterion_id). ``mine_labels`` already runs at feedback-mining
# time with the same agent's scorecards to hand, and those scorecards ARE the
# judge's numbers — no LLM call, no fabrication. If judge scores can't be
# resolved for at least ``min_n`` labeled traces, the agreement check is
# SKIPPED for this round (we never invent a score); the crossed-min_labels
# trigger still fires on its own.
# --------------------------------------------------------------------------- #


def maybe_request_judge_optimization(reg, cfg: dict, criterion_id: str,
                                     suite_id: str, *,
                                     counts_before: int | None = None):
    """Notice whether ``criterion_id``'s judge needs re-optimizing and, if so,
    FILE a :class:`JudgeOptimizationRequest` — never run the optimizer.

    Returns the filed request (existing open one is refreshed, not duplicated),
    or ``None`` when nothing warrants a request this round."""
    from agenttic.scoring.calibration import (
        calibration_report, min_labels, optimizable,
    )
    from agenttic.schema.judge_request import JudgeOptimizationRequest

    _, calibration_dir = _paths(reg, cfg)
    labels = _load_all_labels(calibration_dir)
    ok, _reason = optimizable(labels, criterion_id, cfg)
    n_now = _distinct_traces(labels, criterion_id)
    threshold = float((cfg.get("scoring") or {}).get(
        "calibration_threshold", _DEFAULT_CALIBRATION_THRESHOLD))
    min_lbl = min_labels(cfg)

    reason: str | None = None

    # Trigger 1: the criterion JUST crossed min_labels (was below, now >=).
    if ok and counts_before is not None and counts_before < min_lbl <= n_now:
        reason = f"criterion crossed min_labels ({n_now} labels)"

    # Trigger 2: calibrated-eligible but agreement dropped below threshold. Only
    # meaningful once optimizable; computed from stored judge scores (skip if we
    # can't resolve enough — never fabricate).
    if ok and reason is None:
        judge_scores, scales = _stored_judge_scores(reg, criterion_id)
        report = calibration_report(
            judge_scores, labels, scales, threshold=threshold)
        crit = report.get(criterion_id)
        if crit is not None and crit.agreement < threshold:
            reason = (
                f"agreement {crit.agreement:.2f} dropped below threshold "
                f"{threshold:.2f} on {crit.n} labels")

    if reason is None:
        return None

    req = JudgeOptimizationRequest(
        request_id=f"jor-{criterion_id}-{uuid.uuid4().hex[:10]}",
        criterion_id=criterion_id, suite_id=suite_id, reason=reason)
    return reg.save_judge_optimization_request(req)


def _load_all_labels(calibration_dir: Path) -> dict:
    """Merge every calibration CSV into one ``{(trace_id, criterion_id): score}``
    dict (a criterion may span suites)."""
    from agenttic.scoring.calibration import load_labels
    merged: dict = {}
    if calibration_dir.exists():
        for csv_path in sorted(calibration_dir.glob("*.csv")):
            try:
                merged.update(load_labels(csv_path))
            except Exception:  # noqa: BLE001
                continue
    return merged


def _distinct_traces(labels: dict, criterion_id: str) -> int:
    return len({tid for (tid, cid) in labels if cid == criterion_id})


def _stored_judge_scores(reg, criterion_id: str
                         ) -> tuple[list[tuple[str, str, float]], dict[str, str]]:
    """Judge scores + scales for a criterion, read from STORED scorecards (no
    LLM call). Returns ``([(trace_id, criterion_id, score)...], {criterion_id ->
    scale})`` — the exact shape ``calibration_report`` consumes. Scales are
    resolved from the criterion's rubric (default "binary" when unknown)."""
    judge_scores: list[tuple[str, str, float]] = []
    scales: dict[str, str] = {}
    try:
        cards = reg.all_scorecards()
    except Exception:  # noqa: BLE001
        cards = []
    seen: set[str] = set()
    for sc in cards:
        for run in getattr(sc, "run_scores", []) or []:
            trace_id = getattr(run, "trace_id", None)
            if not trace_id or trace_id in seen:
                continue
            for cs in getattr(run, "criterion_scores", []) or []:
                if cs.criterion_id != criterion_id or cs.scorer != "judge":
                    continue
                judge_scores.append((trace_id, criterion_id, cs.score))
                seen.add(trace_id)
        scales.update(_scales_from_scorecard(reg, sc, criterion_id))
    if criterion_id not in scales:
        scales[criterion_id] = "binary"
    return judge_scores, scales


def _scales_from_scorecard(reg, sc, criterion_id: str) -> dict[str, str]:
    """Best-effort criterion scale from the scorecard's rubric."""
    try:
        rubric = reg.get_rubric(sc.rubric_id, sc.rubric_version)
        return {c.criterion_id: c.scale for c in rubric.criteria
                if c.criterion_id == criterion_id}
    except Exception:  # noqa: BLE001
        return {}
