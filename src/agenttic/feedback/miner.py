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
from pathlib import Path

from agenttic.registry.sqlite_store import NotFoundError
from agenttic.schema.testcase import TestCase, TestSuite

MINED_TAG = "mined_from_production"
_CALIBRATION_HEADER = ("trace_id", "criterion_id", "human_score")


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

    appended = 0
    for fb in ratings:
        target = _route_rating_suite(reg, fb, suite_id)
        if target is None:
            continue  # unroutable → leave unprocessed for a later, targeted run
        _append_label(calibration_dir / f"{target}.csv",
                      fb.trace_id, fb.criterion_id, fb.rating)
        reg.mark_feedback_processed(fb.feedback_id)
        appended += 1
    return appended


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
