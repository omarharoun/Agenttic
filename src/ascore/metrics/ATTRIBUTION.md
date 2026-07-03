# Judge-quality rubrics — attribution & provenance

This note covers `judge_quality.py` (the LLM-judge quality & RAG rubric
evaluators: groundedness, answer/context relevance, hallucination-free,
completeness, coherence, conciseness, tone, helpfulness, instruction-following,
refusal-appropriateness, summarization quality).

## What is original vs. what was referenced

The **rubric wording in `judge_quality.py` is original**, written in Agenttic's
own anchored, PROVISIONAL-aware voice (Hard Rule 2 pass/fail anchors; Hard
Rule 3 binary/three_point scales). The **metric concepts** it implements
(groundedness/faithfulness, answer/context relevance, hallucination detection,
completeness, coherence, conciseness, tone, helpfulness, instruction-following,
refusal-appropriateness, summarization quality) are standard, well-established
evaluation ideas and are **not copyrightable**.

While designing the family, we **consulted Future AGI's Apache-2.0
`system_evals/**/*.yaml` judge prompts** as reference material for the concept
taxonomy and the "anti-bias" framing (those YAMLs carry
`permissions.allow_copy: true`). No prompt text is copied — every rubric here was
written from scratch. This note is kept as a good-faith Apache-2.0 attribution
for that **reference** use, and to state clearly that we changed/rewrote the
material.

## Attribution (Apache License 2.0)

> Portions of the *concept taxonomy and rubric structure* in this package were
> informed by **Future AGI, Inc.**'s open-source evaluation prompts
> (`futureagi/future-agi`, `futureagi/model_hub/system_evals/**/*.yaml`),
> Copyright 2024–2026 Future AGI, Inc., licensed under the Apache License,
> Version 2.0. See <http://www.apache.org/licenses/LICENSE-2.0>.
>
> **Changes:** all rubric prompt text was rewritten in Agenttic's own anchored,
> one-criterion-per-call, calibration-aware format; no upstream prompt wording is
> reproduced. The Future AGI `NOTICE` attribution is preserved here per
> Apache-2.0 §4.

If a future rubric here does adapt upstream *wording* (rather than only the
concept), tag that entry `rubric_source = "adapted-from-apache"` in
`judge_quality.py` and expand this note accordingly.

## What we deliberately did NOT take

- **No** `ee/` (Future AGI Enterprise License 1.0) code — the polished LLM-judge
  scoring/orchestration classes. Not copied, not reconstructed from stubs.
- **No** source from the published, **unlicensed** `ai-evaluation` PyPI wheel
  (no declared license → all rights reserved). Concepts only.

The judge *harness* that scores these rubrics is Agenttic's own
(`ascore.scoring.judge`), and every criterion here is **PROVISIONAL** until a
real judge-vs-human calibration run demonstrates agreement (Hard Rule 6).
