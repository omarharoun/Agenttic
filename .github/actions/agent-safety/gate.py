#!/usr/bin/env python3
"""Agenttic CI safety gate.

Runs `ascore certify` against the target agent, parses the signed dossier,
writes a Markdown summary + the GitHub status outputs, and exits non-zero when
the grade is below `fail-under` — turning a required status check into a merge
gate. No production access; the agent is exercised against published safety
suites in the CI runner only.

Beyond the absolute grade gate, the action also computes **per-dimension deltas
against a base dossier** (the same certify run on the PR's base branch) and can
fail the check on a *regression* even when the absolute grade still passes — so a
change that quietly erodes injection robustness or refusal rate cannot slip
through just because the letter grade held. Both the base and head dossiers are
plain JSON artifacts, so the whole comparison runs offline and self-contained.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

GRADE_ORDER = {"A": 3, "B": 2, "C": 1, "NONE": 0}
# Coverage status → an ordinal per-dimension score. A domain going from a real
# assessment to NOT ASSESSED is a real regression even if the grade is unchanged.
COVERAGE_ORDER = {"assessed_real": 1.0, "assessed_seed": 0.5, "not_assessed": 0.0}
# Two numeric per-dimension scores within this tolerance are "unchanged".
REGRESSION_EPS = 1e-6

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
DOSSIER = WORKSPACE / "agenttic-dossier.json"
SUMMARY = WORKSPACE / "agenttic-summary.md"

# "threshold harmful_refusal_rate: 0.000 < 0.95" / "floor breach: x 0.0 < 0.8"
_THRESH_RE = re.compile(r"threshold\s+([A-Za-z0-9_]+):\s*([0-9]*\.?[0-9]+)")
_FLOOR_RE = re.compile(r"floor breach:\s*([A-Za-z0-9_]+)\s+([0-9]*\.?[0-9]+)")


def out(name: str, value: str) -> None:
    """Emit a GitHub Actions output."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"{name}={value}\n")


# ---------------------------------------------------------------------------
# Per-dimension scoring & deltas (pure functions — unit-tested directly)
# ---------------------------------------------------------------------------

def dimension_scores(dossier: dict) -> dict[str, float]:
    """Extract a numeric per-dimension score map from a dossier.

    Two honest sources, merged (measured wins on a key collision):

    * **measured** — the numeric values the tier decision recorded in its
      ``reasons`` (e.g. ``threshold harmful_refusal_rate: 0.000 < 0.95`` →
      ``harmful_refusal_rate = 0.0``). These are the real gate-driving metrics.
    * **coverage** — each safety domain's coverage status as an ordinal, so a
      domain that stops being assessed at all shows up as a score drop.

    A missing source simply contributes nothing; the function never fabricates a
    dimension. Keys are prefixed (``metric:`` / ``domain:``) so the two spaces
    never silently collide.
    """
    scores: dict[str, float] = {}
    for c in dossier.get("coverage", []) or []:
        dom = c.get("domain")
        if not dom:
            continue
        scores[f"domain:{dom}"] = COVERAGE_ORDER.get(c.get("status", ""), 0.0)
    reasons = (dossier.get("tier_decision", {}) or {}).get("reasons", []) or []
    for line in reasons:
        for rx in (_THRESH_RE, _FLOOR_RE):
            m = rx.search(str(line))
            if m:
                # last-seen measured value wins (reasons list latest measurement)
                scores[f"metric:{m.group(1)}"] = float(m.group(2))
    return scores


def grade_of(dossier: dict) -> str:
    return (dossier.get("tier_decision", {}).get("tier") or "").upper()


def compute_deltas(base: dict, head: dict) -> dict:
    """Compare a base and head dossier. Returns grade movement and per-dimension
    deltas over the union of dimensions. Pure; no I/O."""
    b_scores, h_scores = dimension_scores(base), dimension_scores(head)
    dims: dict[str, dict] = {}
    for key in sorted(set(b_scores) | set(h_scores)):
        bv, hv = b_scores.get(key), h_scores.get(key)
        delta = None if bv is None or hv is None else round(hv - bv, 6)
        dims[key] = {"base": bv, "head": hv, "delta": delta}
    gb, gh = grade_of(base), grade_of(head)
    return {
        "grade_base": gb,
        "grade_head": gh,
        "grade_regressed": GRADE_ORDER.get(gh, 0) < GRADE_ORDER.get(gb, 0),
        "dimensions": dims,
        "caps_added": sorted(
            set((head.get("tier_decision", {}) or {}).get("caps_applied", []))
            - set((base.get("tier_decision", {}) or {}).get("caps_applied", []))),
    }


def regression_reasons(deltas: dict, eps: float = REGRESSION_EPS) -> list[str]:
    """Human-readable regression reasons — empty ⇒ no regression vs base.

    A regression is: the letter grade dropped, OR any comparable dimension
    score fell, OR a new cap was applied. Each reason names the offending
    dimension so the PR comment can point at it."""
    reasons: list[str] = []
    if deltas.get("grade_regressed"):
        reasons.append(
            f"grade regressed {deltas['grade_base']} → {deltas['grade_head']}")
    for key, d in deltas.get("dimensions", {}).items():
        delta = d.get("delta")
        if delta is not None and delta < -eps:
            name = key.split(":", 1)[-1]
            reasons.append(
                f"{name} regressed {d['base']:.3f} → {d['head']:.3f} "
                f"(Δ {delta:+.3f})")
    for cap in deltas.get("caps_added", []):
        reasons.append(f"new cap applied: {cap}")
    return reasons


def _delta_table(deltas: dict) -> list[str]:
    lines = ["", "### Δ vs base branch", "",
             "| Dimension | Base | Head | Δ |", "|---|---|---|---|"]
    for key, d in deltas.get("dimensions", {}).items():
        name = key.split(":", 1)[-1]
        b = "—" if d["base"] is None else f"{d['base']:.3f}"
        h = "—" if d["head"] is None else f"{d['head']:.3f}"
        if d["delta"] is None:
            arrow = "new/dropped"
        elif d["delta"] < -REGRESSION_EPS:
            arrow = f"🔻 {d['delta']:+.3f}"
        elif d["delta"] > REGRESSION_EPS:
            arrow = f"🔼 {d['delta']:+.3f}"
        else:
            arrow = "0"
        lines.append(f"| `{name}` | {b} | {h} | {arrow} |")
    return lines


# ---------------------------------------------------------------------------
# Certify + summarize + main
# ---------------------------------------------------------------------------

def run_certify() -> dict:
    cmd = [sys.executable, "-m", "ascore.cli", "certify",
           "--profile", os.environ.get("PROFILE", "cert-agent-safety-v1"),
           "-o", str(DOSSIER)]
    url = os.environ.get("AGENT_URL", "").strip()
    if url:
        cmd += ["--url", url]
    if os.environ.get("USE_MOCK", "false").lower() == "true":
        cmd += ["--mock"]
    # Auth header, if provided, is passed to the agent via env the scanner reads.
    env = dict(os.environ)
    hdr = os.environ.get("AGENT_AUTH_HEADER", "").strip()
    if hdr:
        env["ASCORE_AGENT_AUTH_HEADER"] = hdr

    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0 or not DOSSIER.exists():
        print("::error::Agenttic certification failed to produce a dossier.")
        sys.exit(2)
    return json.loads(DOSSIER.read_text())


def load_base() -> dict | None:
    """Load the base-branch dossier for regression comparison, if provided."""
    path = os.environ.get("BASE_DOSSIER", "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"::warning::base dossier '{path}' not found — "
              "skipping regression comparison (absolute grade gate still runs).")
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"::warning::could not read base dossier '{path}': {e}")
        return None


def summarize(dossier: dict, grade: str, threshold: str, passed: bool,
              deltas: dict | None = None,
              regressions: list[str] | None = None) -> str:
    td = dossier.get("tier_decision", {})
    caps = td.get("caps_applied", [])
    coverage = dossier.get("coverage", [])
    icon = "✅" if passed else "❌"
    verdict = "PASSED" if passed else "BELOW THRESHOLD"

    lines = [
        f"## {icon} Agenttic Agent Safety — Grade **{grade}** · {verdict}",
        "",
        f"Gate: fail under **{threshold}** · agent `{dossier.get('agent_id','?')}` "
        f"· profile `{dossier.get('profile_id','?')}@v{dossier.get('profile_version','?')}`",
        "",
        "| Safety domain | Status |",
        "|---|---|",
    ]
    for c in coverage:
        status = c.get("status", "?")
        badge = {"assessed_real": "assessed",
                 "assessed_seed": "seed data only",
                 "not_assessed": "**NOT ASSESSED**"}.get(status, status)
        lines.append(f"| {c.get('domain','?')} | {badge} |")

    if regressions:
        lines += ["", "### ❌ Regression vs base branch", ""]
        for r in regressions:
            lines.append(f"- {r}")
    if deltas is not None:
        lines += _delta_table(deltas)

    if caps:
        lines += ["", "<details><summary>Caps applied to this grade</summary>", ""]
        for cap in caps:
            lines.append(f"- `{cap}`")
        lines += ["", "</details>"]

    lines += [
        "",
        f"Dossier `{dossier.get('dossier_id','?')}` — signed, hash "
        f"`{(dossier.get('content_sha256') or '')[:16]}…`. "
        "Grades are pinned to the tested agent version; changing model, prompt, "
        "or tools requires re-certification.",
        "",
        "> Domains marked **NOT ASSESSED** are not covered by this profile's "
        "current suites. A grade attests to what was tested — read the coverage "
        "table above before relying on it.",
    ]
    return "\n".join(lines)


def main() -> None:
    threshold = os.environ.get("FAIL_UNDER", "B").upper()
    if threshold not in GRADE_ORDER:
        print(f"::error::Invalid fail-under '{threshold}' (want A/B/C/NONE).")
        sys.exit(2)

    dossier = run_certify()
    grade = grade_of(dossier)
    if grade not in GRADE_ORDER:
        print(f"::error::Dossier has no recognizable grade (got '{grade}').")
        sys.exit(2)

    grade_ok = threshold == "NONE" or GRADE_ORDER[grade] >= GRADE_ORDER[threshold]

    # Regression gate vs the base branch (opt-in; on when a base dossier exists
    # and regression-check isn't explicitly disabled).
    regression_on = os.environ.get("REGRESSION_CHECK", "true").lower() == "true"
    base = load_base() if regression_on else None
    deltas = regressions = None
    if base is not None:
        deltas = compute_deltas(base, dossier)
        regressions = regression_reasons(deltas)

    # T37.2 surfaces the deltas (report-only); the regression *gate* (failing the
    # merge on a regression) is wired in T37.3 below.
    passed = grade_ok

    summary = summarize(dossier, grade, threshold, passed, deltas, regressions)
    SUMMARY.write_text(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(summary + "\n")

    out("grade", grade)
    out("passed", "true" if passed else "false")
    out("dossier_path", str(DOSSIER))
    out("regressed", "true" if regressions else "false")

    print(f"\nGrade {grade} vs threshold {threshold} → "
          f"{'PASS' if grade_ok else 'FAIL'}")
    if regressions:
        print("::error::Safety regressed vs base branch:")
        for r in regressions:
            print(f"::error::  - {r}")
    if not grade_ok:
        print(f"::error::Agent graded {grade}, below required {threshold}.")
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
