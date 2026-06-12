# Agenttic — Agentic Scoring & Benchmarking Platform

A UVM-style verification testbench where the device under test is an **AI
agent**. Agenttic (package/CLI name: `ascore`) turns business requirements into versioned benchmark suites,
runs any agent against them, scores the runs with deterministic checks plus a
calibrated LLM judge, and produces client-ready scorecards — with a live
monitoring path that detects production drift and triggers re-evaluation.

| UVM concept             | Agenttic component      |
|-------------------------|-------------------------|
| DUT                     | Agent under test        |
| Driver                  | Adapter layer           |
| Monitor                 | Trace capture           |
| Scoreboard              | Scoring engine          |
| Sequence / test         | Test case + rubric      |
| Test plan + coverage DB | Test registry           |
| Checker validation      | Judge calibration       |

## Architecture

```
business inputs ──> benchmark generator ──> test registry (versioned)
                     (LLM + human gate)            │
agents (yours or ──> adapter layer ──> execution harness <┘
 clients', any         │                    │ traces
 framework)      glass-box / black-box      ▼
                                   ┌─ batch evaluation (full rubrics, strong judge)
                                   └─ live monitoring  (sampled, light judge, drift)
                                            │
                                            ▼
                                     client reporting
        ↻ feedback returns as new tests · drift triggers batch re-evaluation
```

See `docs/architecture.png` for the diagram and `SPEC.md` for the build spec
this repo implements (Steps 1–10, all acceptance criteria covered by tests).

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"        # or: pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-...   # needed for generate / run / live judging
```

## Quickstart (pilot suite, no API key needed for the mocked E2E)

```bash
pytest                      # 100 tests, all LLM calls mocked
```

With a real key, the operator flow is:

```bash
# 1. Draft a benchmark suite from a business document
ascore generate job_description.txt --suite-id support-v1

# 2. Human gate: read review/support-v1.md, then
ascore approve support-v1

# 3. Run an agent against it
ascore run --agent ref-agent --suite support-v1            # reference agent
ascore run --agent client-x --suite support-v1 --url http://...  # black-box

# 4. Calibrate the judge against human labels (calibration/support-v1.csv)
ascore calibrate support-v1

# 5. Deliverable
ascore report <scorecard_id> -o report.md

# Regression after any agent/model/prompt change
ascore regress --agent ref-agent
```

A complete hand-written example lives in `examples/pilot_support_triage/`
(10-case support-ticket triage suite + rubric + KB); the end-to-end test
`tests/test_e2e_pipeline.py` runs the entire pipeline on it with mocked
model calls.

## Simulating business workflows with Managed Agents (beta)

Anthropic's [Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview)
hosts the agent loop and a sandboxed container per session — which means you
can stand up a candidate business workflow internally, with zero agent
infrastructure, and immediately benchmark it:

```bash
# 1. Describe the workflow step as a version-controlled agent YAML
#    (see examples/pilot_support_triage/workflow.agent.yaml)
# 2. Deploy it (creates the agent once; re-deploys bump the immutable version)
ascore deploy examples/pilot_support_triage/workflow.agent.yaml

# 3. Benchmark it like any other agent — one session per test case
ascore run --agent triage-wf --suite pilot-support-triage \
           --managed-agent-id agent_01... --environment-id env_01...
```

The `ManagedAgentAdapter` converts the session's live event stream into a
standard glass-box Trace: model requests become `llm_call` spans (with token
usage from `span.model_request_end`), tool use/result pairs become
`tool_call` spans, `session.error` becomes an error span — so the **full
rubric applies**, unlike black-box HTTP agents. The adapter pins the exact
agent version it tested (via `GET /v1/agents/{id}`) into the trace's config
hash, so a regression after a prompt tweak is attributable to that version
bump, and the agent's model feeds Hard Rule 4 judge selection automatically.

This makes the loop for a client engagement: draft the workflow as an agent
YAML → `ascore generate` a suite from their business doc → human-approve →
`ascore deploy` → `ascore run` → iterate on the YAML and `ascore regress`
until the scorecard clears the bar — all before a line of production agent
code exists.

## Visual workflow builder (n8n-style UI)

```bash
npm --prefix ui install && npm --prefix ui run build   # once
uv run ascore pilot                                    # seed the demo suite (DRAFT)
uv run ascore ui                                       # http://127.0.0.1:8700
```

First run: hit **▶ Run** on the starter pipeline — it stops at nothing
until you approve the pilot suite (Resources → suites → review → approve,
or wire a Human Gate node and approve right on the canvas).

An n8n-style canvas over the whole platform: drag nodes from the palette
(business doc → generator → human gate; agent → run suite → score →
scorecard → report; live monitor), wire typed ports (mismatched kinds
refuse to connect), configure nodes in the side panel (forms are generated
from each node's pydantic schema), then **Run**. Nodes animate live over
SSE — the run node shows `7/10 cases`, the gate node parks the execution
with an ✋ **Approve** button (durable across server restarts), failures
mark downstream nodes skipped. Other pages: executions history with node
outputs, and resource browsers for suites (review + approve), scorecards
(rendered reports), and traces (span drill-down).

Dev mode: `uv run ascore ui` + `npm --prefix ui run dev` (Vite on :5173,
proxies `/api`). The engine is headless-first: workflows are documents
(`POST /api/workflows`), executions stream `GET
/api/executions/{id}/events`, so CI can run the same graphs without the
canvas.

## Layout

```
src/ascore/
├── schema/        # Step 1 — Pydantic contracts (trace, testcase, rubric, scorecard)
├── adapters/      # Steps 2 & 7 — base driver, reference agent, black-box HTTP
├── harness/       # Step 3 — async runner: timeouts, transport-only retries
├── scoring/       # Steps 4 & 5 — checks registry, LLM judge, calibration, engine
├── registry/      # Step 6 — SQLite, append-only versioning
├── generator/     # Step 8 — business doc -> draft suite (human gate)
├── live/          # Step 9 — sampled production scoring + drift detection
├── reporting/     # Step 10 — client scorecard reports (Markdown)
├── ops.py         # shared pipeline ops (CLI and UI both call these)
├── server/        # workflow engine + FastAPI/SSE API for the UI
└── cli.py         # ascore command surface (incl. `ascore ui`)
ui/                # React + React Flow canvas (Vite, dark n8n-style theme)
```

## Design rules the code enforces

1. **The trace schema is the contract.** Changes bump `SCHEMA_VERSION`
   (bump rules in `schema/trace.py`).
2. **Judge criteria without pass/fail anchors are invalid** — rejected at
   model-validation time, so they can never reach scoring.
3. **Binary or three-point scales only.** Scores outside {0, 0.5, 1} are
   rejected by the schema.
4. **Judge model ≠ agent model.** Enforced in the judge constructor and the
   config loader.
5. **Agent mistakes are data.** Tool errors become error spans; the harness
   retries transport failures only, never agent behavior, and persists every
   trace including timeouts.
6. **Uncalibrated judge scores are provisional** and labeled as such in every
   scorecard and report.
7. **All model names, thresholds, and rates live in `config.yaml`.**
8. **Everything is versioned and append-only** — re-running suite vN
   reproduces byte-identical inputs forever.

Black-box agents (a bare HTTP endpoint) are scored on the criteria that don't
require trajectory data, and their reports carry an explicit tier banner;
instrumenting an agent for glass-box traces unlocks the full rubric.

## Status & roadmap

All 10 spec steps implemented with their acceptance criteria as tests
(`pytest` → 100 passing). Natural next increments: OpenTelemetry GenAI
export/import for the trace schema, framework adapters (LangGraph, OpenAI
Agents), an HTTP ingest endpoint for the live path, and per-engagement suite
libraries.
