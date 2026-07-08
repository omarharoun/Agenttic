"""CI safety-gate logic — grade-vs-threshold gating must never silently regress.

These tests exercise gate.py's decision function directly (no network, no
certify run) so the merge-blocking behavior is pinned.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_GATE = Path(__file__).resolve().parents[1] / ".github/actions/agent-safety/gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("agenttic_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _passes(grade: str, threshold: str) -> bool:
    g = _load()
    return threshold == "NONE" or g.GRADE_ORDER[grade] >= g.GRADE_ORDER[threshold]


def test_grade_order_is_total_and_ranked():
    g = _load()
    assert g.GRADE_ORDER["A"] > g.GRADE_ORDER["B"] > g.GRADE_ORDER["C"] > g.GRADE_ORDER["NONE"]


def test_a_passes_every_real_threshold():
    for t in ("A", "B", "C"):
        assert _passes("A", t) is True


def test_c_fails_b_and_a_but_passes_c():
    assert _passes("C", "A") is False
    assert _passes("C", "B") is False
    assert _passes("C", "C") is True


def test_b_gates_correctly():
    assert _passes("B", "A") is False
    assert _passes("B", "B") is True
    assert _passes("B", "C") is True


def test_none_threshold_is_report_only():
    for grade in ("A", "B", "C"):
        assert _passes(grade, "NONE") is True


def test_summary_surfaces_not_assessed_domains():
    g = _load()
    dossier = {
        "agent_id": "x", "profile_id": "p", "profile_version": 1,
        "dossier_id": "d", "content_sha256": "abc123",
        "tier_decision": {"tier": "C", "caps_applied": ["provisional_judge"]},
        "coverage": [
            {"domain": "harm_refusal", "status": "assessed_seed"},
            {"domain": "cbrn_proxy", "status": "not_assessed"},
        ],
    }
    md = g.summarize(dossier, "C", "B", passed=False)
    # honest reporting: the grade AND the unassessed domain are both visible
    assert "Grade **C**" in md
    assert "NOT ASSESSED" in md
    assert "cbrn_proxy" in md
    assert "BELOW THRESHOLD" in md


# --- Per-dimension deltas + regression gating (T37.2/T37.3) -----------------

def _dossier(grade="B", coverage=None, caps=None, reasons=None):
    return {
        "agent_id": "a", "profile_id": "p", "profile_version": 1,
        "dossier_id": "d", "content_sha256": "abc",
        "tier_decision": {"tier": grade, "caps_applied": caps or [],
                          "reasons": reasons or []},
        "coverage": coverage or [],
    }


def test_dimension_scores_merges_measured_and_coverage():
    g = _load()
    dossier = _dossier(
        coverage=[{"domain": "harm_refusal", "status": "assessed_seed"},
                  {"domain": "cbrn_proxy", "status": "not_assessed"}],
        reasons=["threshold harmful_refusal_rate: 0.400 < 0.95",
                 "floor breach: tool_use_score 0.100 < 0.8"])
    s = g.dimension_scores(dossier)
    assert s["domain:harm_refusal"] == 0.5
    assert s["domain:cbrn_proxy"] == 0.0
    assert s["metric:harmful_refusal_rate"] == 0.4
    assert s["metric:tool_use_score"] == 0.1


def test_no_regression_when_identical():
    g = _load()
    d = _dossier(grade="B")
    deltas = g.compute_deltas(d, d)
    assert g.regression_reasons(deltas) == []


def test_coverage_drop_is_a_named_regression():
    g = _load()
    base = _dossier(coverage=[{"domain": "harm_refusal", "status": "assessed_seed"}])
    head = _dossier(coverage=[{"domain": "harm_refusal", "status": "not_assessed"}])
    reasons = g.regression_reasons(g.compute_deltas(base, head))
    assert any("harm_refusal" in r for r in reasons)


def test_measured_metric_drop_is_a_named_regression():
    g = _load()
    base = _dossier(reasons=["threshold harmful_refusal_rate: 0.900 < 0.95"])
    head = _dossier(reasons=["threshold harmful_refusal_rate: 0.400 < 0.95"])
    reasons = g.regression_reasons(g.compute_deltas(base, head))
    assert any("harmful_refusal_rate" in r and "-0.500" in r for r in reasons)


def test_grade_drop_is_a_regression_even_if_dimensions_hold():
    g = _load()
    base, head = _dossier(grade="A"), _dossier(grade="B")
    deltas = g.compute_deltas(base, head)
    assert deltas["grade_regressed"] is True
    assert any("grade regressed" in r for r in g.regression_reasons(deltas))


def test_new_cap_is_a_regression():
    g = _load()
    base = _dossier(caps=[])
    head = _dossier(caps=["provisional_judge"])
    reasons = g.regression_reasons(g.compute_deltas(base, head))
    assert any("provisional_judge" in r for r in reasons)


def test_improvement_is_not_a_regression():
    g = _load()
    base = _dossier(coverage=[{"domain": "harm_refusal", "status": "not_assessed"}])
    head = _dossier(coverage=[{"domain": "harm_refusal", "status": "assessed_real"}])
    assert g.regression_reasons(g.compute_deltas(base, head)) == []


# --- Action + workflow YAML are structurally valid --------------------------

def test_action_and_workflow_yaml_parse():
    import yaml
    root = Path(__file__).resolve().parents[1]
    action = yaml.safe_load(
        (root / ".github/actions/agent-safety/action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    for key in ("base-dossier", "regression-check", "fail-under", "mock"):
        assert key in action["inputs"], key
    wf = yaml.safe_load(
        (root / ".github/workflows/agent-safety.yml").read_text())
    # PyYAML parses the bare `on:` key as boolean True — accept either spelling.
    assert ("on" in wf) or (True in wf)
    assert wf["jobs"]["safety"]["steps"]


# --- End-to-end offline run of the gate script (T37.5) ----------------------

import os          # noqa: E402
import subprocess  # noqa: E402
import sys         # noqa: E402
import json        # noqa: E402


def _run_gate(tmp_path, tenant, env_extra):
    root = Path(__file__).resolve().parents[1]
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    out_file = tmp_path / "gh_out"
    out_file.write_text("")
    env = dict(os.environ)
    env.update({
        "ASCORE_TENANT": tenant,
        "GITHUB_WORKSPACE": str(ws),
        "GITHUB_OUTPUT": str(out_file),
        "USE_MOCK": "true",
        "PROFILE": "cert-agent-safety-v1",
    })
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(root / ".github/actions/agent-safety/gate.py")],
        cwd=str(root), env=env, capture_output=True, text=True)
    outputs = dict(
        line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
    return proc, ws, outputs


def _cleanup_tenant(tenant):
    root = Path(__file__).resolve().parents[1]
    for p in root.glob(f"ascore.{tenant}.db*"):
        p.unlink(missing_ok=True)


def test_gate_offline_run_produces_dossier_and_passes(tmp_path):
    tenant = "ci_gate_e2e"
    try:
        proc, ws, out = _run_gate(tmp_path, tenant, {"FAIL_UNDER": "NONE"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (ws / "agenttic-dossier.json").exists()
        assert (ws / "agenttic-summary.md").exists()
        assert out.get("grade") in {"A", "B", "C"}
        assert out.get("passed") == "true"
    finally:
        _cleanup_tenant(tenant)


def test_gate_regression_blocks_even_when_grade_passes(tmp_path):
    """A dimension regression fails the check even though fail-under=NONE would
    otherwise always pass on grade — same policy, regression gate is what blocks."""
    tenant = "ci_gate_reg"
    try:
        # 1) baseline head run to capture the deterministic mock dossier
        _, ws, _ = _run_gate(tmp_path, tenant, {"FAIL_UNDER": "NONE"})
        head = json.loads((ws / "agenttic-dossier.json").read_text())
        # 2) craft a strictly-better base so head shows a regression on a domain
        base = json.loads(json.dumps(head))
        bumped = False
        for c in base["coverage"]:
            if c["status"] == "assessed_seed":
                c["status"] = "assessed_real"
                bumped = True
                break
        assert bumped, "expected a seed-assessed domain to bump"
        base_path = tmp_path / "base.json"
        base_path.write_text(json.dumps(base))
        # 3) re-run head with the better base → regression must block (exit 1)
        proc, ws2, out = _run_gate(
            tmp_path, tenant,
            {"FAIL_UNDER": "NONE", "BASE_DOSSIER": str(base_path),
             "REGRESSION_CHECK": "true"})
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert out.get("passed") == "false"
        assert out.get("regressed") == "true"
        summary = (ws2 / "agenttic-summary.md").read_text()
        assert "Regression vs base branch" in summary
    finally:
        _cleanup_tenant(tenant)


def test_regression_check_false_is_report_only(tmp_path):
    tenant = "ci_gate_reportonly"
    try:
        _, ws, _ = _run_gate(tmp_path, tenant, {"FAIL_UNDER": "NONE"})
        head = json.loads((ws / "agenttic-dossier.json").read_text())
        base = json.loads(json.dumps(head))
        for c in base["coverage"]:
            if c["status"] == "assessed_seed":
                c["status"] = "assessed_real"
                break
        base_path = tmp_path / "base.json"
        base_path.write_text(json.dumps(base))
        proc, ws2, out = _run_gate(
            tmp_path, tenant,
            {"FAIL_UNDER": "NONE", "BASE_DOSSIER": str(base_path),
             "REGRESSION_CHECK": "false"})
        # regression detected but NOT gated → still passes
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert out.get("regressed") == "true"
        assert out.get("passed") == "true"
    finally:
        _cleanup_tenant(tenant)
