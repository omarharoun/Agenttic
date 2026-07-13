"""T12.6 — profile resolution + coverage honesty (SPEC-2 M4)."""

from __future__ import annotations

import tempfile

import pytest
from typer.testing import CliRunner

from agenttic.certification.coverage import coverage, domain_coverage
from agenttic.certification.profiles import (
    ProfileError,
    build_profile,
    load_profile,
    seed_profile,
)
from agenttic.config import load_config
from agenttic.metrics.standard_suites import seed_standard_suites
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.testcase import TestCase, TestSuite
from pathlib import Path


@pytest.fixture()
def cfg():
    return load_config("config.yaml")


@pytest.fixture()
def reg():
    with tempfile.TemporaryDirectory() as tmp:
        r = Registry(db_path=f"{tmp}/t.db")
        seed_standard_suites(r)
        yield r


def test_byte_identical_reresolution(cfg, reg):
    p1 = build_profile(cfg, reg, "cert-agent-safety-v1")
    p2 = build_profile(cfg, reg, "cert-agent-safety-v1")
    assert p1.model_dump_json() == p2.model_dump_json()


def test_seeded_profile_reloads_pinned(cfg, reg):
    seed_profile(cfg, reg)
    loaded = load_profile(cfg, reg, "cert-agent-safety-v1")
    # re-resolution against the registry succeeds and pins are stable
    assert {(r.suite_id, r.version) for r in loaded.suite_refs}


def test_cbrn_proxy_not_assessed_on_defaults(cfg, reg):
    p = build_profile(cfg, reg, "cert-agent-safety-v1")
    cov = {c.domain: c for c in coverage(reg, p)}
    assert cov["cbrn_proxy"].status == "not_assessed"
    assert cov["cbrn_proxy"].evidence_refs == []
    assert "NOT ASSESSED" in (cov["cbrn_proxy"].note or "")


def test_seed_suites_never_assessed_real(cfg, reg):
    p = build_profile(cfg, reg, "cert-agent-safety-v1")
    for c in coverage(reg, p):
        # only real ingested datasets may be assessed_real; seed-only workspace
        # can never report it
        assert c.status != "assessed_real"


def test_ingest_full_split_flips_seed_to_real(cfg, reg):
    # Before ingest: tool_use is seed-only.
    assert domain_coverage(reg, "tool_use").status == "assessed_seed"
    # Ingesting the FULL real public split (dataset_provenance="real") promotes it.
    suite = TestSuite(suite_id="bfcl-simple-v3", version=1, approved=True,
                      business_context="real dataset", dataset_provenance="real")
    case = TestCase(test_id="bfcl-simple-v3-1", suite_id="bfcl-simple-v3",
                    version=1, task_description="d", rubric_id="r")
    reg.save_suite(suite, [case])
    # After a full-split ingest: tool_use promotes to assessed_real.
    cov = domain_coverage(reg, "tool_use")
    assert cov.status == "assessed_real"
    assert "suite:bfcl-simple-v3@v1" in cov.evidence_refs


def test_sample_ingest_stays_seed_not_real(cfg, reg):
    # Corrected honest semantics (Hard Rule 9): a suite ingested from the
    # vendored .sample split (dataset_provenance="seed") must NOT lift the domain
    # to assessed_real — sample data can never read as a real measurement.
    assert domain_coverage(reg, "tool_use").status == "assessed_seed"
    suite = TestSuite(suite_id="bfcl-simple-v3", version=1, approved=True,
                      business_context="seed sample", dataset_provenance="seed")
    case = TestCase(test_id="bfcl-simple-v3-1", suite_id="bfcl-simple-v3",
                    version=1, task_description="d", rubric_id="r")
    reg.save_suite(suite, [case])
    cov = domain_coverage(reg, "tool_use")
    assert cov.status == "assessed_seed"
    # unknown provenance (old payloads / no flag) is also treated as seed, never real
    suite2 = TestSuite(suite_id="tau-bench-v1", version=1, approved=True,
                       business_context="no provenance flag")
    reg.save_suite(suite2, [TestCase(test_id="tau-bench-v1-1", suite_id="tau-bench-v1",
                                     version=1, task_description="d", rubric_id="r")])
    assert domain_coverage(reg, "tool_use").status == "assessed_seed"


def test_unknown_profile_fails_loud(cfg, reg):
    with pytest.raises(ProfileError):
        build_profile(cfg, reg, "no-such-profile")


def test_unapproved_suite_not_pinned(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        r = Registry(db_path=f"{tmp}/t.db")
        # an unapproved tool_use dataset suite
        suite = TestSuite(suite_id="tau-bench-v1", version=1, approved=False,
                          business_context="unapproved")
        case = TestCase(test_id="tau-bench-v1-1", suite_id="tau-bench-v1",
                        version=1, task_description="d", rubric_id="r")
        r.save_suite(suite, [case])
        cfg = load_config("config.yaml")
        p = build_profile(cfg, r, "cert-agent-safety-v1")
        assert all(ref.suite_id != "tau-bench-v1" for ref in p.suite_refs)


def test_cli_profiles_show_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("ASCORE_TENANT", "cliproftest")
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    runner = CliRunner()
    res = runner.invoke(app_ref(), ["profiles", "show", "cert-agent-safety-v1"])
    assert res.exit_code == 0
    out = res.stdout
    assert "cert-agent-safety-v1" in out
    assert "NOT ASSESSED" in out
    assert "cbrn_proxy" in out
    # cleanup the per-tenant db this created
    import os
    for p in ("agenttic.cliproftest.db",):
        if os.path.exists(p):
            os.remove(p)


def app_ref():
    from agenttic.cli import app
    return app
