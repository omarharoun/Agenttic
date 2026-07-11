"""Scan ↔ certificate consistency (the 97.6-vs-98 / dropped-dimensions bug).

The scan result view and the public certificate must not disagree about the
same underlying result. Both derive their headline number and their
per-dimension breakdown from the SAME graded run (``compute_grade`` over the
scorecard's dimension scores). These tests pin that invariant at the assembly
layer so a certificate can never surface a different score — or silently drop
the dimensions — that the scan showed.

The complementary frontend regression (the cert page integer-rounding the
composite to 98 and reading ``scores`` instead of ``dimensions``) is covered in
``ui/src/cert.test.ts``.
"""
from datetime import datetime, timedelta, timezone

from ascore.certification import safety_cert as cert
from ascore.scan import _plain_dimensions


# A run that grades to a NON-integer composite — the exact shape that made the
# scan read "97.6" while the certificate integer-rounded it to "98".
FIXTURE_SCORES = {
    "harmful_refusal_rate": 0.98,
    "injection_robustness": 0.97,
    "no_secret_leak": 0.99,
    "tool_misuse_safety": 0.96,
}


def _build_payload(scores):
    now = datetime.now(timezone.utc)
    return cert.build_certificate_payload(
        cert_id="cert_test", agent_id="agent-1", agent_name="Agent One",
        config_hash="deadbeef", scorecard_id="sc_1", suite_id="safety-battery-v1",
        suite_version=1, dimension_scores=scores, issued_at=now,
        expires_at=now + timedelta(days=365))


def test_scan_and_certificate_show_the_same_headline_number():
    # The scan headline (compute_grade) and the certificate payload composite
    # are the identical value — no divergence at the source.
    scan = cert.compute_grade(FIXTURE_SCORES)
    payload = _build_payload(FIXTURE_SCORES)
    assert payload["composite_score"] == scan["composite_score"]


def test_headline_number_is_one_decimal_not_integer_rounded():
    # The value carries one decimal of precision (round(x, 1)); this fixture is
    # deliberately fractional, so integer-rounding it (the old cert-page bug)
    # would produce a DIFFERENT number — the contradiction we are preventing.
    composite = cert.compute_grade(FIXTURE_SCORES)["composite_score"]
    assert composite == round(composite, 1)
    assert composite != round(composite)  # fractional → int rounding diverges


def test_certificate_carries_the_same_dimensions_the_scan_scored():
    # Every dimension the scan surfaced must be present on the certificate —
    # same criterion set, nothing dropped during dossier/cert assembly.
    scan = cert.compute_grade(FIXTURE_SCORES)
    plain = _plain_dimensions(scan, FIXTURE_SCORES)
    payload = _build_payload(FIXTURE_SCORES)

    scan_criteria = {d["criterion_id"] for d in plain}
    cert_criteria = {d["criterion_id"] for d in payload["dimensions"]}
    assert scan_criteria == cert_criteria
    assert len(cert_criteria) == len(FIXTURE_SCORES)  # all four carried through


def test_certificate_dimensions_are_non_empty_when_a_run_was_graded():
    # A graded run always publishes its breakdown — the cert page must never be
    # able to honestly say "no per-dimension breakdown was published" here.
    payload = _build_payload(FIXTURE_SCORES)
    assert payload["dimensions"], "graded run must publish a per-dimension breakdown"


def test_per_dimension_scores_match_between_scan_and_certificate():
    # The same 0–1 score per dimension on both surfaces (scan percent is just
    # round(100 * score) of the same value).
    scan = cert.compute_grade(FIXTURE_SCORES)
    plain = {d["criterion_id"]: d for d in _plain_dimensions(scan, FIXTURE_SCORES)}
    payload = _build_payload(FIXTURE_SCORES)
    for dim in payload["dimensions"]:
        crit = dim["criterion_id"]
        if crit in plain:
            assert plain[crit]["score"] == dim["score"]
