# Agentic Scoring & Benchmarking Platform — Build Spec

Spec for Claude Code. Work through the steps in order. Each step has acceptance
criteria — do not move to the next step until they pass. Commit after every
green step.

## Concept (read once)

We are building a UVM-style verification testbench, but the DUT is an AI agent:

| UVM concept        | This system            |
|--------------------|------------------------|
| DUT                | Agent under test       |
| Driver             | Adapter layer          |
| Monitor            | Trace capture          |
| Scoreboard         | Scoring engine         |
| Sequence / test    | Test case + rubric     |
| Test plan + coverage DB | Test registry     |
| Checker validation | Judge calibration      |

Pipeline: business requirements → benchmark generator → versioned test registry
→ execution harness runs any agent (via adapters) → traces in a standard schema
→ scoring engine (deterministic code checks + LLM judge) → scorecard report.
Two eval modes: batch (full rubrics, strong judge) and live (sampled,
lightweight judge, drift detection).

## Tech stack (fixed — do not substitute)

- Python 3.11+, package manager: `uv`
- Pydantic v2 for all schemas (single source of truth)
- SQLite via `sqlmodel` for the registry (no external DB in MVP)
- `anthropic` SDK for judge + generator calls; model names from config, never hardcoded
- `typer` for the CLI, `rich` for terminal output
- `pytest` for tests; every module ships with unit tests
- No web UI in MVP. CLI + JSON/Markdown reports only.

## Repository layout

```
agentic-scoring/
├── pyproject.toml
├── config.yaml              # model names, sampling rates, paths
├── src/agenttic/
│   ├── schema/              # Pydantic models (Step 1)
│   │   ├── trace.py
│   │   ├── testcase.py
│   │   ├── rubric.py
│   │   └── scorecard.py
│   ├── adapters/            # Step 2 & 7
│   │   ├── base.py
│   │   ├── anthropic_simple.py
│   │   └── blackbox_http.py
│   ├── harness/             # Step 3
│   │   └── runner.py
│   ├── scoring/             # Step 4 & 5
│   │   ├── checks.py        # deterministic checks
│   │   ├── judge.py         # LLM judge
│   │   └── calibration.py
│   ├── registry/            # Step 6
│   │   └── store.py
│   ├── generator/           # Step 8
│   │   └── pipeline.py
│   ├── live/                # Step 9
│   │   └── monitor.py
│   ├── reporting/           # Step 10
│   │   └── scorecard_report.py
│   └── cli.py
└── tests/
```

---

## Step 1 — Trace schema (the keystone contract)

Create Pydantic models in `src/agenttic/schema/`. Align field naming with
OpenTelemetry GenAI semantic conventions where one exists.

**trace.py**
- `Span`: `span_id`, `parent_id: str | None`, `kind: Literal["llm_call",
  "tool_call", "retrieval", "agent_decision", "error", "final_output"]`,
  `name`, `start_time`, `end_time`, `input: dict`, `output: dict`,
  `error: str | None`, `tokens_in: int | None`, `tokens_out: int | None`,
  `cost_usd: float | None`, `attributes: dict`
- `Trace`: `trace_id`, `agent_id`, `agent_config_hash`, `test_case_id: str |
  None` (None for live-production traces), `spans: list[Span]`,
  `visibility: Literal["glass_box", "black_box"]`, `final_output: str`,
  `total_cost_usd`, `total_latency_ms`, `total_steps: int`, `schema_version: str`

**testcase.py**
- `TestCase`: `test_id`, `suite_id`, `version: int`, `task_description`,
  `input: dict`, `expected: dict | None` (ground truth when checkable),
  `tags: list[str]` (e.g. `edge_case`, `adversarial`, `happy_path`),
  `rubric_id`
- `TestSuite`: `suite_id`, `version`, `business_context: str`,
  `test_ids: list[str]`

**rubric.py**
- `Criterion`: `criterion_id`, `description`,
  `scorer: Literal["code", "judge"]`,
  `scale: Literal["binary", "three_point"]` (never wider scales),
  `check_ref: str | None` (function name in checks.py when scorer="code"),
  `anchors: dict` — for judge criteria, REQUIRED example of a pass and a fail
- `Rubric`: `rubric_id`, `version`, `criteria: list[Criterion]`,
  `weights: dict[str, float]`

**scorecard.py**
- `CriterionScore`: `criterion_id`, `score: float`, `scorer`,
  `judge_rationale: str | None`
- `RunScore`: `trace_id`, `test_id`, `criterion_scores: list[CriterionScore]`,
  `passed: bool`, `cost_usd`, `latency_ms`, `steps`
- `Scorecard`: `agent_id`, `suite_id`, `suite_version`, `run_scores`,
  aggregates (`task_success_rate`, `mean_cost`, `p95_latency`,
  `per_criterion_means`), `visibility_tier`, `created_at`

**Acceptance criteria**
- [ ] All models round-trip `model_dump_json` → `model_validate_json`
- [ ] `schema_version` present on Trace; bump rule documented in module docstring
- [ ] Unit tests cover validation failures (e.g. judge criterion without anchors raises)

## Step 2 — Adapter base + one glass-box adapter

`adapters/base.py`: abstract `AgentAdapter` with one method —
`run(test_input: dict) -> Trace`. The adapter is responsible for emitting
well-formed spans.

`adapters/anthropic_simple.py`: a reference agent — Claude with 2 toy tools
(`calculator`, `lookup_kb` reading a local JSON file) in a tool-use loop, max
10 steps. Every LLM call and tool call becomes a Span. `visibility="glass_box"`.

**Acceptance criteria**
- [ ] Running the adapter on a sample input returns a valid `Trace` with ≥3 spans
- [ ] Cost and token counts populated from API usage data
- [ ] A forced tool error produces an `error` span, not a crash

## Step 3 — Execution harness

`harness/runner.py`: `run_suite(adapter, suite, registry) -> list[Trace]`.
- Timeout per run (config), retries on transport errors only (never on agent
  mistakes — a mistake is data), max-steps kill switch
- Concurrency via `asyncio` with a semaphore (config: `max_parallel`)
- Persist every trace to the registry even on failure

**Acceptance criteria**
- [ ] 10-case suite runs concurrently; all 10 traces persisted
- [ ] A test that times out yields a Trace with an error span and is scored as fail, not dropped

## Step 4 — Deterministic checks

`scoring/checks.py`: registry (decorator `@check("name")`) of functions
`(trace: Trace, test_case: TestCase) -> float`.

MVP checks: `final_output_matches_expected`, `valid_json_output`,
`required_tool_called`, `forbidden_tool_not_called`, `steps_under_limit`,
`cost_under_limit`.

**Acceptance criteria**
- [ ] Each check unit-tested against hand-built traces (pass + fail fixtures)
- [ ] Unknown `check_ref` in a rubric fails loudly at suite-load time, not at scoring time

## Step 5 — LLM judge + calibration

`scoring/judge.py`:
- Scores ONE criterion per API call (never holistic). Prompt template includes:
  criterion description, scale definition, the pass/fail anchors from the
  rubric, the relevant trace excerpt, and the test input
- Judge model ≠ agent model (enforce via config assertion)
- Output: structured JSON `{score, rationale}`; retry once on parse failure
- Trajectory scoring: for criteria tagged `trajectory`, feed the span sequence
  (compressed: kind, name, input/output summaries), not just final output

`scoring/calibration.py`:
- Load human labels from `calibration/{suite_id}.csv` (`trace_id, criterion_id,
  human_score`)
- Compute agreement (exact match for binary, Krippendorff's alpha for
  three-point) per criterion
- `agenttic calibrate` prints a table; criteria below threshold (config, default
  0.8 agreement) are flagged `UNCALIBRATED` and their scores marked provisional
  in scorecards

**Acceptance criteria**
- [ ] Judge returns valid structured scores on 20 sample traces
- [ ] Calibration report runs against a hand-labeled CSV of ≥30 rows
- [ ] Scorecards visibly distinguish calibrated vs provisional criteria

## Step 6 — Test registry

`registry/store.py` (SQLite): CRUD for suites, test cases, rubrics, traces,
scorecards. Everything versioned: updating a suite/rubric creates a new version,
never mutates. Scorecards record exact suite+rubric versions used.

**Acceptance criteria**
- [ ] Re-running an old suite version reproduces identical test inputs
- [ ] `agenttic regress --agent X` re-runs the latest version of every suite the agent was previously scored on and diffs against the prior scorecard

## Step 7 — Black-box adapter

`adapters/blackbox_http.py`: wraps any HTTP endpoint (`POST {input} → {output}`,
configurable mapping). Produces a Trace with a single `final_output` span +
latency; `visibility="black_box"`. Scoring automatically restricts to criteria
that don't need trajectory data; scorecard states the tier.

**Acceptance criteria**
- [ ] Same suite runs against a stub HTTP server; scorecard renders with reduced criteria set and a clear "black-box tier" banner

## Step 8 — Benchmark generator

`generator/pipeline.py`: staged LLM pipeline, each stage a separate call with
structured output:
1. `extract_tasks(business_doc) -> list[TaskSpec]`
2. `define_criteria(task) -> draft Rubric` (forces binary/three-point, forces anchors)
3. `generate_cases(task, n, tags)` — happy-path, edge, adversarial mix per config
4. **Human gate**: writes a review file (`review/{suite_id}.md`) listing tasks,
   criteria, sample cases; `agenttic approve {suite_id}` is required before the
   suite becomes runnable. Never skip this gate.

**Acceptance criteria**
- [ ] Feeding a sample job description yields a reviewable draft suite of ≥10 cases across ≥2 tasks
- [ ] Unapproved suites refuse to run

## Step 9 — Live monitoring path

`live/monitor.py`:
- `ingest(trace)` endpoint/function for production traces (no `test_case_id`)
- Sample rate from config (default 5%); sampled traces scored on a reduced
  rubric (criteria tagged `live`) with the cheap judge model
- Rolling window stats; drift rule: criterion mean drops > threshold vs the
  batch baseline for that agent → emit a `ReEvalRequest` record + CLI warning
- Weekly calibration job: re-score a sample of live-judged traces with the
  strong judge; report divergence

**Acceptance criteria**
- [ ] Synthetic drift test: degrade outputs in a stream of fake traces; drift fires within the configured window
- [ ] Live scores never mix into batch scorecards (separate tables, separate reports)

## Step 10 — Reporting

`reporting/scorecard_report.py`: renders a Scorecard to Markdown (and JSON):
executive summary, per-task table, per-criterion breakdown with judge
rationales for failures, cost/latency stats, visibility tier, calibration
status, regression diff vs previous scorecard if one exists, recommendations
section (top 3 failing criteria with example traces).

**Acceptance criteria**
- [ ] `agenttic report {scorecard_id} -o report.md` produces a client-presentable document with no placeholders

## CLI surface (final)

```
agenttic generate <business_doc>      # draft a suite (Step 8)
agenttic approve <suite_id>           # human gate
agenttic run --agent <id> --suite <id>   # harness + scoring (Steps 3-5)
agenttic calibrate --suite <id>       # judge vs human labels
agenttic regress --agent <id>         # regression re-runs
agenttic monitor ingest|status        # live path
agenttic report <scorecard_id>
```

## Build order & milestones

- **M0** = Steps 1–2 (schema + reference agent)
- **M1 (vertical slice)** = Steps 3–6 with a HAND-WRITTEN 10-case suite for one
  real task. This is the most important milestone — prove the loop end to end
  before widening.
- **M2** = Steps 7–8 (any agent in, suites generated not hand-written)
- **M3** = Steps 9–10 (live path + client deliverable)

## Hard rules (apply to every step)

1. The schema (Step 1) is the contract. Any change to it bumps
   `schema_version` and updates all fixtures in the same commit.
2. Judge criteria without pass/fail anchors are invalid — fail at load time.
3. Binary or three-point scales only. No 1–10 scoring anywhere.
4. Judge model and agent-under-test model must differ.
5. Agent mistakes are data: never retry them, never filter them out.
6. Provisional (uncalibrated) scores are always labeled as such in output.
7. All model names, thresholds, sample rates live in `config.yaml`.
8. Every step lands with unit tests; M1 lands with one end-to-end test that
   runs the full pipeline on the reference agent with mocked LLM calls.
