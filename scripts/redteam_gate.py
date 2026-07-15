#!/usr/bin/env python3
"""Self-red-team injection regression gate (build-time arms-race hook).

The injection patch (`metrics/injection_detect.py`) + its adversarial regression
tests shipped, but nothing FAILED THE BUILD when a red-team attack that *should*
be caught starts scoring robust again. This gate closes that loop: it runs the
shipped, labeled red-team response corpus through the REAL `injection_robust`
check and exits NON-ZERO on a regression, so a merge that reopens a known bypass
(or over-corrects into a false positive) is blocked in CI.

What counts as a regression (either direction):

  · FALSE-NEGATIVE regression — a case the detector MUST catch (ground-truth
    `hijacked: true`, and NOT a published honest tail) scores ROBUST. This is the
    arms-race guard: a reopened bypass fails the build.

  · FALSE-POSITIVE regression — a benign case (`hijacked: false`) scores
    COMPROMISED. This guards against over-correction: hardening the detector must
    not start flagging safe agents.

Published honest tails are EXEMPT from the must-catch set. `metrics/redteam.py`
is deliberately honest that the lexical detector misses non-English compliance
and unknown tool families; those corpus records carry a note beginning
``HONEST EVADER`` and are allowed to score robust WITHOUT failing the build. If
one of them later starts getting caught, the gate simply notes the improvement.
The exemption is self-documenting IN THE CORPUS (the ``HONEST EVADER`` note), not
a hard-coded id list here — so adding/removing a documented tail needs no gate
edit, and any *undocumented* hijack that regresses still fails.

Deterministic and offline: no LLM, no network. Same corpus + same detector ->
same verdict. Runnable in CI (`python scripts/redteam_gate.py`), locally
(`make redteam-gate` / `uv run python scripts/redteam_gate.py`), and importable
by the gate's own self-test (`run_gate`).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Marker (in a corpus record's ``note``) that designates a documented,
# published detector blind spot — exempt from the must-catch set.
PUBLISHED_TAIL_MARKER = "HONEST EVADER"


def _is_published_tail(rec: dict) -> bool:
    return PUBLISHED_TAIL_MARKER in (rec.get("note") or "")


def _robust(rec: dict) -> bool:
    """Run the REAL registered ``injection_robust`` check on one corpus record.

    Returns True if the detector scored the response robust (>0), i.e. NOT
    compromised. Deterministic: a fixed timestamp is used for the synthetic spans
    so the verdict never depends on wall-clock."""
    # Imported lazily so `--help` and unit collection don't require the package
    # to be importable before argument parsing.
    from agenttic.schema.testcase import TestCase
    from agenttic.schema.trace import Span, Trace
    from agenttic.scoring.checks import run_check

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spans = [
        Span(span_id=f"s{i}", kind="tool_call", name=tc["name"],
             start_time=t0, end_time=t0, input=tc.get("input", {}))
        for i, tc in enumerate(rec.get("tool_calls", []))
    ]
    trace = Trace(trace_id=rec.get("response_id", "r"), agent_id="redteam-gate",
                  agent_config_hash="redteam-gate",
                  test_case_id=rec.get("response_id", "r"),
                  visibility="black_box",
                  final_output=rec.get("final_output", ""), spans=spans)
    case = TestCase(test_id=rec.get("response_id", "r"), suite_id="redteam-gate",
                    task_description=rec.get("note", ""),
                    expected={"injection_target": rec["injection_target"]},
                    rubric_id="redteam-gate")
    return run_check("injection_robust", trace, case) >= 1.0


@dataclass
class GateResult:
    n: int = 0
    n_must_catch: int = 0
    n_benign: int = 0
    n_published_tails: int = 0
    false_negative_regressions: list[dict] = field(default_factory=list)
    false_positive_regressions: list[dict] = field(default_factory=list)
    published_tails_now_caught: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.false_negative_regressions
                    or self.false_positive_regressions)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "n_must_catch": self.n_must_catch,
            "n_benign": self.n_benign,
            "n_published_tails": self.n_published_tails,
            "false_negative_regressions": self.false_negative_regressions,
            "false_positive_regressions": self.false_positive_regressions,
            "published_tails_now_caught": self.published_tails_now_caught,
            "ok": self.ok,
        }


def run_gate(records: list[dict]) -> GateResult:
    """Score every corpus record through the real detector and collect regressions.

    Pure over its input (given the same detector): takes the parsed corpus, returns
    a structured verdict. Used by both ``main`` and the gate's self-test."""
    res = GateResult(n=len(records))
    for rec in records:
        hijacked = bool(rec.get("hijacked"))
        rid = rec.get("response_id", "?")
        robust = _robust(rec)
        if hijacked and _is_published_tail(rec):
            res.n_published_tails += 1
            if not robust:
                res.published_tails_now_caught.append(rid)
            continue
        if hijacked:
            res.n_must_catch += 1
            if robust:  # MUST be caught but scored robust -> reopened bypass
                res.false_negative_regressions.append(
                    {"response_id": rid, "technique": rec.get("technique"),
                     "injection_target": rec.get("injection_target"),
                     "note": rec.get("note", "")})
        else:
            res.n_benign += 1
            if not robust:  # benign but scored compromised -> over-correction
                res.false_positive_regressions.append(
                    {"response_id": rid, "technique": rec.get("technique"),
                     "note": rec.get("note", "")})
    return res


def _load_corpus(path: str | None) -> list[dict]:
    from agenttic.metrics.redteam import load_response_corpus
    return load_response_corpus(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=None,
                    help="Path to a red-team response corpus JSONL "
                         "(default: the shipped redteam_injection_responses.jsonl).")
    ap.add_argument("--json", action="store_true",
                    help="Emit the machine-readable verdict as JSON.")
    args = ap.parse_args(argv)

    records = _load_corpus(args.corpus)
    res = run_gate(records)

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print("self-red-team injection regression gate")
        print(f"  corpus records      : {res.n}")
        print(f"  must-catch cases    : {res.n_must_catch}")
        print(f"  benign controls     : {res.n_benign}")
        print(f"  published tails      : {res.n_published_tails} (exempt)")
        if res.published_tails_now_caught:
            print(f"  tails now caught     : {res.published_tails_now_caught} "
                  "(improvement — consider removing the HONEST EVADER note)")
        if res.false_negative_regressions:
            print("\n  ✗ FALSE-NEGATIVE regressions (a should-be-caught attack "
                  "scored ROBUST — reopened bypass):")
            for f in res.false_negative_regressions:
                print(f"      - {f['response_id']} [{f['technique']}] "
                      f"target={f['injection_target']!r}")
        if res.false_positive_regressions:
            print("\n  ✗ FALSE-POSITIVE regressions (a benign response scored "
                  "COMPROMISED — over-correction):")
            for f in res.false_positive_regressions:
                print(f"      - {f['response_id']} [{f['technique']}]")
        if res.ok:
            print("\n  ✓ PASS — no injection-detector regressions.")
        else:
            print("\n  ✗ FAIL — injection-detector regression(s) above. "
                  "Build blocked.")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
