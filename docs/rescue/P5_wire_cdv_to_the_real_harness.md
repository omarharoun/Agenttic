# P5 — Wire CDV to the real harness

The CDV loop was built with its execution seam left as a parameter, and nobody
ever supplied the argument. This phase supplies it. It is the smallest change in
the rescue and it is the one that makes the two-number coverage story — *what we
asked to test* versus *what the run exhibited* — true for the first time.

**Citation correction.** The handoff cites `SPEC13_COVERAGE_DRIVEN.md:84` for
"a thin adapter, not a rewrite". The sentence is at **`SPEC13_COVERAGE_DRIVEN.md:86`**
("The CDV loop takes an injected executor rather than reaching into the harness
directly, so it stays testable offline; wiring it to the real harness + scoring
engine is a thin adapter, not a rewrite"). Everything else cited below was read
and is accurate as written.

---

## 1. Context

### The seam that was never filled

`verification/cdv.py:77` declares the contract:

```python
Executor = Callable[[RealizedScenario], ExecutionResult]
```

with a docstring at `cdv.py:75-76` promising "Real wiring runs the existing
harness + scoring engine". `run_until_closure` (`cdv.py:201`) takes `execute` as
its third positional argument and has **zero production callers** — every call
site in the repo is a test (`tests/stimulus/test_cdv.py:310,313,335,366`,
`tests/verification/test_signoff.py:78`), and every one of them injects a
`def execute(scn): return ExecutionResult(trace=_trace(), ...)` that ignores the
scenario entirely.

So the loop that "closes coverage instead of counting passes" has never once
been pointed at an agent.

### What that costs, concretely

`coverage/collect.py:35-41` defines the observation:

```python
@dataclass
class Sample:
    trace: Trace
    scenario: dict | None = None
    requested: dict[str, str] | None = None   # coverpoint_id -> bin_id
```

`collect()` records the stimulus side at `collect.py:293-295` — `requested` is
the only source of `stimulus_hits`. The live run path builds its samples at
**`ops.py:304`**:

```python
report = collect(baseline_model(), [Sample(trace=t) for t in traces])
```

No `scenario`, no `requested`. Therefore, on every run the product actually
performs today:

- `CoverageReport.stimulus_closure` (`collect.py:151-154`) is **always 0.0**;
- `CoverageReport.divergence()` (`collect.py:164-173`) is **always `[]`**;
- `signoff_report.py:39-41` prints "stimulus vs trace: **0% requested** / N%
  exhibited" on every sign-off, which is not a finding, it is a missing input;
- `verify_op`'s coverage summary (`ops.py:305-322`) does not even carry the two
  keys — `stimulus_closure` and `stimulus_vs_trace_divergence` exist in
  `CoverageReport.as_dict()` (`collect.py:204,211`) but are dropped on the way
  to the scorecard, so no console or report can show them.

The CDV loop already does this correctly — `cdv.py:257-258` builds
`Sample(trace=ex.trace, scenario=scn.as_dict(), requested=dict(p))`. The
information exists in exactly one place that never runs.

### And the two legs that stay dark

`schema/signoff.py:393-405` populates `ConvergenceLeg` and `EnvelopeLeg` from a
`cdv_result`. `ops.py:338-342` calls `build_signoff` with `coverage_report` and
`assertion_results` only. `cdv_result` is never passed by any production caller,
so every sign-off the product issues reads:

```
4 · CONVERGENCE            not run
6 · ENVELOPE               not run
```

(`reporting/signoff_report.py:99` and `:106-109`).

### What this phase is NOT

`schema/signoff.py:195-203` — `signs_off` binds on **coverage closed +
assertions populated with zero violations + zero formal counterexamples + no
illegal-bin hits**. Convergence and envelope are not in that expression, and
`refusal_reasons` (`signoff.py:223`) deliberately mirrors it "condition for
condition, and lists nothing else … the other legs are reported as scope, not as
blockers."

**P5 does not make the gate stricter. It makes the report true.** Any framing of
this phase as "tightening certification" is an overclaim.

---

## 2. Acceptance criteria

Each is checkable by running the command named.

1. **`verify_op` accepts the stimulus side.**
   `verify_op(traces, samples=[Sample(trace=t, scenario=s, requested={"tool_condition": "timeout"})])`
   returns a summary whose `["stimulus_closure"] > 0.0`. Today this raises
   `TypeError: verify_op() got an unexpected keyword argument 'samples'`
   (`ops.py:279` takes one positional parameter).

2. **Divergence names the bin.** For a sample whose `requested` asks for
   `tool_condition=timeout` over a trace containing no timeout signal, the
   summary's `["stimulus_vs_trace_divergence"]` contains exactly
   `{"coverpoint_id": "tool_condition", "bin_id": "timeout", "requested": 1, "exhibited": 0}`.

3. **The old call shape is unchanged.** `verify_op(traces)` with no `samples`
   returns every key it returns today with the same values, plus
   `stimulus_closure == 0.0` and `stimulus_vs_trace_divergence == []`.
   Verified by `pytest tests/verification/test_run_verification.py tests/coverage/test_action_risk.py -q`
   staying green with no edits to those files.

4. **`requested` is the point the solver drew.** For every recorded run,
   `run.sample().requested == dict(run.scenario.point)` — the same mapping
   `cdv.py:258` builds from the sampled point.

5. **Two computations of the same coverage agree.** For a CDV run, the
   scorecard's `coverage["trace_closure"]` equals `cdv_result.report.trace_closure`
   to 4 decimal places (same model, same samples, `collect()` is pure).

6. **Convergence and envelope go from `not_run` to `populated`.**
   `agenttic cdv --agent <id> --space space-conversational_transactional --rubric <id> --mock`
   exits 0 and the rendered sign-off's line 4 reads
   `N distinct failure signature(s) over M scenarios` with `M == cdv_result.scenarios_run`
   and `M > 0`; line 6 reads a non-zero `closure/$`.

7. **A flat curve from a detector that never ran is not reported.** When every
   `RunScore` carries a `scoring_error` (judge outage) and no oracle failure
   fired, `cdv_op` passes `cdv_result=None` to `build_signoff` and the
   scorecard's `signoff["convergence"]["status"] == "not_run"`.

8. **`signs_off` is bit-identical with and without CDV.** For a fixed coverage
   report and assertion result set,
   `build_signoff(...).signs_off == build_signoff(..., cdv_result=r).signs_off`
   for `r` in a set including one result with 12 distinct failure signatures and
   a non-flat curve.

9. **No silent single-message fallback.** `cdv_op(...)` called without
   `run_scenario=` raises `TypeError` (required keyword-only argument). There is
   no default that flattens a `RealizedScenario` to one `adapter.run()` call.

10. **The budget counts both kinds of spend.** For an N-scenario run,
    `cdv_result.dollars_spent == sum(r.trace.total_cost_usd) + sum(r.score.scoring_cost_usd)`
    within 1e-9.

11. **The human gate is not routed around.** After `agenttic cdv`,
    `reg.get_suite("cdv:<space_id>")` raises `NotFoundError`, and the frozen
    regressions are written to
    `<paths.review_dir>/cdv-<space_id>-v<version>-<run_id>.json` with
    `"approved": false` on every entry.

12. **The stimulus ceiling is stated, not hidden.** For a sample set requesting
    every legal value of every dimension of `seed_space()` under
    `baseline_model()`, `report.stimulus_closure == 0.6` exactly, and
    `coverpoints["trajectory"].stimulus_closure == coverpoints["action_risk"].stimulus_closure == 0.0`.
    Those two are run *outputs*, not requestable inputs
    (`stimulus/spaces/conversational_transactional.py:3-5`), so 0.6 is the
    arithmetic maximum of `collect.py:152-154` under this pairing, and the spec
    for any later phase must not treat it as a shortfall.

13. `pytest -q` green (modulo the 4 pre-existing `test_dist_quickstart` failures
    noted in `SPEC13_COVERAGE_DRIVEN.md:100-103`), and
    `git diff --stat -- tests/` shows only new files.

---

## 3. Design

### 3.1 The dependency this phase does not own

The executor must hand the scenario to something that can *stage* it — inject
the tool failure named in `RealizedScenario.injected_failures`, seed the world
from `env_seed`, drive a user across turns. None of that exists today
(`adapters/base.py:32` is `run(test_input: dict) -> Trace`, called once per case
at `harness/runner.py:137`). P5 declares the shape it consumes and nothing more:

```python
class ScenarioRunner(Protocol):
    """Run ONE realized scenario against ONE agent and return its Trace.

    P5 does not implement this; the environment/session phase does. It is
    declared here because this is the only module that consumes it, and because
    a Protocol is what lets the wiring be built and tested against a double
    while the real runner is still being written.
    """

    def __call__(self, scenario: RealizedScenario, *,
                 adapter: AgentAdapter, store: TraceStore) -> Trace: ...
```

If the environment phase ships `run_scenario_suite(adapter, scenarios, store, config)
-> list[Trace]`, `ops.cdv_op`'s caller adapts it with a one-element call. That
costs nothing: `run_until_closure` drives `execute` sequentially inside a plain
Python loop (`cdv.py:252-274`), so there is no batch to exploit.

`run_scenario` is **required, keyword-only, with no default**. A default that
JSON-dumped `scenario.text` into one user message would reproduce exactly the
defect this rescue exists to remove, and would do it behind a
`ConvergenceLeg(status="populated")`.

### 3.2 New module — `src/agenttic/verification/executor.py`

```python
@dataclass
class ScenarioRun:
    """One scenario's artifacts, kept together so coverage, scoring and the
    frozen regression all read the SAME run."""
    scenario: RealizedScenario
    trace: Trace
    score: RunScore | None

    def sample(self) -> Sample:
        return Sample(trace=self.trace, scenario=self.scenario.as_dict(),
                      requested=dict(self.scenario.point))


def scenario_to_case(scn: RealizedScenario, *, suite_id: str,
                     rubric_id: str) -> TestCase: ...

def trajectory_bin(trace: Trace) -> str: ...

def oracle_failures(trace: Trace,
                    expectation: Expectation | None) -> list[FailureSignature]: ...

def score_failures(score: RunScore, trajectory: str) -> list[FailureSignature]: ...

def harness_executor(cfg: dict, reg: Registry, adapter: AgentAdapter, *,
                     rubric: Rubric, run_scenario: ScenarioRunner,
                     suite_id: str, judge_client=None,
                     on_progress: ProgressFn | None = None,
                     ) -> tuple[Executor, list[ScenarioRun]]:
    """Return ``(execute, runs)``. ``runs`` is the recorder: the loop appends to
    it in scenario order, which is how the caller gets the (scenario, trace)
    pairs back — CDVResult carries the scenarios (cdv.py:114) but drops the
    traces."""
```

**Why a recorder rather than a change to `cdv.py`.** `CDVResult` keeps
`scenarios` but not `traces`, and `run_until_closure` keeps its `samples` list
local (`cdv.py:217,257`). Rather than widen `CDVResult` — which would touch the
module the tests pin — the executor is a closure over a list it owns. **P5
changes zero lines of `verification/cdv.py`.** That is what "a thin adapter, not
a rewrite" has to mean in practice.

`execute(scn)` does, per scenario:

1. `case = scenario_to_case(scn, suite_id=suite_id, rubric_id=rubric.rubric_id)`.
2. `trace = run_scenario(scn, adapter=adapter, store=reg)` — the runner persists.
3. `score = (await score_op(cfg, reg, [trace], [case], agent_model_of(adapter),
   judge_client=judge_client, rubric_override=rubric))[0]`.
4. `failures = oracle_failures(trace, scn.expectation) + score_failures(score, traj)`.
5. `passed = (score.scoring_error is None and score.passed) and not oracle-side failures`.
6. Append `ScenarioRun(scn, trace, score)`; return
   `ExecutionResult(trace=trace, passed=passed, failures=failures,
   cost_usd=trace.total_cost_usd + score.scoring_cost_usd)`.

Steps 2 and 3 are bridged by **one** `asyncio.run(...)` per scenario over an
inner `async def`, mirroring how the harness already bridges sync adapters
(`harness/runner.py:10-11`). `cdv_op` is therefore synchronous; async callers use
`await asyncio.to_thread(cdv_op, ...)`.

**`cost_usd` counts both.** `Budget.max_dollars` (`cdv.py:49`) is the only
ceiling `run_until_closure` enforces, and it is charged from `ex.cost_usd`
(`cdv.py:260`). Counting only `trace.total_cost_usd` would leave judge spend
uncapped. Note the check at `cdv.py:273` happens *after* the charge, so the loop
can overshoot by at most one scenario — expected, documented, not a bug to fix
here.

**`scenario_to_case` sets `expected=None`.** `TestCase.expected`
(`schema/testcase.py:20`) is *check configuration* — `scoring/engine.py:153-156`
runs it through `checks.repair_expected`, which fills defaults keyed off the
rubric's `check_ref`s (`scoring/checks.py:161-179`). The derived oracle is not
that shape and must not be smuggled in there. The `Expectation` travels on
`scn.expectation` and into the frozen regression, where it belongs.

`input={"message": scn.text}`, `task_description` composed from the abstract
point, `tags=["generated", "cdv"]`.

### 3.3 Failure signatures — where `bug_curve` gets its content

`FailureSignature(criterion_id, failure_mode, trajectory_bin)` (`cdv.py:53-62`).
Two sources, both deterministic in their classification:

**From the scoring engine** (read-only; `scoring/**` is untouched). For each
`CriterionScore` with `score < 1.0`:
`FailureSignature(criterion_id=cs.criterion_id, failure_mode=f"{cs.scorer}:{cs.score:g}",
trajectory_bin=trajectory_bin(trace))`.
A `RunScore` with `scoring_error` set yields **no** signatures — a judge outage
is scoring infrastructure failing, not the agent failing, exactly as
`schema/scorecard.py:35-39` already treats it for aggregates.

**From the derived oracle**, the deterministic subset only:

- `expectation.forbidden_tools` — a `tool_call` span with that name is
  `FailureSignature("oracle.forbidden_tools", f"called:{name}", traj)`.
- `expectation.must_escalate` — no escalation is
  `FailureSignature("oracle.must_escalate", "never_escalated", traj)`, decided by
  `run_predicate("traj_escalated_to_human", trace)`.

`expectation.must_convey` is **not checked**. Deciding whether an agent conveyed
"the request is ambiguous and must be clarified" (`stimulus/oracle.py:130`) is
semantic; checking it with substring matching would repeat the mistake
`coverage/extractors.py:172` already makes, where `_tool_signal()` sniffs
serialized span JSON and calls the coincidence a coverage hit. The module
docstring must say so.

`trajectory_bin(trace)` iterates `TRAJECTORY.bins`
(`coverage/models/conversational_transactional.py:34-52`) and returns the first
`predicate_ref` that fires via `run_predicate`, else `"other"` — reusing the
registry rather than re-deriving trajectory shape.

### 3.4 `ops.py` changes

```python
def verify_op(traces: list, *, samples: list | None = None,
              cdv_result=None) -> tuple[list, dict]:
```

- `ops.py:304` becomes
  `collect(baseline_model(), list(samples) if samples is not None else [Sample(trace=t) for t in traces])`.
  The coverage **model** stays `baseline_model()` unconditionally, so the
  scorecard's coverage and the CDV loop's own report are the same computation
  over the same inputs and cannot disagree — the concern `ops.py:330-333`
  already raises about the sign-off.
- The summary gains two keys: `"stimulus_closure": round(report.stimulus_closure, 4)`
  and `"stimulus_vs_trace_divergence": report.divergence()`. Additive; the
  console reads this dict and needs no change to keep working.
- `build_signoff(...)` gains `cdv_result=cdv_result`.

```python
def aggregate_op(reg, *, agent_id, suite, rubric, runs, visibility,
                 traces=None, samples=None, cdv_result=None) -> Scorecard:
```
Pass-through to `verify_op` at `ops.py:403`. Both new parameters default to
`None`, so `metrics/runner.py:85` and every existing caller are unaffected.

```python
@dataclass
class CDVOutcome:
    cdv: CDVResult
    runs: list[ScenarioRun]
    scorecard: Scorecard


def cdv_op(cfg: dict, reg: Registry, adapter: AgentAdapter, *,
           space: ScenarioSpace, rubric: Rubric,
           run_scenario: ScenarioRunner,
           coverage_model: CoverageModel | None = None,
           policy: PolicyDoc | None = None, seed: int = 0,
           budget: Budget | None = None, judge_client=None,
           on_progress: ProgressFn | None = None) -> CDVOutcome:
```

Body, in order:

1. `coverage_model = coverage_model or baseline_model()`; `budget = budget or Budget(**cfg.get("cdv", {}))`.
2. `execute, runs = harness_executor(...)` with `suite_id=f"cdv:{space.space_id}"`.
3. `res = run_until_closure(space, coverage_model, execute, budget, seed=seed,
   batch_size=cfg["cdv"]["batch_size"], policy=policy)` — `realize_client` stays
   `None`, so realization is the offline template (`stimulus/realize.py:119-121`)
   and the loop adds no generation spend.
4. **Vacuity guard.** `detector_ran = any(r.score is not None and r.score.scoring_error is None for r in runs)`.
   If `not detector_ran`, pass `cdv_result=None` onward: a flat bug curve
   produced by a detector that never ran is the same error `unexercised` exists
   to prevent (`SPEC13_COVERAGE_DRIVEN.md:29-31`, Hard Rule 60).
5. `reg.save_scenario_space(space)`, catching `DuplicateVersionError` — the store
   is append-only by design (`registry/sqlite_store.py:1335-1349`).
6. Write frozen-regression proposals to `<review_dir>/cdv-<space_id>-v<version>-<run_id>.json`,
   every entry `"approved": false`, the same review-directory convention the
   generator's human gate already uses (`cfg["paths"]["review_dir"]`).
7. `aggregate_op(reg, agent_id=adapter.agent_id, suite=ephemeral, rubric=rubric,
   runs=[r.score for r in runs if r.score], visibility=adapter.visibility,
   traces=[r.trace for r in runs], samples=[r.sample() for r in runs],
   cdv_result=res if detector_ran else None)`.

The `ephemeral` `TestSuite(suite_id=f"cdv:{space.space_id}", version=space.version,
approved=False)` is constructed and **never persisted**. It exists because
`Scorecard.aggregate` needs a suite id and version. This does not route around
the Step 8 human gate: that gate lives in `harness.run_suite` (`harness/runner.py:82-86`)
and guards *running a stored suite*; generated CDV stimulus is not a stored
suite, and Hard Rule 63's gate is on **promotion**, which step 6 honours by
writing proposals and nothing else.

### 3.5 CLI and config

New `agenttic cdv` command mirroring `run` (`cli.py:268-327`) for agent
resolution and `--mock`: `--agent`, `--space`, `--rubric`, `--seed`,
`--max-scenarios`, `--mock`, `--config`. It resolves the space from
`reg.get_scenario_space(...)` (falling back to the seeded
`spaces.conversational_transactional.seed_space()`), imports the real
`ScenarioRunner`, calls `cdv_op`, and prints `reporting/signoff_report.render`.

New `config.yaml` section — `config.py:13-22` validates only judge≠agent and the
incidents SLA surface, so this needs no schema change, and it satisfies the file's
own banner at `config.yaml:1`:

```yaml
cdv:                      # coverage-directed generation (SPEC-13 Step 61)
  max_scenarios: 60       # hard ceiling; the loop stops cleanly and reports partial closure
  max_dollars: 5.0        # agent execution + judge spend, both charged
  max_rounds: 6
  batch_size: 10
```

Defaults are deliberately below `cdv.Budget`'s (`200 / $25 / 12` at `cdv.py:48-50`):
a first real CDV run should cost single-digit dollars, not surprise someone.

---

## 4. Files touched

| Path | Change |
| --- | --- |
| `src/agenttic/verification/executor.py` | **NEW.** `ScenarioRunner` protocol, `ScenarioRun`, `scenario_to_case`, `trajectory_bin`, `oracle_failures`, `score_failures`, `harness_executor`. The whole adapter. |
| `src/agenttic/ops.py` | `verify_op` gains keyword-only `samples` / `cdv_result`; line 304 uses `samples` when given; summary gains `stimulus_closure` + `stimulus_vs_trace_divergence`; `build_signoff` call gains `cdv_result`. `aggregate_op` gains the same two pass-through kwargs. New `CDVOutcome` + `cdv_op`. |
| `src/agenttic/cli.py` | New `cdv` command. |
| `config.yaml` | New `cdv:` section. |
| `docs/SPEC13_COVERAGE_DRIVEN.md` | Line 86's future tense ("wiring it … is a thin adapter") becomes a statement of what landed and where. A stale "not yet wired" note in a build record is the same class of dishonesty this phase removes. |
| `tests/verification/test_cdv_wiring.py` | **NEW.** See §5. |

Not touched: `src/agenttic/verification/cdv.py`, `src/agenttic/scoring/**`,
`src/agenttic/schema/signoff.py`, `src/agenttic/coverage/**`, `ui/**`, any
existing test.

---

## 5. Tests — `tests/verification/test_cdv_wiring.py`

Offline throughout: the `no_network` fixture (`tests/verification/conftest.py:34-45`)
is applied to the whole module, the scenario runner is a double, and the judge is
a fake client, matching the existing offline seam
(`tests/test_harness.py:38` `StubAdapter`, `tests/test_discrimination.py:31`
`ScriptedAgent`).

**Fail on today's code:**

1. `test_verify_op_records_the_stimulus_side_when_samples_are_supplied`
   — passes `samples=` with `requested={"tool_condition": "timeout"}` and asserts
   `summary["stimulus_closure"] > 0`. Today: `TypeError` at `ops.py:279`.
   Proves criterion 1.
2. `test_divergence_names_the_bin_we_asked_for_and_never_got`
   — asserts the exact divergence record from criterion 2. Today: the key does
   not exist in the summary at all (`ops.py:305-322`). Proves criteria 2 and the
   fact that `divergence()` has never had an input.
3. `test_cdv_op_wires_the_loop_to_the_real_harness`
   — runs `cdv_op` with a double runner over a 3-value toy space; asserts
   `outcome.cdv.scenarios_run > 0`, every `runs[i].sample().requested ==
   dict(runs[i].scenario.point)`, and
   `scorecard.coverage["trace_closure"] == round(outcome.cdv.report.trace_closure, 4)`.
   Today: `ImportError: cannot import name 'cdv_op'`. Proves criteria 4, 5, 6.
4. `test_cdv_op_refuses_without_a_scenario_runner`
   — `pytest.raises(TypeError)`. Proves criterion 9: no silent single-message
   fallback can be added later without failing this.
5. `test_convergence_is_not_populated_when_the_failure_detector_never_ran`
   — every `RunScore` carries `scoring_error`; asserts
   `signoff["convergence"]["status"] == "not_run"` even though 40+ scenarios ran
   and the curve is trivially flat. Proves criterion 7.
6. `test_the_budget_counts_judge_spend_as_well_as_agent_spend`
   — a double whose traces cost $0.01 and whose scoring costs $0.02; asserts
   `dollars_spent == 0.03 * n`. Proves criterion 10.
7. `test_a_scoring_outage_is_not_a_failure_signature`
   — one scenario scored, one with `scoring_error`; asserts the errored one
   contributes no entry to `bug_curve`'s signature set. Proves the
   infrastructure/agent distinction §3.3 relies on.
8. `test_cdv_writes_regression_proposals_and_creates_no_suite`
   — asserts the review-dir JSON exists with `approved: false` everywhere, and
   `reg.get_suite("cdv:...")` raises `NotFoundError`. Proves criterion 11.

**Guards that pass before and after** (they exist to stop a later phase quietly
changing the meaning of this one):

9. `test_convergence_and_envelope_do_not_change_signs_off`
   — the same coverage report and assertion results, with and without a
   `cdv_result` carrying 12 signatures and a rising curve; asserts `signs_off`
   and `refusal_reasons()` are identical. This is the anti-overclaim test for
   `signoff.py:195`.
10. `test_verify_op_without_samples_is_unchanged`
    — asserts the pre-existing summary keys are untouched and the two new keys
    read `0.0` / `[]`. Proves criterion 3 for callers like `metrics/runner.py:85`.
11. `test_stimulus_closure_ceiling_under_the_baseline_model_is_three_fifths`
    — requests every legal value of every dimension of `seed_space()`; asserts
    `report.stimulus_closure == 0.6` and that `trajectory` and `action_risk` sit
    at `0.0` because they are outputs. Proves criterion 12 and stops a later
    phase reading 0.6 as a bug.
12. `test_the_wiring_runs_with_the_network_blocked`
    — the whole `cdv_op` path under `no_network`. Proves the loop stays CI-runnable,
    which is the property `stimulus/realize.py:12-13` claims for the template path.

---

## 6. Risks, and what this phase deliberately does not do

### Risks

**R1 — the assertion bug will make every CDV sign-off refuse (sequencing).**
`verification/builtins.py:172` (`never_tool_call_after_final_output`) takes
`finals[0]` at `:180` and flags any later tool call as a `high` violation.
`:195` (`never_pii_after_redaction`, critical) and `:312`
(`never_cross_tenant_identifiers`, critical) have the same first-event-then-forever
shape. On any multi-turn trace — which is precisely what the environment phase
will start producing — every turn after the first ends in a `final_output` span,
so the next turn's tool call is a guaranteed false violation. `AssertionLeg.violations > 0`
**does** block `signs_off` (`signoff.py:197-198`). P5 does not fix this. If the
real runner lands before the assertion fix, every CDV run will refuse to sign
off. That is deny-by-default and therefore safe, but it is useless, so the
assertion fix should land first.

**R2 — the numbers will get worse, and that is the deliverable.** Today
divergence is `[]` because nothing is ever requested. After P5 it will be a long
list — `session_shape.multi_turn`, `session_shape.resumed_with_memory` and most
of `tool_condition` will be requested and never exhibited until the environment
phase lands. Nobody may "fix" that by dropping `requested`.

**R3 — real spend.** Every round is real agent and judge calls. `Budget` is
checked after the charge (`cdv.py:273`), so overshoot by one scenario is
possible; the config defaults in §3.5 are deliberately an order of magnitude
below `Budget`'s dataclass defaults.

**R4 — `curve_flattened` is a weak signal at small N.** `cdv.py:142-143` returns
True at `window=40` scenarios with no new signature, including the case where
there were never any signatures. Criterion 7 blocks the pathological version
(detector never ran); the honest version — 40 scenarios, real scoring, zero
failures — still reads "curve FLAT" next to "0 distinct failure signature(s)"
(`signoff_report.py:92-97`), which is the correct pair of facts to print
together.

**R5 — a scorecard whose `suite_id` is not in the registry.** Verified safe on
the paths that exist: `ops._scorecard_with_context` (`ops.py:504-510`) fetches
only the rubric, and `certification/coverage.py:31-33` skips `NotFoundError`.
Any future code that assumes `scorecard.suite_id` resolves will break on CDV
scorecards.

**R6 — recollection cost.** `run_until_closure` re-runs `collect()` over *all*
samples every round (`cdv.py:277`), so predicate evaluation is
O(rounds × scenarios). Free at 60 scenarios; not a streaming design, and not
changed here.

### Deliberately not done

- **No environment, no simulated user, no multi-turn session.** P5 supplies the
  executor; whatever stages the scenario behind `ScenarioRunner` comes from
  another phase. `RealizedScenario.injected_failures` and `env_seed`
  (`stimulus/realize.py:78-79`) are handed to the runner and P5 does nothing
  with them itself.
- **No change to `signs_off`, `scoring/**`, or the Step 14 promotion gate.**
  Convergence and envelope remain scope.
- **No fix to `verification/builtins.py:180/195/312`** (R1).
- **No fix to `coverage/extractors.py:214`** (`_turns()` counting `llm_call`
  spans, so one human message with three tool calls is credited
  `session_multi_turn`), **`:228`** (`session_resumed_with_memory` reading
  attributes nothing sets, then degrading to a substring match on span names),
  or **`:172`** (`_tool_signal()` sniffing serialized span JSON). Wiring the
  stimulus side makes these extractors *observable* — divergence will show
  requested-but-never-exhibited bins — which is the evidence a later phase needs
  in order to fix them.
- **No `must_convey` check.** Semantic; see §3.3.
- **No `attestation.user_source`** (`schema/attestation.py:35`). Whether the user
  in a run was real or simulated is a fact the runner knows and P5 does not.
- **No de-hardcoding of the 0.95 closure target** (`coverage/model.py:201`,
  `coverage/models/baseline.py:39`,
  `coverage/models/conversational_transactional.py:196`, `schema/signoff.py:73`).
- **No UI change.** The two new summary keys are additive; the console keeps
  working untouched, and `CoverageWheel`'s `session_shape` sector stays hatched
  until something actually exhibits a multi-turn session.
- **No frozen-regression table and no promotion path.** Proposals are written to
  the review directory as files; a human gate for them is a separate phase.
