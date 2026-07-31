# P0 — Stop over-reporting

**Phase intent:** correct what coverage *claims*. No new capability is added. This
is the only rescue phase that changes existing scorecard numbers, and it changes
them by removing credit that was never earned.

`bins_fingerprint()` changes. That is the point, and it is the mechanism that
makes the change a diff a human approves rather than a silent edit
(`coverage/model.py:236-251`). Scorecards already written keep the fingerprint
they were written with — nothing recomputes coverage for a stored scorecard
(`ops.py:307` writes it into the row; `signoff_from_run` at `ops.py:349`
re-parses the stored JSON and never recollects).

---

## 1. Context — what is broken, verified by reading

Every citation below was opened and read. Where the briefing I was given was
wrong, it is corrected in **bold**.

### 1.1 The coverpoint that measures the wrong thing

`coverage/extractors.py:214` — `_turns()` counts `llm_call` spans:

```python
def _turns(trace: Trace) -> int:
    return sum(1 for s in trace.spans if s.kind == "llm_call")
```

`AnthropicSimpleAgent.run` (`adapters/anthropic_simple.py:122-189`) seeds exactly
one user message and then loops: one `llm_call` span per tool-use iteration.
Three tool calls means four `llm_call` spans, which `session_multi_turn`
(`extractors.py:223`) reports as a multi-turn session. Nobody spoke twice. The
coverpoint is named `session_shape` and described as "Single exchange,
multi-turn, or resumed against prior memory"
(`coverage/models/conversational_transactional.py:66-75`); what it actually
measures is agent steps.

This converts an untested situation into a covered one, and closure inherits it:
`session_shape` is one of five required coverpoints in the baseline model
(`coverage/models/baseline.py:54`), so the mislabel is worth up to 1/3 of one
sixth of the headline on every multi-step run.

### 1.2 The bin that cannot be hit the way it says, and can be hit the way it does not

`coverage/extractors.py:228-233`:

```python
@predicate("session_resumed_with_memory")
def _resumed(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "resumed") is True or _attr(trace, "memory_seeded") is True:
        return True
    return any("memory" in (s.name or "").lower()
               or "resume" in (s.name or "").lower() for s in trace.spans)
```

**Correction to the briefing:** this bin is not "structurally unhittable". Its
*declared* path is — nothing in the repo ever sets `resumed` or `memory_seeded`
(`grep` over `src/` finds only this read). But the substring fallback is very
hittable, and hittable for the wrong reason: any agent with a tool named
`memory_lookup` claims the bin. Worse, `session_single_turn` and
`session_multi_turn` both AND-in `not _resumed(...)` (`:220`, `:225`), so such an
agent silently loses the two turn bins as well. The bug is not absence; it is a
false positive that poisons its neighbours.

### 1.3 `tool_condition` is string coincidence

`coverage/extractors.py:172`:

```python
def _tool_signal(trace: Trace, *needles: str) -> bool:
    return any(any(n in _blob(s) for n in needles)
               for s in trace.spans if s.kind in ("tool_call", "error"))
```

`_blob` (`:69-76`) serializes `input`, `output` and `attributes` to JSON and
lowercases the lot. OTel-ingested spans carry content **digests** in exactly
those fields — `ingest/mapping.py:168-172` sets
`span.input["content_sha256"]` / `span.output["content_sha256"]`. A sha256 hex
digest is 64 characters drawn from `0-9a-f`, so it contains the needles
`"500"`, `"502"`, `"503"`, `"504"` and `"429"` by chance.

Measured, 20 000 trials over a span carrying one input digest and one output
digest (`hashlib.sha256`, `random.Random(7)`):

| predicate | false-credit rate per span |
|---|---|
| `tool_error_5xx` | **11.5 %** |
| `tool_rate_limited` | **3.0 %** |
| `tool_timeout` | 0 % (its needles are alphabetic) |

That is not a hypothetical. `tool_condition` is described as "What the
environment did to the agent" (`conversational_transactional.py:54`) and there is
no environment: nothing in the run path injects a timeout, a 5xx or a rate limit.
Every credit in that group today is either a real error message the agent's own
tool produced, or coincidence.

### 1.4 The public surface does not name the hole

`server/routes/capabilities.py:195-204` — `not_covered` names model internals,
memory semantics, multi-agent interaction, and undeclared bins. It does **not**
say that there is no environment, no simulated user, and no real multi-turn
session. Those are the three largest limits of the product and the endpoint is
silent on all three, while the same endpoint enumerates `session_shape` and
`tool_condition` as things it measures (`:54-74`).

`ui/src/pages/LandingPage.tsx:207-211` goes further and advertises them:

> "a service times out, a customer pushes back, it is asked to do something it
> cannot undo"

That sentence enumerates fault injection, a simulated user, and irreversible
actions. The first two do not exist. `LandingPage.tsx:87` also pins
`session_shape` at `0.333` in the illustrative wheel — the exact number the
mislabel produces.

### 1.5 The stimulus side advertises a dimension it cannot realize

**New finding, not in the briefing.**
`stimulus/spaces/conversational_transactional.py:35-36` declares:

```python
Dimension("session_shape", ("single_turn", "multi_turn", "resumed_with_memory")),
```

`stimulus/realize.py:124-133` reads `intent`, `emotional_register`,
`data_condition`, `policy_vector` and `tool_condition`. It never reads
`session_shape`. So a point asking for `session_shape=multi_turn` realizes text
identical to `single_turn`, and `collect` records a **stimulus hit** for it
(`coverage/collect.py:293-295`). The generator claims a corner it cannot reach,
on the stimulus side, by construction.

### 1.6 Stimulus closure is reported as a measurement when nothing was requested

`ops.py:304` collects with `Sample(trace=t)` only — no `scenario`, no
`requested`. `CoverageReport.stimulus_closure` (`collect.py:78-80`) therefore
returns `0.0`, and `build_signoff` copies it into the signed evidence
(`schema/signoff.py:364`), where `reporting/signoff_report.py:40` prints
"stimulus 0% requested / trace N% exhibited". Zero-requested is not a
measurement; it is the absence of one.

### 1.7 The 95 % target is hardcoded against the repo's own banner

`config.yaml:1` says "All model names, thresholds, sample rates live here
(Hard Rule 7)". **Correction to the briefing: it is five files in `src/`, not
four**, plus two more literals:

- `coverage/model.py:201` — `closure_target: float = 0.95`
- `coverage/collect.py:141` — `closure_target: float = 0.95`
- `coverage/models/baseline.py:39` — `closure_target: float = 0.95`
- `coverage/models/conversational_transactional.py:196` — `closure_target=0.95`
- `schema/signoff.py:73` — `closure_target: float = 0.95`
- `reporting/scorecard_report.py:174` — `cov.get("closure_target", 0.95)`
- `ui/src/verification.tsx:185` — `target={c.closure_target ?? 0.95}`

### 1.8 Dead advertisements

- `redteam/probe.py:29` lists `"multi_turn"` in `TECHNIQUES`;
  `redteam/authors.py:101`, `:128`, `:156` implement it as one message that
  *asserts* prior context ("Thanks, that helped! One more thing:"). The technique
  is real and useful. Its name is a lie.
- `rubric_engine/synthesize.py:199-208` emits a scaffold case with
  `input={"feature_scaffold": feature}` and `tags=[..., f"feature:{feature}"]`;
  `rubric_engine/evaluate.py:71-75` computes `covered` from exactly those tags.
  Since `synthesize_suite` (`:231-237`) adds a scaffold for *every* uncovered
  required feature, `missing` is provably always empty — the suite-match arm of
  `integrity_check` is dead code, and `multi_turn_state`
  (`schema/archetype.py:31`, required by four archetypes in `cores.py`) is
  "covered" by a case that is one dict.
- `stimulus/oracle.py:31-36` — `PolicyDoc`'s docstring says "every field is
  something the oracle reads"; `refund_window_days` is read nowhere
  (`grep` over `src/` and `tests/` finds only the declaration).
- `verification/cdv.py:75-77` — `Executor`'s docstring says "Real wiring runs the
  existing harness + scoring engine". Verified: `run_until_closure`
  (`cdv.py:201`) has **zero** callers outside `tests/`.

### 1.9 Four live bugs

- **A hash as a prompt.** `hardening.py:263-273` returns `(dict(span.input),
  True)` for the first span with any input. For an OTel-ingested trace that input
  is `{"content_sha256": …, "parts": N}` (`ingest/mapping.py:168-172`). The
  promoted regression case would be re-run by `harness/runner.py:137` as
  `adapter.run(tc.input)` — the agent is handed a digest as its prompt and the
  case is marked `complete=True`.
- **A hash rendered to the judge.** `ingest/mapping.py:290-293` falls back to
  `last.output.get("content_sha256", "") or last.name` as the trace's
  `final_output`. `live/monitor.py:91` sends that trace to
  `judge.score_criterion`, and `scoring/judge.py:135` renders
  `f"AGENT FINAL OUTPUT:\n{trace.final_output}"` verbatim.
- **Stale-trace resume.** `harness/runner.py:99-107` reuses a persisted trace
  keyed on `adapter.config_hash()`. `adapters/anthropic_simple.py:113-120` —
  `describe()` returns adapter, model, system_prompt, tools, max_steps. The
  knowledge base the `lookup_kb` tool reads (`:233`) is not in it. Edit `kb.json`
  and every prior trace is silently reused as if it were current.
- **Shared adapter state under the harness's threads.**
  `adapters/blackbox_http.py:136` `self._last_call = 0.0`, read at `:175` and
  written at `:188`. `run_suite` executes up to `harness.max_parallel`
  (`config.yaml:13` = 5) `adapter.run` calls on ONE adapter instance via
  `asyncio.to_thread` (`runner.py:137`). N threads read the same `_last_call`,
  compute the same wait, sleep it, and fire together — the promised floor
  divided by N. **Honest reachability note:** the one production path that sets
  `min_interval_s` (`connect.py:228`) is also the one that forces
  `max_parallel=1` (`server/routes/scan.py:238-240`), so the race is not
  currently reachable in-product. It is reachable from the library API and is one
  config edit away. The class of defect — a documented invariant that only holds
  by accident — is exactly what this phase exists to stop.

### 1.10 The executable spec already exists and is red

`tests/coverage/test_session_shape.py` is present in the working tree and
**untracked** (`git status --porcelain` → `?? tests/coverage/test_session_shape.py`
at `c3651ac`). It runs today as **8 failed**:

```
UnknownPredicateError: 'agent_steps_multi'                      (x2)
AssertionError: assert True is False   # session_multi_turn on a tool loop
AssertionError: assert False is True   # session_single_turn on a tool loop
ValidationError: kind ... 'user_turn'                           (x4)
```

It pins the predicate names (`agent_steps_single`, `agent_steps_multi`), the new
span kind (`user_turn`), and the tightened resumption rule. It is the spec for
§(a) and §(b) below and must be committed as part of this phase. Per Hard Rule 1
of `CLAUDE.md` it must not be edited.

---

## 2. Acceptance criteria

Each is checkable by running the command shown. No criterion says "improve".

1. `pytest -q tests/coverage/test_session_shape.py` reports **8 passed, 0
   failed**. (Today: 8 failed.) The file is committed and unmodified.
2. `python -c "from agenttic.schema.trace import SCHEMA_VERSION, SpanKind; import typing; assert SCHEMA_VERSION=='0.3.0'; assert 'user_turn' in typing.get_args(SpanKind)"` exits 0.
3. `baseline_model().coverpoint("agent_steps")` exists, is `kind="deterministic"`,
   and its bin ids are exactly `["single_step","multi_step","other"]`;
   `"agent_steps" in agenttic.coverage.model.DETERMINISTIC_BY_CONSTRUCTION`.
4. `baseline_model().coverpoint("session_shape").measurable is False` and its
   `not_measurable_reason` is non-empty; its bin `resumed_with_memory` has
   `waived is True` with a non-empty `reason`. Constructing either without a
   reason raises `ValidationError`.
5. `verify_op(traces)` output satisfies:
   `cov["per_coverpoint"]["session_shape"]["closure"] is None`,
   `cov["per_coverpoint"]["session_shape"]["not_measurable"]` is non-empty,
   `cov["per_coverpoint"]["session_shape"]["unhit"] == []`, and no entry in
   `cov["holes"]` has `where == "session_shape"`.
6. `baseline_model().bins_fingerprint() != "<the v2 fingerprint recorded in the
   test>"`, and `baseline_model().version == 3`. A scorecard fixture whose stored
   `coverage.bins_fingerprint` is a v2 value still reads back that exact value
   after `signoff_from_run` — nothing recomputes it.
7. Provenance guard, three checks in one test module:
   (a) a trace whose only tool span carries `output={"content_sha256":
   "…500…"}` and no `error` does **not** hit `tool_error_5xx`;
   (b) `run_predicate("tool_timeout", trace, {"injected_failures": ["timeout"]})`
   is `True` for a trace with no error text at all;
   (c) a span with `kind="error", name="timeout"` hits `tool_timeout`.
8. `GET /api/capabilities` → `not_covered` contains three entries whose text
   names, respectively: no simulated environment / fault injection, no simulated
   user or second human turn, no real resumed sessions. Asserted by substring in
   `tests/test_capabilities.py`-style test; `BANNED_CLAIMS` check still passes.
9. `grep -rn "0\.95" src/agenttic/coverage src/agenttic/schema/signoff.py src/agenttic/reporting/scorecard_report.py` returns exactly one line:
   the definition of `DEFAULT_CLOSURE_TARGET` in
   `src/agenttic/coverage/targets.py`. Both `config.yaml` and `config.prod.yaml`
   contain a `coverage:` block with `closure_target: 0.95`. With
   `cfg["coverage"]["closure_target"] = 0.5`, `verify_op(traces, cfg=cfg)["closure_target"] == 0.5`.
   `load_config` raises on `closure_target: 1.5`.
10. `grep -rn "refund_window_days" src tests` returns nothing.
11. `"multi_turn" not in agenttic.redteam.probe.TECHNIQUES` and
    `len(TECHNIQUES)` is unchanged at 6; every `AttackSpec.technique` produced by
    `redteam/authors.py` is in `TECHNIQUES` (existing invariant, re-asserted).
12. For a draft whose required features are covered only by scaffolds,
    `evaluate(...)` returns a draft with `scaffold_only_features ==
    ["multi_turn_state", …]` and that list appears in `draft.review`. A feature
    with no case at all still yields `INTEGRITY_FAILED` (unchanged).
13. `hardening._reconstruct_input(t)` returns `({}, False)` for a trace whose
    only span input is `{"content_sha256": "…", "parts": 1}`, and
    `live_catch_candidates` reports `input_reconstructed: False` for it.
14. `spans_to_traces` never returns a `Trace` whose `final_output` is a bare
    hex digest — for a content-redacted group it returns `final_output == ""` and
    a `no_final_output:<trace_id>` note in the report.
    `LiveMonitor.ingest(t)` for such a trace returns `False`, stores the trace,
    runs assertions, and makes **zero** judge calls (asserted with a judge double
    that raises).
15. `AnthropicSimpleAgent.describe()` contains `kb_sha256`; two agents over the
    same KB file have equal `config_hash()`, and rewriting the KB's contents
    changes it. `run_suite(resume=True)` does not reuse a trace whose
    `agent_config_hash` was produced against different KB contents.
16. 8 threads calling `BlackBoxHTTPAgent.run` on one instance with
    `min_interval_s=0.05` and a stub transport produce request timestamps whose
    consecutive gaps are all `>= 0.05` (minus a 5 ms tolerance). Fails on today's
    code.
17. `seed_space()` declares no `session_shape` dimension, and a test asserts
    every declared `dim_id` is read by `realize()` (enumerated against the keys
    `realize` consumes) — so a future dimension cannot be added without a
    realizer.
18. `cd ui && npm run verify` passes. `DECLARED_COVERPOINTS` contains
    `agent_steps`; `LANDING_WHEEL`'s `session_shape` entry has `value: null`; the
    landing copy at the "Why we said no" section no longer asserts that a service
    is made to time out or that a customer pushes back.
19. `pytest -q` is green (2311 tests + the new ones), and `signs_off` semantics
    are unchanged: `schema/signoff.py:195-203` is not edited except for the
    `closure_target` default and the `stimulus_closure` type widening.

---

## 3. Design

### (a) `agent_steps` and a `session_shape` that counts humans

`schema/trace.py` — add one `SpanKind` value and bump. Per the module's own rule
(`:5-13`) a new `Span.kind` is a MINOR bump, and Hard Rule 1 requires the bump in
the same commit. No fixture pins the literal `"0.2.0"` (every fixture imports
`SCHEMA_VERSION`; only `tests/test_schema.py:102` compares against the symbol),
so this is a one-line edit.

```python
SCHEMA_VERSION = "0.3.0"  # 0.3.0: + Span.kind "user_turn" (MINOR)

SpanKind = Literal[
    "llm_call", "tool_call", "retrieval", "agent_decision", "error",
    "final_output",
    # A turn taken by the *human* (or a simulated user standing in for one).
    # Nothing emits this yet — that is P3. It exists now so `session_shape` can
    # be defined against the thing it names instead of against llm_call spans.
    "user_turn",
]
```

`coverage/extractors.py` — two counters where there was one:

```python
def _agent_steps(trace: Trace) -> int:
    """Model calls. This is what `_turns()` actually counted."""
    return sum(1 for s in trace.spans if s.kind == "llm_call")

def _human_turns(trace: Trace) -> int:
    """Turns taken by the other party. Nothing emits `user_turn` before P3, so
    this is 0 on every trace in existence — which is why the coverpoint that
    reads it is declared not-measurable rather than reported as covered."""
    return sum(1 for s in trace.spans if s.kind == "user_turn")
```

Predicates (names pinned by the red test):

| predicate | rule |
|---|---|
| `agent_steps_single` | `_agent_steps(trace) == 1` |
| `agent_steps_multi` | `_agent_steps(trace) >= 2` |
| `session_single_turn` | `_human_turns(trace) <= 1 and not _resumed(...)` |
| `session_multi_turn` | `_human_turns(trace) >= 2 and not _resumed(...)` |
| `session_resumed_with_memory` | `_attr(trace,"resumed") is True or _attr(trace,"memory_seeded") is True` — the substring fallback is deleted |

`agent_steps == 0` matches neither bin and lands in `other`. That is deliberate:
a black-box trace has no observable step count, and `other` drift is a finding
(`coverage/model.py:11-13`), not a silent zero. Expect black-box scans to report
`agent_steps` other-drift at 1.0 — true, and previously invisible.

New coverpoint in `coverage/models/conversational_transactional.py` (both models
import it, so there is one definition):

```python
AGENT_STEPS = Coverpoint(
    coverpoint_id="agent_steps",
    description=("How many model calls one exchange took. This is what the old "
                 "`session_shape` measured; it is a real and useful axis, and it "
                 "is not a session."),
    kind="deterministic",
    bins=[_det("single_step", "agent_steps_single"),
          _det("multi_step", "agent_steps_multi"),
          OTHER])
```

`"agent_steps"` joins `DETERMINISTIC_BY_CONSTRUCTION` (`coverage/model.py:39-45`).

### (b) not-measurable coverpoints and the waived bin

`session_shape` keeps its bins and its predicates. What changes is that the model
declares it cannot be measured yet — the vacuity rule (`Hard Rule 60`) turned on
coverage itself. New fields on `Coverpoint`, mirroring `Bin.waived`
(`coverage/model.py:100-103`):

```python
class Coverpoint(BaseModel):
    ...
    #: False when no producer in the system can emit the evidence this
    #: coverpoint reads. Reported as not-measurable; excluded from closure and
    #: from holes. NOT the same as "unhit": an unhit bin is a finding, an
    #: unmeasurable one is a confession.
    measurable: bool = True
    not_measurable_reason: str = ""
```

Validator: `not measurable` requires a non-blank reason. `measurable` joins the
`bins_fingerprint()` payload (`coverage/model.py:241-249`) so flipping it in P3
is an approved diff.

```python
SESSION_SHAPE = Coverpoint(
    coverpoint_id="session_shape", ...,
    measurable=False,
    not_measurable_reason=(
        "no producer emits `user_turn` spans: a case is one dict delivered as one "
        "user message (adapters/base.py:32), so there is no second human turn to "
        "observe. Reported as not measured rather than credited to single_turn."),
    bins=[
        _det("single_turn", "session_single_turn"),
        _det("multi_turn", "session_multi_turn"),
        Bin(bin_id="resumed_with_memory",
            predicate_ref="session_resumed_with_memory",
            waived=True,
            reason=("nothing in the harness seeds prior memory or sets the "
                    "`resumed` span attribute, so this bin can only be reached "
                    "by an agent that happens to name a tool 'memory_*' — which "
                    "is coincidence, not evidence. Waived until a resumed-session "
                    "harness exists.")),
        OTHER,
    ])
```

Collection (`coverage/collect.py`):

- `CoverpointCoverage` gains `measurable: bool = True` and
  `not_measurable_reason: str = ""`, populated in `collect()` from the model.
- `unhit` returns `[]` when not measurable.
- `CoverageReport.trace_closure` (`:144-149`) filters
  `c.required and c.measurable`.
- `holes()` (`:183-196`) skips not-measurable coverpoints.
- `as_dict()` (`:212-218`) emits `"trace_closure": None` plus
  `"not_measurable": reason` for them.

`ops.py:313-316` mirrors this in the run summary (`"closure": None,
"not_measurable": …, "unhit": []`), and `schema/signoff.py:366-367` skips
not-measurable coverpoints when building `unhit_bins`.

`reporting/scorecard_report.py:186` currently formats `cp.get('closure', 0)`
with `:.0%` and would raise `TypeError` on `None`. It renders
`not measurable — <reason>` in the closure column instead.

The console needs no change: `dimsFromCoverage`
(`ui/src/components/ds/CoverageWheel.tsx:200-206`) already maps a non-numeric
`closure` to `null`, and a `null` dimension is drawn hatched to the rim
(`:125`). The sector flips from a fake 33 % wedge to the honest hatch with zero
component edits. `DECLARED_COVERPOINTS` (`:210`) gains `"agent_steps"` so the new
axis renders.

### (c) `tool_condition` provenance guard

Two changes, both narrow.

First, a digest is never evidence. `_blob` (`extractors.py:69-76`) drops
digest-shaped values before serializing:

```python
_DIGEST_RE = re.compile(r"^[0-9a-f]{32,}$")
_DIGEST_KEYS = ("content_sha256",)

def _evidential(obj):
    """Strip content digests before any substring test. A sha256 hex string
    contains '500' about 11% of the time and '429' about 3% of the time
    (measured, 20k trials), which is how ingested traffic was silently crediting
    tool_error_5xx and tool_rate_limited."""
```

Second, the five `tool_*` predicates move off `_tool_signal` onto a guarded
helper. Credit requires provenance: the condition was *injected*, or a span
carries a *structured* error naming it.

```python
_INJECTABLE = ("timeout", "error_5xx", "rate_limited", "stale_data",
               "malformed_response")

def _injected(scenario: dict | None, condition: str) -> bool:
    """The scenario asked for this failure. `realize()` fills
    `injected_failures` from the tool_condition bin id (stimulus/realize.py:157),
    so the names line up 1:1 with the bins."""
    return bool(scenario) and condition in (scenario.get("injected_failures") or [])

def _condition_signal(trace: Trace, scenario: dict | None, condition: str,
                      *needles: str) -> bool:
    """A tool condition is credited only with provenance.

    Either the scenario injected it, or a span actually errored and its
    STRUCTURED error channel names it — `span.error`, `output["error"]`, or the
    name of a synthesized `error` span (harness/runner.py:52-56 puts the
    condition in the name). A needle found anywhere in a serialized span is
    coincidence; there is no environment in this build that could have caused it.
    """
    if _injected(scenario, condition):
        return True
    for s in trace.spans:
        if s.kind not in ("tool_call", "error") or not _errored(s):
            continue
        text = " ".join(filter(None, [
            s.name if s.kind == "error" else "",
            s.error or "",
            str((s.output or {}).get("error") or ""),
        ])).lower()
        if any(n in text for n in needles):
            return True
    return False
```

The `data_*` predicates keep `_tool_signal` — restricting them to the error
channel would silently drop `ambiguous` / `contradictory`, which have no error
representation, and redesigning `data_condition` is not this phase. They do get
the digest strip, which is what was crediting them falsely.

`_tool_signal` keeps its signature; only its inputs are cleaned.

### (d) Disclose the absent environment

`server/routes/capabilities.py` — three entries appended to `not_covered`, worded
so a reader can act on them and phrased to avoid the `BANNED_CLAIMS` substrings:

```python
"the environment around the agent — we observe the tool failures a run happens "
"to hit; we do not inject them. A `tool_condition` bin is credited only when a "
"scenario injected the condition or a span carries a real error",

"a simulated user — a case is one message delivered once. Nothing pushes back, "
"changes its mind, or takes a second turn, so `session_shape` is reported as "
"not measured rather than counted as single-turn coverage",

"resumed sessions and prior memory in a scored run — the memory battery tests a "
"store directly (see supply_chain.memory); no scored agent run is resumed "
"against seeded state",
```

`ui/src/pages/LandingPage.tsx` — the sentence at `:207-211` is rewritten to
describe the axis the product actually measures, and the illustrative wheel entry
`{ id: "session_shape", value: 0.333 }` (`:87`) becomes `value: null` with
`{ id: "agent_steps", value: … }` added; the `label` prop at `:200-201` is
updated to match the new hatch count, since it is the accessible text and must
not contradict the drawing.

### (e) The closure target moves to config

New `src/agenttic/coverage/targets.py` — one definition, no dependencies:

```python
DEFAULT_CLOSURE_TARGET = 0.95

def closure_target(cfg: dict | None = None) -> float:
    """The closure target, from `coverage.closure_target` in config.yaml.
    Hard Rule 7: config.yaml:1 claims every threshold lives there; this one was
    written into five modules instead."""
```

All five `0.95` literals become `DEFAULT_CLOSURE_TARGET`.
`baseline_model` / `seed_model` take `closure_target: float | None = None` and
resolve `None` to the default, so existing callers are unaffected.
`verify_op(traces, *, cfg: dict | None = None)` — a keyword with a default, so
the eight existing `verify_op([...])` call sites in `tests/` keep working;
`ops.py:403` passes `cfg`.

`config.yaml` / `config.prod.yaml` gain:

```yaml
coverage:
  closure_target: 0.95   # trace-closure bar the sign-off gates on (Hard Rule 56)
```

`config.py` gains `_validate_coverage_surface(cfg)` alongside
`_validate_certification_surface` (`config.py:28`): validate only when the block
is present, require a number in `(0, 1]`.

`ui/src/verification.tsx:185`'s `?? 0.95` stays. The browser has no
`config.yaml`; the value always arrives on the payload, and the fallback is a
render guard, not a threshold. Named here so it is a decision, not an oversight.

Also in this section: `CoverageLeg.stimulus_closure` widens to
`float | None` and `build_signoff` passes `None` when no sample carried a
`requested` mapping (`CoverageReport` gains a `stimulus_requested: bool`).
`signoff_report.py:38-41` prints "stimulus not requested" for `None`. Stored
sign-offs are unaffected: `signoff_from_run` re-parses stored JSON, `0.0` parses
into `float | None` and dumps back as `0.0`, so `content_sha256()` of an existing
sign-off is bit-identical and every issued certificate still verifies
(`certification/attest.py:239`).

### (f) Delete or rename the dead advertisements

| where | change |
|---|---|
| `stimulus/oracle.py:36` | delete `refund_window_days`. Nothing reads it; the class docstring at `:31-32` claims everything is read. A comment names what would have to exist first (an `order_age_days` stimulus dimension). |
| `stimulus/spaces/conversational_transactional.py:35` | delete the `session_shape` dimension. `realize()` never reads it, so requesting `multi_turn` recorded a stimulus hit for text identical to single-turn. Changes `space.fingerprint()` — the same approved-diff mechanism as bins. |
| `redteam/probe.py:29` + `authors.py:101,128,156` | rename the technique `multi_turn` → `false_prior_context`. The attack is real: a single message asserting a conversation that never happened. Only its name over-claims. |
| `verification/cdv.py:75-77` | correct the `Executor` docstring: state that no production caller supplies a real executor today and name the phase that will. Do not delete `run_until_closure` — it is the loop P2 wires up. |
| `rubric_engine/evaluate.py:71` | see below. |

For `evaluate.py:71`, the minimal honest fix that adds no capability:

```python
SCAFFOLD_TAG = "scaffold"

def feature_coverage(cases) -> tuple[set[str], set[str]]:
    """(exercised, scaffold_only).

    A scaffold case NAMES a feature; it does not exercise it. Deriving `covered`
    from the same `feature:` tag the scaffold writes on itself made the check a
    tautology — `missing` was provably always empty.
    """
```

`_scaffold_case` (`synthesize.py:199`) adds `SCAFFOLD_TAG` to its tags.
`integrity_check` keeps its `(ok, problems)` signature and keeps blocking only on
a feature with **no** case at all — unchanged behaviour, so no existing flow
regresses. `EvaluationDraft` gains `scaffold_only_features: list[str]`, populated
in `evaluate()` and rendered in `review`, and
`schema/archetype.py` gains, beside `SUITE_FEATURES`:

```python
#: features no harness in this build can exercise, with the reason. A scaffold
#: for one of these is a placeholder, not coverage (same rule as a waived bin).
UNEXERCISABLE_FEATURES = {
    "multi_turn_state": "a case is one dict delivered as one user message; there "
                        "is no second turn to hold state across",
}
```

Promoting scaffold-only to a hard block is P3's call, once multi-turn cases can
actually be generated. P0 makes it visible and named.

### (g) The four live bugs

New `src/agenttic/trace_content.py` — one implementation, three importers:

```python
_PLACEHOLDER_KEYS = frozenset({"content_sha256", "parts", "tool_name"})

def digest_only(payload: dict) -> bool:
    """True when a span's input/output carries only a content digest and its
    provenance — the shape ingest/mapping.py:168-172 writes when the producer
    sent no content. A digest is a reference to content, never content."""

def content_bearing(trace: Trace) -> bool:
    """True when the trace's final_output is text a reader (or a judge) can
    evaluate, rather than a digest or nothing."""
```

- **`hardening.py:263`** — `_reconstruct_input` skips digest-only span inputs and
  returns `({}, False)` if nothing usable remains. The existing honest path takes
  over: the case promotes as `needs-review` + `partial` with `expected=None`
  (`hardening.py:322-341`).
- **`ingest/mapping.py:290-293`** — never fall back to a digest. `final_output`
  becomes `""` and the report gains a `no_final_output:<trace_id>` note. The
  trace is still stored; we simply do not pretend to know what the agent said.
  Downstream this makes such a trace land in `trajectory:other`, which is the
  truth.
- **`live/monitor.py:88`** — before sampling, `if not content_bearing(trace):
  return False`. The trace is still stored and still gets assertions (`:85-87`),
  which are deterministic and read spans, not the final output. **No file under
  `src/agenttic/scoring/**` is touched** — this changes what is handed to the
  judge, not how the judge scores.
- **`adapters/anthropic_simple.py:113`** — `describe()` gains
  `"kb_sha256": <sha256 of the KB file's bytes, "" if unreadable>`. Content, not
  path: a path is machine-specific and would make config hashes
  non-portable, and the failure mode is a changed KB, not a moved one.
  `describe()` stays total and deterministic — it never raises.
  Consequence: the reference agent's `config_hash` changes once, so traces
  recorded before this commit will not resume. That is correct; they were
  recorded against an unpinned KB.
- **`adapters/blackbox_http.py:172-177`** — the gate serializes itself:

```python
def _throttle(self) -> None:
    """Serialize the gate, not just the sleep.

    run_suite executes up to harness.max_parallel runs on ONE adapter instance
    in worker threads (harness/runner.py:137). An unlocked read-sleep-write lets
    N threads compute the same wait and fire together, which is the promised
    floor divided by N. Stamping inside the lock also makes the interval mean
    what it says: seconds between REQUESTS, not between completions.
    """
    if self.min_interval_s <= 0:
        return
    with self._rate_lock:
        wait = self.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
```

`self._rate_lock = threading.Lock()` in `__init__`; the post-transport stamp at
`:188` is deleted.

---

## 4. Files touched

| path | change |
|---|---|
| `src/agenttic/schema/trace.py` | `SCHEMA_VERSION` → `"0.3.0"`; `SpanKind` += `"user_turn"` with the WHY comment |
| `src/agenttic/coverage/extractors.py` | `_turns` → `_agent_steps` + `_human_turns`; new `agent_steps_single/_multi`; `session_*` rewritten; `_resumed` substring fallback deleted; `_evidential` digest strip in `_blob`; `_injected` + `_condition_signal`; five `tool_*` predicates moved onto it |
| `src/agenttic/coverage/model.py` | `Coverpoint.measurable` / `not_measurable_reason` + validator; `"agent_steps"` into `DETERMINISTIC_BY_CONSTRUCTION`; `measurable` into `bins_fingerprint()`; `0.95` → `DEFAULT_CLOSURE_TARGET` |
| `src/agenttic/coverage/collect.py` | `CoverpointCoverage.measurable`; `unhit`/`holes`/`trace_closure`/`as_dict` respect it; `stimulus_requested` flag; `0.95` → constant |
| `src/agenttic/coverage/targets.py` | **new** — `DEFAULT_CLOSURE_TARGET`, `closure_target(cfg)` |
| `src/agenttic/coverage/models/conversational_transactional.py` | `AGENT_STEPS` coverpoint; `SESSION_SHAPE` not-measurable + `resumed_with_memory` waived; `seed_model` v3, target from arg |
| `src/agenttic/coverage/models/baseline.py` | `AGENT_STEPS` into the coverpoint list; `baseline_model(version=3, closure_target=None)`; `BASELINE_LIMITS` names the not-measurable axis |
| `src/agenttic/schema/signoff.py` | `closure_target` default → constant; `stimulus_closure: float \| None`; `build_signoff` skips not-measurable coverpoints in `unhit_bins`. **`signs_off` (`:195-203`) untouched** |
| `src/agenttic/ops.py` | `verify_op(traces, *, cfg=None)`; summary carries `not_measurable`; `ops.py:403` passes `cfg` |
| `src/agenttic/config.py` | `_validate_coverage_surface` |
| `config.yaml`, `config.prod.yaml` | `coverage: {closure_target: 0.95}` |
| `src/agenttic/reporting/scorecard_report.py` | `0.95` literal → constant; renders `not measurable — <reason>` |
| `src/agenttic/reporting/signoff_report.py` | prints "stimulus not requested" for `None` |
| `src/agenttic/server/routes/capabilities.py` | three `not_covered` entries |
| `src/agenttic/trace_content.py` | **new** — `digest_only`, `content_bearing` |
| `src/agenttic/hardening.py` | `_reconstruct_input` rejects digest-only inputs |
| `src/agenttic/ingest/mapping.py` | no digest fallback for `final_output`; `no_final_output` note |
| `src/agenttic/live/monitor.py` | skip judge scoring for a non-content-bearing trace |
| `src/agenttic/adapters/anthropic_simple.py` | `describe()` += `kb_sha256` |
| `src/agenttic/adapters/blackbox_http.py` | `_rate_lock`; `_throttle` stamps inside the lock; delete `:188` |
| `src/agenttic/stimulus/oracle.py` | delete `refund_window_days` |
| `src/agenttic/stimulus/spaces/conversational_transactional.py` | delete the `session_shape` dimension |
| `src/agenttic/verification/cdv.py` | `Executor` docstring corrected |
| `src/agenttic/redteam/probe.py`, `redteam/authors.py` | `multi_turn` → `false_prior_context` |
| `src/agenttic/rubric_engine/evaluate.py` | `feature_coverage`; `scaffold_only_features` on the draft + review |
| `src/agenttic/rubric_engine/synthesize.py` | `_scaffold_case` tags itself `scaffold` |
| `src/agenttic/schema/archetype.py` | `UNEXERCISABLE_FEATURES` |
| `ui/src/components/ds/CoverageWheel.tsx` | `DECLARED_COVERPOINTS` += `agent_steps` |
| `ui/src/pages/LandingPage.tsx` | wheel entry + a11y label + the "times out / pushes back" copy |
| `tests/coverage/test_session_shape.py` | **commit as-is, unmodified** |
| + the new test modules in §5 | |

---

## 5. Tests

**Already red on today's code** (`tests/coverage/test_session_shape.py`, 8
failures, run above): `test_tool_loop_is_multi_step`,
`test_tool_loop_is_not_multi_turn`, `test_single_exchange_is_single_turn`,
`test_a_direct_answer_is_single_turn_and_single_step`,
`test_two_human_turns_is_multi_turn`,
`test_one_human_turn_with_many_steps_stays_single_turn`,
`test_span_name_alone_does_not_prove_resumption`,
`test_declared_resumption_is_honoured`. These are the phase's definition of done
for §(a)/§(b) and must not be edited.

New:

`tests/coverage/test_not_measurable.py`
- `test_a_not_measurable_coverpoint_needs_a_named_reason` — constructing one
  without a reason raises, mirroring the waived-bin rule.
- `test_session_shape_is_excluded_from_closure_not_scored_zero` — the headline is
  the mean over the measurable required coverpoints; adding/removing
  `session_shape` does not move it.
- `test_a_not_measurable_coverpoint_produces_no_holes` — an unmeasurable bin is
  not a hole a generator can be told to fill.
- `test_the_report_renders_not_measurable_instead_of_zero_percent` — guards the
  `TypeError` that `scorecard_report.py:186` would otherwise raise on `None`.
- `test_measurability_is_in_the_bins_fingerprint` — P3 cannot flip it silently.

`tests/coverage/test_tool_condition_provenance.py`
- `test_a_content_digest_is_not_a_5xx` — **fails on today's code**: a span with
  `output={"content_sha256": "…500…"}` and no error currently hits
  `tool_error_5xx`. Uses a fixed digest containing `500`, so it is deterministic
  rather than probabilistic.
- `test_an_injected_failure_credits_the_bin` — `scenario={"injected_failures":
  ["timeout"]}` with a clean trace.
- `test_a_structured_transport_error_credits_the_bin` — an `error` span named
  `timeout` (the shape `harness/runner.py:142-145` synthesizes).
- `test_a_200_body_mentioning_timeout_is_not_a_timeout` — the exact
  over-report being removed.
- `test_data_condition_still_reads_the_error_channel` — the `data_*` predicates
  are not collateral damage.

`tests/coverage/test_closure_target_from_config.py`
- `test_the_target_comes_from_config_not_five_modules` — `closure_target(cfg)`
  with `0.5` reaches `verify_op` output, the sign-off leg and the report line.
- `test_a_target_outside_zero_to_one_fails_at_load` — `load_config` raises.
- `test_no_module_hardcodes_the_target` — greps `src/agenttic/coverage`,
  `schema/signoff.py`, `reporting/scorecard_report.py` for `0.95` and asserts the
  only hit is the constant's definition.

`tests/test_trace_content.py`
- `test_a_digest_is_not_a_reconstructed_input` — **fails on today's code**:
  `hardening._reconstruct_input` returns `complete=True` for
  `{"content_sha256": …}`.
- `test_ingest_never_names_a_digest_as_the_final_output` — **fails on today's
  code** (`ingest/mapping.py:293`).
- `test_the_judge_is_never_asked_to_score_a_digest` — `LiveMonitor.ingest` with a
  judge double whose `score_criterion` raises; asserts the trace is stored,
  assertions ran, and the judge was not called.

`tests/test_adapter_kb_pinning.py`
- `test_the_kb_is_part_of_the_config_hash` — **fails on today's code**: two
  agents over KBs with different contents currently share a `config_hash`.
- `test_resume_does_not_reuse_a_trace_from_a_different_kb` — drives `run_suite`
  with a persisted trace under the old hash and asserts the case re-runs.
- `test_describe_never_raises_on_a_missing_kb` — `kb_sha256 == ""`.

`tests/test_blackbox_rate_limit.py`
- `test_concurrent_runs_still_honour_the_minimum_interval` — **fails on today's
  code**: 8 threads, `min_interval_s=0.05`, stub transport recording
  `time.monotonic()`; asserts every consecutive gap `>= 0.045`.

`tests/stimulus/test_space_declares_only_what_it_realizes.py`
- `test_every_declared_dimension_is_read_by_realize` — **fails on today's code**:
  `seed_space()` declares `session_shape` and `realize()` never reads it.

`tests/rubric_engine/test_scaffold_is_not_coverage.py`
- `test_a_scaffold_does_not_exercise_the_feature_it_names` — **fails on today's
  code**: `integrity_check`'s `covered` set is derived from the scaffold's own
  tag.
- `test_scaffold_only_features_are_named_in_the_review`.
- `test_a_feature_with_no_case_at_all_still_fails_integrity` — the blocking
  behaviour is unchanged.

`tests/test_capabilities.py` (extend the existing module, do not edit its
existing assertions)
- `test_the_surface_names_the_absent_environment_user_and_sessions`.

`tests/redteam/test_technique_names.py`
- `test_no_technique_claims_a_multi_turn_attack` — asserts `"multi_turn"` is
  gone, `false_prior_context` is present, and every authored `AttackSpec`
  technique is in `TECHNIQUES`.

UI (`cd ui && npm run verify`)
- `ui/src/coverage-wheel.test.tsx` — extend: `DECLARED_COVERPOINTS` contains
  `agent_steps`; a `session_shape` entry with a non-numeric closure renders
  `cw-unmeasured`.
- `ui/src/landing.test.tsx` — **new** (there is no landing test module today):
  the "Why we said no" copy does not claim a service is made to time out or that
  a customer pushes back; `LANDING_WHEEL`'s `session_shape` value is `null` and
  its a11y `label` prop agrees with the number of hatched dimensions.
- Playwright visual baselines under `ui/e2e/__screenshots__/visual.spec.ts/` will
  need regenerating (`npm run e2e:update`) — the hatched `session_shape` sector
  and the extra `agent_steps` spoke are deliberate visual changes, and the
  regenerated snapshots are the diff a reviewer approves.

Full gate: `pytest -q` and `cd ui && npm run verify`.

---

## 6. Risks, and what this phase deliberately does not do

**Risks**

1. **Every stored scorecard's numbers become non-comparable with new ones.** That
   is the intended consequence; `bins_fingerprint()` and the model version bump
   (v2 → v3) are the mechanism that makes it visible. Risk is that a UI or report
   compares across the boundary — `certification/tiers.py:236` reads
   `closure_target` from stored verification data and must keep reading the
   stored value, never a freshly computed one.
2. **Closure does not uniformly go down.** Measured on the existing
   happy-path fixture (`tests/verification/test_run_verification.py::_happy`,
   20 traces): today `trace_closure = 0.1799` with `session_shape` at `0.3333`.
   After P0, `session_shape` leaves the aggregate and `agent_steps` enters at
   `0.5` (single-step hit, multi-step unhit), so the headline moves to
   approximately `0.208` — **up**. The correction is to *what* is claimed, not to
   the magnitude. Any acceptance criterion phrased as "closure decreases" would
   be wrong; none is. `test_every_run_gets_coverage_with_zero_model_calls`
   asserts `< 0.5` and still holds.
3. **Black-box runs will show `agent_steps` other-drift at 1.0.** True and newly
   visible: a black-box trace has no observable step count. If this reads badly
   in the console it is a copy problem, not a reason to credit `single_step`.
4. **The reference agent's `config_hash` changes once** (kb_sha256), so pre-P0
   traces stop resuming and the next run re-spends. One-time, correct, and cheap
   at pilot size.
5. **`SCHEMA_VERSION` 0.2.0 → 0.3.0 with no migration mechanism.** `migrations.py`
   is DB DDL only. Adding a `Literal` member is backward-compatible for reading
   (every stored trace still validates) and the version string is only ever
   compared against the symbol (`tests/test_schema.py:102`). No stored trace is
   rewritten.
6. **Playwright baselines change.** Visual regression will fail until
   `npm run e2e:update` is run and the diffs reviewed; the hatched
   `session_shape` sector is exactly the change a reviewer should see.
7. **`false_prior_context` renames a technique that is baked into stored test
   ids.** `AttackSpec.test_id` is `f"{kind}-{technique}-{idx:03d}"`
   (`redteam/probe.py:45`), so `secret-multi_turn-000` exists in any registry
   that has run a red-team probe; the rename changes future ids only, and old
   rows keep theirs. Note also that `AttackSpec.technique` is a bare `str` with a
   comment (`probe.py:38`) — **nothing validates it against `TECHNIQUES`**, which
   is why the name and the implementation could diverge unnoticed. The new test
   in §5 adds that check.

**Deliberately NOT in this phase**

- **No environment, no fault injection, no simulated user, no second turn.** P0
  removes the claims; P1–P3 build the thing. Nothing here makes an agent
  experience a timeout.
- **No wiring of `run_until_closure` into production** (`cdv.py:201`). Supplying a
  real `Executor` is the CDV phase; P0 only corrects its docstring so the code
  stops asserting that the wiring exists.
- **No fix to `ops.py:304` dropping the scenario.** The live path has no
  scenarios to pass; passing one requires a stimulus-driven runner. P0 instead
  stops reporting `stimulus_closure = 0.0` as though it were measured.
- **No change to `scoring/**`.** The digest-as-evidence fixes are made at the
  ingest and live-monitor boundaries. `scoring/judge.py:135` is not edited.
- **No change to `signs_off` or the Step 14 promotion gate.**
  `schema/signoff.py:195-203` gates on coverage + assertions + formal; that
  remains. Note the interaction: making `session_shape` not-measurable removes a
  coverpoint that could never close, which makes closure *reachable* where it
  previously was not — the gate is unchanged, but what reaches it changes.
- **No fix to `verification/builtins.py:172/195/312`** (`finals[0]`, first-event-
  then-forever). Those bugs only fire on multi-turn traces, which cannot exist
  until P3 emits `user_turn` spans. Fixing them now would be untestable.
- **No relabelling of the landing wheel as an illustration.** `LANDING_WHEEL`
  carries plausible numbers presented as "your" result; the copy decision belongs
  to the owner. P0 only removes the number that becomes impossible.
- **No `ui/src/verification.tsx` `?? 0.95` removal.** Render guard, not a
  threshold; documented in §(e).
