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

Tiered judging (advisor tool, beta ``advisor-tool-2026-03-01``):
* When ``advisor_model`` is set, the judge runs on a cheaper executor model
  and may consult the stronger advisor model mid-generation for borderline
  judgments only. Both the executor AND the advisor must differ from the
  agent-under-test model (Hard Rule 4 applies to every model that shapes a
  score). Use :func:`make_judge` to pick the right configuration per run.
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

ADVISOR_SYSTEM_PROMPT = (
    "You are a strict, consistent evaluation judge for AI agent outputs. "
    "Judge ONLY the single criterion given. If and only if the judgment is "
    "genuinely borderline against the anchors, consult the `advisor` tool "
    "once before deciding; for clear-cut cases decide directly. Your final "
    'message must be ONLY a JSON object {"score": <number>, "rationale": '
    '"<one or two sentences>"} and nothing else.'
)

ADVISOR_BETA = "advisor-tool-2026-03-01"
_MAX_PAUSE_CONTINUATIONS = 5

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
        advisor_model: str | None = None,
        advisor_max_tokens: int = 2048,
        advisor_max_uses: int = 1,
        cfg: dict | None = None,
        retry_policy=None,
    ):
        if model == agent_model:
            raise ValueError(
                f"judge model must differ from agent model ({model!r}) — Hard Rule 4"
            )
        if advisor_model is not None and advisor_model == agent_model:
            raise ValueError(
                f"advisor model must differ from agent model ({advisor_model!r})"
                " — Hard Rule 4 applies to every model that shapes a score"
            )
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.advisor_model = advisor_model
        self.advisor_max_tokens = advisor_max_tokens
        self.advisor_max_uses = advisor_max_uses
        self.cfg = cfg  # for pricing; cost is 0 when not provided
        from ascore.retry import RetryPolicy
        self.retry_policy = retry_policy or (
            RetryPolicy.from_cfg(cfg) if cfg else RetryPolicy())

    def score_criterion(
        self, criterion: Criterion, trace: Trace, tc: TestCase
    ) -> CriterionScore:
        if criterion.scorer != "judge":
            raise ValueError(f"criterion {criterion.criterion_id} is not judge-scored")
        prompt = build_judge_prompt(criterion, trace, tc)
        last_err = "no attempts made"
        for _ in range(self.max_retries + 1):
            resp = self._create(prompt)
            texts = [
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            ]
            # Advisor mode: the executor may emit preamble text before the
            # advisor call; only the final text block holds the JSON verdict.
            raw = (texts[-1] if texts else "") if self.advisor_model else "".join(texts)
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
                cost_usd=self._call_cost(resp),
            )
        raise JudgeError(
            f"criterion {criterion.criterion_id}, trace {trace.trace_id}: "
            f"no valid judge output after {self.max_retries + 1} attempts ({last_err})"
        )

    def _call_cost(self, resp) -> float:
        """USD cost of a judge call from its token usage (0 without pricing).
        Advisor-tool tokens reported on the response are priced at the executor
        model rate — an approximation, flagged in the cost estimate's notes."""
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "input_tokens", None)
        tout = getattr(usage, "output_tokens", None)
        try:  # observability counters (best-effort)
            from ascore.server.metrics import record_tokens
            record_tokens("judge", tin, tout)
        except Exception:  # noqa: BLE001
            pass
        if not self.cfg:
            return 0.0
        from ascore.pricing import token_cost
        return token_cost(self.cfg, self.model, tin, tout)

    def _create(self, prompt: str):
        from ascore.retry import with_retry
        if self.advisor_model is None:
            return with_retry(lambda: self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ), self.retry_policy, op="judge")
        messages = [{"role": "user", "content": prompt}]
        for _ in range(_MAX_PAUSE_CONTINUATIONS + 1):
            resp = with_retry(lambda: self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=ADVISOR_SYSTEM_PROMPT,
                messages=messages,
                betas=[ADVISOR_BETA],
                tools=[{
                    "type": "advisor_20260301",
                    "name": "advisor",
                    "model": self.advisor_model,
                    "max_uses": self.advisor_max_uses,
                    "max_tokens": self.advisor_max_tokens,
                }],
            ), self.retry_policy, op="judge-advisor")
            if getattr(resp, "stop_reason", None) != "pause_turn":
                return resp
            # dangling advisor call: re-send so the server resumes it
            messages = messages + [{"role": "assistant", "content": resp.content}]
        raise JudgeError(
            f"advisor call did not complete after {_MAX_PAUSE_CONTINUATIONS} "
            "pause_turn continuations"
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


def make_judge(cfg: dict, agent_model: str, client=None) -> LLMJudge:
    """Pick the judge configuration for one run, respecting Hard Rule 4.

    Preferred: tiered judge — ``judge_executor`` (cheap) consulting
    ``judge_strong`` as an advisor on borderline calls. Falls back to a plain
    ``judge_strong`` judge whenever the executor or advisor would coincide
    with the agent-under-test model (e.g. the Sonnet reference agent).
    """
    executor = cfg["models"].get("judge_executor")
    strong = cfg["models"]["judge_strong"]
    if executor and executor != agent_model and strong != agent_model:
        return LLMJudge(
            model=executor,
            agent_model=agent_model,
            client=client,
            advisor_model=strong,
            advisor_max_tokens=cfg.get("scoring", {}).get("advisor_max_tokens", 2048),
            cfg=cfg,
        )
    return LLMJudge(model=strong, agent_model=agent_model, client=client, cfg=cfg)
