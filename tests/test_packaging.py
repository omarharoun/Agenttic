"""Deployment artifacts exist and are valid (prod config loads, compose/CI parse)."""

from pathlib import Path

import yaml

from ascore.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_deploy_files_present():
    for f in ("Dockerfile", ".dockerignore", "docker-compose.yml",
              "config.prod.yaml", ".env.example",
              ".github/workflows/ci.yml",
              "scripts/backup.sh", "scripts/restore.sh",
              "docs/OPERATIONS.md"):
        assert (ROOT / f).is_file(), f


def test_prod_config_loads():
    cfg = load_config(ROOT / "config.prod.yaml")
    assert cfg["auth"]["required"] is True            # fail closed in prod
    assert cfg["models"]["judge_strong"] != cfg["models"]["agent_default"]
    assert cfg["budget"]["max_run_cost_usd"] > 0


def test_compose_is_valid_yaml_with_profiles():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "app" in compose["services"]
    assert compose["services"]["postgres"]["profiles"] == ["postgres"]
    assert compose["services"]["redis"]["profiles"] == ["redis"]
    # Caddy reverse proxy is opt-in (profile-gated) and ships with a Caddyfile
    assert compose["services"]["caddy"]["profiles"] == ["caddy"]
    assert (ROOT / "Caddyfile").is_file()


def test_ci_workflow_is_valid_yaml():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML parses the `on:` key as boolean True — accept either form
    assert ("jobs" in ci) and {"backend", "frontend", "docker"} <= set(ci["jobs"])


def test_dockerfile_is_nonroot_and_multistage():
    df = (ROOT / "Dockerfile").read_text()
    assert "FROM node:" in df and "FROM python:" in df   # multi-stage
    assert "USER appuser" in df                           # non-root
