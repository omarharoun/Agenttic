"""Step 5 acceptance tests (SPEC.md):
- Judge returns valid structured scores on 20 sample traces
- Calibration report runs against a hand-labeled CSV of >=30 rows
- Scorecards visibly distinguish calibrated vs provisional criteria
Plus: parse retry, JudgeError, Hard Rule 4, trajectory-tagged prompts.
"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.calibration import (
    calibration_report,
    krippendorff_alpha_interval,
    load_labels,
)
from ascore.scoring.engine import score_run
from ascore.scoring.judge import JudgeError, LLMJudge

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def make_trace(i=0, final="The refund takes 30 days.", tools=("lookup_kb",)):
    spans = [Span(span_id=f"l{i}", kind="llm_call", name="m", start_time=NOW, end_time=NOW)]
    spans += [Span(span_id=f"t{i}{j}", kind="tool_call", name=t, start_time=NOW,
                   end_time=NOW, input={"key": "refund_policy"}) for j, t in enumerate(tools)]
    spans += [Span(span_id=f"f{i}", kind="final_output", name="final_output",
                   start_time=NOW, end_time=NOW)]
    return Trace(trace_id=f"tr-{i}", agent_id="agent", agent_config_hash="h",
                 test_case_id=f"tc-{i}", spans=spans, visibility="glass_box",
                 final_output=final, total_cost_usd=0.01, total_steps=len(spans) - 1,
                 schema_version=SCHEMA_VERSION)


def make_tc(i=0, expected=None):
    return TestCase(test_id=f"tc-{i}", suite_id="s-1", task_description="answer policy q",
                    input={"q": "refund?"}, expected=expected, rubric_id="r-1")


TONE = Criterion(criterion_id="tone", description="Professional, empathetic tone",
                 scorer="judge", scale="three_point",
                 anchors={"pass": "Calm and specific.", "fail": "Sarcastic."})
EFFICIENT = Criterion(criterion_id="efficient", description="No redundant tool calls",
                      scorer="judge", scale="binary", tags=["trajectory"],
                      anchors={"pass": "Each tool called once, purposefully.",
                               "fail": "Same lookup repeated needlessly."})


class FakeJudgeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        return NS(content=[NS(type="text", text=self.replies.pop(0))])


def make_judge(replies, **kw):
    defaults = dict(model="judge-model", agent_model="agent-model")
    defaults.update(kw)
    return LLMJudge(client=FakeJudgeClient(replies), **defaults)


class TestJudgeStructuredScores:
    def test_twenty_traces_all_valid(self):
        replies = [json.dumps({"score": s, "rationale": f"r{i}"})
                   for i, s in enumerate([1.0, 0.5, 0.0, 1.0] * 5)]
        judge = make_judge(replies)
        scores = [judge.score_criterion(TONE, make_trace(i), make_tc(i))
                  for i in range(20)]
        assert len(scores) == 20
        assert all(s.score in (0.0, 0.5, 1.0) for s in scores)
        assert all(s.scorer == "judge" and s.judge_rationale for s in scores)

    def test_parse_retry_recovers(self):
        judge = make_judge(["garbage not json", '{"score": 1, "rationale": "ok"}'])
        assert judge.score_criterion(TONE, make_trace(), make_tc()).score == 1.0

    def test_out_of_scale_then_exhausted_raises(self):
        judge = make_judge(['{"score": 0.7, "rationale": "x"}',
                            '{"score": 7, "rationale": "x"}'])
        with pytest.raises(JudgeError, match="no valid judge output"):
            judge.score_criterion(TONE, make_trace(), make_tc())

    def test_judge_model_must_differ_from_agent(self):
        with pytest.raises(ValueError, match="Hard Rule 4"):
            LLMJudge(model="same", agent_model="same", client=object())

    def test_one_criterion_per_call(self):
        judge = make_judge(['{"score": 1, "rationale": "a"}',
                            '{"score": 0, "rationale": "b"}'])
        judge.score_criterion(TONE, make_trace(), make_tc())
        judge.score_criterion(EFFICIENT, make_trace(), make_tc())
        assert len(judge.client.requests) == 2

    def test_prompt_contains_anchors_and_input(self):
        judge = make_judge(['{"score": 1, "rationale": "a"}'])
        judge.score_criterion(TONE, make_trace(), make_tc())
        prompt = judge.client.requests[0]["messages"][0]["content"]
        assert "Calm and specific." in prompt and "Sarcastic." in prompt
        assert "refund?" in prompt

    def test_trajectory_tag_feeds_span_sequence(self):
        judge = make_judge(['{"score": 1, "rationale": "a"}'] * 2)
        judge.score_criterion(EFFICIENT, make_trace(), make_tc())
        traj_prompt = judge.client.requests[0]["messages"][0]["content"]
        assert "AGENT TRAJECTORY" in traj_prompt and "tool_call" in traj_prompt
        judge.score_criterion(TONE, make_trace(), make_tc())
        final_prompt = judge.client.requests[1]["messages"][0]["content"]
        assert "AGENT FINAL OUTPUT" in final_prompt


class TestCalibration:
    def make_csv(self, tmp_path, rows):
        p = tmp_path / "s-1.csv"
        p.write_text("trace_id,criterion_id,human_score\n"
                     + "\n".join(f"{t},{c},{s}" for t, c, s in rows))
        return p

    def test_report_on_36_labels(self, tmp_path):
        # 18 tone labels (three_point) + 18 efficient labels (binary) = 36 rows
        judge_scores, rows = [], []
        for i in range(18):
            tone_judge = [1.0, 0.5, 0.0][i % 3]
            tone_human = tone_judge if i < 16 else 0.0          # mostly agree
            eff_judge = float(i % 2)
            eff_human = eff_judge if i < 9 else 1.0 - eff_judge  # half disagree
            judge_scores += [(f"tr-{i}", "tone", tone_judge),
                             (f"tr-{i}", "efficient", eff_judge)]
            rows += [(f"tr-{i}", "tone", tone_human),
                     (f"tr-{i}", "efficient", eff_human)]
        labels = load_labels(self.make_csv(tmp_path, rows))
        assert len(labels) == 36
        report = calibration_report(
            judge_scores, labels,
            scales={"tone": "three_point", "efficient": "binary"}, threshold=0.8,
        )
        assert report["tone"].n == report["efficient"].n == 18
        assert report["tone"].calibrated is True
        assert report["efficient"].calibrated is False  # ~50% agreement

    def test_alpha_perfect_agreement_is_one(self):
        assert krippendorff_alpha_interval([(1.0, 1.0), (0.0, 0.0), (0.5, 0.5)]) == 1.0

    def test_alpha_poor_agreement_is_low(self):
        assert krippendorff_alpha_interval([(1.0, 0.0), (0.0, 1.0)] * 5) < 0.0

    def test_too_few_labels_never_calibrated(self):
        report = calibration_report(
            [("tr-0", "tone", 1.0)], {("tr-0", "tone"): 1.0},
            scales={"tone": "three_point"}, min_n=5,
        )
        assert report["tone"].agreement == 1.0
        assert report["tone"].calibrated is False


class TestEngineProvisionalMarking:
    RUBRIC = Rubric(rubric_id="r-1", criteria=[
        Criterion(criterion_id="routing", description="d", scorer="code",
                  scale="binary", check_ref="final_output_matches_expected"),
        TONE,
    ], weights={"routing": 2.0, "tone": 1.0})

    def test_run_score_mixes_code_and_judge_and_marks_provisional(self):
        judge = make_judge(['{"score": 0.5, "rationale": "decent"}'])
        rs = score_run(
            make_trace(final="billing"), make_tc(expected={"final_output": "billing"}),
            self.RUBRIC, judge, uncalibrated={"tone"},
        )
        by_id = {s.criterion_id: s for s in rs.criterion_scores}
        assert by_id["routing"].score == 1.0 and by_id["routing"].calibrated
        assert by_id["tone"].score == 0.5 and not by_id["tone"].calibrated
        # weighted mean = (1*2 + 0.5*1)/3 = 0.833 >= 0.7
        assert rs.passed is True

    def test_failing_weighted_mean(self):
        judge = make_judge(['{"score": 0, "rationale": "rude"}'])
        rs = score_run(
            make_trace(final="wrong"), make_tc(expected={"final_output": "billing"}),
            self.RUBRIC, judge,
        )
        assert rs.passed is False

    def test_judge_criteria_without_judge_rejected(self):
        with pytest.raises(ValueError, match="no judge provided"):
            score_run(make_trace(), make_tc(), self.RUBRIC, None)
