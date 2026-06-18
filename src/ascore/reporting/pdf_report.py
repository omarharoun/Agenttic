"""Render a Scorecard to a polished, on-brand PDF (the client deliverable).

Uses fpdf2 — pure-Python, no native dependencies (so it fits the slim VM, unlike
WeasyPrint's cairo/pango stack or headless Chrome). The PDF mirrors the markdown
report section-for-section: executive summary, cost, per-case results, errored
cases, criterion breakdown, judge rationales, regression diff, recommendations.

Noor look: Clay (#C96442) accent, serif (Times) display echoing Newsreader,
sans (Helvetica) body echoing Hanken Grotesk, mono (Courier) for IDs echoing
JetBrains Mono, and clean ruled tables. Core fonts are used (not bundled TTFs)
to keep the image small; the accent, hierarchy and tables carry the brand.
"""

from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import Scorecard

CLAY = (201, 100, 66)
INK = (33, 31, 26)
MUTED = (110, 105, 95)
OK = (63, 107, 52)
FAIL = (168, 65, 42)
ERR = (152, 103, 27)
LINE = (230, 224, 210)

_SUBST = {
    "—": "-", "–": "-", "→": "->", "✓": "OK", "✕": "x", "✗": "x", "⚠": "!",
    "…": "...", "•": "-", "·": "-", "≥": ">=", "“": '"', "”": '"', "‘": "'", "’": "'",
}


def _san(s: object) -> str:
    """Core PDF fonts are latin-1; map the report's typographic glyphs and drop
    anything else that can't be encoded."""
    out = str(s)
    for k, v in _SUBST.items():
        out = out.replace(k, v)
    return out.encode("latin-1", "replace").decode("latin-1")


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


class _Report(FPDF):
    def header(self):  # noqa: D401 — fpdf hook
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, _san(f"Agenttic · agent safety report · page {self.page_no()}"),
                  align="C")


def render_pdf(sc: Scorecard, rubric: Rubric, previous: Scorecard | None = None) -> bytes:
    crit_by_id = {c.criterion_id: c for c in rubric.criteria}
    calibrated_ids = {s.criterion_id for r in sc.run_scores for s in r.criterion_scores if s.calibrated}
    provisional_ids = {s.criterion_id for r in sc.run_scores for s in r.criterion_scores if not s.calibrated}
    errored = [r for r in sc.run_scores if r.scoring_error]
    scored = [r for r in sc.run_scores if not r.scoring_error]
    n, n_err, n_scored = len(sc.run_scores), len(errored), len(scored)
    n_pass = sum(1 for r in scored if r.passed)

    pdf = _Report(format="A4")
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(16, 16, 16)
    pdf.add_page()

    def h1(text):
        pdf.set_font("Times", "B", 22); pdf.set_text_color(*INK)
        pdf.multi_cell(0, 9, _san(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def section(text):
        pdf.ln(3)
        pdf.set_font("Times", "B", 14); pdf.set_text_color(*CLAY)
        pdf.cell(0, 8, _san(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(*CLAY); pdf.set_line_width(0.5)
        y = pdf.get_y(); pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(2)

    def body(text, size=10, color=INK):
        pdf.set_font("Helvetica", "", size); pdf.set_text_color(*color)
        pdf.multi_cell(0, 5.2, _san(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # title
    h1(f"Agent Evaluation Scorecard")
    pdf.set_font("Courier", "", 9); pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5, _san(
        f"{sc.agent_id}   ·   suite {sc.suite_id} v{sc.suite_version}   ·   "
        f"rubric {sc.rubric_id} v{sc.rubric_version}   ·   {sc.created_at:%Y-%m-%d %H:%M} UTC"),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    tier_note = ("Full trajectory-level scoring (glass-box instrumentation)."
                 if sc.visibility_tier == "glass_box"
                 else "Black-box tier: input/output scoring only; trajectory criteria "
                      "were not assessable.")
    cost_note = (f"Mean cost ${sc.mean_cost_usd:.4f} per run, p95 latency "
                 f"{sc.p95_latency_ms:.0f} ms. {tier_note}")

    section("Executive summary")
    if n_scored == 0:
        body(f"No test cases could be scored. All {n} case(s) errored during "
             f"scoring (the agent ran, but the scoring config was invalid - see "
             f"Errored cases). Task success rate is not available. {cost_note}")
    else:
        err_note = (f" {n_err} case(s) errored during scoring and were excluded "
                    f"from the rate." if n_err else "")
        # headline rate
        pdf.set_font("Times", "B", 26)
        pdf.set_text_color(*(OK if sc.task_success_rate >= 0.7 else FAIL))
        pdf.cell(0, 11, _san(f"{_pct(sc.task_success_rate)} task success"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        body(f"Passed {n_pass} of {n_scored} scored case(s).{err_note} {cost_note}")

    section("Cost")
    body(f"Agent execution: ${sc.total_cost_usd:.4f}  ({sc.mean_cost_usd:.4f}/run x {n} runs)")
    body(f"Scoring (judge): ${sc.total_scoring_cost_usd:.4f}")
    body(f"Total run cost: ${sc.total_cost_usd + sc.total_scoring_cost_usd:.4f}")

    # results by case
    section("Results by test case")
    head_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=CLAY)
    pdf.set_draw_color(*LINE); pdf.set_line_width(0.2); pdf.set_font("Helvetica", "", 9)
    with pdf.table(headings_style=head_style, col_widths=(40, 16, 16, 16, 12),
                   text_align=("LEFT", "CENTER", "RIGHT", "RIGHT", "RIGHT"),
                   line_height=5.5) as table:
        table.row(["Test case", "Result", "Cost $", "Lat ms", "Steps"])
        for r in sc.run_scores:
            result, color = (("ERROR", ERR) if r.scoring_error
                             else (("PASS", OK) if r.passed else ("FAIL", FAIL)))
            row = table.row()
            row.cell(_san(r.test_id), style=FontFace(family="Courier"))
            row.cell(result, style=FontFace(emphasis="BOLD", color=color))
            row.cell(f"{r.cost_usd:.4f}")
            row.cell(f"{r.latency_ms:.0f}")
            row.cell(str(r.steps))

    if errored:
        section("Errored cases")
        body(f"{n_err} case(s) could not be scored. These are scoring/config "
             "failures, not agent task failures, and are excluded from the success rate.",
             color=MUTED)
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=head_style, col_widths=(40, 100),
                       text_align="LEFT", line_height=5) as table:
            table.row(["Test case", "Error"])
            for r in errored:
                row = table.row()
                row.cell(_san(r.test_id), style=FontFace(family="Courier"))
                row.cell(_san((r.scoring_error or "")[:200]))

    # criterion breakdown
    section("Criterion breakdown")
    if not sc.per_criterion_means:
        body("No criteria scored - all cases errored.", color=MUTED)
    else:
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=head_style, col_widths=(50, 20, 24, 46),
                       text_align=("LEFT", "CENTER", "RIGHT", "LEFT"), line_height=5.5) as table:
            table.row(["Criterion", "Scorer", "Mean", "Status"])
            for cid, mean in sorted(sc.per_criterion_means.items()):
                crit = crit_by_id.get(cid)
                scorer = crit.scorer if crit else "?"
                status = ("deterministic" if scorer == "code"
                          else ("calibrated" if cid in calibrated_ids and cid not in provisional_ids
                                else "PROVISIONAL (uncalibrated)"))
                row = table.row()
                row.cell(_san(cid), style=FontFace(family="Courier"))
                row.cell(_san(scorer)); row.cell(_pct(mean)); row.cell(_san(status))

    # judge rationales
    rationales = [(r.test_id, s) for r in sc.run_scores for s in r.criterion_scores
                  if s.score < 1.0 and s.judge_rationale]
    if rationales:
        section("Judge rationales for sub-perfect scores")
        for test_id, s in rationales[:15]:
            body(f"- {test_id} / {s.criterion_id} (score {s.score}): {s.judge_rationale}",
                 size=9, color=MUTED)

    # regression
    if previous is not None:
        section("Regression vs previous run")
        delta = sc.task_success_rate - previous.task_success_rate
        arrow = "improved" if delta > 0 else ("regressed" if delta < 0 else "unchanged")
        body(f"Compared to scorecard {previous.scorecard_id} "
             f"({previous.created_at:%Y-%m-%d}): task success rate {arrow}, "
             f"{_pct(previous.task_success_rate)} -> {_pct(sc.task_success_rate)}.")
        for cid, mean in sorted(sc.per_criterion_means.items()):
            prev = previous.per_criterion_means.get(cid)
            if prev is not None and abs(mean - prev) > 1e-9:
                body(f"- {cid}: {_pct(prev)} -> {_pct(mean)}", size=9, color=MUTED)

    # recommendations
    section("Recommendations")
    worst = sorted(sc.per_criterion_means.items(), key=lambda kv: kv[1])[:3]
    if not worst:
        body("No scored criteria to draw recommendations from - fix the scoring "
             "configuration (see Errored cases) and re-run.", color=MUTED)
    for cid, mean in worst:
        desc = crit_by_id[cid].description if cid in crit_by_id else cid
        body(f"- Improve {cid} ({_pct(mean)}): {desc}")
    if provisional_ids:
        body("- Calibrate the judge for: " + ", ".join(sorted(provisional_ids))
             + " - scores are provisional until judge-human agreement is measured (>= 0.8).")

    return bytes(pdf.output())
