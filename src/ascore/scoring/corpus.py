"""Calibration corpus runner — turns "the scorers are calibrated" from a claim
into a *demonstrated, reproducible number* (SPEC Hard Rule 6).

We ship a small, human-labeled corpus (``calibration_corpus.jsonl``) of real
agent outputs with gold human judgments for the **heuristic/proxy deterministic
checks** (refusal detection, injection robustness, secret-leak, faithfulness
gate). This module runs the *actual* check over each record and compares its
score to the human label using the existing calibration machinery
(``scoring.calibration``: exact-match / Krippendorff's alpha). The output is a
per-criterion agreement report — a genuine calibration measurement produced
offline, no API key required.

HONESTY / scope (read before quoting any number):

* The corpus is a **small SEED set hand-authored by the Agenttic team (v1)**, not
  a large third-party inter-annotator study. Treat the agreement figures as a
  first, reproducible datapoint, not a definitive calibration.
* It covers the **deterministic heuristic checks only**. The **LLM judge** is NOT
  calibrated here (that needs judge runs against these traces), so every judge
  criterion stays **PROVISIONAL/uncalibrated** — see ``uncalibrated_criteria``.
* Tail cases the lexical checks get wrong are included on purpose, so agreement
  is an honest measure of the checks' real limits, not a rigged 1.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from ascore.schema.testcase import TestCase
from ascore.schema.trace import Span, Trace
from ascore.scoring.calibration import (
    CriterionCalibration,
    calibration_report,
)
from ascore.scoring.checks import run_check

CORPUS_VERSION = "deterministic-calibration/v1"
DEFAULT_THRESHOLD = 0.8
DEFAULT_MIN_N = 5

#: Heuristic / proxy deterministic checks — ones that APPROXIMATE a human
#: judgment (lexical refusal detection, injection-block markers, groundedness
#: overlap) and therefore genuinely need calibration. Pure-deterministic checks
#: with unambiguous ground truth (was the tool called? is the JSON valid?) are
#: calibrated by construction and are not in scope for this corpus.
HEURISTIC_CRITERIA: frozenset[str] = frozenset({
    "harmful_action_refused",
    "injection_robust",
    "tool_misuse_safety",
    "no_secret_leak",
    "faithfulness_grounded",
})


def _corpus_path() -> Path:
    return Path(str(resources.files("ascore.scoring") / "calibration_corpus.jsonl"))


def load_corpus(path: str | Path | None = None) -> list[dict]:
    """Load the corpus records (skipping the ``_comment`` header line)."""
    p = Path(path) if path else _corpus_path()
    records: list[dict] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "_comment" in rec:
                continue
            records.append(rec)
    if not records:
        raise ValueError(f"no calibration records found in {p}")
    return records


def _record_trace_case(rec: dict) -> tuple[Trace, TestCase]:
    """Build a minimal (black-box) trace + test case from a corpus record so the
    real check can run over it exactly as it would in the scoring pipeline."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spans = [
        Span(span_id=f"s{i}", kind="tool_call", name=tc["name"],
             start_time=t0, end_time=t0, input=tc.get("input", {}))
        for i, tc in enumerate(rec.get("tool_calls", []))
    ]
    trace = Trace(
        trace_id=rec["case_id"], agent_id="calibration-corpus",
        agent_config_hash="calibration", test_case_id=rec["case_id"],
        visibility="black_box", final_output=rec.get("final_output", ""),
        spans=spans)
    case = TestCase(
        test_id=rec["case_id"], suite_id="calibration-corpus",
        task_description=rec.get("note", ""), expected=rec.get("expected") or {},
        rubric_id="calibration")
    return trace, case


@dataclass(frozen=True)
class CorpusCalibration:
    version: str
    n_records: int
    per_criterion: dict[str, CriterionCalibration]
    disagreements: list[dict]     # (case_id, criterion, check_score, human_score)
    threshold: float
    min_n: int

    @property
    def calibrated_criteria(self) -> set[str]:
        return {cid for cid, c in self.per_criterion.items() if c.calibrated}

    @property
    def overall_agreement(self) -> float:
        """Sample-weighted mean agreement across covered criteria."""
        total_n = sum(c.n for c in self.per_criterion.values())
        if not total_n:
            return 0.0
        return sum(c.agreement * c.n for c in self.per_criterion.values()) / total_n

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "n_records": self.n_records,
            "threshold": self.threshold,
            "min_n": self.min_n,
            "overall_agreement": round(self.overall_agreement, 4),
            "calibrated_criteria": sorted(self.calibrated_criteria),
            "per_criterion": {
                cid: {"n": c.n, "agreement": round(c.agreement, 4),
                      "calibrated": c.calibrated}
                for cid, c in self.per_criterion.items()
            },
            "disagreements": self.disagreements,
            "note": ("Seed corpus (Agenttic team, v1) demonstrating calibration "
                     "of the deterministic heuristic checks only. The LLM judge "
                     "is NOT calibrated here and remains provisional. Tail "
                     "disagreements are intentional and reflect the lexical "
                     "checks' real limits."),
        }


def run_corpus_calibration(path: str | Path | None = None, *,
                           threshold: float = DEFAULT_THRESHOLD,
                           min_n: int = DEFAULT_MIN_N) -> CorpusCalibration:
    """Run every corpus record through its actual check and measure agreement
    with the human labels. Returns a demonstrated per-criterion calibration."""
    records = load_corpus(path)
    check_scores: list[tuple[str, str, float]] = []
    labels: dict[tuple[str, str], float] = {}
    scales: dict[str, str] = {}
    disagreements: list[dict] = []

    for rec in records:
        trace, case = _record_trace_case(rec)
        cid = rec["criterion_id"]
        score = run_check(rec["check_ref"], trace, case)
        human = float(rec["human_score"])
        check_scores.append((rec["case_id"], cid, score))
        labels[(rec["case_id"], cid)] = human
        scales[cid] = rec.get("scale", "binary")
        if score != human:
            disagreements.append({
                "case_id": rec["case_id"], "criterion_id": cid,
                "check_score": score, "human_score": human,
                "note": rec.get("note", "")})

    report = calibration_report(check_scores, labels, scales,
                                threshold=threshold, min_n=min_n)
    return CorpusCalibration(
        version=CORPUS_VERSION, n_records=len(records), per_criterion=report,
        disagreements=disagreements, threshold=threshold, min_n=min_n)


# --------------------------------------------------------------------------- #
# Scoring wiring — which criteria must be shown PROVISIONAL (Hard Rule 6).
# --------------------------------------------------------------------------- #

# Cache the demonstrated set (the corpus is static and read-only).
_CALIBRATED_CACHE: set[str] | None = None


def demonstrated_calibrated(path: str | Path | None = None) -> set[str]:
    """The set of criteria the shipped corpus DEMONSTRATES are calibrated. Cached
    for the default corpus."""
    global _CALIBRATED_CACHE
    if path is None and _CALIBRATED_CACHE is not None:
        return set(_CALIBRATED_CACHE)
    try:
        result = run_corpus_calibration(path).calibrated_criteria
    except Exception:  # noqa: BLE001 — never let calibration lookup break scoring
        result = set()
    if path is None:
        _CALIBRATED_CACHE = set(result)
    return set(result)


def uncalibrated_criteria(criterion_ids, scorers: dict[str, str] | None = None,
                          *, path: str | Path | None = None) -> set[str]:
    """Given the criteria a run will score, return the subset that must be marked
    PROVISIONAL (uncalibrated), so the UI never shows an unproven score as
    trusted (Hard Rule 6):

    * every **judge** criterion (judge calibration is not demonstrated), plus
    * every **heuristic** deterministic check not demonstrated-calibrated by the
      shipped corpus.

    Pure-deterministic checks (unambiguous ground truth) are calibrated by
    construction and are never flagged."""
    scorers = scorers or {}
    calibrated = demonstrated_calibrated(path)
    out: set[str] = set()
    for cid in criterion_ids:
        if scorers.get(cid) == "judge":
            out.add(cid)
        elif cid in HEURISTIC_CRITERIA and cid not in calibrated:
            out.add(cid)
    return out
