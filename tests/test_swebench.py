"""SWE-bench Verified ingestion: parses REAL Verified records (issue + gold patch
+ FAIL_TO_PASS / PASS_TO_PASS) into canonical testcases, scores them through the
OFFLINE PROXY checks (patch produced / gold files localized), labels the suite as
the real dataset, and surfaces the honest caveat that official resolve-rate needs
the Docker execution harness (NOT computed here). SWE-bench is MIT-licensed and
public, so the vendored sample is genuine upstream content."""

import uuid
from datetime import datetime, timezone

import pytest

from ascore.metrics.canonical_checks import (
    swebench_patch_generated, swebench_patch_targets_gold_files)
from ascore.metrics.datasets import dataset_infos, get_adapter
from ascore.metrics.datasets.swebench import SWEBenchAdapter, patched_files
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.metrics.swebench_resolve import (
    ExecutionHarnessRequired, ResolveInstance, harness_available, resolve_rate)
from ascore.registry.sqlite_store import Registry
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)

# A minimal real-style unified diff touching one file.
_PATCH_ONE = (
    "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n"
    "--- a/astropy/modeling/separable.py\n"
    "+++ b/astropy/modeling/separable.py\n"
    "@@ -100,7 +100,7 @@\n"
    "-    bad line\n"
    "+    good line\n")


def _trace(tid, *, output):
    span = Span(span_id="f", kind="final_output", name="final_output",
                start_time=NOW, end_time=NOW)
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output=output,
                 spans=[span], schema_version=SCHEMA_VERSION)


# -- diff parsing ----------------------------------------------------------

def test_patched_files_extracts_paths():
    assert patched_files(_PATCH_ONE) == {"astropy/modeling/separable.py"}
    # empty / non-diff -> empty set
    assert patched_files("") == set()
    assert patched_files("no diff here, just prose") == set()
    # a new-file diff (+++ b/path with /dev/null source) is still localized
    newf = ("diff --git a/x/new.py b/x/new.py\n--- /dev/null\n+++ b/x/new.py\n"
            "@@ -0,0 +1 @@\n+print(1)\n")
    assert patched_files(newf) == {"x/new.py"}


# -- parsing real records into valid testcases -----------------------------

def test_parses_real_records_into_valid_testcases():
    cases = SWEBenchAdapter().load_records()       # vendored REAL sample
    assert len(cases) >= 5
    c = cases[0]
    assert c.suite_id == "swebench-verified-v1"
    assert c.rubric_id == "swebench-verified-v1-rubric"
    assert c.expected["source"] == "SWE-bench Verified"
    assert c.input["problem_statement"]            # issue text preserved
    assert c.input["base_commit"]                  # repo state preserved
    assert c.expected["gold_files"]                # gold-patch files localized
    assert isinstance(c.expected["fail_to_pass"], list) and c.expected["fail_to_pass"]
    assert isinstance(c.expected["pass_to_pass"], list)
    # honesty flags echoed onto every case
    assert c.expected["requires_execution_harness"] is True
    assert c.expected["scoring"] == "offline_proxy_not_official_resolve_rate"
    assert "swebench" in c.tags and "code_agent" in c.tags
    # sample spans several real repos (genuine upstream content, not a placeholder)
    repos = {c.input["repo"] for c in cases}
    assert len(repos) >= 5
    assert any("/" in r for r in repos)


def test_sample_comment_header_skipped():
    # the JSONL's leading _comment object must not become a test case
    cases = SWEBenchAdapter().load_records()
    assert all(c.input.get("instance_id") for c in cases)


# -- PROXY checks: patch-bearing vs empty output ---------------------------

def test_patch_generated_proxy():
    cases = SWEBenchAdapter().load_records()
    c = cases[0]
    # a patch-bearing output passes the patch-rate proxy; an empty one fails
    assert swebench_patch_generated(_trace(c.test_id, output=_PATCH_ONE), c) == 1.0
    assert swebench_patch_generated(_trace(c.test_id, output=""), c) == 0.0
    assert swebench_patch_generated(_trace(c.test_id, output="I couldn't fix it"), c) == 0.0


def test_patch_targets_gold_files_proxy_is_fractional():
    cases = SWEBenchAdapter().load_records()
    # pick a single-gold-file case for a clean 1.0 / 0.0 contrast
    c = next(c for c in cases if len(c.expected["gold_files"]) == 1)
    gold_file = c.expected["gold_files"][0]
    hit = (f"diff --git a/{gold_file} b/{gold_file}\n--- a/{gold_file}\n"
           f"+++ b/{gold_file}\n@@ -1 +1 @@\n-x\n+y\n")
    miss = ("diff --git a/totally/unrelated.py b/totally/unrelated.py\n"
            "--- a/totally/unrelated.py\n+++ b/totally/unrelated.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert swebench_patch_targets_gold_files(_trace(c.test_id, output=hit), c) == 1.0
    assert swebench_patch_targets_gold_files(_trace(c.test_id, output=miss), c) == 0.0
    assert swebench_patch_targets_gold_files(_trace(c.test_id, output=""), c) == 0.0

    # fractional: a multi-gold-file case scored by a patch that hits only some
    multi = next((c for c in cases if len(c.expected["gold_files"]) >= 2), None)
    if multi is not None:
        one = multi.expected["gold_files"][0]
        partial = (f"diff --git a/{one} b/{one}\n--- a/{one}\n+++ b/{one}\n"
                   f"@@ -1 +1 @@\n-x\n+y\n")
        score = swebench_patch_targets_gold_files(_trace(multi.test_id, output=partial), multi)
        assert 0.0 < score < 1.0


# -- end-to-end scoring through the suite/rubric ---------------------------

def test_case_scores_through_proxy_rubric(tmp_path):
    reg = Registry(tmp_path / "swe.db")
    summary = SWEBenchAdapter().ingest(reg)
    assert summary["ingested"] >= 5
    suite, cases = reg.get_suite("swebench-verified-v1")
    rubric = reg.get_rubric("swebench-verified-v1-rubric")
    c = next(c for c in cases if len(c.expected["gold_files"]) == 1)
    gold_file = c.expected["gold_files"][0]
    good = _trace(c.test_id, output=(
        f"diff --git a/{gold_file} b/{gold_file}\n--- a/{gold_file}\n"
        f"+++ b/{gold_file}\n@@ -1 +1 @@\n-x\n+y\n"))
    rs = score_run(good, c, rubric)
    assert rs.scoring_error is None and rs.passed is True
    bad = _trace(c.test_id, output="sorry, no patch")
    rs2 = score_run(bad, c, rubric)
    assert rs2.scoring_error is None and rs2.passed is False


# -- suite labeled real dataset + execution-harness caveat surfaced --------

def test_suite_labeled_real_dataset_and_canonical(tmp_path):
    reg = Registry(tmp_path / "swe.db")
    SWEBenchAdapter().ingest(reg)
    suite, _ = reg.get_suite("swebench-verified-v1")
    assert suite.approved is True
    assert "SWE-bench Verified (real dataset)" in suite.business_context
    assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
    assert "REAL public dataset" not in suite.business_context
    assert "swebench-verified-v1" in DATASET_SUITE_IDS
    assert "swebench-verified-v1" in canonical_suite_ids(reg)   # feeds the index


def test_execution_harness_caveat_surfaced():
    # the honest flag distinguishing offline proxy from official resolve-rate
    assert SWEBenchAdapter.info.requires_execution_harness is True
    assert SWEBenchAdapter.info.license == "MIT"
    assert SWEBenchAdapter.info.gated is False                  # public, not gated
    infos = {i.dataset_id: i for i in dataset_infos()}
    assert "swebench" in infos
    assert infos["swebench"].requires_execution_harness is True
    assert isinstance(get_adapter("swebench"), SWEBenchAdapter)


# -- official resolve-rate interface is execution-gated --------------------

def test_resolve_rate_requires_execution_harness(monkeypatch):
    monkeypatch.delenv("ASCORE_SWEBENCH_HARNESS", raising=False)
    assert harness_available() is False          # no infra configured here
    inst = ResolveInstance(instance_id="x__y-1", repo="x/y", base_commit="abc",
                           candidate_patch=_PATCH_ONE,
                           fail_to_pass=["t::a"], pass_to_pass=["t::b"])
    # we never substitute the proxy for the real metric: with no harness it
    # raises, loudly — an honest gate, not a fabricated number.
    with pytest.raises(ExecutionHarnessRequired):
        resolve_rate([inst])


def test_resolve_rate_uses_a_supplied_harness():
    # The path is REAL, not stubbed: a conforming harness is used and the
    # official aggregation (resolved / total) is computed.
    inst_a = ResolveInstance(instance_id="a", repo="x/y", base_commit="c",
                             candidate_patch=_PATCH_ONE, fail_to_pass=["t::a"],
                             pass_to_pass=["t::b"])
    inst_b = ResolveInstance(instance_id="b", repo="x/y", base_commit="c",
                             candidate_patch=_PATCH_ONE, fail_to_pass=["t::a"],
                             pass_to_pass=["t::b"])

    class OneResolvedHarness:
        def resolved(self, instance):
            return instance.instance_id == "a"

    assert resolve_rate([inst_a, inst_b], harness=OneResolvedHarness()) == 0.5


def test_harness_available_detects_configuration(monkeypatch):
    # Env-gated, not hard-coded: a docker harness needs docker + the swebench
    # package (absent here, so still False); a custom dotted path counts as
    # "configured".
    monkeypatch.setenv("ASCORE_SWEBENCH_HARNESS", "docker")
    from ascore.metrics import swebench_resolve as swe
    assert swe.harness_available() == (swe._docker_present()
                                       and swe._swebench_present())
    monkeypatch.setenv("ASCORE_SWEBENCH_HARNESS", "mypkg.mod:make")
    assert swe.harness_available() is True
    monkeypatch.setenv("ASCORE_SWEBENCH_HARNESS", "off")
    assert swe.harness_available() is False


def test_reproduction_status_is_honest(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from ascore.metrics.reproduction import reproduction_report
    rep = reproduction_report()
    by_wedge = {w["wedge"]: w for w in rep["wedges"]}
    # the code wedge is still a proxy (needs the Docker resolve-rate harness)
    assert by_wedge["code"]["status"] == "proxy"
    assert by_wedge["code"]["official_metric"] == "resolve-rate"
    # BFCL has a RECORDED reproduction (published value inside our Wilson
    # interval), but it is not re-measured live here: reproduced (live) False,
    # recorded True.
    assert rep["any_reproduced"] is False
    assert rep["any_reproduced_recorded"] is True
    assert by_wedge["tool_calling"]["status"] == "reproduced_recorded"
    assert by_wedge["tool_calling"]["reproduced"] is False
    assert by_wedge["tool_calling"]["recorded"] is True
    mr = by_wedge["tool_calling"]["detail"]["model_reproduction"]
    assert mr["published_within_interval"] is True
    assert mr["wilson_low"] <= mr["published_accuracy"] <= mr["wilson_high"]
    # every wedge states what real reproduction requires
    assert all(w["requires"] for w in rep["wedges"])


def test_public_reproduction_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from ascore.registry.sqlite_store import Registry
    from ascore.server.app import create_app

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1,"
        " max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path}/a.db, review_dir: {tmp_path}/r,"
        f" calibration_dir: {tmp_path}/c}}\n"
        "auth: {token: adm, required: true, allow_signup: true,"
        " signup_role: operator, session_secret: testsecret}\n")
    reg = Registry(tmp_path / "a.db")
    with TestClient(create_app(str(cfg_path), registry=reg)) as c:
        r = c.get("/api/public/reproduction")    # no auth
        assert r.status_code == 200
        body = r.json()
        assert body["any_reproduced"] is False            # nothing reproduced LIVE
        assert body["any_reproduced_recorded"] is True    # BFCL recorded run
        assert any(w["wedge"] == "code" for w in body["wedges"])
        tc = {w["wedge"]: w for w in body["wedges"]}["tool_calling"]
        assert tc["reproduced"] is False and tc["recorded"] is True


# -- idempotent ingest -----------------------------------------------------

def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "swe.db")
    first = SWEBenchAdapter().ingest(reg)
    assert first["already_present"] is False
    again = SWEBenchAdapter().ingest(reg)
    assert again["already_present"] is True
