"""Certification dossier renderers (SPEC-2 T14.4) — markdown / json / inspect / pdf.

Every renderer treats ``not_assessed`` domains as **visually distinct** (a bold
``NOT ASSESSED`` marker, never a number) and emits **zero placeholder-derived
numbers**: a domain with no real/seed evidence shows its status + caveat only.
Numbers that ARE shown resolve to persisted refs carried on the dossier.
"""

from __future__ import annotations

import json

from ascore.certification.hashing import canonical_json
from ascore.schema.certification import Dossier

_STATUS_MD = {
    "assessed_real": "assessed (real)",
    "assessed_seed": "assessed (seed)",
    "not_assessed": "**NOT ASSESSED**",
}


def render_json(dossier: Dossier) -> str:
    """Canonical JSON (the verifiable artifact). Sorted keys, UTF-8."""
    return canonical_json(dossier.model_dump(mode="json"))


def render_inspect(dossier: Dossier) -> dict:
    """A minimal Inspect-interop view: the dossier's identity + its EvalLog ref
    plus the tier/coverage summary (no fabricated scores)."""
    return {
        "interop": "agenttic-dossier/inspect",
        "dossier_id": dossier.dossier_id,
        "agent_id": dossier.agent_id,
        "eval_log_ref": dossier.inspect_log_ref,
        "tier": dossier.tier_decision.tier,
        "profile": f"{dossier.profile_id}@v{dossier.profile_version}",
        "content_sha256": dossier.content_sha256,
        "coverage": [
            {"domain": c.domain, "status": c.status,
             "evidence_refs": list(c.evidence_refs)}
            for c in dossier.coverage
        ],
    }


def render_md(dossier: Dossier) -> str:
    td = dossier.tier_decision
    lines: list[str] = []
    lines.append(f"# Certification Dossier — {dossier.agent_id}")
    lines.append("")
    lines.append(f"- **Tier:** {td.tier}")
    lines.append(f"- **Profile:** `{dossier.profile_id}@v{dossier.profile_version}`")
    lines.append(f"- **Attestation:** {dossier.attestation.mode}")
    lines.append(f"- **Agent config hash:** `{dossier.agent_config_hash}`")
    lines.append(f"- **Content SHA-256:** `{dossier.content_sha256}`")
    if dossier.prev_dossier_sha256:
        lines.append(f"- **Chained from:** `{dossier.prev_dossier_sha256[:16]}…`")
    lines.append("")

    if td.caps_applied:
        lines.append("## Caps applied")
        for cap in td.caps_applied:
            lines.append(f"- `{cap}`")
        lines.append("")

    lines.append("## Domain coverage")
    lines.append("")
    lines.append("| Domain | Coverage | Evidence |")
    lines.append("| --- | --- | --- |")
    for c in dossier.coverage:
        status = _STATUS_MD.get(c.status, c.status)
        # zero placeholder-derived numbers: not_assessed shows no number, only refs
        evidence = ", ".join(f"`{r}`" for r in c.evidence_refs) or "—"
        lines.append(f"| {c.domain} | {status} | {evidence} |")
    lines.append("")

    if dossier.scorecard_refs:
        lines.append("## Evidence refs")
        for r in dossier.scorecard_refs:
            lines.append(f"- `{r}`")
        lines.append("")

    if dossier.caveats:
        lines.append("## Caveats")
        for cav in dossier.caveats:
            lines.append(f"- {cav}")
        lines.append("")

    lines.append("---")
    lines.append(
        "_This dossier is evidence, not a compliance determination. See "
        "docs/REGULATORY_CROSSWALK.md for how these artifacts map to EU CoP / "
        "CA SB 53 / NY RAISE clause families._")
    return "\n".join(lines)


def render_pdf(dossier: Dossier) -> bytes:
    """A compact PDF rendering. Reuses the report's fpdf2 core-font style; NOT
    ASSESSED domains are printed in the fail color, never as a number."""
    from fpdf import FPDF

    from ascore.reporting.pdf_report import CLAY, FAIL, INK, LINE, MUTED, OK, _san

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Times", "B", 20)
    pdf.set_text_color(*INK)
    pdf.cell(0, 12, _san(f"Certification Dossier — {dossier.agent_id}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    for label, val in [
        ("Tier", dossier.tier_decision.tier),
        ("Profile", f"{dossier.profile_id}@v{dossier.profile_version}"),
        ("Attestation", dossier.attestation.mode),
        ("Content SHA-256", dossier.content_sha256 or ""),
    ]:
        pdf.cell(0, 6, _san(f"{label}: {val}"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_font("Times", "B", 13)
    pdf.set_text_color(*CLAY)
    pdf.cell(0, 9, "Domain coverage", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for c in dossier.coverage:
        if c.status == "not_assessed":
            pdf.set_text_color(*FAIL)
            body = f"{c.domain}: NOT ASSESSED"
        else:
            pdf.set_text_color(*(OK if c.status == "assessed_real" else INK))
            refs = ", ".join(c.evidence_refs) or "-"
            body = f"{c.domain}: {c.status} [{refs}]"
        pdf.cell(0, 6, _san(body), new_x="LMARGIN", new_y="NEXT")

    if dossier.caveats:
        pdf.ln(2)
        pdf.set_font("Times", "B", 13)
        pdf.set_text_color(*CLAY)
        pdf.cell(0, 9, "Caveats", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        for cav in dossier.caveats:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.epw, 5, _san(f"- {cav}"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*MUTED)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, 4, _san(
        "This dossier is evidence, not a compliance determination. See "
        "docs/REGULATORY_CROSSWALK.md for EU CoP / CA SB 53 / NY RAISE mapping."),
        new_x="LMARGIN", new_y="NEXT")

    pdf.set_draw_color(*LINE)
    out = pdf.output()
    return bytes(out)


def render(dossier: Dossier, fmt: str = "md"):
    fmt = fmt.lower()
    if fmt == "md":
        return render_md(dossier)
    if fmt == "json":
        return render_json(dossier)
    if fmt == "inspect":
        return json.dumps(render_inspect(dossier), indent=2)
    if fmt == "pdf":
        return render_pdf(dossier)
    raise ValueError(f"unknown dossier render format {fmt!r}")
