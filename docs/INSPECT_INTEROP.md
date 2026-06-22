# Inspect interop — agenttic ⇄ `inspect_ai` EvalLog

Implements item **#5** of [RESEARCH_TESTING_SURVEY.md](RESEARCH_TESTING_SURVEY.md):
a bidirectional bridge between agenttic's evaluation records and UK AISI
[Inspect](https://inspect.aisi.org.uk/)'s `EvalLog` format. Being Inspect-
compatible lets third parties re-run our evals in a harness they already trust
and opens the [`inspect_evals`](https://github.com/UKGovernmentBEIS/inspect_evals)
catalog for comparison.

No runtime dependency on `inspect_ai`: we emit/parse the documented `EvalLog`
JSON schema directly. The produced JSON validates against
`inspect_ai.log.EvalLog` and loads with `read_eval_log` (verified in
`tests/test_inspect_interop.py` when `inspect_ai` is installed).

## Surfaces

| Surface | Call |
| --- | --- |
| Python | `ascore.interop.to_inspect_log(scorecard, rubric=…, traces=…, testcases=…)` → `dict`; `ascore.interop.from_inspect_log(dict)` → `{scorecard, traces, rubric}` |
| Ops | `ascore.ops.inspect_log_op(reg, scorecard_id)` (pulls scorecard + rubric + traces from the registry) |
| HTTP | `GET /api/scorecards/{id}/inspect.json` — auth + tenant scoped, exactly like the PDF export |
| CLI | `ascore inspect-export <scorecard_id> [--out f.json]` and `ascore inspect-import <log.json> [--save]` |

## The 1:1 model mapping

| agenttic | Inspect | Notes |
| --- | --- | --- |
| `TestSuite` / `TestCase` | `Task` / dataset of `Sample` | `suite_id`→`eval.task`, `suite_version`→`eval.task_version` |
| agent under test (`agent_id`) | `model` | the evaluated subject |
| `Trace` (one run) | a `Sample`'s `messages` + `output` + `model_usage` | rendered projection; see lossy edges |
| `Rubric` / `Criterion` | `Scorer` | one Inspect `Score` per criterion; criteria recorded in `eval.metadata` |
| `RunScore` / `CriterionScore` | `Sample.scores` (dict) | `score`→`Score.value`, `judge_rationale`→`Score.explanation` |
| `Scorecard` aggregates | `EvalResults.scores` (metrics) | `task_success_rate` + `per_criterion_means` |
| `scorecard_id` | `eval.run_id` | |
| `created_at` | `eval.created` | |

agenttic-specific values with no native Inspect slot live under an `agenttic`
namespace inside the standard `metadata` dicts (`eval.metadata`,
`results.metadata`, `sample.metadata`). `metadata` is first-class in the Inspect
schema, so this is faithful interop, not a side-car.

## What round-trips 1:1 (no loss)

For an **agenttic-origin** log, `from_inspect_log(to_inspect_log(x)) == x` on:

- **Scorecard:** `scorecard_id`, `agent_id`, `suite_id`, `suite_version`,
  `rubric_id`, `rubric_version`, `visibility_tier`, `created_at`,
  `task_success_rate`, `mean_cost_usd`, `total_cost_usd`,
  `total_scoring_cost_usd`, `p95_latency_ms`, `per_criterion_means`,
  `errored_test_ids`.
- **RunScore:** `test_id`, `trace_id`, `passed`, `cost_usd`, `scoring_cost_usd`,
  `latency_ms`, `steps`, `scoring_error`.
- **CriterionScore:** `criterion_id`, `score`, `scorer`, `calibrated`,
  `judge_rationale`, `cost_usd`.
- **Trace:** every `Span` (timings, IO, tokens, attributes, parent links),
  `agent_config_hash`, `final_output`, totals, `visibility`, `schema_version`.
- **Rubric** (when supplied).

## Lossy edges (by design)

1. **Native messages are a projection.** Each sample's
   `messages`/`output`/`model_usage` are rendered from the trace so a foreign
   Inspect viewer sees a real transcript, but the span *tree* (parent/child
   nesting, per-span IO/attributes) is flattened into a linear message list.
   Round-trip stays lossless only because the untouched spans are preserved in
   `sample.metadata.agenttic.trace.spans`; the flattened `messages` view alone
   is lossy.

2. **Foreign EvalLogs** (not produced by agenttic) recover only the mappable
   subset:
   - `Score.value` is snapped to agenttic's `{0, 0.5, 1}` scale (Hard Rule 3):
     `"C"`/`True`/≥0.75 → `1.0`, `"partial"`/≈0.5 → `0.5`, else `0.0`.
   - Scorecard aggregates are **recomputed** via `Scorecard.aggregate`, not read
     back (a foreign log has no `results.metadata.agenttic`).
   - `passed` is derived (all criteria scoring `1.0` ⇒ pass).
   - Traces become best-effort black-box traces carrying only `final_output`.
   - Inspect features with no agenttic equivalent are dropped: `events`,
     `store`, `attachments`, `sandbox`, `choices`, score `reducers`, and
     multi-epoch sampling.

3. **Cost split.** agenttic separates agent-execution cost from judge/scoring
   cost; Inspect has a single `total_cost`. We export execution cost to
   `ModelUsage.total_cost` and keep the exact split in metadata.
