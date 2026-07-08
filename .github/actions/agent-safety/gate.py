#!/usr/bin/env python3
"""Agenttic CI safety gate.

Runs `ascore certify` against the target agent, parses the signed dossier,
writes a Markdown summary + the GitHub status outputs, and exits non-zero when
the grade is below `fail-under` — turning a required status check into a merge
gate. No production access; the agent is exercised against published safety
suites in the CI runner only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GRADE_ORDER = {"A": 3, "B": 2, "C": 1, "NONE": 0}
WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
DOSSIER = WORKSPACE / "agenttic-dossier.json"
SUMMARY = WORKSPACE / "agenttic-summary.md"


def out(name: str, value: str) -> None:
    """Emit a GitHub Actions output."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"{name}={value}\n")


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


def summarize(dossier: dict, grade: str, threshold: str, passed: bool) -> str:
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
    grade = (dossier.get("tier_decision", {}).get("tier") or "").upper()
    if grade not in GRADE_ORDER:
        print(f"::error::Dossier has no recognizable grade (got '{grade}').")
        sys.exit(2)

    passed = threshold == "NONE" or GRADE_ORDER[grade] >= GRADE_ORDER[threshold]

    summary = summarize(dossier, grade, threshold, passed)
    SUMMARY.write_text(summary)
    # also surface in the Actions run summary
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(summary + "\n")

    out("grade", grade)
    out("passed", "true" if passed else "false")
    out("dossier_path", str(DOSSIER))

    print(f"\nGrade {grade} vs threshold {threshold} → "
          f"{'PASS' if passed else 'FAIL'}")
    if not passed:
        print(f"::error::Agent graded {grade}, below required {threshold}.")
        sys.exit(1)


if __name__ == "__main__":
    main()
