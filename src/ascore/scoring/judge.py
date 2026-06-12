"""LLM judge — the qualitative half of the scoreboard.

Design rules (SPEC.md Step 5):
* ONE criterion per API call. Never holistic scoring.
* Prompt always contains: criterion description, scale definition, the
  rubric's pass/fail anchors, the test input, and the trace evidence.
* Criteria tagged ``trajectory`` are judged on the compressed span sequence,
  not just the final output.
* Judge model must differ from the agent-under-test model (Hard Rule 4).
* Output is strict JSON ``{"score": <allowed value>, "rationale": "..."}``;
  one retry on parse/validation failure, then ``JudgeError``.
"""

from __future__ import annotations

import json

from ascore.schema.rubric import Criterion
from ascore.schema.scorecard import CriterionScore
from ascore.schema.testcase import TestCase
from ascore.schema.trace import Trace

ALLOWED_SCORES = {"binary": (0.0, 1.0), "three_point": (0.0, 0.5, 1.0)}

SCALE_DEFINITIONS = {
    "binary": "Score 1 if the criterion is met, 0 if it is not.",
    "three_point": "Score 1 if fully met, 0.5 if partially met, 0 if not met.",
}

SYSTEM_PROMPT = (
    "You are a strict, consistent evaluation judge for AI agent outputs. "
    "Judge ONLY the single criterion given. Respond with ONLY a JSON object "
    '{"score": <number>, "rationale": "<one or two sentences>"} and nothing else.'
)

_TRUNC = 400  # max chars per span field in compressed trajectories


class JudgeError(RuntimeError):
    """Judge could not produce a valid structured score."""


def compress_trajectory(trace: Trace) -> list[dict]:
    """Span sequence reduced to what a judge needs: kind, name, io summaries."""
    out = []
    for s in trace.spans:
        out.append({
            "kind": s.kind,
            "name": s.name,
            "input": json.dumps(s.input)[:_TRUNC],
            "output": json.dumps(s.output)[:_TRUNC],
            "error": s.error,
        })
    return out


def build_judge_prompt(criterion: Criterion, trace: Trace, tc: TestCase) -> str:
    if "trajectory" in criterion.tags:
        evidence = (
            "AGENT TRAJECTORY (ordered steps):\n"
            + json.dumps(compress_trajectory(trace), indent=1)
        )
    else:
        evidence = f"AGENT FINAL OUTPUT:\n{trace.final_output}"
    return (
        f"CRITERION: {criterion.description}\n\n"
        f"SCALE: {SCALE_DEFINITIONS[criterion.scale]}\n\n"
        f"ANCHOR — example of a PASS: {criterion.anchors['pass']}\n"
        f"ANCHOR — example of a FAIL: {criterion.anchors['fail']}\n\n"
        f"TASK INPUT GIVEN TO THE AGENT:\n{json.dumps(tc.input)}\n\n"
        f"{evidence}\n\n"
        "Judge the criterion now."
    )


class LLMJudge:
    def __init__(
        self,
        *,
        model: str,
        agent_model: str,
        client=None,
        max_retries: int = 1,
        max_tokens: int = 300,
    ):
        if model == agent_model:
            raise ValueError(
                f"judge model must differ from agent model ({model!r}) — Hard Rule 4"
            )
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    def score_criterion(
        self, criterion: Criterion, trace: Trace, tc: TestCase
    ) -> CriterionScore:
        if criterion.scorer != "judge":
            raise ValueError(f"criterion {criterion.criterion_id} is not judge-scored")
        prompt = build_judge_prompt(criterion, trace, tc)
        last_err = "no attempts made"
        for _ in range(self.max_retries + 1):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            try:
                score, rationale = self._parse(raw, criterion.scale)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                last_err = f"{type(exc).__name__}: {exc}; raw={raw[:200]!r}"
                continue
            return CriterionScore(
                criterion_id=criterion.criterion_id,
                score=score,
                scorer="judge",
                judge_rationale=rationale,
            )
        raise JudgeError(
            f"criterion {criterion.criterion_id}, trace {trace.trace_id}: "
            f"no valid judge output after {self.max_retries + 1} attempts ({last_err})"
        )

    @staticmethod
    def _parse(raw: str, scale: str) -> tuple[float, str]:
        data = json.loads(raw.strip())
        score = float(data["score"])
        if score not in ALLOWED_SCORES[scale]:
            raise ValueError(
                f"score {score} not in allowed {scale} values {ALLOWED_SCORES[scale]}"
            )
        return score, str(data.get("rationale", ""))
