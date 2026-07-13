"""A/B comparison report — the client deliverable for a head-to-head run.

Mirrors the single-agent scorecard report (Markdown + on-brand PDF) but framed
as a paired comparison: the verdict up top, the two variants, the paired success
comparison with its McNemar p-value, per-criterion deltas, the flipped-case
diff, and cost/latency. The PDF reuses the scorecard PDF's brand styling so the
two deliverables look like one family.
"""

from __future__ import annotations

from agenttic.schema.ab import ABComparison


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _signed_pct(x: float) -> str:
    return f"{'+' if x >= 0 else ''}{100 * x:.0f}pp"


def _sig(p: float, significant: bool, underpowered: bool = False) -> str:
    if significant:
        return f"significant (p={p:.3f})"
    if underpowered:
        return f"n.s. — too few (p={p:.2f})"
    return f"not significant (p={p:.2f})"


def render_ab_markdown(c: ABComparison) -> str:
    la, lb = c.label_a, c.label_b
    mc = c.mcnemar
    lines = [
        f"# A/B Comparison — `{la}` vs `{lb}`",
        "",
        f"Suite `{c.suite_id}` v{c.suite_version} · rubric `{c.rubric_id}` "
        f"v{c.rubric_version} · generated {c.created_at:%Y-%m-%d %H:%M} UTC",
        "",
        "## Verdict",
        "",
        f"**{c.verdict}**",
        "",
        "## Variants",
        "",
        "| | Variant | Configuration |",
        "|---|---|---|",
        f"| **{la}** | `{c.variant_a.agent_id}` | {c.variant_a.summary()} |",
        f"| **{lb}** | `{c.variant_b.agent_id}` | {c.variant_b.summary()} |",
        "",
        "## Overall success (paired)",
        "",
        f"Compared over **{c.n_paired}** case(s) scored by both variants.",
        "",
        "| | Success rate |",
        "|---|---|",
        f"| {la} | {_pct(c.success_rate_a)} |",
        f"| {lb} | {_pct(c.success_rate_b)} |",
        f"| Δ ({lb}−{la}) | {_signed_pct(c.success_delta)} |",
        "",
        f"McNemar's paired test: {mc['b']} case(s) only {la} passed, "
        f"{mc['c']} case(s) only {lb} passed → "
        f"**{_sig(mc['p_value'], mc['significant'], mc.get('underpowered'))}** "
        f"({mc['test']}).",
    ]

    lines += ["", "## Per-criterion deltas", "",
              f"| Criterion | {la} | {lb} | Δ | Favors | Significance | n |",
              "|---|---|---|---|---|---|---|"]
    if not c.per_criterion:
        lines.append("| _(no criteria scored on paired cases)_ | — | — | — | — | — | — |")
    for cc in c.per_criterion:
        favors = "—" if cc.direction == "tie" else (lb if cc.direction == "B" else la)
        lines.append(
            f"| `{cc.criterion_id}` | {_pct(cc.mean_a)} | {_pct(cc.mean_b)} "
            f"| {_signed_pct(cc.delta)} | {favors} | {_sig(cc.p_value, cc.significant)} "
            f"| {cc.n} |")

    gains = [f for f in c.flipped_cases if f.direction == "gain"]
    losses = [f for f in c.flipped_cases if f.direction == "loss"]
    lines += ["", "## Flipped cases", "",
              f"{len(c.flipped_cases)} case(s) changed outcome between the variants "
              f"({len(gains)} gained by {lb}, {len(losses)} lost)."]
    if c.flipped_cases:
        lines += ["", "| Test case | " + f"{la}" + " | " + f"{lb}" + " | Direction |",
                  "|---|---|---|---|"]
        for f in c.flipped_cases:
            arrow = f"{la} fail → {lb} pass" if f.direction == "gain" \
                else f"{la} pass → {lb} fail"
            lines.append(f"| `{f.test_id}` | {'PASS' if f.a_passed else 'FAIL'} "
                         f"| {'PASS' if f.b_passed else 'FAIL'} | {arrow} |")

    lines += ["", "## Cost & latency", "",
              "| | Mean cost/run | Total cost | p95 latency |",
              "|---|---|---|---|",
              f"| {la} | ${c.mean_cost_a:.4f} | ${c.total_cost_a:.4f} | "
              f"{c.p95_latency_a:.0f} ms |",
              f"| {lb} | ${c.mean_cost_b:.4f} | ${c.total_cost_b:.4f} | "
              f"{c.p95_latency_b:.0f} ms |"]

    if c.excluded_test_ids:
        lines += ["", "## Excluded cases", "",
                  f"{len(c.excluded_test_ids)} case(s) errored in at least one "
                  "variant and were excluded from the comparison (scoring/config "
                  "failures, not task failures): "
                  + ", ".join(f"`{t}`" for t in c.excluded_test_ids) + "."]

    return "\n".join(lines) + "\n"


def render_ab_pdf(c: ABComparison) -> bytes:
    """On-brand PDF mirroring the markdown comparison, reusing the scorecard
    PDF's styling primitives."""
    from fpdf.enums import XPos, YPos
    from fpdf.fonts import FontFace

    from agenttic.reporting.pdf_report import (
        CLAY, ERR, FAIL, INK, LINE, MUTED, OK, _Report, _san,
    )

    la, lb = c.label_a, c.label_b
    mc = c.mcnemar

    pdf = _Report(format="A4")
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(16, 16, 16)
    pdf.add_page()

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

    head_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=CLAY)

    # title
    pdf.set_font("Times", "B", 22); pdf.set_text_color(*INK)
    pdf.multi_cell(0, 9, _san(f"A/B Comparison - {la} vs {lb}"),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "", 9); pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5, _san(
        f"suite {c.suite_id} v{c.suite_version}   ·   rubric {c.rubric_id} "
        f"v{c.rubric_version}   ·   {c.created_at:%Y-%m-%d %H:%M} UTC"),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    section("Verdict")
    sig = mc["significant"]
    pdf.set_font("Times", "B", 15)
    pdf.set_text_color(*(OK if sig else MUTED))
    pdf.multi_cell(0, 7, _san(c.verdict), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    section("Variants")
    pdf.set_font("Helvetica", "", 9)
    with pdf.table(headings_style=head_style, col_widths=(10, 30, 100),
                   text_align="LEFT", line_height=5.5) as t:
        t.row(["", "Variant", "Configuration"])
        t.row([la, _san(c.variant_a.agent_id), _san(c.variant_a.summary())])
        t.row([lb, _san(c.variant_b.agent_id), _san(c.variant_b.summary())])

    section("Overall success (paired)")
    body(f"Compared over {c.n_paired} case(s) scored by both variants.")
    pdf.set_font("Helvetica", "", 9)
    with pdf.table(headings_style=head_style, col_widths=(40, 30),
                   text_align=("LEFT", "RIGHT"), line_height=5.5) as t:
        t.row(["", "Success rate"])
        t.row([la, _pct(c.success_rate_a)])
        t.row([lb, _pct(c.success_rate_b)])
        t.row([f"Delta ({lb}-{la})", _signed_pct(c.success_delta)])
    body(f"McNemar's paired test: {mc['b']} case(s) only {la} passed, "
         f"{mc['c']} case(s) only {lb} passed -> "
         f"{_sig(mc['p_value'], sig, mc.get('underpowered'))} ({mc['test']}).",
         size=9, color=MUTED)

    section("Per-criterion deltas")
    if not c.per_criterion:
        body("No criteria scored on the paired cases.", color=MUTED)
    else:
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=head_style,
                       col_widths=(40, 18, 18, 18, 16, 30),
                       text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT", "CENTER", "LEFT"),
                       line_height=5.5) as t:
            t.row(["Criterion", la, lb, "Delta", "Favors", "Significance"])
            for cc in c.per_criterion:
                favors = "-" if cc.direction == "tie" else (
                    lb if cc.direction == "B" else la)
                row = t.row()
                row.cell(_san(cc.criterion_id), style=FontFace(family="Courier"))
                row.cell(_pct(cc.mean_a)); row.cell(_pct(cc.mean_b))
                row.cell(_signed_pct(cc.delta)); row.cell(favors)
                row.cell(_san(_sig(cc.p_value, cc.significant)))

    gains = [f for f in c.flipped_cases if f.direction == "gain"]
    losses = [f for f in c.flipped_cases if f.direction == "loss"]
    section("Flipped cases")
    body(f"{len(c.flipped_cases)} case(s) changed outcome "
         f"({len(gains)} gained by {lb}, {len(losses)} lost).")
    if c.flipped_cases:
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=head_style, col_widths=(40, 18, 18, 50),
                       text_align=("LEFT", "CENTER", "CENTER", "LEFT"),
                       line_height=5.5) as t:
            t.row(["Test case", la, lb, "Direction"])
            for f in c.flipped_cases:
                arrow = (f"{la} fail -> {lb} pass" if f.direction == "gain"
                         else f"{la} pass -> {lb} fail")
                row = t.row()
                row.cell(_san(f.test_id), style=FontFace(family="Courier"))
                row.cell("PASS" if f.a_passed else "FAIL",
                         style=FontFace(color=OK if f.a_passed else FAIL))
                row.cell("PASS" if f.b_passed else "FAIL",
                         style=FontFace(color=OK if f.b_passed else FAIL))
                row.cell(_san(arrow))

    section("Cost & latency")
    pdf.set_font("Helvetica", "", 9)
    with pdf.table(headings_style=head_style, col_widths=(30, 36, 32, 36),
                   text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT"),
                   line_height=5.5) as t:
        t.row(["", "Mean cost/run", "Total cost", "p95 latency"])
        t.row([la, f"${c.mean_cost_a:.4f}", f"${c.total_cost_a:.4f}",
               f"{c.p95_latency_a:.0f} ms"])
        t.row([lb, f"${c.mean_cost_b:.4f}", f"${c.total_cost_b:.4f}",
               f"{c.p95_latency_b:.0f} ms"])

    if c.excluded_test_ids:
        section("Excluded cases")
        body(f"{len(c.excluded_test_ids)} case(s) errored in at least one variant "
             "and were excluded from the comparison (scoring/config failures, not "
             "task failures): " + ", ".join(c.excluded_test_ids) + ".",
             size=9, color=ERR)

    return bytes(pdf.output())
