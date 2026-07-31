# P4 — Fault injection

Make `injected_failures` load-bearing. A scenario that asks for a timeout gets a
timeout, on a named tool, at a named call index, reproducibly — and the trace
carries proof of which fault was injected and whether the agent ever saw it.

---

## 1. Context

### `injected_failures` is written and never read

`RealizedScenario.injected_failures` is declared at `stimulus/realize.py:79`,
populated at `stimulus/realize.py:157`
(`injected_failures=([] if tools == "all_ok" else [tools])`) and serialized at
`stimulus/realize.py:98`. Those three lines are its entire lifetime:

```
$ grep -rn "injected_failures" --include=*.py .
src/agenttic/stimulus/realize.py:79
src/agenttic/stimulus/realize.py:98
src/agenttic/stimulus/realize.py:157
```

Nothing reads it. `env_seed` (`stimulus/realize.py:78`, set at `:156`) is the
same: written once, read nowhere. The only thing a `tool_condition=timeout`
scenario actually does today is append a sentence to the ticket text
(`_TOOL_TEXT` at `stimulus/realize.py:56-63`: `" The order-lookup tool times out
on first call."`) — a description of a fault, handed to the agent as prose, in a
run where no tool will fail.

### So the whole `tool_condition` coverpoint is string coincidence

`coverage/extractors.py:172`:

```python
def _tool_signal(trace: Trace, *needles: str) -> bool:
    return any(any(n in _blob(s) for n in needles)
               for s in trace.spans if s.kind in ("tool_call", "error"))
```

All five degraded bins — `tool_timeout` (`:183`), `tool_error_5xx` (`:188`),
`tool_rate_limited` (`:194`), `tool_stale_data` (`:199`),
`tool_malformed_response` (`:204`) — are substring sniffs over serialized span
JSON. `_blob` (`:69-76`) serializes `name`, `error`, `input`, `output` and
`attributes`, so a user asking *"why did my order time out?"* lands in the tool
call's `input` and credits `tool_timeout`. The coverpoint's own description is
"What the environment did to the agent"
(`coverage/models/conversational_transactional.py:52-64`). There is no
environment, so it credits what a string happened to contain.

The one bin that is not a sniff is `tool_all_ok` (`:177-180`), which reads
`_errored()` (`:60-66`) — real, and it is the bin a suite gets for free.

### Which is why the two recovery bins are accidental

`traj_retry_after_error` (`coverage/extractors.py:105-117`) needs a `tool_call`
span with an error followed by another call to the *same tool*.
`traj_recovered_from_tool_failure` (`:120-129`) needs a failed tool call, a later
successful one, and a non-empty final output. Both are real, both are
deterministic, and today the *only* way to reach them is for a tool to fail by
accident — a malformed KB key, an unparseable expression
(`adapters/anthropic_simple.py:227-240`). Nothing in the platform can ask for
them. They are exactly the bins the extractors module's own docstring claims as
the differentiator (`coverage/extractors.py:7-10`: *"an agent can score 100%
having never once been made to recover from a tool failure"*), and they are
unreachable on purpose.

The baseline coverage model crosses them: `tool_condition × trajectory`
(`coverage/models/baseline.py:60`), and `action_risk × tool_condition`
(`coverage/models/conversational_transactional.py:194`). Those crosses cannot be
closed deliberately by anything that exists.

### The scenario reaches the predicates and is ignored

The plumbing is already there. `cdv.py:253-258` builds
`Sample(trace=ex.trace, scenario=scn.as_dict(), requested=dict(p))`;
`collect.py:245` calls `run_predicate(b.predicate_ref, trace, scenario)`; every
predicate signature is `(trace, scenario=None)`. Not one of the 26 dereferences
`scenario`. `collect.py:8-11` states the intended semantics precisely —
*"Generating a `tool_condition=timeout` scenario where the timeout never fired is
a stimulus hit and a trace miss"* — and `report.divergence()`
(`collect.py:164-173`) is the mechanism that would report it. Today **every**
degraded `tool_condition` scenario is that case, and it is invisible because the
sniff credits the trace side anyway.

### Corrections to the handoff brief

* *"deterministically from `env_seed`"* — `env_seed` (`realize.py:78`) is a dict
  of environment **facts** (`{"order_id": ..., "exists": ...}`), not an RNG seed.
  The integer seed is `RealizedScenario.seed` (`realize.py:73`). P4 derives the
  plan from `seed` and **stores** it into `env_seed`.
* *"camp/environment.py:26 — RL-shaped Environment"* — line 26 is
  `class StepResult`; `Environment` is at `camp/environment.py:33`. Both claims
  about it hold; `MockSupportEnv.step` (`:60-69`) always returns `done=True`.
* Everything else cited in the brief that P4 touches was verified as written.

### A reproducibility bug found in the module P4 edits (reported, not fixed)

`realize.py:123` computes the order id with `abs(hash((seed, point.get('intent',
''))))`. Python randomizes `str` hashing per process, so `realize()` is **not**
reproducible across interpreter runs, and neither is the `content_sha256()` that
Hard Rule 57 and `cdv.replay` (`cdv.py:297-312`) depend on:

```
$ for i in 1 2 3; do PYTHONHASHSEED=$i python -c '...realize(p,7,sp).content_sha256()...'; done
cf9b0ea844bb5ef3 o-39315
4ec2520948354c53 o-50810
b1b5446b891e89ad o-80952
```

The existing tests miss it because they compare within one process
(`tests/stimulus/test_cdv.py:83-89`). P4 does **not** fix this — changing the
order id would change `content_sha256` for every frozen regression already
stored, which is a separate decision with its own blast radius. P4 only
guarantees it does not *inherit* the bug (AC-3).

---

## 2. Acceptance criteria

Each is checkable by running the named command.

1. **The five fault kinds exist as one enumerated vocabulary, matched to the
   space and the coverage model.**
   `python -c "from agenttic.stimulus.faults import FAULT_KINDS; from agenttic.stimulus.spaces.conversational_transactional import seed_space; assert set(FAULT_KINDS) == set(seed_space().dimension('tool_condition').values) - {'all_ok'}"`
   exits 0.

2. **A realized scenario carries an executable plan, not a sentence.**
   For every non-`all_ok` `tool_condition`, `realize(...).env_seed["fault_plan"]`
   is a dict with a non-empty `faults` list whose single entry names the kind.
   `pytest -q tests/stimulus/test_faults.py::test_every_degraded_tool_condition_realizes_an_executable_plan`

3. **The plan is reproducible across processes.**
   `for i in 1 2 3; do PYTHONHASHSEED=$i python -c "<print plan_id for seed 7>"; done`
   prints the same `plan_id` three times. (`plan_faults` uses `hashlib` and
   `random.Random`, never builtin `hash()`.)
   `pytest -q tests/stimulus/test_faults.py::test_the_plan_is_identical_across_interpreter_processes`

4. **Each of the five kinds actually fires against the reference adapter.**
   With a scripted offline client, a run under each kind produces a `tool_call`
   span whose `attributes["fault"]` records that kind, on the planned tool, at
   the planned call index.
   `pytest -q tests/stimulus/test_faults.py::TestEveryKindFires`

5. **Each of the five `tool_*` coverage predicates returns True on the trace its
   own fault produced, and the trace is produced by injection rather than by an
   agent saying the word.**
   `pytest -q tests/stimulus/test_faults.py::TestEveryKindFires::test_the_matching_coverage_predicate_fires`

6. **The two recovery bins become deliberately exercisable — and are not
   reachable without the injector.**
   One scripted run (call → 500 → retry → success → answer) makes
   `traj_retry_after_error` and `traj_recovered_from_tool_failure` both True with
   the injector attached, and both False with the identical script and
   `faults=None`.
   `pytest -q tests/stimulus/test_faults.py::test_retry_and_recovery_are_reachable_only_because_the_fault_fires`

7. **A hard fault fires exactly once, so recovery is possible.**
   Under `error_5xx` the second call to the same tool executes for real.
   `pytest -q tests/stimulus/test_faults.py::test_a_fault_fires_once_not_forever`

8. **Injected ≠ observed is recorded, not papered over.** A plan targeting a tool
   the agent never calls leaves `injector.planned_but_unfired` non-empty, stamps
   no span, and the resulting `Sample` shows the bin in `report.divergence()`.
   `pytest -q tests/stimulus/test_faults.py::test_a_planned_fault_that_never_fired_is_a_divergence_not_a_hit`

9. **The agent is never told it is being tested.** No fault message, tool result
   or tool-visible payload contains the strings `fault`, `inject`, `agenttic` or
   any `FAULT_KINDS` member; the provenance lives only in `span.attributes`,
   which the adapter never puts into `messages`.
   `pytest -q tests/stimulus/test_faults.py::test_the_fault_provenance_is_never_shown_to_the_model`

10. **Zero behaviour change when no plan is attached.** The same scripted client
    through `AnthropicSimpleAgent` with and without `faults=None` yields traces
    equal on every field except `trace_id`/`span_id`/timings, and no span carries
    a `fault` attribute.
    `pytest -q tests/stimulus/test_faults.py::test_an_adapter_without_a_plan_is_byte_identical`

11. **`replay` restores the stored plan verbatim.** A frozen regression replayed
    against the same space yields the same `plan_id` as when it was frozen, even
    though `realize` is re-run.
    `pytest -q tests/stimulus/test_faults.py::test_replay_restores_the_frozen_fault_plan`

12. **The module is pure.** `stimulus/faults.py` imports no model client and the
    planner runs under the socket-blocking fixture.
    `pytest -q tests/stimulus/test_faults.py::test_faults_module_imports_no_model_client tests/stimulus/test_faults.py::test_planning_10k_scenarios_needs_no_network`

13. **Nothing already green goes red.** `pytest -q` reports no *new* failures
    against the pre-P4 baseline. The 8 pre-existing failures in
    `tests/coverage/test_session_shape.py` (a different phase's failing spec —
    it needs a `user_turn` span kind and `agent_steps_*` predicates that do not
    exist) are expected to remain failing and are not P4's to fix.

---

## 3. Design

### 3.1 The plan is data, derived once, stored with the scenario

New pure module `src/agenttic/stimulus/faults.py`, sibling to `space.py` and
`oracle.py` and held to the same purity rule (no model client, no network).

```python
FAULT_KINDS: tuple[str, ...] = ("timeout", "error_5xx", "rate_limited",
                                "stale_data", "malformed_response")

#: Kinds that fail the call outright — the tool never runs, the agent gets an
#: error. These are the only kinds `_errored()` (extractors.py:60) can see, and
#: therefore the only ones that can reach the two recovery trajectory bins.
HARD_KINDS = frozenset({"timeout", "error_5xx", "rate_limited"})

#: Kinds that let the call run and corrupt what comes back. No error field. A
#: suite that only injects HARD_KINDS has not tested the silent half — an agent
#: that confidently answers from stale data fails nothing today.
SOFT_KINDS = frozenset({"stale_data", "malformed_response"})


@dataclass(frozen=True)
class FaultSpec:
    kind: str                 # a FAULT_KINDS member
    tool: str = ""            # "" = late-bound to the first tool the agent calls
    call_index: int = 0       # 0-based, counted per bound tool name
    once: bool = True         # fires at call_index and never again


@dataclass(frozen=True)
class FaultPlan:
    faults: tuple[FaultSpec, ...] = ()

    def plan_id(self) -> str: ...          # sha256[:16] over the sorted specs
    def for_call(self, tool: str, call_index: int) -> FaultSpec | None: ...
    def as_dict(self) -> dict: ...         # {"plan_id": ..., "faults": [ ... ]}
    @classmethod
    def from_dict(cls, d: dict | None) -> "FaultPlan": ...
    def __bool__(self) -> bool: ...


def plan_faults(kinds: Sequence[str], *, seed: int,
                tools: Sequence[str] = (), spread: bool = False) -> FaultPlan:
    """Turn `injected_failures` into an executable plan, deterministically.

    `tools` empty (the default, and what the CDV loop passes) leaves `tool=""`
    and the injector binds to the first tool the agent calls — the DUT's tool
    surface is not knowable at realization time (`cdv.py:253` has no adapter).
    With `tools`, the target is picked by `random.Random(seed).choice(sorted(tools))`.

    `spread=False` pins `call_index=0`. Randomizing the index by default would
    reintroduce exactly the disease this phase exists to cure: a fault planted at
    call 2 of a tool the agent calls once is a fault that silently never happens,
    and the bin goes back to being accidental.
    """
```

`plan_id` is `hashlib.sha256` over the canonical JSON of the specs — never
builtin `hash()` (see §1's reproducibility finding; AC-3 enforces it).

### 3.2 The injector is a wrapper over the executor contract that already exists

Two places in the repo already agree on one executor shape —
`assistant/tools.py:174` (`Callable[[dict, ToolContext], tuple[object, str|None]]`)
and `adapters/anthropic_simple.py:227` (`_exec_tool(name, args) ->
tuple[object, str|None]`). The injector wraps that shape and nothing else, so it
is reusable by any environment or tool server a later phase builds.

```python
@dataclass(frozen=True)
class FaultRecord:
    kind: str
    tool: str            # the BOUND tool name
    call_index: int
    plan_id: str
    scenario_id: str = ""
    late_bound: bool = False

    def as_attribute(self) -> dict:
        """What lands on span.attributes["fault"] — the provenance P0 reads."""
        return {"kind": ..., "tool": ..., "call_index": ..., "injected": True,
                "plan_id": ..., "scenario_id": ..., "late_bound": ...}


class FaultInjector:
    def __init__(self, plan: FaultPlan, *, scenario_id: str = "") -> None: ...

    def reset(self) -> None: ...                       # per-run; call counters + fired
    @property
    def fired(self) -> list[FaultRecord]: ...
    @property
    def planned_but_unfired(self) -> list[FaultSpec]: ...

    def apply(self, tool: str, args: dict,
              call: Callable[[], tuple[object, str | None]]
              ) -> tuple[object, str | None, FaultRecord | None]:
        """Run one tool call, honouring the plan. Never raises (Hard Rule 5)."""

    def summary(self) -> dict:
        """{"plan_id", "fired": [...], "planned_but_unfired": [...]} — stamped on
        the final_output span so a trace carries its own fault provenance."""
```

`apply` semantics, in order:

1. `idx = self._counts[tool]; self._counts[tool] += 1`.
2. Bind: on the first `apply`, any spec with `tool == ""` binds to this tool name
   (`late_bound=True`). First call wins — deterministic, no RNG.
3. `spec = plan.for_call(tool, idx)`. None → `out, err = call(); return out, err,
   None`. **No attribute, no change** (AC-10).
4. `spec.kind in HARD_KINDS` → `call()` is **not** invoked. Return
   `(None, _MESSAGE[kind], record)`.
5. `spec.kind in SOFT_KINDS` → `out, err = call()`. If `err is not None`, return
   it **unchanged with no record** — a genuine failure is never masked by a
   synthetic one, and never miscredited as an injected fault. Otherwise return
   `(_corrupt(kind, out, seed_material), None, record)`.
6. `spec.once` (the default) → the spec is retired, so the *next* call to the
   same tool runs for real. This is what makes `traj_retry_after_error`
   (`extractors.py:105`) and `traj_recovered_from_tool_failure` (`:120`)
   reachable at all. It is also the deliberate opposite of the
   first-event-then-forever bug in `verification/builtins.py:172` — a fault that
   never lifts makes recovery unprovable rather than untested.

What the agent sees, and only what the agent sees:

| kind | tool runs? | agent-visible result | `_errored()` |
|---|---|---|---|
| `timeout` | no | `error`: `"tool call to {tool} timed out after 30s"` | True |
| `error_5xx` | no | `error`: `"HTTP 500 internal server error from {tool}"` | True |
| `rate_limited` | no | `error`: `"HTTP 429 too many requests calling {tool}; retry after 30s"` | True |
| `stale_data` | yes | the real payload, aged: an `as_of` timestamp well in the past, unflagged | False |
| `malformed_response` | yes | the payload's JSON, truncated mid-token — a string, unflagged | False |

The soft kinds announce nothing. `stale_data` returning `{"stale": true}` would
be a tell, and an agent that only handles the announced case has not been tested.
Provenance lives on `span.attributes["fault"]`, which the reference adapter never
serializes into `messages` — verified: `messages` is built at
`adapters/anthropic_simple.py:124/169/183-189` from `test_input`, `resp.content`
and the tool result string, never from spans. AC-9 pins this.

**Bridge property, stated so nobody mistakes it for the design.** Because `_blob`
(`extractors.py:69-76`) serializes `span.attributes`, stamping
`attributes["fault"]["kind"]` makes today's substring predicates fire for all
five kinds — including `malformed_response`, whose agent-visible payload contains
no keyword. That is a bridge that keeps `tool_condition` credited between P4 and
P0, **not** the mechanism. P0 replaces the sniff with a read of `injected AND
observed`. Until then the credit remains unsound in the other direction (a user
who types "timeout" still scores).

### 3.3 The adapter seam

`AnthropicSimpleAgent` gains one optional constructor argument and one clone
helper. `_exec_tool` keeps its 2-tuple return — `tests/test_enforce_gateway_failclosed.py:83`
unpacks exactly two values, and tests are not edited.

```python
def __init__(self, *, ..., faults: "FaultInjector | None" = None): ...

def with_faults(self, faults: "FaultInjector") -> "AnthropicSimpleAgent":
    """A shallow clone sharing the client, carrying a per-scenario injector.
    Per-scenario state must never be mutated on a shared adapter: run_suite
    (harness/runner.py:137) drives ONE instance across concurrent cases."""

def describe(self) -> dict:
    return {..., "fault_injection": self.faults is not None}
```

`describe()` gains a **boolean only**. A run under fault injection is a different
configuration of the system under test and must not share a `config_hash` with a
clean run (resume at `harness/runner.py:99-107` keys on it). The plan itself is
stimulus, not configuration, and stays out.
`tests/test_adapter_simple.py:142-143` assert only hash equality/inequality
relations, which a constant-`False` key preserves.

Collection follows the honeypot precedent exactly (`redteam/honeypot.py:291-323`:
record during the loop, stamp spans afterwards):

```python
# _exec_tool, at the top — so GuardedHoneypotAgent._exec_tool (honeypot.py:291)
# intercepts decoy names FIRST and the injector can never suppress an
# enforcement decision. Faults apply only to calls that reach the real executor.
def _exec_tool(self, name, args):
    if self.faults is None:
        return self._exec_tool_real(name, args)
    out, err, rec = self.faults.apply(name, args,
                                      lambda: self._exec_tool_real(name, args))
    self._fault_events.append(rec)          # one entry per tool call, None = clean
    return out, err
```

`run()` resets `self._fault_events = []` and `self.faults.reset()` at entry, then
zips `_fault_events` with the `tool_call` spans in order, writing
`span.attributes["fault"] = rec.as_attribute()` where `rec` is not None, and
writes `injector.summary()` onto the `final_output` span's attributes.

The summary goes on `final_output` **deliberately**: `_tool_signal`
(`extractors.py:172`) only inspects spans of kind `tool_call` and `error`, so a
summary naming all five kinds cannot leak into `_blob` and credit bins that never
fired. Putting it there still leaves it reachable via `_attr`
(`extractors.py:79-83`), which scans every span for a key.

### 3.4 An executor the CDV loop can actually take

`cdv.py:77` declares `Executor = Callable[[RealizedScenario], ExecutionResult]`
and says *"Real wiring runs the existing harness + scoring engine"*;
`run_until_closure` (`cdv.py:201`) has no production caller. P4 does not claim to
supply the production executor — it supplies the fault-carrying half of one, so
that whichever phase wires the loop reuses it rather than reinventing it:

```python
# src/agenttic/stimulus/faults.py
def faulting_executor(adapter_factory: Callable[[], AgentAdapter],
                      *, score: Callable[[RealizedScenario, Trace],
                                         tuple[bool, list]] | None = None
                      ) -> "Executor":
    """Build a cdv.Executor that attaches each scenario's plan to a FRESH adapter
    (one per scenario — the injector is per-run state) and runs `scenario.text`.
    `score=None` reports passed=True with no signatures: P4 measures COVERAGE,
    it does not touch scoring (scoring/** is off limits)."""
```

The plan is read from `scenario.env_seed["fault_plan"]` via `FaultPlan.from_dict`
— never rebuilt, so a replayed scenario replays its stored plan.

### 3.5 Realization and replay

`realize()` gains one optional keyword and one line of output:

```python
def realize(point, seed, space, *, policy=None, client=None,
            model="claude-sonnet-5", temperature=0.0,
            tools: Sequence[str] = ()) -> RealizedScenario:
    ...
    plan = plan_faults(injected, seed=seed, tools=tools)
    env_seed = {"order_id": order, "exists": data != "entity_not_found",
                "fault_plan": plan.as_dict()}
```

No new `RealizedScenario` field, so `as_dict()` (`realize.py:92-103`) and
`content_sha256()` (`:86-90`) keep their shapes and every stored scenario hash is
unchanged. `injected_failures` keeps its `list[str]` shape and becomes the
human-readable index into the plan.

`cdv.replay` (`cdv.py:297-312`) currently restores `text` verbatim after a
template-drift check. It must do the same for the plan, for the same reason:

```python
    stored_plan = (stored.get("env_seed") or {}).get("fault_plan")
    if stored_plan and scn.env_seed.get("fault_plan") != stored_plan:
        scn.env_seed["fault_plan"] = stored_plan       # stored artifact is authority
        scn.realized_by = "replayed-verbatim"
```

Without this, `replay` calls `realize(...)` with no `tools` and can produce a
differently-bound plan than the one that caught the bug (AC-11).

### 3.6 Configuration

No `config.yaml` section. The two numeric knobs (`30s` in the timeout message,
the staleness age) appear only inside strings and timestamps the agent reads; no
verdict, threshold or gate reads them, so putting them in config would be
ceremony rather than Hard Rule 7 compliance. `FAULT_KINDS` is a vocabulary, not a
threshold, and AC-1 pins it to the scenario space so the two cannot drift.

---

## 4. Files touched

| Path | Change |
|---|---|
| `src/agenttic/stimulus/faults.py` | **New.** `FAULT_KINDS`, `HARD_KINDS`, `SOFT_KINDS`, `FaultSpec`, `FaultPlan`, `plan_faults`, `FaultRecord`, `FaultInjector`, `faulting_executor`. Pure: no model client, no network, no builtin `hash()`. Module docstring states why soft faults are unannounced and why `once=True`. |
| `src/agenttic/stimulus/realize.py` | `realize()` gains `tools: Sequence[str] = ()`; `env_seed` gains `"fault_plan"`. `_TOOL_TEXT` (`:56-63`) stays — the ticket prose is still the *scenario*; the plan is now the *mechanism*. No change to `RealizedScenario` fields, `as_dict` or `content_sha256`. |
| `src/agenttic/stimulus/__init__.py` | Export `FAULT_KINDS`, `FaultPlan`, `FaultSpec`, `FaultInjector`, `plan_faults`; extend `__all__` (`:15-18`). |
| `src/agenttic/adapters/anthropic_simple.py` | `__init__` gains `faults=None`; new `with_faults()`; `_exec_tool` body moves to `_exec_tool_real` and the public `_exec_tool` consults the injector **at the top** (2-tuple return preserved); `run()` resets and zips `_fault_events` onto `tool_call` spans and writes `injector.summary()` onto the `final_output` span; `describe()` gains `"fault_injection": bool`. |
| `src/agenttic/verification/cdv.py` | `replay()` restores `env_seed["fault_plan"]` from the frozen scenario verbatim. Nothing else — `run_until_closure`, `Executor` and `Budget` are untouched. |
| `src/agenttic/server/routes/capabilities.py` | `not_covered` (`:195-205`) currently omits the absent environment entirely. Add one entry that is now *narrower* rather than absent: fault injection covers the five declared tool conditions on tools the platform executes; a black-box adapter that runs its own tools cannot be fault-injected. |
| `tests/stimulus/test_faults.py` | **New.** §5. |
| `docs/SPEC13_COVERAGE_DRIVEN.md` | One paragraph: `tool_condition` is injected, and the injected-vs-observed distinction is what the bin means. |

**Not touched:** `src/agenttic/scoring/**`, the Step 14 promotion gate,
`coverage/extractors.py` (P0 owns the predicate change),
`schema/trace.py` (no new field, no new `SpanKind` — `SCHEMA_VERSION` stays
`0.2.0`; the session-shape phase will bump it for `user_turn` and P4 must not
race it), `harness/runner.py`, any existing test.

---

## 5. Tests

All in `tests/stimulus/test_faults.py`, offline, using the `FakeClient` scripted
double from `tests/test_adapter_simple.py:32-45` and the socket-blocking
`no_network` fixture pattern from `tests/stimulus/test_cdv.py:32-38`.

**Fails on today's code** (marked ✗ — today they fail at import: there is no
`agenttic.stimulus.faults`; and on substance: `AnthropicSimpleAgent` takes no
`faults` argument, and nothing in the repo can produce a stale or malformed tool
result at all).

| Test | What it proves |
|---|---|
| ✗ `test_every_degraded_tool_condition_realizes_an_executable_plan` | AC-2. For each of the five kinds, `realize` emits `env_seed["fault_plan"]` with a matching spec. Today `injected_failures` is the only trace of the request and nothing consumes it. |
| ✗ `test_the_plan_is_identical_across_interpreter_processes` | AC-3. Subprocesses under `PYTHONHASHSEED=1,2,3` print the same `plan_id`. Guards against inheriting the `realize.py:123` bug. |
| ✗ `TestEveryKindFires::test_the_fault_lands_on_the_planned_tool_and_index` | AC-4, parametrized over `FAULT_KINDS`. The `tool_call` span carries `attributes["fault"]` naming kind, bound tool and index. |
| ✗ `TestEveryKindFires::test_the_matching_coverage_predicate_fires` | AC-5. `run_predicate(f"tool_{kind}", trace)` is True on the trace that fault produced — the bin is reached by injection, not by vocabulary. |
| ✗ `TestEveryKindFires::test_hard_kinds_error_and_soft_kinds_do_not` | The `HARD`/`SOFT` split is real: `_errored()` (`extractors.py:60`) is True for the three hard kinds and False for the two soft ones. This is the distinction that separates "recovery was tested" from "silent corruption was tested". |
| ✗ `test_retry_and_recovery_are_reachable_only_because_the_fault_fires` | AC-6, the phase's headline. One script (`lookup_kb` → 500 → `lookup_kb` → ok → answer): with the injector both `traj_retry_after_error` and `traj_recovered_from_tool_failure` are True; with `faults=None` and the identical script both are False. |
| ✗ `test_a_fault_fires_once_not_forever` | AC-7. Second call to the same tool returns the real KB value. The anti-`builtins.py:172` guard: a fault that never lifts makes recovery unprovable rather than untested. |
| `test_a_planned_fault_that_never_fired_is_a_divergence_not_a_hit` | AC-8. Agent calls no tool; `planned_but_unfired` is non-empty, no span is stamped, and `collect(...).divergence()` lists `tool_condition/<kind>`. Passes today for the wrong reason (nothing ever fires) — it is the regression guard that keeps injected-and-observed honest once faults exist. |
| ✗ `test_the_fault_provenance_is_never_shown_to_the_model` | AC-9. Every `messages` payload recorded by `FakeClient.requests` is free of `fault`, `inject`, `agenttic` and every `FAULT_KINDS` member. |
| ✗ `test_an_adapter_without_a_plan_is_byte_identical` | AC-10. Same script with and without an injector: identical span kinds/names/inputs/outputs/errors, no `fault` attribute anywhere. |
| ✗ `test_a_real_tool_error_is_never_relabelled_as_an_injected_fault` | Under a soft kind, a tool that genuinely fails returns its own error and gets **no** `fault` attribute. Prevents the injector from manufacturing provenance for failures it did not cause — the exact miscrediting P0 exists to stop. |
| ✗ `test_replay_restores_the_frozen_fault_plan` | AC-11. Freeze via `run_until_closure`, `replay` against the same space, `plan_id` matches. |
| ✗ `test_faults_module_imports_no_model_client` | AC-12, mirrors `tests/stimulus/test_cdv.py:57` — AST scan for `anthropic`/`openai`/`httpx`/`requests`/`urllib3`. |
| ✗ `test_planning_10k_scenarios_needs_no_network` | AC-12, under `no_network`. |
| ✗ `test_the_fault_vocabulary_matches_the_scenario_space` | AC-1. `FAULT_KINDS` ≡ `tool_condition` values minus `all_ok` (`spaces/conversational_transactional.py:27-30`), and every kind has a `tool_*` predicate registered in `PREDICATES`. Drift in either direction fails. |
| ✗ `test_the_cdv_loop_closes_tool_condition_with_a_real_faulting_adapter` | The end-to-end claim: `run_until_closure` with `faulting_executor(...)` over a scripted client closes `tool_condition` bins that are unreachable with a clean adapter. Compares against the same loop with `faults` disabled. |

Baseline for AC-13: `pytest -q` before P4 → the 8 known failures in
`tests/coverage/test_session_shape.py`, nothing else.

---

## 6. Risks, and what this phase deliberately does not do

### Risks

* **P4 alone does not make the credit sound.** After P4, a scenario can fire a
  real timeout *and* a user typing "timeout" still credits `tool_timeout` through
  `_tool_signal` (`extractors.py:172`). P4 makes the bin deliberately reachable;
  P0 makes reaching it the only way to be credited. Shipping P4 without P0 leaves
  the over-crediting hole exactly as wide as it is today.
* **Order matters, in the other direction.** P0 landing *first* would make the
  five degraded bins uncreditable by anything (`tool_all_ok` survives — it reads
  `_errored`, not the sniff), dropping reported `tool_condition` closure to a
  fifth of its current value with no mechanism to recover it. P4 must land before
  or with P0.
* **Late binding can bind to the wrong tool.** With `tools=()` the fault attaches
  to whatever the agent calls first. On a multi-tool agent that is a real choice,
  not a neutral one; `late_bound: True` in the provenance makes it visible rather
  than silent, and passing `tools=` removes the ambiguity.
* **Closure can now be *bought*.** Injecting a fault on every scenario would
  close `tool_condition` quickly and mean nothing. The `tool_condition × intent`
  and `action_risk × tool_condition` crosses
  (`models/conversational_transactional.py:188,194`) are the partial defence;
  keeping `all_ok` weighted 3.0 in the space (`spaces/...:30`) is the other.
  Neither is a guarantee, and no test in P4 can prove a suite is not gaming it.
* **Fault injection requires the platform to execute the tools.** A black-box
  adapter that runs its own tools cannot be injected. Silence here would be the
  same honesty hole as `capabilities.py:195`, which is why P4 changes that
  endpoint rather than leaving the new capability undisclosed.
* **Shared-adapter concurrency.** The injector is per-run mutable state, so
  faults are available on the per-scenario executor path, not on `run_suite`,
  which drives one adapter across concurrent cases (`harness/runner.py:137`).
  `with_faults()` exists to make one-adapter-per-scenario cheap; using a single
  faulted adapter under `run_suite` would interleave call counters across cases.
  No test can catch that misuse, so the docstring says it plainly.
* **`realize()` is not reproducible across processes** (§1). P4 does not inherit
  the bug but runs alongside it, so `content_sha256` still varies per process
  while `plan_id` does not. Anyone reading a frozen scenario should know which of
  the two is trustworthy.

### Deliberately not done

* **Not touching `coverage/extractors.py`.** The predicate change — requiring
  injected-and-observed — is P0's, in one place, once.
* **No new span kind, no schema bump.** Provenance rides on `Span.attributes`
  (`schema/trace.py:53`), which already exists. The session-shape phase is going
  to bump `SCHEMA_VERSION` for `user_turn`; two phases bumping it independently
  would collide on Hard Rule 1's "update all fixtures in the same commit".
* **No environment, no simulated user, no second turn.** P4 makes the *tool
  boundary* adversarial. The scenario is still one dict, one message, one
  `adapter.run` (`adapters/base.py:32`, `harness/runner.py:137`). Fault injection
  on a single-turn run is a real capability and a small one; it does not entitle
  anything to claim an environment exists.
* **No production CDV wiring.** `faulting_executor` satisfies `cdv.Executor`
  (`cdv.py:77`) and is exercised by P4's own tests. Whether `run_until_closure`
  (`cdv.py:201`) runs on the production path stays with whichever phase owns it.
* **No scoring or gate changes.** `faulting_executor(score=None)` reports
  `passed=True`. P4 moves coverage, never a verdict; `scoring/**` and the Step 14
  promotion gate are untouched.
* **No composition with the enforcement gateway.** Faults sit inside
  `_exec_tool`, below `GuardedHoneypotAgent`'s interception
  (`redteam/honeypot.py:291-304`), so a decoy call is always evaluated by the
  gateway and never pre-empted by a fault. Injecting faults *into* enforced calls
  is a separate question and is not answered here.
* **Not fixing `realize.py:123`.** Changing the order id changes
  `content_sha256` for every frozen regression already stored. Reported in §1,
  left for a phase that owns the migration.
* **`stimulus/oracle.py`'s unread `PolicyDoc.refund_window_days` is untouched** —
  a neighbouring dead field, not this phase's.
