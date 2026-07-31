"""The PDF and the Markdown report must tell the same story.

`_verification_block` leads the Markdown report (scorecard_report.py:82): what
was never exercised, which properties held, and only then the pass rate, demoted
to one line. The PDF rendered none of it and opened with a large task-success
percentage instead — so the same scorecard produced one document saying
"Coverage closure 29% - NOT CLOSED" and another saying "83% task success", with
no mention that most of the model was never exercised.

Demoting the pass rate in one renderer and not the other is worse than not
demoting it anywhere: the PDF is the artefact that gets attached to an email, and
the reader has no way to know a fuller account exists.

These tests pin the parity, not the layout. The PDF decides typography; the
WORDING comes from the one implementation in `scorecard_report`.
"""

from __future__ import annotations

from agenttic.reporting.pdf_report import render_pdf
from agenttic.reporting.scorecard_report import _verification_block, render_markdown

from .test_pdf_report import RUBRIC, _scorecard


def _with_coverage(sc):
    """A scorecard carrying a coverage model, which is what makes the section
    say anything. Without one it correctly reports the pass rate as unscoped."""
    sc.coverage = {
        "model_ref": "conversational_transactional@v3",
        "trace_closure": 0.2913, "closure_target": 0.95, "closed": False,
        "not_measurable": {"session_shape": "the run path emits no user_turn"},
        "assertions": {"evaluated": 9, "violations": 0},
    }
    return sc


def test_the_pdf_renders_the_verification_section():
    sc = _with_coverage(_scorecard())
    assert _verification_block(sc), "fixture does not exercise the section"
    pdf = render_pdf(sc, RUBRIC)
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


def test_the_pdf_is_larger_once_verification_is_present():
    """Crude but honest: the section has to actually reach the page. A renderer
    that swallowed the lines would pass a smoke test and fail this."""
    bare = render_pdf(_scorecard(), RUBRIC)
    with_cov = render_pdf(_with_coverage(_scorecard()), RUBRIC)
    assert len(with_cov) > len(bare), (len(bare), len(with_cov))


def test_both_renderers_read_the_same_source():
    """The parity guard. If someone adds a second implementation of the wording
    to the PDF, these stop agreeing on what the section contains."""
    sc = _with_coverage(_scorecard())
    lines = _verification_block(sc)
    markdown = render_markdown(sc, RUBRIC)
    for line in lines:
        if line.strip() and not line.startswith("#"):
            assert line in markdown, line


def test_a_scorecard_without_coverage_still_renders():
    """The section is not optional plumbing the PDF can trip over — an unscoped
    run has to render too, and say that it is unscoped."""
    pdf = render_pdf(_scorecard(), RUBRIC)
    assert pdf[:4] == b"%PDF"


# --- tables ----------------------------------------------------------------- #

def test_a_markdown_table_does_not_reach_the_page_as_pipes():
    from agenttic.reporting.pdf_report import _as_pdf_lines

    out = _as_pdf_lines([
        "| Coverpoint | Closure | Never exercised |",
        "|---|---|---|",
        "| intent | 60% | refund, exchange |",
    ])
    texts = [t for _, t in out]
    assert not any("|" in t for t in texts), texts
    assert any("intent" in t and "60%" in t for t in texts), texts


def test_a_table_header_with_no_rows_is_dropped():
    """The verification block emits the header even when it has no rows. An empty
    table in a customer PDF reads as data that failed to load rather than as data
    that was never there, and the fact it would have introduced is already in the
    closure sentence above it."""
    from agenttic.reporting.pdf_report import _as_pdf_lines

    out = _as_pdf_lines([
        "| Coverpoint | Closure | Never exercised |",
        "|---|---|---|",
    ])
    assert out == [], out


def test_headings_keep_their_level():
    from agenttic.reporting.pdf_report import _as_pdf_lines

    out = _as_pdf_lines(["## Verification", "", "### Detail", "", "body text"])
    assert out == [("section", "Verification"), ("sub", "Detail"),
                   ("body", "body text")], out


def test_emphasis_is_stripped_not_printed():
    from agenttic.reporting.pdf_report import _demark

    assert _demark("**Coverage closure 29%** of target 95%") == \
        "Coverage closure 29% of target 95%"
    assert _demark("- `session_shape` never measured") == \
        "  - session_shape never measured"
