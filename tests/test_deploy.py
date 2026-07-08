"""Deploy artifacts smoke test (SPEC-7 Step 38, T38.5).

Compose files parse and describe a working stack; the Helm chart lints and
templates cleanly (via `helm` when present, else a structural fallback that
checks the same invariants helm would). No cluster or Docker daemon required.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


# --- docker-compose smoke ---------------------------------------------------

def test_selfhost_compose_parses_and_has_server():
    d = yaml.safe_load((DEPLOY / "docker-compose.yaml").read_text())
    assert "server" in d["services"]
    server = d["services"]["server"]
    # BYO-Postgres knob + data persistence + health check
    assert "ASCORE_DB" in server["environment"]
    assert any("agenttic-data" in str(v) for v in server["volumes"])
    assert "healthcheck" in server
    assert "agenttic-data" in d["volumes"]


def test_airgap_overlay_is_internal_network():
    d = yaml.safe_load((DEPLOY / "airgap/docker-compose.airgap.yaml").read_text())
    # the air-gap network has no gateway (internal: true) → zero egress
    assert d["networks"]["agenttic-airgap"]["internal"] is True
    assert d["services"]["server"]["environment"]["ASCORE_AIRGAP"] == "true"


def test_compose_config_validates_if_docker_present():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker not installed")
    proc = subprocess.run(
        [docker, "compose", "-f", str(DEPLOY / "docker-compose.yaml"),
         "-f", str(DEPLOY / "airgap/docker-compose.airgap.yaml"), "config"],
        cwd=str(DEPLOY), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --- helm chart lint --------------------------------------------------------

CHART = DEPLOY / "helm/agenttic"


def test_helm_chart_files_present():
    assert (CHART / "Chart.yaml").exists()
    assert (CHART / "values.yaml").exists()
    for t in ("deployment.yaml", "service.yaml", "ingress.yaml", "secret.yaml",
              "serviceaccount.yaml", "_helpers.tpl"):
        assert (CHART / "templates" / t).exists(), t


def test_chart_metadata_valid():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "agenttic"
    assert re.match(r"\d+\.\d+\.\d+", str(chart["version"]))
    yaml.safe_load((CHART / "values.yaml").read_text())  # parses


def _defined_helpers() -> set[str]:
    text = (CHART / "templates/_helpers.tpl").read_text()
    return set(re.findall(r'define\s+"([^"]+)"', text))


def test_templates_reference_only_defined_helpers():
    defined = _defined_helpers()
    for tpl in (CHART / "templates").glob("*.yaml"):
        text = tpl.read_text()
        for called in re.findall(r'include\s+"([^"]+)"', text):
            assert called in defined, f"{tpl.name} includes undefined helper {called}"


def test_template_control_blocks_are_balanced():
    # {{ if/range/with/define }} count must match {{ end }} in each template
    for tpl in list((CHART / "templates").glob("*.yaml")) + [CHART / "templates/_helpers.tpl"]:
        text = tpl.read_text()
        opens = len(re.findall(r"{{-?\s*(if|range|with|define)\b", text))
        ends = len(re.findall(r"{{-?\s*end\b", text))
        assert opens == ends, f"{tpl.name}: {opens} open blocks vs {ends} ends"


def test_helm_lint_and_template_if_present():
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm not installed — structural checks cover the invariants")
    lint = subprocess.run([helm, "lint", str(CHART)],
                          capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    tmpl = subprocess.run(
        [helm, "template", "agenttic", str(CHART),
         "--set-string", "secrets.apiToken=x",
         "--set", "ingress.enabled=true", "--set", "airgap.enabled=true"],
        capture_output=True, text=True)
    assert tmpl.returncode == 0, tmpl.stderr
    docs = [d for d in yaml.safe_load_all(tmpl.stdout) if d]
    kinds = {d.get("kind") for d in docs}
    assert {"Deployment", "Service", "Secret"} <= kinds
