"""Tests for the Evaluator Plugin Interface.

Covers: the normalized EvalResult schema (provenance/vocab/verdict invariants);
the no-crash contract (a case error becomes outcome="error"); not_assessed when
Inspect is unavailable; the dimension mapping tables; the orchestrator never
emitting a blended number without its breakdown; the union-passport round-trip
(sign→verify, tamper→fail) AND an old-format dossier still verifying under the
same code with an unchanged signing kid; the license gate; fresh-failure
promotion into the regression suite; and one full end-to-end run.
"""

from __future__ import annotations

import copy
import subprocess
import sys

import pytest

from agenttic.evaluators.base import AgentTarget, Capabilities
from agenttic.schema.eval_result import (
    DIMENSION_VOCAB_VERSION,
    DIMENSIONS,
    EvalResult,
    EvalResultError,
)


# --------------------------------------------------------------------------- #
# EvalResult schema — provenance / vocabulary / verdict invariants.
# --------------------------------------------------------------------------- #


def _ok_result(**over) -> EvalResult:
    base = dict(
        source="agenttic-gen", source_version="agenttic-1.0.1",
        source_license="LicenseRef-Agenttic-Proprietary",
        dimension="injection_robustness", test_id="t1", probe="p",
        outcome="pass", score=1.0, raw={"x": 1}, oracle="deterministic")
    base.update(over)
    return EvalResult(**base)


class TestEvalResultSchema:
    def test_valid_result(self):
        r = _ok_result()
        assert r.assessed is True
        assert r.to_dict()["source_license"] == "LicenseRef-Agenttic-Proprietary"

    @pytest.mark.parametrize("field", ["source", "source_version", "source_license"])
    def test_provenance_required(self, field):
        with pytest.raises(EvalResultError):
            _ok_result(**{field: ""})
        with pytest.raises(EvalResultError):
            _ok_result(**{field: "   "})

    def test_dimension_must_be_in_controlled_vocab(self):
        with pytest.raises(EvalResultError):
            _ok_result(dimension="made_up_dimension")

    def test_bad_outcome_rejected(self):
        with pytest.raises(EvalResultError):
            _ok_result(outcome="maybe")

    def test_score_out_of_range_rejected(self):
        with pytest.raises(EvalResultError):
            _ok_result(score=1.5)
        with pytest.raises(EvalResultError):
            _ok_result(score=-0.1)

    def test_error_outcome_must_have_none_score(self):
        with pytest.raises(EvalResultError):
            _ok_result(outcome="error", score=0.0)
        # error with None score is fine
        r = _ok_result(outcome="error", score=None)
        assert r.assessed is False

    def test_not_assessed_is_not_assessed_bool(self):
        r = _ok_result(outcome="not_assessed", score=None)
        assert r.assessed is False

    def test_raw_must_be_dict(self):
        with pytest.raises(EvalResultError):
            _ok_result(raw=["not", "a", "dict"])


# --------------------------------------------------------------------------- #
# Dimension mapping tables — every native category maps into the owned vocab.
# --------------------------------------------------------------------------- #


class TestMappingTables:
    def test_agenttic_gen_mapping_targets_are_controlled(self):
        from agenttic.evaluators.agenttic_gen import (
            CRITERION_TO_DIMENSION,
            MAPPING_VERSION,
        )
        assert MAPPING_VERSION
        assert CRITERION_TO_DIMENSION  # non-empty
        for native, dim in CRITERION_TO_DIMENSION.items():
            assert dim in DIMENSIONS, f"{native}->{dim} not in controlled vocab"

    def test_inspect_mapping_targets_are_controlled(self):
        from agenttic.evaluators.inspect_adapter import (
            INSPECT_CATEGORY_TO_DIMENSION,
            INSPECT_MAP_VERSION,
        )
        assert INSPECT_MAP_VERSION
        for native, dim in INSPECT_CATEGORY_TO_DIMENSION.items():
            assert dim in DIMENSIONS, f"{native}->{dim} not in controlled vocab"

    def test_vocab_version_is_pinned(self):
        assert DIMENSION_VOCAB_VERSION == "agenttic-dimensions/v1"


# --------------------------------------------------------------------------- #
# No-crash contract: a case error becomes outcome="error", never a raise.
# --------------------------------------------------------------------------- #


class TestNoCrashContract:
    def test_scoring_failure_becomes_error_rows(self, monkeypatch):
        from agenttic.evaluators.agenttic_gen import AgenttixGenAdapter

        # Force the scorer to blow up on every case — the adapter must NOT
        # propagate it; every probe must come back as an error row.
        import agenttic.scoring.engine as engine

        def boom(*a, **k):
            raise RuntimeError("scorer exploded")

        monkeypatch.setattr(engine, "score_run", boom)

        target = AgentTarget.reference()
        rows = AgenttixGenAdapter().run(target, {"n": 6})
        assert rows, "expected error rows, got none"
        assert all(r.outcome == "error" for r in rows)
        assert all(r.score is None for r in rows)
        assert all(r.dimension in DIMENSIONS for r in rows)

    def test_agent_run_failure_becomes_error_rows(self):
        from agenttic.evaluators.agenttic_gen import AgenttixGenAdapter

        target = AgentTarget.reference()

        class ExplodingAdapter:
            agent_id = target.adapter.agent_id
            visibility = "glass_box"

            def describe(self):
                return target.adapter.describe()

            def config_hash(self):
                return target.adapter.config_hash()

            def run(self, *a, **k):
                raise RuntimeError("agent process died")

        target.adapter = ExplodingAdapter()
        rows = AgenttixGenAdapter().run(target, {"n": 4})
        assert rows and all(r.outcome == "error" for r in rows)


# --------------------------------------------------------------------------- #
# not_assessed when Inspect is unavailable (never an assumed pass).
# --------------------------------------------------------------------------- #


class TestInspectUnavailable:
    def test_capabilities_unavailable_when_sdk_absent(self, monkeypatch):
        import agenttic.evaluators.inspect_adapter as ia

        monkeypatch.setattr(ia, "_INSPECT_AVAILABLE", False)
        adapter = ia.InspectAdapter()
        caps = adapter.capabilities()
        assert caps.available is False
        assert caps.unavailable_reason
        # It still declares the dimensions it *would* cover, for coverage.
        assert set(caps.dimensions) == {"harmful_refusal", "faithfulness"}

    def test_run_returns_empty_and_orchestrator_stamps_not_assessed(self, monkeypatch):
        import agenttic.evaluators.inspect_adapter as ia
        from agenttic.evaluators.orchestrator import run_evaluation

        monkeypatch.setattr(ia, "_INSPECT_AVAILABLE", False)
        target = AgentTarget.reference()
        report = run_evaluation(target, [ia.InspectAdapter()],
                                deployment_mode="self_hosted")
        src = report.per_source[0]
        assert src.ran is False
        assert src.assessed_dimensions == []
        assert all(c["status"] == "not_assessed" for c in report.coverage)
        # No fabricated pass: source has no index.
        assert src.source_index is None


# --------------------------------------------------------------------------- #
# Orchestrator: never a blended number without its breakdown.
# --------------------------------------------------------------------------- #


class TestNoNakedBlend:
    def test_index_only_reachable_with_breakdown(self):
        from agenttic.evaluators.orchestrator import run_evaluation

        target = AgentTarget.reference()
        report = run_evaluation(target, deployment_mode="self_hosted")

        # There is no bare scalar index attribute.
        assert not hasattr(report, "index")
        overall, breakdown = report.index_with_breakdown()
        assert "per_source_index" in breakdown
        assert "coverage_summary" in breakdown
        # Every serialized index carries its decomposition.
        d = report.to_dict()
        assert isinstance(d["index"], dict)
        assert "per_source_index" in d["index"]
        assert "overall" in d["index"]

    def test_headline_decomposes_to_sources(self):
        from agenttic.evaluators.orchestrator import run_evaluation

        target = AgentTarget.reference()
        report = run_evaluation(target, deployment_mode="self_hosted")
        head = report.render_headline()
        assert "agenttic-gen" in head and "inspect_ai" in head
        assert "coverage" in head


# --------------------------------------------------------------------------- #
# License gate.
# --------------------------------------------------------------------------- #


class TestLicenseGate:
    def test_permissive_and_first_party_allowed_everywhere(self):
        from agenttic.evaluators.license_gate import evaluate_gate

        for mode in ("hosted", "self_hosted"):
            assert evaluate_gate(source="i", source_license="MIT",
                                 first_party=False, deployment_mode=mode).allowed
            assert evaluate_gate(source="g",
                                 source_license="LicenseRef-Agenttic-Proprietary",
                                 first_party=True, deployment_mode=mode).allowed

    def test_source_available_and_agpl_refused_when_hosted(self):
        from agenttic.evaluators.license_gate import evaluate_gate

        for lic in ("Elastic-2.0", "SSPL-1.0", "BUSL-1.1", "AGPL-3.0"):
            hosted = evaluate_gate(source="x", source_license=lic,
                                   first_party=False, deployment_mode="hosted")
            self_h = evaluate_gate(source="x", source_license=lic,
                                   first_party=False, deployment_mode="self_hosted")
            assert hosted.decision == "refused"
            assert self_h.decision == "allowed"  # relaxed

    def test_unknown_fails_closed_when_hosted(self):
        from agenttic.evaluators.license_gate import evaluate_gate

        assert evaluate_gate(source="x", source_license="Weird-9",
                             first_party=False,
                             deployment_mode="hosted").decision == "refused"

    def test_gated_out_source_is_not_assessed_and_recorded(self):
        from agenttic.evaluators.base import AgentTarget
        from agenttic.evaluators.orchestrator import run_evaluation

        class AgplAdapter:
            id = "evil-agpl"
            version = "9.9"
            license = "AGPL-3.0"
            first_party = False

            def capabilities(self):
                return Capabilities(available=True, dimensions=("faithfulness",),
                                    oracle="vendor")

            def run(self, target, config=None):  # must never be reached
                raise AssertionError("gated-out adapter must not run")

        target = AgentTarget.reference()
        report = run_evaluation(target, [AgplAdapter()], deployment_mode="hosted")
        src = report.per_source[0]
        assert src.ran is False
        assert all(c["status"] == "not_assessed" for c in report.coverage)
        gate = report.gate_decisions[0]
        assert gate.decision == "refused"
        assert gate.classification == "network_copyleft"


# --------------------------------------------------------------------------- #
# Union passport: round-trip + old-format still verifies + kid unchanged.
# --------------------------------------------------------------------------- #

# The deterministic dev signing-key kid. Derived from the UNTOUCHED _DEV_KEY_SEED;
# this test fails if anyone changes the key material (kids MUST NOT change).
_DEV_KID = "ed25519:0a90ccff6e485447"


class TestUnionPassport:
    def test_dev_signing_kid_is_unchanged(self):
        from agenttic.certification import safety_cert as sc

        kid = sc.key_id(sc.signing_key({}).public_key())
        assert kid == _DEV_KID, "signing kid changed — old certs would break"

    def test_fresh_two_source_passport_round_trips(self):
        from agenttic.evaluators.orchestrator import run_evaluation
        from agenttic.evaluators.passport import build_union_passport

        target = AgentTarget.reference()
        report = run_evaluation(target, deployment_mode="self_hosted")
        pp = build_union_passport(report, cfg={})

        assert pp.public_key_id == _DEV_KID
        assert pp.verify(cfg={}) is True

        # Signed payload carries agent version + every source's provenance.
        payload = pp.signed_payload
        assert payload["agent_version"] == report.agent_version
        srcs = {s["source"] for s in payload["sources"]}
        assert srcs == {"agenttic-gen", "inspect_ai"}
        for s in payload["sources"]:
            assert s["source_version"] and s["source_license"]
        assert payload["coverage"]  # coverage table present
        assert payload["attribution"]  # license attribution present
        assert payload["index"]["per_source"]  # decomposition present

        # Tamper one field → verification fails.
        from agenttic.certification.safety_cert import (
            published_public_keys,
            verify_certificate,
        )
        pub = next(e["public_key_b64"] for e in published_public_keys({})
                   if e["kid"] == pp.public_key_id)
        bad = copy.deepcopy(payload)
        bad["agent_id"] = "someone-else"
        assert verify_certificate(bad, pp.signature, pub) is False

    def test_old_format_dossier_still_verifies_under_new_code(self):
        """A single-source (legacy) certificate payload — built by the UNCHANGED
        build_certificate_payload — still signs and verifies, with the same kid.
        Proves the union additions are backward-compatible."""
        from datetime import datetime, timezone

        from agenttic.certification.safety_cert import (
            build_certificate_payload,
            expiry_from,
            published_public_keys,
            sign_certificate,
            verify_certificate,
        )

        issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old_payload = build_certificate_payload(
            cert_id="cert-legacy-1", agent_id="agent-x", agent_name="Agent X",
            config_hash="deadbeef", scorecard_id="sc-1", suite_id="std-safety",
            suite_version=1,
            dimension_scores={"harmful_refusal_rate": 1.0,
                              "injection_robustness": 1.0},
            issued_at=issued, expires_at=expiry_from(issued))
        assert "sources" not in old_payload  # genuinely the old shape

        signed, sig = sign_certificate(old_payload, cfg={})
        assert signed["public_key_id"] == _DEV_KID
        pub = next(e["public_key_b64"] for e in published_public_keys({})
                   if e["kid"] == signed["public_key_id"])
        assert verify_certificate(signed, sig, pub) is True


# --------------------------------------------------------------------------- #
# Fresh-failure promotion into the versioned regression suite (no fabrication).
# --------------------------------------------------------------------------- #


class TestPromotion:
    def test_promote_fresh_failures_into_regression_suite(self, tmp_path):
        from agenttic.evaluators.agenttic_gen import AgenttixGenAdapter
        from agenttic.registry.sqlite_store import Registry

        reg = Registry(tmp_path / "promote.db")
        target = AgentTarget.reference()
        adapter = AgenttixGenAdapter()
        rows = adapter.run(target, {"n": 12})
        assert any(r.outcome == "fail" for r in rows)  # some breakers exist

        summary = adapter.promote_failures(reg)
        assert summary["regression_suite_id"]  # a regression suite was created
        assert summary["total_cases"] > 0
        # The promoted suite really exists in the registry.
        suite, cases = reg.get_suite(summary["regression_suite_id"])
        assert len(cases) == summary["total_cases"]


# --------------------------------------------------------------------------- #
# Base-import hygiene: `import agenttic` pulls NO evaluator SDK.
# --------------------------------------------------------------------------- #


class TestBaseImportHygiene:
    def test_import_agenttic_does_not_import_inspect_ai(self):
        code = (
            "import sys\n"
            "import agenttic\n"
            "import agenttic.evaluators\n"
            "import agenttic.evaluators.base\n"
            "import agenttic.evaluators.orchestrator\n"
            "import agenttic.evaluators.agenttic_gen\n"
            "assert 'inspect_ai' not in sys.modules, 'inspect_ai leaked at base import'\n"
            "print('clean')\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True)
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout


# --------------------------------------------------------------------------- #
# End-to-end: reference agent → BOTH evaluators → decomposed Index + coverage →
# signed passport → verified.
# --------------------------------------------------------------------------- #


class TestEndToEnd:
    def test_union_of_two_sources_signs_and_verifies(self):
        from agenttic.evaluators.orchestrator import discover_adapters, run_evaluation
        from agenttic.evaluators.passport import build_union_passport

        target = AgentTarget.reference()
        adapters = discover_adapters()
        assert {a.id for a in adapters} >= {"agenttic-gen", "inspect_ai"}

        report = run_evaluation(target, adapters, deployment_mode="self_hosted")

        # Both sources contributed assessed rows (a genuine two-source union).
        by_source = {sr.source: sr for sr in report.per_source}
        assert by_source["agenttic-gen"].assessed_dimensions
        assert by_source["inspect_ai"].assessed_dimensions

        # The union covers dimensions NEITHER source covers alone — the point of
        # a multi-evaluator passport.
        gen_dims = set(by_source["agenttic-gen"].assessed_dimensions)
        insp_dims = set(by_source["inspect_ai"].assessed_dimensions)
        assert insp_dims - gen_dims  # inspect adds harmful_refusal / faithfulness

        overall, breakdown = report.index_with_breakdown()
        assert overall is not None
        assert set(breakdown["per_source_index"]) == {"agenttic-gen", "inspect_ai"}

        pp = build_union_passport(report, cfg={})
        assert pp.verify(cfg={}) is True
