"""CLI surface for SPEC-12 Steps 57 & 58 — `certify-memory` and `catalog-check`.

Both are CI-shaped commands: they print findings a human can act on and exit
non-zero when the subject fails, so they can gate a pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from agenttic.certification.attest import build_manifest, sign_manifest
from agenttic.certification.catalog import Catalog, CatalogEntry
from agenttic.cli import app

runner = CliRunner()
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


# ---- certify-memory --------------------------------------------------------- #

def test_certify_memory_reference_passes_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    out = tmp_path / "memory.json"
    r = runner.invoke(app, ["certify-memory", "--reference", "--capacity", "32",
                            "--out", str(out)])
    assert r.exit_code == 0, r.output
    doc = json.loads(out.read_text())
    assert doc["passed"] is True
    assert doc["score"] == 1.0


def test_certify_memory_fails_a_defective_store_and_exits_nonzero(tmp_path):
    r = runner.invoke(app, [
        "certify-memory",
        "--store", "tests.fixtures.memory_store_fixture:LeakyMemoryStore",
        "--capacity", "16", "--name", "leaky"])
    assert r.exit_code == 1
    # the output NAMES the defects rather than printing a bare score
    assert "principal_isolation" in r.output
    assert "blast radius" in r.output


def test_certify_memory_requires_a_subject():
    r = runner.invoke(app, ["certify-memory"])
    assert r.exit_code != 0
    assert "--store" in r.output


def test_certify_memory_writes_a_signed_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    att = tmp_path / "memory-manifest.json"
    r = runner.invoke(app, ["certify-memory", "--reference", "--capacity", "32",
                            "--attest", str(att)])
    assert r.exit_code == 0, r.output
    doc = json.loads(att.read_text())
    assert doc["manifest"]["subject"]["agent_id"] == "memory:reference"
    assert doc["signature"] and doc["kid"].startswith("ed25519:")


# ---- catalog-check ---------------------------------------------------------- #

def _catalog_file(tmp_path, monkeypatch) -> tuple[str, str]:
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    signed = sign_manifest(build_manifest(
        manifest_id="m-agent", agent_id="triage", agent_config_hash="cfg-triage",
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard={"score": 0.9}, issued_at=NOW))
    cat = Catalog(owner="acme")
    cat.register(CatalogEntry(subject_id="payments-mcp", kind="mcp_server",
                              version="3.1", recorded_at=NOW))
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="1.0",
                              depends_on=("mcp_server:payments-mcp@3.1",),
                              recorded_at=NOW))
    cat.promote("agent:triage@1.0", approver="dana", rationale="ok",
                signed=signed, now=NOW)

    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(cat.export(now=NOW)), encoding="utf-8")
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    (mdir / "m-agent.json").write_text(signed.model_dump_json(), encoding="utf-8")
    return str(cat_path), str(mdir)


def test_catalog_check_reports_an_uncertified_dependency_and_exits_nonzero(
        tmp_path, monkeypatch):
    """The agent is promoted; the MCP server it depends on is not."""
    cat_path, mdir = _catalog_file(tmp_path, monkeypatch)
    r = runner.invoke(app, ["catalog-check", cat_path, "--manifests", mdir])
    assert r.exit_code == 1
    assert "uncertified_dependency" in r.output
    assert "error" in r.output.lower()


def test_catalog_check_is_clean_when_the_whole_chain_is_promoted(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    mk = lambda mid, aid: sign_manifest(build_manifest(   # noqa: E731
        manifest_id=mid, agent_id=aid, agent_config_hash=f"cfg-{aid}",
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard={"score": 0.9}, issued_at=NOW))
    agent, server = mk("m-agent", "triage"), mk("m-mcp", "payments-mcp")

    cat = Catalog(owner="acme")
    cat.register(CatalogEntry(subject_id="payments-mcp", kind="mcp_server",
                              version="3.1", recorded_at=NOW))
    cat.register(CatalogEntry(subject_id="triage", kind="agent", version="1.0",
                              depends_on=("mcp_server:payments-mcp@3.1",),
                              recorded_at=NOW))
    cat.promote("mcp_server:payments-mcp@3.1", approver="dana",
                rationale="battery 1.00", signed=server, now=NOW)
    cat.promote("agent:triage@1.0", approver="dana", rationale="ok",
                signed=agent, now=NOW)

    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(cat.export(now=NOW)), encoding="utf-8")
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    for sm in (agent, server):
        (mdir / f"{sm.manifest.manifest_id}.json").write_text(
            sm.model_dump_json(), encoding="utf-8")

    r = runner.invoke(app, ["catalog-check", str(cat_path), "--manifests", str(mdir)])
    assert r.exit_code == 0, r.output
    assert "conformant" in r.output


def test_catalog_check_counts_entries_by_status(tmp_path, monkeypatch):
    cat_path, mdir = _catalog_file(tmp_path, monkeypatch)
    r = runner.invoke(app, ["catalog-check", cat_path, "--manifests", mdir])
    assert "1 candidate" in r.output
    assert "1 promoted" in r.output
