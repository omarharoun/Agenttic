"""The closure target is a threshold, and thresholds live in config.yaml.

`config.yaml:1` says so — "All model names, thresholds, sample rates live here
(Hard Rule 7)" — while the bar the sign-off gates on was written into the code
as the same `0.95` literal in five modules. Five copies is five chances to
disagree, and the one file an operator would edit was not among them.

These tests pin that the value comes from config, that an explicit argument
still wins so no caller broke, and that a missing config is a documented
fallback rather than a crash: a coverage model must be constructible inside a
process that has never seen this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from agenttic.coverage.models.baseline import baseline_model
from agenttic.coverage.models.conversational_transactional import seed_model
from agenttic.coverage.targets import (
    DEFAULT_CLOSURE_TARGET, _from_config_file, closure_target)

REPO = Path(__file__).resolve().parents[2]


class TestTheTargetComesFromConfig:
    def test_a_configured_target_is_used(self):
        cfg = {"coverage": {"closure_target": 0.5}}
        assert closure_target(cfg) == 0.5
        assert baseline_model(cfg=cfg).closure_target == 0.5
        assert seed_model(cfg=cfg).closure_target == 0.5

    def test_an_explicit_argument_still_wins(self):
        """Existing callers pass the value directly; none of them broke."""
        cfg = {"coverage": {"closure_target": 0.5}}
        assert baseline_model(closure_target=0.8, cfg=cfg).closure_target == 0.8
        assert baseline_model(2, 0.8).closure_target == 0.8      # positional

    def test_both_shipped_configs_declare_it(self):
        for name in ("config.yaml", "config.prod.yaml"):
            cfg = yaml.safe_load((REPO / name).read_text())
            assert cfg["coverage"]["closure_target"] == 0.95, name

    def test_the_config_file_is_read_from_disk(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("coverage:\n  closure_target: 0.75\n")
        assert _from_config_file(str(p)) == 0.75


class TestNoConfigIsGuessedFromTheCwd:
    """`closure_target()` used to fall back to reading `config.DEFAULT_PATH` — the
    relative literal `Path("config.yaml")` — resolved against the process CWD.
    That answers a different question from "what config did this process load":
    an operator running `--config other.yaml` had their threshold ignored in
    silence, and the server gives every workspace its own cfg (app.py:223), so a
    process-wide CWD read could serve one tenant's bar to another.
    """

    def test_a_config_beside_the_cwd_is_not_picked_up(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text("coverage:\n  closure_target: 0.10\n")
        monkeypatch.chdir(tmp_path)
        assert closure_target() == DEFAULT_CLOSURE_TARGET
        assert closure_target() != 0.10

    def test_an_unresolved_target_warns_and_names_its_call_site(self, caplog):
        """A caller with no config is legitimate (the SPEC-8 library API runs in
        processes that never saw this repo) but at this depth it is
        indistinguishable from a caller that HAS a config and forgot to pass it.
        Only the warning separates them, so the warning has to exist and has to
        name the frame that needs fixing."""
        import logging

        import agenttic.coverage.targets as targets
        targets._warn_once.cache_clear()      # the dedupe is process-lifetime
        with caplog.at_level(logging.WARNING, logger=targets.__name__):
            assert closure_target() == DEFAULT_CLOSURE_TARGET
        assert caplog.records, "an unresolved threshold must not be silent"
        msg = caplog.records[-1].getMessage()
        assert "no config supplied" in msg
        assert "test_closure_target_from_config.py" in msg, msg

    def test_a_config_without_a_coverage_block_warns_too(self, caplog):
        """The scaffolded-project case: `release/scaffold_assets/config.yaml` had
        no coverage block, so a new project ran on the fallback and nothing said
        so. It declares one now; this keeps the silence from coming back."""
        import logging

        import agenttic.coverage.targets as targets
        targets._warn_once.cache_clear()
        with caplog.at_level(logging.WARNING, logger=targets.__name__):
            assert closure_target({"models": {}}) == DEFAULT_CLOSURE_TARGET
        assert "no usable coverage.closure_target" in caplog.records[-1].getMessage()

    def test_the_scaffolded_project_declares_the_target(self):
        cfg = yaml.safe_load(
            (REPO / "src/agenttic/release/scaffold_assets/config.yaml").read_text())
        assert cfg["coverage"]["closure_target"] == 0.95


class TestABadTargetIsRejectedAtLoadTime:
    """`closure_target` deliberately never raises — it runs inside coverage-model
    construction, where raising costs a run its coverage entirely. That makes a
    typo invisible exactly when it matters: `closure_target: 1.5` loaded clean and
    every run was silently measured against the fallback while the operator
    believed the bar had moved. Loudness belongs at load, where a human is
    watching a command refuse to start.
    """

    BASE = ("models: {agent_default: a, judge_strong: j}\n"
            "harness: {timeout_seconds: 10}\n")

    def _load(self, tmp_path, coverage_yaml: str):
        from agenttic.config import load_config
        p = tmp_path / "config.yaml"
        p.write_text(self.BASE + coverage_yaml)
        return load_config(p)

    @pytest.mark.parametrize("bad", ["1.5", "0", "-0.2", "95", "'high'", "true", "[]"])
    def test_a_target_outside_zero_to_one_refuses_to_load(self, tmp_path, bad):
        with pytest.raises(ValueError, match="coverage.closure_target"):
            self._load(tmp_path, f"coverage:\n  closure_target: {bad}\n")

    def test_a_good_target_loads(self, tmp_path):
        cfg = self._load(tmp_path, "coverage:\n  closure_target: 0.8\n")
        assert closure_target(cfg) == 0.8
        assert self._load(tmp_path, "coverage:\n  closure_target: 1\n") is not None

    def test_an_absent_section_still_loads(self, tmp_path):
        """Validate only what is present — the same rule the certification
        surface follows (config.py:29). A config predating the section is valid
        and runs on the documented default."""
        assert self._load(tmp_path, "") is not None
        assert self._load(tmp_path, "coverage: {}\n") is not None

    def test_a_non_mapping_section_refuses_to_load(self, tmp_path):
        with pytest.raises(ValueError, match="coverage must be a mapping"):
            self._load(tmp_path, "coverage: 0.95\n")

    def test_both_shipped_configs_survive_their_own_validation(self):
        from agenttic.config import load_config
        for name in ("config.yaml", "config.prod.yaml"):
            assert load_config(REPO / name)["coverage"]["closure_target"] == 0.95


class TestTheFallbackIsSafe:
    def test_no_config_falls_back_to_the_documented_default(self, tmp_path):
        assert _from_config_file(str(tmp_path / "absent.yaml")) is None
        assert closure_target({}) == DEFAULT_CLOSURE_TARGET

    def test_an_unusable_value_falls_back_rather_than_raising(self):
        """Validating a bad configured value loudly belongs at load time, where
        a human is watching. Here it must not raise: this runs inside model
        construction, and a model that cannot be built is a run with no coverage
        at all."""
        for bad in ("", None, 0, 1.5, -0.2, "high"):
            assert closure_target({"coverage": {"closure_target": bad}}) == \
                DEFAULT_CLOSURE_TARGET, bad


class TestTheLiteralHasOneHome:
    def test_the_coverage_models_do_not_hardcode_the_target(self):
        """One definition. `coverage/collect.py`, `schema/signoff.py` and
        `reporting/scorecard_report.py` carry their own copies still — they are
        outside this change's scope and are tracked separately."""
        owned = ["src/agenttic/coverage/model.py",
                 "src/agenttic/coverage/extractors.py",
                 "src/agenttic/coverage/models/baseline.py",
                 "src/agenttic/coverage/models/conversational_transactional.py"]
        for rel in owned:
            body = (REPO / rel).read_text()
            assert not re.search(r"(?<![.\d])0\.95\b", body), (
                f"{rel} hardcodes the closure target; it belongs in config.yaml "
                "with coverage/targets.py as the only fallback")
        targets = (REPO / "src/agenttic/coverage/targets.py").read_text()
        code = [ln for ln in targets.splitlines()
                if "0.95" in ln and "``" not in ln]   # prose mentions it too
        assert code == ["DEFAULT_CLOSURE_TARGET = 0.95"]
