"""Multi-tenancy: each tenant is an isolated workspace (own DB). Data created
under one tenant is invisible to another; the default tenant maps to the
configured DB."""

from pathlib import Path

from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import Workspaces, create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth:
  token: "adm"
  tokens:
    acme_tok: {role: operator, tenant: acme}
    globex_tok: {role: operator, tenant: globex}
"""


def _app(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "agenttic.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return create_app(str(cfg), registry=Registry(tmp_path / "agenttic.db"))


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def test_catalog_is_isolated_per_tenant(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        # acme registers an agent
        assert c.post("/api/agents/catalog", headers=_hdr("acme_tok"),
                      json={"agent_id": "acme-bot", "variant": "reference"}
                      ).status_code == 200
        # globex sees an empty catalog — full isolation
        globex = c.get("/api/agents/catalog", headers=_hdr("globex_tok")).json()
        assert globex["agents"] == []
        acme = c.get("/api/agents/catalog", headers=_hdr("acme_tok")).json()
        assert [a["agent_id"] for a in acme["agents"]] == ["acme-bot"]
        # admin (default tenant) also doesn't see acme's agent
        admin = c.get("/api/agents/catalog", headers=_hdr("adm")).json()
        assert admin["agents"] == []


def test_tenant_dbs_are_separate_files(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/agents/catalog", headers=_hdr("acme_tok"),
               json={"agent_id": "a", "variant": "reference"})
    # acme's data lives in a sibling DB, not the default one. The base DB here
    # is configured as agenttic.db (see CONFIG), so the per-tenant sibling is
    # derived as agenttic.acme.db — an on-disk literal, NOT an import path.
    assert (tmp_path / "agenttic.acme.db").exists()


def test_workspace_normalize_rejects_bad_names():
    assert Workspaces.normalize("good-name_1") == "good-name_1"
    assert Workspaces.normalize("../etc") == "default"
    assert Workspaces.normalize("") == "default"
    assert Workspaces.normalize(None) == "default"
