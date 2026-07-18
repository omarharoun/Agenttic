"""The "moat" surface — read/write endpoints that expose the SPEC-2/SPEC-3
self-improvement evidence to the console (SPEC-4 Step 20).

Everything here is a thin, honest projection of already-persisted state:

* **Agent-config lineage** (SPEC-2 Step 14): the promotion ledger
  (``agent_config_lineage`` / ``get_agent_config``) rendered as a family tree
  with each node's FULL gate receipt VERBATIM — the promote/reject reason, the
  per-criterion deltas, the epsilon, and the cost/latency verdicts exactly as
  the optimizer recorded them (``AgentConfig.reason`` / ``diff_summary`` /
  ``payload``). Rejected siblings are included: the ledger is a search history,
  not a winners' list.
* **Judge-config lineage** (SPEC-3): ``judge_lineage`` v1→vN, with the
  before/after train+holdout agreement parsed out of each config's ``changelog``
  round record.
* **Calibration** (SPEC-3): per-criterion agreement / label_count / status
  (calibrated | PROVISIONAL | insufficient_labels) plus the open
  judge-optimization requests, a next-unlabeled trace for the labeling
  workspace, and a label-append endpoint that writes to THE SAME CSV store the
  CLI ``calibrate`` reads (``{calibration_dir}/{suite_id}.csv``).
* **Escalations** (SPEC-2 Step 12): pending (unresolved) escalation traces with
  the question + trace context + the autonomy policy that triggered them, plus
  the resolved history (escalation-decision ``HumanFeedback``). ``respond``
  persists the decision EXACTLY as the Step-12 harness ``HumanChannel`` path
  does — ``HumanFeedback(source="escalation", kind="escalation_decision")`` —
  so a console resolution and a programmatic one leave identical registry state.

Authed + tenant-scoped (mounted under the auth-protected router group), so every
read/write is confined to the caller's own tenant via ``request.state.reg``.
"""

from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from agenttic.registry.sqlite_store import NotFoundError, TraceRow
from agenttic.scoring.calibration import (
    calibration_report,
    load_labels,
    min_labels,
)
from agenttic.server.auth import require_operator

router = APIRouter(tags=["moat"])

RATING_SCALE = (0.0, 0.5, 1.0)


# --------------------------------------------------------------------------- #
# 1. Agent-config lineage — the promotion ledger family tree + gate receipts.
# --------------------------------------------------------------------------- #


def _scorecard_success_rate(reg, scorecard_ids: list[str]) -> float | None:
    """The task_success_rate of the (last) scorecard a config was gated on, or
    None when none is retrievable — never a fabricated number."""
    rate: float | None = None
    for sid in scorecard_ids or []:
        try:
            sc = reg.get_scorecard(sid)
        except NotFoundError:
            continue
        rate = sc.task_success_rate
    return rate


def _gate_receipt(cfg) -> dict:
    """The FULL gate receipt for one ledger node, verbatim. The optimizer records
    its promote/reject verdict in ``reason`` (the compare_scorecards reason string
    — per-criterion deltas, epsilon-drop / cost / latency vetoes) and the human
    changelog in ``diff_summary``; ``payload`` carries the config + any structured
    gate detail. We surface all three unmodified so the receipt is auditable."""
    return {
        "reason": cfg.reason or "",
        "diff_summary": cfg.diff_summary or "",
        "payload": cfg.payload or {},
    }


@router.get("/lineage/agents/{agent_id}")
def agent_lineage(agent_id: str, request: Request):
    """The agent-config family tree for ``agent_id`` (SPEC-2 Step 14).

    ``nodes`` are every ledger entry — promoted, rejected, or pending — each with
    its hash, parent_hash, status, created_at, task_success_rate (when a scorecard
    is retrievable), and its FULL gate receipt verbatim. ``edges`` are
    parent_hash→hash links. Rejected siblings are included (the ledger is a
    search history). Empty tree (no error) for an agent that never learned."""
    reg = request.state.reg
    configs = reg.agent_config_lineage(agent_id)
    nodes = []
    edges = []
    for c in configs:
        nodes.append({
            "hash": c.agent_config_hash,
            "parent_hash": c.parent_hash or None,
            "status": c.status,
            "created_at": c.created_at.isoformat(),
            "diff_summary": c.diff_summary or "",
            "scorecard_ids": list(c.scorecard_ids or []),
            "approved_by": c.approved_by or None,
            "task_success_rate": _scorecard_success_rate(
                reg, list(c.scorecard_ids or [])),
            "gate_receipt": _gate_receipt(c),
        })
        if c.parent_hash:
            edges.append({"from": c.parent_hash, "to": c.agent_config_hash})
    return {"agent_id": agent_id, "nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# 2. Judge-config lineage — v1→vN, agreement before/after on train + holdout.
# --------------------------------------------------------------------------- #


def _round_record(changelog: str) -> dict | None:
    """Parse the ``round={...}`` tag the judge optimizer folds into a config's
    changelog (train/holdout before/after agreement). Returns None when the
    changelog carries no round record (e.g. the seed v1 config)."""
    if not changelog:
        return None
    marker = "round="
    idx = changelog.rfind(marker)
    if idx < 0:
        return None
    try:
        rec = json.loads(changelog[idx + len(marker):])
    except (ValueError, TypeError):
        return None
    train = rec.get("train") or [None, None]
    holdout = rec.get("holdout") or [None, None]
    return {
        "round": rec.get("round"),
        "promoted": rec.get("promoted"),
        "reason": rec.get("reason", ""),
        "train_before": train[0] if len(train) > 0 else None,
        "train_after": train[1] if len(train) > 1 else None,
        "holdout_before": holdout[0] if len(holdout) > 0 else None,
        "holdout_after": holdout[1] if len(holdout) > 1 else None,
        "n_holdout_scored": rec.get("n_holdout_scored", 0),
    }


@router.get("/lineage/judges/{criterion_id}")
def judge_lineage(criterion_id: str, request: Request):
    """The judge-config lineage for a criterion (SPEC-3): every JudgeConfig
    v1→vN with status, parent_id, changelog, and the parsed before/after
    agreement on BOTH the train and held-out splits. ``active`` names the single
    live version (or null)."""
    reg = request.state.reg
    lineage = reg.judge_lineage(criterion_id)
    active = reg.active_judge_config(criterion_id)
    nodes = []
    for jc in lineage:
        nodes.append({
            "judge_config_id": jc.judge_config_id,
            "version": jc.version,
            "parent_id": jc.parent_id,
            "status": jc.status,
            "changelog": jc.changelog or "",
            "created_at": jc.created_at.isoformat(),
            "round_record": _round_record(jc.changelog or ""),
        })
    return {
        "criterion_id": criterion_id,
        "active_version": active.version if active else None,
        "nodes": nodes,
    }


# --------------------------------------------------------------------------- #
# 3. Calibration — per-criterion status + labeling workspace + label writes.
# --------------------------------------------------------------------------- #


def _labels_path(cfg: dict, suite_id: str) -> Path:
    cal_dir = Path((cfg.get("paths") or {}).get("calibration_dir", "calibration"))
    return cal_dir / f"{suite_id}.csv"


def _load_labels_safe(path: Path) -> dict:
    """load_labels, but an empty/missing file is an empty label set (not an
    error) — the labeling workspace starts from zero."""
    if not path.exists():
        return {}
    try:
        return load_labels(path)
    except ValueError:
        return {}


def _suite_scales(reg, suite_id: str) -> dict[str, str]:
    """criterion_id -> scale for every rubric used by the suite."""
    scales: dict[str, str] = {}
    try:
        _suite, cases = reg.get_suite(suite_id)
    except NotFoundError:
        return scales
    for rid in {c.rubric_id for c in cases}:
        try:
            rubric = reg.get_rubric(rid)
        except NotFoundError:
            continue
        for crit in rubric.criteria:
            scales[crit.criterion_id] = crit.scale
    return scales


def _judge_scores_for_suite(reg, suite_id: str) -> list[tuple[str, str, float]]:
    """Every recorded JUDGE criterion score for a suite (trace_id, criterion_id,
    score) — the same collection the CLI ``calibrate`` builds."""
    collected: list[tuple[str, str, float]] = []
    with Session(reg.engine) as s:
        from agenttic.registry.sqlite_store import ScorecardRow
        rows = s.exec(select(ScorecardRow).where(
            ScorecardRow.tenant_id == reg.tenant,
            ScorecardRow.suite_id == suite_id)).all()
    from agenttic.schema.scorecard import Scorecard
    for row in rows:
        sc = Scorecard.model_validate_json(row.payload)
        for r in sc.run_scores:
            for cs in r.criterion_scores:
                if cs.scorer == "judge":
                    collected.append((r.trace_id, cs.criterion_id, cs.score))
    return collected


def _calibration_rows_for_suite(reg, cfg: dict, suite_id: str) -> list[dict]:
    """Per-criterion calibration rows for one suite: agreement, label_count,
    threshold, and status (calibrated | PROVISIONAL | insufficient_labels)."""
    threshold = float((cfg.get("scoring") or {}).get(
        "calibration_threshold", 0.8))
    need = min_labels(cfg)
    labels = _load_labels_safe(_labels_path(cfg, suite_id))
    scales = _suite_scales(reg, suite_id)
    judge_scores = _judge_scores_for_suite(reg, suite_id)
    report = calibration_report(judge_scores, labels, scales, threshold=threshold)
    # total labels per criterion (independent of how many judge scores pair up)
    label_totals: dict[str, int] = {}
    for (_tid, cid) in labels:
        label_totals[cid] = label_totals.get(cid, 0) + 1

    rows = []
    seen: set[str] = set()
    for cid, cal in report.items():
        seen.add(cid)
        n_labels = label_totals.get(cid, cal.n)
        if n_labels < need:
            status = "insufficient_labels"
        elif cal.calibrated:
            status = "calibrated"
        else:
            status = "PROVISIONAL"
        rows.append({
            "criterion_id": cid,
            "suite_id": suite_id,
            "agreement": cal.agreement,
            "label_count": n_labels,
            "paired_count": cal.n,
            "threshold": threshold,
            "min_labels": need,
            "status": status,
        })
    # criteria that have labels but no paired judge score yet
    for cid, n_labels in label_totals.items():
        if cid in seen:
            continue
        rows.append({
            "criterion_id": cid,
            "suite_id": suite_id,
            "agreement": None,
            "label_count": n_labels,
            "paired_count": 0,
            "threshold": threshold,
            "min_labels": need,
            "status": "insufficient_labels"
            if n_labels < need else "PROVISIONAL",
        })
    return sorted(rows, key=lambda r: r["criterion_id"])


@router.get("/calibration")
def calibration(request: Request, suite_id: str | None = None):
    """Per-criterion calibration status across the tenant's suites (or one
    ``suite_id``), plus the open judge-optimization requests (SPEC-3 Step 15.4).

    Each criterion row carries agreement, label_count, threshold and a status of
    ``calibrated`` | ``PROVISIONAL`` (enough labels but below threshold) |
    ``insufficient_labels`` (fewer than ``judge_learning.min_labels``)."""
    reg = request.state.reg
    cfg = request.state.cfg
    if suite_id is not None:
        suite_ids = [suite_id]
    else:
        suite_ids = [s["suite_id"] for s in reg.list_suites()]
    rows: list[dict] = []
    for sid in suite_ids:
        rows.extend(_calibration_rows_for_suite(reg, cfg, sid))
    open_requests = [{
        "request_id": r.request_id,
        "criterion_id": r.criterion_id,
        "suite_id": r.suite_id,
        "reason": r.reason,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    } for r in reg.open_judge_optimization_requests()]
    return {"criteria": rows, "open_requests": open_requests}


def _suite_id_for_trace(reg, trace) -> str | None:
    """Resolve the suite a trace's test case belongs to (labels are keyed by
    suite). Scans the tenant's suites for the test_case_id — small N, exact."""
    if not trace.test_case_id:
        return None
    for s in reg.list_suites():
        try:
            _suite, cases = reg.get_suite(s["suite_id"])
        except NotFoundError:
            continue
        if any(c.test_id == trace.test_case_id for c in cases):
            return s["suite_id"]
    return None


@router.get("/calibration/{criterion_id}/next-unlabeled")
def next_unlabeled(criterion_id: str, request: Request):
    """A single trace awaiting a human label for ``criterion_id`` — the unit of
    work for the labeling workspace. Returns the trace, the criterion, and the
    label anchors (the {0, 0.5, 1} scale + its descriptor). ``exhausted`` is true
    when every trace scored on this criterion already has a human label."""
    reg = request.state.reg
    # criterion definition + scale (search the tenant's rubrics)
    criterion = None
    for rid_row in _all_rubric_ids(reg):
        try:
            rubric = reg.get_rubric(rid_row)
        except NotFoundError:
            continue
        for crit in rubric.criteria:
            if crit.criterion_id == criterion_id:
                criterion = crit
                break
        if criterion is not None:
            break
    if criterion is None:
        raise HTTPException(404, f"criterion {criterion_id} not found")

    anchors = [
        {"score": 0.0, "label": "fails the criterion"},
        {"score": 0.5, "label": "partially meets the criterion"},
        {"score": 1.0, "label": "fully meets the criterion"},
    ]
    if criterion.scale == "binary":
        anchors = [a for a in anchors if a["score"] in (0.0, 1.0)]

    # every trace with a judge score on this criterion, minus already-labeled.
    with Session(reg.engine) as s:
        trace_rows = s.exec(select(TraceRow).where(
            TraceRow.tenant_id == reg.tenant,
            TraceRow.mode == "batch").order_by(TraceRow.id)).all()
    for row in trace_rows:
        trace = reg.get_trace(row.trace_id)
        suite_id = _suite_id_for_trace(reg, trace)
        if suite_id is None:
            continue
        labels = _load_labels_safe(_labels_path(request.state.cfg, suite_id))
        if (trace.trace_id, criterion_id) in labels:
            continue
        return {
            "exhausted": False,
            "criterion": {
                "criterion_id": criterion.criterion_id,
                "description": getattr(criterion, "description", ""),
                "scale": criterion.scale,
            },
            "trace": trace.model_dump(mode="json"),
            "suite_id": suite_id,
            "anchors": anchors,
        }
    return {"exhausted": True, "criterion": {
        "criterion_id": criterion.criterion_id,
        "description": getattr(criterion, "description", ""),
        "scale": criterion.scale}, "trace": None, "suite_id": None,
        "anchors": anchors}


def _all_rubric_ids(reg) -> list[str]:
    ids: set[str] = set()
    for s in reg.list_suites():
        try:
            _suite, cases = reg.get_suite(s["suite_id"])
        except NotFoundError:
            continue
        for c in cases:
            ids.add(c.rubric_id)
    return sorted(ids)


class LabelRequest(BaseModel):
    trace_id: str
    criterion_id: str
    score: float


@router.post("/calibration/labels", dependencies=[Depends(require_operator)])
def add_label(body: LabelRequest, request: Request):
    """Append a human label to the calibration set — the SAME CSV store the CLI
    ``calibrate`` reads (``{calibration_dir}/{suite_id}.csv``, columns
    ``trace_id,criterion_id,human_score``). ``score`` must sit on the shared
    {0, 0.5, 1} judge scale (Hard Rule 3). Returns the criterion's updated
    calibration row so the workspace can advance."""
    if body.score not in RATING_SCALE:
        raise HTTPException(
            422, f"score {body.score} is off the {RATING_SCALE} scale — human "
                 "labels obey the same three-point scale as judges (Hard Rule 3)")
    reg = request.state.reg
    cfg = request.state.cfg
    try:
        trace = reg.get_trace(body.trace_id)
    except NotFoundError:
        raise HTTPException(404, f"trace {body.trace_id} not found")
    suite_id = _suite_id_for_trace(reg, trace)
    if suite_id is None:
        raise HTTPException(
            422, f"trace {body.trace_id} has no test case in any suite — "
                 "cannot attach a calibration label")

    path = _labels_path(cfg, suite_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_labels_safe(path)
    existing[(body.trace_id, body.criterion_id)] = body.score
    # rewrite the CSV (idempotent per (trace, criterion): a re-label overwrites).
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trace_id", "criterion_id", "human_score"])
        for (tid, cid), score in sorted(existing.items()):
            w.writerow([tid, cid, score])

    rows = _calibration_rows_for_suite(reg, cfg, suite_id)
    row = next((r for r in rows if r["criterion_id"] == body.criterion_id), None)
    return {
        "ok": True,
        "suite_id": suite_id,
        "labels_path": str(path),
        "label_count": len(existing),
        "criterion": row,
    }


# --------------------------------------------------------------------------- #
# 4/5. Escalations — pending (unresolved) + resolved history + respond + count.
# --------------------------------------------------------------------------- #


def _escalation_span(trace):
    """The ``kind='escalation'`` span carrying the question + context (incl. the
    autonomy policy that triggered it), or None."""
    for span in trace.spans:
        if span.kind == "escalation":
            return span
    return None


def _pending_escalations(reg) -> list[dict]:
    """Unresolved escalation traces — persisted with
    ``final_output == 'ESCALATED_UNRESOLVED'`` (Step 12, no human channel)."""
    with Session(reg.engine) as s:
        rows = s.exec(select(TraceRow).where(
            TraceRow.tenant_id == reg.tenant).order_by(TraceRow.id)).all()
    pending = []
    for row in rows:
        trace = reg.get_trace(row.trace_id)
        if not (trace.escalated
                and str(trace.final_output) == "ESCALATED_UNRESOLVED"):
            continue
        # already resolved by a decision on this trace? then it's not pending.
        resolved = any(f.kind == "escalation_decision"
                       for f in reg.feedback_for_trace(trace.trace_id))
        if resolved:
            continue
        span = _escalation_span(trace)
        question = ""
        context: dict = {}
        autonomy_policy: dict = {}
        if span is not None:
            question = str(span.input.get("question", ""))
            context = dict(span.input.get("context", {}) or {})
            # the autonomy policy that triggered the halt lives in the span
            # context (tool + tool_input + policy) — surface it verbatim.
            autonomy_policy = {
                "tool": context.get("tool"),
                "tool_input": context.get("tool_input"),
                "policy": context.get("policy"),
            }
        pending.append({
            "trace_id": trace.trace_id,
            "agent_id": trace.agent_id,
            "test_case_id": trace.test_case_id,
            "question": question,
            "context": context,
            "autonomy_policy": autonomy_policy,
        })
    return pending


def _resolved_escalations(reg) -> list[dict]:
    """The resolved history — every escalation-decision HumanFeedback, newest
    last (append-only order)."""
    out = []
    with Session(reg.engine) as s:
        from agenttic.registry.sqlite_store import FeedbackRow
        rows = s.exec(select(FeedbackRow).where(
            FeedbackRow.tenant_id == reg.tenant).order_by(
            FeedbackRow.created_at)).all()
    from agenttic.schema.feedback import HumanFeedback
    for row in rows:
        fb = HumanFeedback.model_validate_json(row.payload)
        if fb.source != "escalation" or fb.kind != "escalation_decision":
            continue
        out.append({
            "feedback_id": fb.feedback_id,
            "trace_id": fb.trace_id,
            "agent_id": fb.agent_id,
            "response": fb.rationale,
            "created_at": fb.created_at.isoformat(),
        })
    return out


@router.get("/escalations")
def escalations(request: Request):
    """Pending (unresolved) escalations + the resolved history, plus
    ``pending_count`` for the top-bar badge (SPEC-2 Step 12)."""
    reg = request.state.reg
    pending = _pending_escalations(reg)
    return {
        "pending": pending,
        "pending_count": len(pending),
        "resolved": _resolved_escalations(reg),
    }


class EscalationResponse(BaseModel):
    response: str


@router.post("/escalations/{trace_id}/respond",
             dependencies=[Depends(require_operator)])
def respond_escalation(trace_id: str, body: EscalationResponse, request: Request):
    """Resolve a pending escalation by persisting the human's decision EXACTLY as
    the Step-12 harness ``HumanChannel`` path does — an append-only
    ``HumanFeedback(source='escalation', kind='escalation_decision',
    rationale=<response>)`` (see ``harness.runner._persist_escalation_feedback``).
    A console resolution and a programmatic one therefore leave identical
    registry state (SPEC-4 20.3 acceptance). Returns the resolved item."""
    reg = request.state.reg
    try:
        trace = reg.get_trace(trace_id)
    except NotFoundError:
        raise HTTPException(404, f"trace {trace_id} not found")

    from agenttic.schema.feedback import HumanFeedback
    feedback = HumanFeedback(
        feedback_id=uuid.uuid4().hex,
        trace_id=trace_id,
        agent_id=trace.agent_id,
        source="escalation",
        kind="escalation_decision",
        rationale=body.response,
        created_at=datetime.now(timezone.utc),
    )
    reg.save_feedback(feedback)
    return {
        "resolved": {
            "feedback_id": feedback.feedback_id,
            "trace_id": trace_id,
            "agent_id": trace.agent_id,
            "response": feedback.rationale,
            "created_at": feedback.created_at.isoformat(),
        },
        "pending_count": len(_pending_escalations(reg)),
    }
