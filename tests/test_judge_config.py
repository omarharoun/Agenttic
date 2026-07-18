"""SPEC-3 Step 15.1 — judge configs become versioned artifacts.

Acceptance criteria:
- BYTE-IDENTICAL: with the v1 seed active config, the rendered prompt equals
  build_judge_prompt for the SAME fence, and scoring is unchanged.
- SINGLE ACTIVE: two active configs for one criterion is impossible.
- FEW-SHOT: a config WITH few_shot_examples renders them; empty ⇒ it doesn't.
- Round-trip + registry CRUD (save / active / lineage), like test_feedback_schema.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agenttic.registry.sqlite_store import (
    DuplicateVersionError,
    NotFoundError,
    Registry,
)
from agenttic.schema.judge_config import (
    JUDGE_CONFIG_SCHEMA_VERSION,
    JudgeConfig,
    render_judge_prompt,
    seed_config_for,
)
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring import judge as judge_mod
from agenttic.scoring.judge import LLMJudge, build_judge_prompt

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)

TONE = Criterion(criterion_id="tone", description="Professional, empathetic tone",
                 scorer="judge", scale="three_point",
                 anchors={"pass": "Calm and specific.", "fail": "Sarcastic."})


def _trace(final="The refund takes 30 days.") -> Trace:
    spans = [Span(span_id="f0", kind="final_output", name="final_output",
                  start_time=NOW, end_time=NOW)]
    return Trace(trace_id="tr-0", agent_id="agent", agent_config_hash="h",
                 test_case_id="tc-0", spans=spans, visibility="glass_box",
                 final_output=final, total_cost_usd=0.01, total_steps=0,
                 schema_version=SCHEMA_VERSION)


def _tc() -> TestCase:
    return TestCase(test_id="tc-0", suite_id="s-1", task_description="answer",
                    input={"q": "refund?"}, rubric_id="r-1")


# --------------------------------------------------------------------------- #
# Schema round-trip
# --------------------------------------------------------------------------- #


class TestJudgeConfigSchema:
    def test_version_constant_present(self):
        assert JUDGE_CONFIG_SCHEMA_VERSION

    def test_json_round_trip(self):
        cfg = seed_config_for("tone").model_copy(update={
            "few_shot_examples": [
                {"trace_excerpt": "e", "human_score": 1.0, "rationale": "r"}]})
        assert JudgeConfig.model_validate_json(cfg.model_dump_json()) == cfg

    def test_seed_is_active_v1(self):
        cfg = seed_config_for("tone")
        assert cfg.version == 1 and cfg.status == "active"
        assert cfg.criterion_id == "tone" and cfg.few_shot_examples == []
        assert cfg.system_prompt == judge_mod.SYSTEM_PROMPT

    def test_bad_version_rejected(self):
        with pytest.raises(Exception):
            JudgeConfig(judge_config_id="x", version=0, criterion_id="tone",
                        system_prompt="s", instruction_template="t")


# --------------------------------------------------------------------------- #
# Byte-identical: seed config == build_judge_prompt for the same fence
# --------------------------------------------------------------------------- #


class TestByteIdentical:
    def test_rendered_seed_prompt_equals_builtin(self):
        fence = "UNTRUSTED_AGENT_OUTPUT_deadbeef"
        seed = seed_config_for("tone")
        rendered = render_judge_prompt(seed, TONE, _trace(), _tc(), fence=fence)
        builtin = build_judge_prompt(TONE, _trace(), _tc(), fence=fence)
        assert rendered == builtin

    def test_scoring_unchanged_between_paths(self, tmp_path, monkeypatch):
        # Pin the per-call fence so both paths render an identical prompt.
        monkeypatch.setattr(judge_mod.secrets, "token_hex", lambda n: "f" * 32)
        verdict = json.dumps({"score": 1.0, "rationale": "calm"})

        class FakeClient:
            def __init__(self):
                self.requests = []
                self.messages = NS(create=self._create)

            def _create(self, **kw):
                self.requests.append(kw)
                return NS(content=[NS(type="text", text=verdict)],
                          usage=NS(input_tokens=5, output_tokens=5))

        # Path A: no registry ⇒ built-in prompt.
        c_a = FakeClient()
        j_a = LLMJudge(model="judge-x", agent_model="agent-y", client=c_a)
        s_a = j_a.score_criterion(TONE, _trace(), _tc())

        # Path B: registry with the active seed config ⇒ rendered prompt.
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        c_b = FakeClient()
        j_b = LLMJudge(model="judge-x", agent_model="agent-y", client=c_b, reg=reg)
        s_b = j_b.score_criterion(TONE, _trace(), _tc())

        # Same user prompt, same system prompt, same resulting score.
        assert c_a.requests[0]["messages"] == c_b.requests[0]["messages"]
        assert c_a.requests[0]["system"] == c_b.requests[0]["system"]
        assert s_a.score == s_b.score == 1.0


# --------------------------------------------------------------------------- #
# Few-shot rendering
# --------------------------------------------------------------------------- #


class TestFewShotRendering:
    def test_examples_appear_in_prompt(self):
        fence = "UNTRUSTED_AGENT_OUTPUT_abc"
        cfg = seed_config_for("tone").model_copy(update={
            "few_shot_examples": [
                {"trace_excerpt": "SNIPPET_ALPHA", "human_score": 0.5,
                 "rationale": "REASON_BETA"}]})
        prompt = render_judge_prompt(cfg, TONE, _trace(), _tc(), fence=fence)
        assert "LABELED EXAMPLES" in prompt
        assert "SNIPPET_ALPHA" in prompt and "REASON_BETA" in prompt
        # the labeled block precedes the final instruction
        assert prompt.index("SNIPPET_ALPHA") < prompt.index("Judge the criterion now.")

    def test_empty_examples_render_no_block(self):
        fence = "UNTRUSTED_AGENT_OUTPUT_abc"
        cfg = seed_config_for("tone")  # few_shot_examples == []
        prompt = render_judge_prompt(cfg, TONE, _trace(), _tc(), fence=fence)
        assert "LABELED EXAMPLES" not in prompt
        assert prompt == build_judge_prompt(TONE, _trace(), _tc(), fence=fence)


# --------------------------------------------------------------------------- #
# Registry CRUD + single-active invariant
# --------------------------------------------------------------------------- #


class TestJudgeConfigRegistry:
    def test_save_active_lineage(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        # a candidate v2 (not active — coexists fine)
        v2 = JudgeConfig(judge_config_id="tone:v2", version=2, criterion_id="tone",
                         system_prompt="s2", instruction_template="builtin_v1",
                         parent_id="tone:v1", status="candidate")
        reg.save_judge_config(v2)
        active = reg.active_judge_config("tone")
        assert active is not None and active.judge_config_id == "tone:v1"
        lineage = reg.judge_lineage("tone")
        assert [c.version for c in lineage] == [1, 2]

    def test_active_none_when_absent(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        assert reg.active_judge_config("nope") is None

    def test_two_actives_impossible(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        # attempting to persist a SECOND active for the same criterion is refused
        second = JudgeConfig(judge_config_id="tone:v2", version=2,
                             criterion_id="tone", system_prompt="s2",
                             instruction_template="builtin_v1", status="active")
        with pytest.raises(ValueError, match="already has an active"):
            reg.save_judge_config(second)
        # and there is still exactly one active
        assert len([c for c in reg.judge_lineage("tone")
                    if c.status == "active"]) == 1

    def test_set_active_flips_atomically(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        cand = JudgeConfig(judge_config_id="tone:v2", version=2,
                           criterion_id="tone", system_prompt="s2",
                           instruction_template="builtin_v1", status="candidate")
        reg.save_judge_config(cand)
        promoted = reg.set_active_judge_config("tone", "tone:v2")
        assert promoted.status == "active"
        assert reg.active_judge_config("tone").judge_config_id == "tone:v2"
        # exactly one active; the old one is retired
        lineage = {c.judge_config_id: c.status for c in reg.judge_lineage("tone")}
        assert lineage == {"tone:v1": "retired", "tone:v2": "active"}

    def test_set_active_missing_raises(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        with pytest.raises(NotFoundError):
            reg.set_active_judge_config("tone", "nope")

    def test_duplicate_version_refused(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        dup = JudgeConfig(judge_config_id="tone:v1b", version=1,
                          criterion_id="tone", system_prompt="s",
                          instruction_template="builtin_v1", status="candidate")
        with pytest.raises(DuplicateVersionError):
            reg.save_judge_config(dup)

    def test_tenant_isolation(self, tmp_path):
        reg = Registry(tmp_path / "j.db")
        reg.save_judge_config(seed_config_for("tone"))
        other = Registry(engine=reg.engine, tenant="t2")
        assert other.active_judge_config("tone") is None
        assert other.judge_lineage("tone") == []


# --------------------------------------------------------------------------- #
# Migration v26 — eager seeding from existing judge criteria
# --------------------------------------------------------------------------- #


class TestMigrationSeeding:
    def test_migration_seeds_active_config_for_existing_judge_criteria(self, tmp_path):
        from agenttic.migrations import _seed_judge_configs, run_migrations
        from agenttic.registry.sqlite_store import make_engine

        # Build a DB up to v25 only, save a rubric with a judge criterion, then
        # run the v26 seeding migration and assert an active v1 config exists.
        engine = make_engine(f"sqlite:///{tmp_path / 'seed.db'}")
        from agenttic.migrations import MIGRATIONS
        pre = [m for m in MIGRATIONS if m[0] <= 25]
        run_migrations(engine, pre)
        reg = Registry(engine=engine)  # runs full head incl. v26 (idempotent)

        # save a fresh rubric with a NEW judge criterion, then re-run v26 seeding
        rubric = Rubric(rubric_id="r-seed", version=1, criteria=[
            Criterion(criterion_id="freshness", description="Up to date",
                      scorer="judge", scale="binary",
                      anchors={"pass": "current", "fail": "stale"}),
            Criterion(criterion_id="len_ok", description="length",
                      scorer="code", scale="binary", check_ref="chk.len"),
        ])
        reg.save_rubric(rubric)
        with engine.begin() as conn:
            _seed_judge_configs(conn)  # idempotent re-run picks up the new rubric

        active = reg.active_judge_config("freshness")
        assert active is not None and active.version == 1 and active.status == "active"
        assert active.system_prompt == judge_mod.SYSTEM_PROMPT
        # code-scored criterion gets NO judge config
        assert reg.active_judge_config("len_ok") is None
        # idempotent: seeding again does not create a v2 / duplicate
        with engine.begin() as conn:
            _seed_judge_configs(conn)
        assert len(reg.judge_lineage("freshness")) == 1
