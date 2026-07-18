"""Judge config — the judge's prompt becomes a versioned, optimizable artifact
(SPEC-3 Step 15.1).

Today a judge's behaviour is baked into two module constants in
``scoring.judge``: ``SYSTEM_PROMPT`` and the structure hard-coded in
``build_judge_prompt``. That makes a judge un-versionable — you cannot A/B two
judge prompts, roll one back, or attach the human-labeled few-shot examples an
optimizer would learn from. Step 15.1 lifts the judge prompt OUT of code and
into a per-criterion, versioned registry artifact:

* One :class:`JudgeConfig` per criterion, carrying the system prompt, the
  instruction-template structure, and (later) human-labeled few-shot examples.
* Exactly ONE ``status="active"`` config per (tenant, criterion) — the one the
  live judge renders with. The rest form an auditable lineage
  (candidate → active → retired/rejected), chained by ``parent_id``.

**Zero-behavior-change invariant.** The v1 *seed* config
(:func:`seed_config_for`) reproduces today's judge prompt BYTE-FOR-BYTE:
``render_judge_prompt(seed, criterion, trace, tc, fence=f)`` equals
``build_judge_prompt(criterion, trace, tc, fence=f)`` for the same fence, and
its ``system_prompt`` is ``judge.SYSTEM_PROMPT``. Few-shot examples are the only
additive surface — an empty list renders no block, so the seed is identical to
what shipped before this step.

This schema is independent of the trace schema, so it carries its own version
(``JUDGE_CONFIG_SCHEMA_VERSION``) and does NOT bump ``trace.SCHEMA_VERSION``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: Bumped when a JudgeConfig field is added/removed/renamed, so stored configs
#: stay interpretable across versions (independent of trace.SCHEMA_VERSION).
JUDGE_CONFIG_SCHEMA_VERSION = "0.1.0"

JudgeConfigStatus = Literal["candidate", "active", "rejected", "retired"]

#: Identifies the seed (v1) instruction template. Any config carrying this
#: template renders through the built-in ``build_judge_prompt`` structure, so
#: the seed is provably byte-identical to today's judge prompt.
SEED_INSTRUCTION_TEMPLATE = "builtin_v1"


class JudgeConfig(BaseModel):
    """One versioned judge configuration for a single criterion.

    Persisted in the registry (``JudgeConfigRow``); the live judge renders the
    active config via :func:`render_judge_prompt`. Optimized offline (Step 15.3)
    by proposing a ``candidate`` with new few-shot examples / instructions and
    promoting it to ``active`` once it beats the incumbent on the calibration
    set.
    """

    judge_config_id: str
    version: int
    criterion_id: str
    system_prompt: str
    instruction_template: str
    #: Human-labeled exemplars: each ``{trace_excerpt, human_score, rationale}``.
    few_shot_examples: list[dict] = Field(default_factory=list)
    parent_id: str | None = None
    changelog: str = ""
    status: JudgeConfigStatus = "candidate"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _check(self) -> "JudgeConfig":
        if self.version < 1:
            raise ValueError("JudgeConfig.version must be >= 1")
        for i, ex in enumerate(self.few_shot_examples):
            if not isinstance(ex, dict):
                raise ValueError(
                    f"few_shot_examples[{i}] must be a dict "
                    "{trace_excerpt, human_score, rationale}")
        return self


def _render_few_shot(examples: list[dict]) -> str:
    """A clearly-labeled few-shot block, or "" when there are none. Rendered
    BEFORE "Judge the criterion now." An empty list ⇒ "" ⇒ the prompt is
    byte-identical to the pre-15.1 judge prompt."""
    if not examples:
        return ""
    lines = [
        "LABELED EXAMPLES (human-scored; use them to calibrate your judgment):"
    ]
    for i, ex in enumerate(examples, 1):
        excerpt = str(ex.get("trace_excerpt", ""))
        score = ex.get("human_score", "")
        rationale = str(ex.get("rationale", ""))
        lines.append(f"Example {i}:")
        lines.append(f"  EVIDENCE: {excerpt}")
        lines.append(f"  HUMAN SCORE: {score}")
        lines.append(f"  RATIONALE: {rationale}")
    return "\n".join(lines) + "\n\n"


def render_judge_prompt(
    config: "JudgeConfig",
    criterion,
    trace,
    tc,
    *,
    fence: str,
) -> str:
    """Render the user-channel judge prompt for ``config``.

    For the seed template this reproduces :func:`agenttic.scoring.judge.
    build_judge_prompt` EXACTLY given the same ``fence`` (few_shot empty ⇒ no
    block). Few-shot examples, when present, render in a labeled block placed
    immediately BEFORE "Judge the criterion now." — additive, so a config with
    no examples is byte-identical to today's prompt.
    """
    from agenttic.scoring.judge import build_judge_prompt

    base = build_judge_prompt(criterion, trace, tc, fence=fence)
    block = _render_few_shot(config.few_shot_examples)
    if not block:
        return base
    tail = "Judge the criterion now."
    # Insert the labeled-examples block immediately before the final instruction.
    return base.replace(tail, block + tail)


def seed_config_for(criterion_id: str) -> "JudgeConfig":
    """The v1 / active seed config for a criterion.

    Its ``system_prompt`` is ``judge.SYSTEM_PROMPT`` and its
    ``instruction_template`` is the seed marker, so :func:`render_judge_prompt`
    reproduces today's ``build_judge_prompt`` output byte-for-byte (few-shot
    examples empty). This is what migration v26 seeds and what the live judge
    falls back to when no config has been persisted yet.
    """
    from agenttic.scoring.judge import SYSTEM_PROMPT

    return JudgeConfig(
        judge_config_id=f"{criterion_id}:v1",
        version=1,
        criterion_id=criterion_id,
        system_prompt=SYSTEM_PROMPT,
        instruction_template=SEED_INSTRUCTION_TEMPLATE,
        few_shot_examples=[],
        parent_id=None,
        changelog="seed: extracted from built-in judge prompt (v1)",
        status="active",
    )
