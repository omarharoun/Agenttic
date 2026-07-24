"""SPEC-12 Step 58 (M39) — catalog conformance.

Acceptance:

1. Registration is not approval; promotion refuses without evidence and a named
   approver, and names the specific missing thing.
2. Evidence that expired or was revoked cannot promote.
3. A challenger replacing an incumbent must be shadowed, and a per-case
   regression blocks promotion even when the average improved.
4. Retirement cascades to dependents and suspends their manifests.
5. Conformance reports lapsed evidence and uncertified dependencies; it never
   quietly repairs them.
6. The catalog exports as a canonical, hashable document.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenttic.certification.attest import (
    append_revocation, build_manifest, new_revocation_list, sign_manifest)
from agenttic.certification.catalog import (
    Catalog, CatalogEntry, PromotionRefused, shadow_compare)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
SCORECARD = {"agent_id": "triage", "score": 0.91}


@pytest.fixture(autouse=True)
def _local_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))


def _signed(manifest_id="m-1", agent_id="triage", issued=NOW, days=90,
            scorecard=None):
    return sign_manifest(build_manifest(
        manifest_id=manifest_id, agent_id=agent_id,
        agent_config_hash=f"cfg-{agent_id}",
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard=scorecard if scorecard is not None else SCORECARD,
        issued_at=issued, expires_in_days=days))


def _catalog_with_candidate(ref_id="triage", depends_on=()):
    cat = Catalog(owner="acme")
    cat.register(CatalogEntry(subject_id=ref_id, kind="agent", version="1.0",
                              depends_on=depends_on, recorded_at=NOW))
    return cat


# ---- 1. registration is not approval --------------------------------------- #

def test_cannot_register_straight_into_promoted():
    cat = Catalog()
    with pytest.raises(PromotionRefused, match="promotion requires evidence"):
        cat.register(CatalogEntry(subject_id="x", kind="agent", status="promoted"))


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(approver="", rationale="looks fine"), "NAMED approver"),
    (dict(approver="dana", rationale=""), "requires a rationale"),
    (dict(approver="dana", rationale="ok", signed=None), "no signed evidence"),
])
def test_promotion_refuses_and_names_the_missing_thing(kwargs, fragment):
    cat = _catalog_with_candidate()
    kwargs.setdefault("signed", _signed())
    with pytest.raises(PromotionRefused, match=fragment):
        cat.promote("agent:triage@1.0", now=NOW, **kwargs)
    assert cat.get("agent:triage@1.0").status == "candidate"


def test_promotion_records_who_approved_it_and_why():
    cat = _catalog_with_candidate()
    rec = cat.promote("agent:triage@1.0", approver="dana.whitfield",
                      rationale="coverage closure 0.82, no critical assertions",
                      signed=_signed(), scorecard=SCORECARD, now=NOW)
    assert rec.approver == "dana.whitfield"
    assert rec.to_status == "promoted"
    assert rec.manifest_status == "valid"
    assert cat.get("agent:triage@1.0").status == "promoted"
    assert cat.records == [rec]


def test_unknown_ref_is_refused():
    cat = Catalog()
    with pytest.raises(PromotionRefused, match="not registered"):
        cat.promote("agent:ghost@1.0", approver="d", rationale="r",
                    signed=_signed(), now=NOW)


# ---- 2. lapsed / revoked evidence cannot promote ---------------------------- #

def test_expired_evidence_cannot_promote():
    cat = _catalog_with_candidate()
    signed = _signed(issued=NOW - timedelta(days=120), days=90)
    with pytest.raises(PromotionRefused, match="evidence is expired"):
        cat.promote("agent:triage@1.0", approver="d", rationale="r",
                    signed=signed, now=NOW)


def test_revoked_evidence_cannot_promote():
    cat = _catalog_with_candidate()
    signed = _signed()
    rl = new_revocation_list()
    append_revocation(rl, manifest_id="m-1", subject_config_hash="cfg-triage",
                      reason="drift detected in production", now=NOW)
    with pytest.raises(PromotionRefused, match="drift detected"):
        cat.promote("agent:triage@1.0", approver="d", rationale="r",
                    signed=signed, revocations=rl, now=NOW)


def test_altered_scorecard_cannot_promote():
    cat = _catalog_with_candidate()
    signed = _signed()
    with pytest.raises(PromotionRefused, match="scorecard altered"):
        cat.promote("agent:triage@1.0", approver="d", rationale="r",
                    signed=signed, scorecard={"agent_id": "triage", "score": 0.99},
                    now=NOW)


# ---- 3. shadow mode --------------------------------------------------------- #

def test_shadow_compare_counts_regressions_per_case():
    rep = shadow_compare("agent:v1@1.0", "agent:v2@2.0", [
        ("refund_policy", 1.0, 0.0),      # regression
        ("routing", 1.0, 1.0),            # agreement
        ("tone", 0.0, 1.0),               # improvement
        ("escalation", 0.0, 1.0),         # improvement
    ])
    assert rep.regressions == ["refund_policy"]
    assert rep.improvements == ["tone", "escalation"]
    assert rep.agreement_rate == pytest.approx(0.25)
    # the average IMPROVED while a working case broke — which is the point
    assert rep.challenger_score > rep.incumbent_score
    assert not rep.clean


def test_challenger_with_a_regression_is_refused_despite_a_better_average():
    cat = _catalog_with_candidate()
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="2.0",
                              recorded_at=NOW))
    cat.promote("agent:triage@1.0", approver="d", rationale="incumbent",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    cat.start_shadow("agent:triage@2.0")
    rep = shadow_compare("agent:triage@1.0", "agent:triage@2.0",
                         [("refund_policy", 1.0, 0.0), ("tone", 0.0, 1.0),
                          ("routing", 0.0, 1.0)])
    with pytest.raises(PromotionRefused, match="regressed 1 case"):
        cat.promote("agent:triage@2.0", approver="d", rationale="better average",
                    signed=_signed("m-2", "triage2"), shadow=rep,
                    incumbent_ref="agent:triage@1.0", now=NOW)
    assert cat.get("agent:triage@2.0").status == "shadow"
    assert cat.get("agent:triage@1.0").status == "promoted"


def test_replacing_an_incumbent_without_a_shadow_report_is_refused():
    cat = _catalog_with_candidate()
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="2.0"))
    cat.promote("agent:triage@1.0", approver="d", rationale="incumbent",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    with pytest.raises(PromotionRefused, match="without a shadow comparison"):
        cat.promote("agent:triage@2.0", approver="d", rationale="ship it",
                    signed=_signed("m-2", "triage2"),
                    incumbent_ref="agent:triage@1.0", now=NOW)


def test_a_shadow_report_for_a_different_pair_is_refused():
    cat = _catalog_with_candidate()
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="2.0"))
    cat.promote("agent:triage@1.0", approver="d", rationale="incumbent",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    wrong = shadow_compare("agent:other@1.0", "agent:other@2.0",
                           [("c", 1.0, 1.0)])
    with pytest.raises(PromotionRefused, match="shadow report is for"):
        cat.promote("agent:triage@2.0", approver="d", rationale="r",
                    signed=_signed("m-2", "triage2"), shadow=wrong,
                    incumbent_ref="agent:triage@1.0", now=NOW)


def test_a_clean_challenger_is_promoted_and_supersedes_the_incumbent():
    cat = _catalog_with_candidate()
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="2.0"))
    cat.promote("agent:triage@1.0", approver="d", rationale="incumbent",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    cat.start_shadow("agent:triage@2.0")
    rep = shadow_compare("agent:triage@1.0", "agent:triage@2.0",
                         [("refund_policy", 1.0, 1.0), ("tone", 0.0, 1.0)])
    assert rep.clean
    cat.promote("agent:triage@2.0", approver="d", rationale="clean shadow",
                signed=_signed("m-2", "triage2"), shadow=rep,
                incumbent_ref="agent:triage@1.0", now=NOW)
    assert cat.get("agent:triage@2.0").status == "promoted"
    # the incumbent steps down rather than staying silently approved
    assert cat.get("agent:triage@1.0").status == "candidate"
    assert "superseded by" in cat.get("agent:triage@1.0").notes


# ---- 4. retirement cascades ------------------------------------------------- #

def _agent_on_a_server():
    cat = Catalog(owner="acme")
    cat.register(CatalogEntry(subject_id="payments-mcp", kind="mcp_server",
                              version="3.1", recorded_at=NOW))
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="1.0",
                              depends_on=("mcp_server:payments-mcp@3.1",),
                              recorded_at=NOW))
    cat.promote("mcp_server:payments-mcp@3.1", approver="d", rationale="battery 1.00",
                signed=_signed("m-mcp", "payments-mcp"), now=NOW)
    cat.promote("agent:triage@1.0", approver="d", rationale="ok",
                signed=_signed("m-agent", "triage"), scorecard=SCORECARD, now=NOW)
    return cat


def test_retiring_a_component_cascades_to_the_agents_certified_with_it():
    cat = _agent_on_a_server()
    rl = new_revocation_list()
    rec = cat.retire("mcp_server:payments-mcp@3.1",
                     reason="tool-response injection found in v3.1",
                     approver="dana", revocations=rl, now=NOW)

    assert rec.cascaded_to == ["agent:triage@1.0"]
    assert cat.get("mcp_server:payments-mcp@3.1").status == "retired"
    # the agent is NOT silently still approved
    assert cat.get("agent:triage@1.0").status == "needs_reverification"
    assert "payments-mcp" in cat.get("agent:triage@1.0").notes

    # both manifests hit the revocation list, with distinct statuses
    assert rl.status_for("m-mcp").status == "revoked"
    assert rl.status_for("m-agent").status == "suspended"
    assert rl.status_for("m-agent").source == "catalog:retire_cascade"


def test_retirement_requires_a_reason_and_an_approver():
    cat = _agent_on_a_server()
    with pytest.raises(PromotionRefused, match="named approver and a reason"):
        cat.retire("mcp_server:payments-mcp@3.1", reason="", approver="d", now=NOW)


def test_a_retired_entry_is_not_promoted_back():
    cat = _agent_on_a_server()
    cat.retire("mcp_server:payments-mcp@3.1", reason="withdrawn", approver="d",
               now=NOW)
    with pytest.raises(PromotionRefused, match="withdrawn entry is not promoted"):
        cat.promote("mcp_server:payments-mcp@3.1", approver="d", rationale="oops",
                    signed=_signed("m-mcp", "payments-mcp"), now=NOW)


def test_retired_entries_stay_visible_in_the_register():
    cat = _agent_on_a_server()
    cat.retire("mcp_server:payments-mcp@3.1", reason="withdrawn", approver="d",
               now=NOW)
    refs = [e.ref for e in cat.entries]
    assert "mcp_server:payments-mcp@3.1" in refs, \
        "a withdrawn entry must remain auditable, not vanish"


# ---- 5. conformance reports, never repairs ---------------------------------- #

def test_conformance_reports_a_cascaded_entry_as_an_error():
    cat = _agent_on_a_server()
    cat.retire("mcp_server:payments-mcp@3.1", reason="injection", approver="d",
               now=NOW)
    findings = cat.check_conformance(now=NOW)
    problems = {f.problem for f in findings}
    assert "needs_reverification" in problems
    # reported, not repaired: the entry is still in that state afterwards
    assert cat.get("agent:triage@1.0").status == "needs_reverification"


def test_conformance_flags_expired_evidence_on_a_promoted_entry():
    cat = _catalog_with_candidate()
    signed = _signed()
    cat.promote("agent:triage@1.0", approver="d", rationale="r", signed=signed,
                scorecard=SCORECARD, now=NOW)
    later = NOW + timedelta(days=120)
    findings = cat.check_conformance(manifests={"m-1": signed}, now=later)
    assert [f.problem for f in findings] == ["evidence_expired"]
    assert findings[0].severity == "error"


def test_conformance_flags_a_promoted_agent_on_an_unpromoted_dependency():
    cat = Catalog()
    cat.register(CatalogEntry(subject_id="payments-mcp", kind="mcp_server",
                              version="3.1"))
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="1.0",
                              depends_on=("mcp_server:payments-mcp@3.1",)))
    cat.promote("agent:triage@1.0", approver="d", rationale="r",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    findings = cat.check_conformance(now=NOW)
    assert any(f.problem == "uncertified_dependency" for f in findings)
    assert any("is not" in f.detail for f in findings)


def test_conformance_flags_a_dependency_that_is_not_in_the_catalog_at_all():
    cat = Catalog()
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="1.0",
                              depends_on=("tool:mystery@1",)))
    cat.promote("agent:triage@1.0", approver="d", rationale="r",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    findings = cat.check_conformance(now=NOW)
    assert any(f.problem == "unregistered_dependency" for f in findings)


def test_a_referenced_but_unsupplied_manifest_is_a_warning_not_a_pass():
    cat = _catalog_with_candidate()
    cat.promote("agent:triage@1.0", approver="d", rationale="r",
                signed=_signed(), scorecard=SCORECARD, now=NOW)
    findings = cat.check_conformance(manifests={}, now=NOW)
    assert [f.problem for f in findings] == ["evidence_unavailable"]
    assert findings[0].severity == "warning"


def test_a_clean_catalog_with_its_evidence_supplied_reports_nothing():
    cat = _agent_on_a_server()
    manifests = {"m-mcp": _signed("m-mcp", "payments-mcp"),
                 "m-agent": _signed("m-agent", "triage")}
    assert cat.check_conformance(manifests=manifests, now=NOW) == []


# ---- 6. export -------------------------------------------------------------- #

def test_export_is_canonical_and_hashable():
    cat = _agent_on_a_server()
    a = cat.export_sha256(now=NOW)
    b = cat.export_sha256(now=NOW)
    assert a == b, "the same catalog must hash identically"
    doc = cat.export(now=NOW)
    assert doc["owner"] == "acme"
    assert doc["counts"]["promoted"] == 2
    assert {e["ref"] for e in doc["entries"]} == {
        "mcp_server:payments-mcp@3.1", "agent:triage@1.0"}


def test_export_hash_changes_when_the_register_changes():
    cat = _agent_on_a_server()
    before = cat.export_sha256(now=NOW)
    cat.retire("mcp_server:payments-mcp@3.1", reason="withdrawn", approver="d",
               now=NOW)
    assert cat.export_sha256(now=NOW) != before


def test_export_round_trips_so_someone_else_can_check_the_register():
    cat = _agent_on_a_server()
    cat.retire("mcp_server:payments-mcp@3.1", reason="injection found",
               approver="dana", now=NOW)
    doc = cat.export(now=NOW)

    rebuilt = Catalog.from_export(doc)
    assert rebuilt.export_sha256(now=NOW) == cat.export_sha256(now=NOW)
    assert rebuilt.get("agent:triage@1.0").status == "needs_reverification"
    assert rebuilt.get("mcp_server:payments-mcp@3.1").status == "retired"
    # and the rebuilt register answers the same conformance question
    assert ([f.problem for f in rebuilt.check_conformance(now=NOW)]
            == [f.problem for f in cat.check_conformance(now=NOW)])


def test_export_carries_the_promotion_and_retirement_records():
    cat = _agent_on_a_server()
    cat.retire("mcp_server:payments-mcp@3.1", reason="injection found",
               approver="dana", now=NOW)
    kinds = [r["kind"] for r in cat.export(now=NOW)["records"]]
    assert kinds == ["promotion", "promotion", "retirement"]
