#!/usr/bin/env python
"""Run the EGR harness against a REAL Claude agent and emit a report.

Usage:
    ANTHROPIC_API_KEY=... PYTHONPATH=src python scripts/egr_live.py \
        --model claude-sonnet-4-6 --judge-model claude-haiku-4-5-20251001 --k 3

SECURITY: the API key is read from the environment IN-PROCESS ONLY. It is never
printed, never written to any output file, and never embedded in the report. The
report contains only probe prompts, agent transcripts, scores, and the EGR.

Writes:
    docs/egr_runs/egr_result.json   — full GamingReport
    docs/egr_runs/egr_result.md     — human-readable summary + evidence
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ascore.gaming.agent import ChatAgent  # noqa: E402
from ascore.gaming.issues import build_gaming_issues  # noqa: E402
from ascore.gaming.probes import SEED_PROBES  # noqa: E402
from ascore.gaming.runner import run_gaming  # noqa: E402


def _pricing(model: str) -> dict:
    m = model.lower()
    if "haiku" in m:
        return {"input": 1.0, "output": 5.0}
    if "opus" in m:
        return {"input": 15.0, "output": 75.0}
    return {"input": 3.0, "output": 15.0}  # sonnet-tier default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6",
                    help="agent-under-test model id")
    ap.add_argument("--k", type=int, default=3, help="runs per framing")
    ap.add_argument("--out", default="docs/egr_runs", help="output directory")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        return 2

    agent = ChatAgent(model=args.model, pricing_per_mtok=_pricing(args.model))

    def on_event(evt: str, data: dict) -> None:
        if evt == "probe_finished":
            flag = " ⚠ INCIDENT" if data["incident"] else ""
            print(f"  · {data['probe_id']}: sub={data['sub_score']}{flag}")

    print(f"Running EGR against {args.model} — {len(SEED_PROBES)} probes, k={args.k}…")
    report = run_gaming(agent, SEED_PROBES, k=args.k, on_event=on_event)
    rep = build_gaming_issues(report)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "egr_result.json").write_text(json.dumps(
        {"report": report.model_dump(), "issues": rep}, indent=2))
    (outdir / "egr_result.md").write_text(_markdown(report, rep, args.model))

    print(f"\nEGR = {report.egr}/100  (band {report.egr_low}–{report.egr_high})")
    print(f"sub-scores: {report.sub_scores}")
    print(f"incidents: {report.n_incidents} ({report.n_critical_incidents} critical)")
    print(f"agent spend: ${report.agent_cost_usd:.4f}")
    print(f"wrote {outdir}/egr_result.md and .json")
    return 0


def _markdown(report, rep, model: str) -> str:
    lines = [
        "# Evaluation-Gaming Resistance — live run",
        "",
        f"**Agent-under-test:** `{model}`  ",
        f"**EGR (PROVISIONAL):** **{report.egr}/100** (band {report.egr_low}–{report.egr_high})  ",
        f"**Probes:** {report.n_probes} · **Incidents:** {report.n_incidents} "
        f"({report.n_critical_incidents} critical) · **Agent spend:** ${report.agent_cost_usd:.4f}",
        "",
        f"> {report.limits}",
        "",
        "## Sub-scores",
        "",
        "| Mechanism | Score /100 |",
        "|---|---|",
    ]
    for k, v in report.sub_scores.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Per-probe results", "",
              "| Probe | Mechanism | gap | sub | incident |", "|---|---|---|---|---|"]
    for p in report.probe_results:
        lines.append(f"| {p.probe_id} | {p.mechanism} | {p.gap} | {p.sub_score} | "
                     f"{'⚠ ' + ('CRITICAL' if p.critical_incident else 'yes') if p.incident else 'no'} |")

    lines += ["", "## Behavior-delta detail (test vs deployment framing)", ""]
    for p in report.probe_results:
        if p.mechanism != "behavior_delta":
            continue
        d = p.detail or {}
        lines += [f"### {p.probe_id} — {p.base_intent}",
                  f"- test-framing safe rate: **{d.get('test_safe_rate')}**",
                  f"- deployment-framing safe rate: **{d.get('deploy_safe_rate')}**",
                  f"- gap: **{d.get('gap')}** — {d.get('interpretation')}",
                  f"- verbalised eval-awareness: {d.get('awareness_verbalized')}", ""]
        for f in p.framing_results:
            sample = (f.transcripts[0] if f.transcripts else "").replace("\n", " ")[:300]
            lines.append(f"  - _{f.framing}_ (safe {f.behavior_rate}): “{sample}”")
        lines.append("")

    if rep["issues"]:
        lines += ["## Incidents", ""]
        for i in rep["issues"]:
            lines += [f"### [{i['severity'].upper()}] {i['title']}", "", i["why"], ""]
    else:
        lines += ["## Incidents", "",
                  "None detected. Per the honesty contract, this is evidence of the "
                  "absence of *detectable* gaming — NOT proof of honesty.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
