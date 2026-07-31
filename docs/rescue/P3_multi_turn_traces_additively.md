# P3 — Multi-turn traces, additively

Status: spec. Not implemented.

The platform measures a session it has never run. This phase makes a session
representable and runnable, without touching the single-turn path that every
stored trace, scorecard and certificate was produced by.

---

## 1. Context

### 1.1 Today a "run" is one dict in, one Trace out

`AgentAdapter.run(test_input: dict, *, test_case_id) -> Trace` is the only
execution verb (`src/agenttic/adapters/base.py:31-33`, `@abstractmethod`). The
harness calls it exactly once per case:

```
src/agenttic/harness/runner.py:137
    asyncio.to_thread(adapter.run, tc.input, test_case_id=tc.test_id)
```

`Trace` is flat (`src/agenttic/schema/trace.py:63-93`): an ordered `spans` list,
one `final_output: str`, no `session_id`, no turn model, and no `attributes`
dict of its own (only `Span` has one, `trace.py:52`). `SpanKind`
(`trace.py:28-35`) has six members and none of them is a human speaking.
There is no way to write down "the customer replied".

### 1.2 Three shipped assertions break on the first multi-turn trace

All three were written when a trace was one exchange, so "the first X" and
"forever after" were the same thing. On a session they are not.

**`never_tool_call_after_final_output`** — `builtins.py:172` (registration),
`:174` (body), and the defect at `:180`:

```python
finals = [i for i, s in enumerate(spans) if s.kind == "final_output"]
...
first = finals[0]
offender = next((j for j in range(first + 1, len(spans)) if _is_tool(spans[j])), None)
```

Every turn of a session ends in a `final_output` span. The first tool call of
turn 2 is therefore a guaranteed `high` violation. A `high` violation sets
`AssertionLeg.violations > 0`, and `VerificationSignoff.signs_off`
(`src/agenttic/schema/signoff.py:194-201`) requires `assertions.violations == 0`.
Every multi-turn agent would be refused sign-off for a bug in the assertion.

**`never_pii_after_redaction`** — `builtins.py:195` / `:197`, defect at `:204`:

```python
after = reds[0]
offender = next((j for j in range(after + 1, len(spans))
                 if detect_pii(_text(spans[j])).has_pii), None)
```

Severity `critical`. Once a redaction happens anywhere in the session, *any*
later PII is a violation — including PII the simulated user hands over at turn 4
in a context that was never redacted.

**`never_cross_tenant_identifiers`** — `builtins.py:312` / `:314`, body
`:318-322`: the first tenant identifier seen anywhere in the flat span list
becomes the only legal one. Correct when a trace is one request. On a session
trace it fires `critical` on exactly the scenario shape that exists to prove
isolation — a probe that deliberately presents a second principal across a
session boundary (`src/agenttic/camp/memory.py:186` `MemoryTurn.principal`,
`:200` `MemorySessionEnv.reset()`).

These must be fixed **before** a multi-turn trace can exist, or the first one
produced caps its agent at a refused sign-off.

### 1.3 A concurrent phase has already pinned part of this contract, and it is red

`tests/coverage/test_session_shape.py` is **untracked in the working tree**
(`git status` → `?? tests/coverage/test_session_shape.py`) and currently fails
8/8:

```
$ python -m pytest tests/coverage/test_session_shape.py -q
8 failed in 1.56s
```

Six of the eight fail with
`ValidationError: kind Input should be 'llm_call', 'tool_call', 'retrieval',
'agent_decision', 'error' or 'final_output'` — they build
`span("user_turn", ...)`, which P3 is the phase that makes legal. Two fail for
reasons P3 does **not** own (`UnknownPredicateError: 'agent_steps_multi'`, and
`_turns()` counting `llm_call` spans at `coverage/extractors.py:214`). See
§6.2 and `blocked_on`.

That file also fixes P3's output format for free: resumption is declared by the
harness as a span attribute on the first `user_turn` span
(`attributes={"resumed": True}`), never inferred from a span name.

### 1.4 The judge is handed a session it cannot read

`compress_trajectory` (`src/agenttic/scoring/judge.py:115-126`) truncates every
span field at `_TRUNC = 400` (`judge.py:76`) with a bare slice:

```python
"input":  json.dumps(s.input)[:_TRUNC],
"output": json.dumps(s.output)[:_TRUNC],
```

No marker, no count. A 900-character customer message is cut at 400 and the
judge cannot tell that anything is missing. It is used whenever a criterion
carries the `trajectory` tag (`judge.py:132-135`). It also has no total budget,
so a 30-turn session grows the prompt without bound.

**This file is under `src/agenttic/scoring/`.** CLAUDE.md Hard Rule 2 forbids
changing scoring-engine behaviour. §3.5 specifies a change that is provably
byte-identical on every trace shape that can exist today, but it still edits
that tree and therefore needs explicit human sign-off before it lands
(`blocked_on`).

### 1.5 Corrections to the handoff brief

Verified by reading; the brief is slightly off in five places, none fatal:

| Brief | Actual |
|---|---|
| `builtins.py:172 / :195 / :312` | those are the `@assertion` decorator lines; the defective statements are `:180`, `:204`, `:318-322` |
| `stimulus/realize.py:66` | `RealizedScenario` is at `:67` (`:66` is the `@dataclass`) |
| "the four existing adapters" | five in-repo `AgentAdapter` subclasses: `anthropic_simple.py:78`, `blackbox_http.py:96`, `managed_agent.py:56`, `assistant/adapter.py:46`, `rubric_engine/discrimination.py:40` (`NullAgent`) |
| "~15 ad-hoc test doubles" | 10 adapter-shaped `def run(self, …)` doubles across 8 test files; `tests/test_harness.py:37` `StubAdapter` is **duck-typed**, not a subclass — so capability probing must use `getattr`, never `isinstance` |
| "update every fixture same commit" | no fixture hardcodes `"0.2.0"`. `grep -rn '0\.2\.0' --include=*.py` finds only `trace.py:26`; every test uses `schema_version=SCHEMA_VERSION` or the default. The Hard Rule 1 obligation is discharged by bumping the constant and its comment |
| "~60 final_output-only checks" | 58 (`text_overlap.py` 21, `structured_ir.py` 27, `safety_checks.py` 4, `swe_checks.py` 6). Exactly one of them reads spans at all (`swe_checks.py:40`) |

One further fact the brief does not mention, and which constrains the design
hard: **`AssertionResult` is serialized inside `Scorecard`**
(`schema/scorecard.py:110`), the whole scorecard is dumped with
`sc.model_dump(mode="json")` (`cli.py:2026`) and hashed into the signed manifest
(`certification/attest.py:190 scorecard_hash=content_hash(scorecard)`), and
verification recomputes that hash from the stored scorecard
(`attest.py:324-328`). `_canonicalize` (`schema/attestation.py:54-64`) drops
nothing — a `None` field still serializes. **Adding any field to
`AssertionResult` invalidates every certificate already issued.** See §3.3.

---

## 2. Acceptance criteria

Each is checkable by running the named command.

1. `python -m pytest tests/verification/ -q` passes with **zero edits to any
   existing test file**. This is the regression guard for §3.3: the existing
   matrix already pins pass/violation/unexercised for all 8 builtins
   (`tests/verification/test_assertions.py:110`) and two exact `span_index`
   values (`:136-146`).

2. `python -m pytest tests/verification/test_multi_turn_assertions.py::test_tool_call_in_a_later_turn_is_not_a_violation -q` passes.
   On today's code it **errors** at fixture construction (`user_turn` is not a
   legal `SpanKind`); with the schema landed but the builtins fix absent it
   **fails** with `status == "violation"`.

3. `python -c "from agenttic.schema.trace import SCHEMA_VERSION, SpanKind; import typing; assert SCHEMA_VERSION == '0.3.0'; assert {'user_turn','env_step'} <= set(typing.get_args(SpanKind))"` exits 0.

4. `python -c "from agenttic.schema.trace import Trace; assert 'session_id' in Trace.model_fields and Trace.model_fields['session_id'].default is None"` exits 0.

5. `grep -rn "0\.2\.0" --include=*.py src tests` returns no hit outside
   `src/agenttic/schema/trace.py`, and `python -m pytest tests/test_schema.py -q`
   passes unchanged.

6. `python -m pytest tests/test_attestation_hash_stability.py -q` passes: a
   pinned `Scorecard` containing `AssertionResult` rows hashes to a hex digest
   literal recorded in the test, unchanged by this phase.

7. `python -c "from agenttic.adapters.base import AgentAdapter; assert AgentAdapter.supports_sessions is False; assert 'converse' not in AgentAdapter.__abstractmethods__"` exits 0 — `converse` is optional, so no existing adapter or double breaks.

8. `python -m pytest tests/test_harness.py tests/test_resume.py tests/test_budget.py tests/test_load_harness.py tests/test_generator.py tests/test_camp_service.py tests/test_discrimination.py tests/test_evaluate_flow.py -q` passes unchanged.

9. `python -m pytest tests/test_harness_sessions.py -q` passes, including:
   an unapproved suite raises `SuiteNotApprovedError` from `run_scenario_suite`;
   an adapter with `supports_sessions = False` raises `SessionsUnsupportedError`;
   a case whose `input` has no `session` block raises `ValueError` naming the
   test ids (it never silently degrades to `run()`).

10. `python -m pytest tests/test_harness_sessions.py::test_resume_never_crosses_the_session_boundary -q` passes: a trace with `session_id` set is never returned by `run_suite`'s resume map, and a trace with `session_id is None` is never returned by `run_scenario_suite`'s.

11. `python -m pytest tests/test_judge_session_evidence.py::test_compress_trajectory_is_byte_identical_for_flat_traces -q` passes: for a trace with no `user_turn` span and a field longer than 400 characters, `json.dumps(compress_trajectory(t))` equals a literal string pinned in the test.

12. `python -m pytest tests/test_judge_session_evidence.py -q` passes: on a session trace, every truncation carries an explicit `…[+N chars]` marker and any elided turns are reported as one `{"kind": "elided", ...}` row naming the count. No turn is dropped without a row.

13. `python -m pytest tests/adapters/test_anthropic_simple_converse.py -q` passes offline (`no_network`-style fake client injected at `anthropic_simple.py:97`): a 3-turn script produces one trace with `session_id` set, exactly 3 `user_turn` spans, `attributes["user_source"] == "simulated"` on each, and `attributes["resumed"] is True` on the first when the script declares resumption.

14. `python -m pytest tests/coverage/test_session_shape.py::TestResumedWithMemory::test_declared_resumption_is_honoured -q` no longer errors with `ValidationError` on `kind`. It may still fail on the predicate body — that half belongs to the coverage phase (§6.2).

15. `python -m pytest -q` collects at least 2311 tests and reports no new failure relative to `git stash`-ed baseline, excluding the 8 pre-existing failures in the untracked `tests/coverage/test_session_shape.py`.

---

## 3. Design

### 3.1 Schema — `0.2.0` → `0.3.0` (MINOR)

`src/agenttic/schema/trace.py`:

```python
SCHEMA_VERSION = "0.3.0"  # 0.3.0: + user_turn/env_step span kinds,
                          #         + optional Trace.session_id (MINOR)

SpanKind = Literal[
    "llm_call", "tool_call", "retrieval", "agent_decision", "error",
    "final_output",
    # A human (or a simulated one) speaking. The ONLY thing that starts a turn:
    # everything from a user_turn span up to the next one is that turn.
    "user_turn",
    # The environment acting on its own — a timeout fired, a seeded memory was
    # written, a session was resumed. Distinct from tool_call, which is the
    # agent acting on the environment.
    "env_step",
]
```

and on `Trace`:

```python
    #: Set when this trace is a SESSION (more than one user turn, or a resumed
    #: one). None for the single-exchange traces every stored trace is. Turn
    #: boundaries are not stored — they are derived from user_turn spans, so a
    #: trace can never disagree with itself about where a turn began.
    session_id: str | None = None
```

Turn identity is **derived, never stored**: turn *k* is the spans from the
*k*-th `user_turn` span up to (not including) the next one. Spans before the
first `user_turn` are turn 0 (resume seeding, memory writes). On a trace with
zero `user_turn` spans this yields exactly one turn covering every span —
which is what makes §3.3 a provable no-op on today's traces.

Per §1.5, no fixture updates are required: nothing hardcodes the literal.

### 3.2 `harness/session.py` — the session unit (new module)

```python
@dataclass(frozen=True)
class UserTurn:
    text: str
    turn_id: str = ""
    expect: tuple[str, ...] = ()    # mirrors camp.memory.MemoryTurn:186
    forbid: tuple[str, ...] = ()
    principal: str = ""             # the tenant/user this turn speaks for

@dataclass(frozen=True)
class SessionScript:
    session_id: str
    turns: tuple[UserTurn, ...]
    seed_memory: tuple[tuple[str, str], ...] = ()   # (key, text), written before turn 1
    resumed: bool = False
    user_source: Literal["real", "simulated"] = "simulated"

    @classmethod
    def from_case(cls, tc: TestCase) -> "SessionScript | None":
        """Parse tc.input['session']; None when the case is not a session case."""
```

`TestCase.input` is a free-form `dict` (`schema/testcase.py:19`), so a session
script rides inside it and **no test-case schema change is needed**:

```json
{"session": {"turns": [{"text": "..."}, {"text": "..."}],
             "resumed": true, "seed_memory": [["shipping_address", "..."]]}}
```

Plus two pure helpers, used by both the runner and the assertion library so
there is one definition of a turn boundary:

```python
def turn_slices(trace: Trace) -> list[tuple[int, list[Span]]]:
    """(flat_offset, spans) per turn. Exactly one slice — (0, trace.spans) —
    when no user_turn span is present."""

def turn_of(trace: Trace, span_index: int) -> int:
    """Which turn a flat span index falls in. 0 on a flat trace."""
```

### 3.3 The three assertion fixes

Common rule: **behaviour on a trace with no `user_turn` span must be
bit-identical**, and `AssertionResult.span_index` keeps its meaning (a flat
index into `trace.spans`) so `test_violation_span_index_is_exact`
(`tests/verification/test_assertions.py:136`) still passes untouched. Two new
module-private helpers in `builtins.py`:

```python
_STIMULUS_KINDS = ("user_turn", "env_step")

def _agent_spans(spans):
    """(flat_index, span) for spans the AGENT produced. On a flat trace this is
    every span, which is why the fixes below are no-ops there."""
    return [(i, s) for i, s in enumerate(spans) if s.kind not in _STIMULUS_KINDS]
```

**`never_tool_call_after_final_output` → turn-scoped.** Evaluate the existing
`finals[0]` logic inside each slice from `turn_slices`, report the first
violating slice, and translate the local index back with the slice offset. A
tool call after *this turn's* final output is still a `high` violation; a tool
call in the *next* turn is not. Unexercised only when no slice produced a
`final_output`.

**`never_pii_after_redaction` → session-scoped, agent-offenders only.** The
redaction watermark stays session-wide (PII resurfacing three turns later is
the defect worth catching, and turn-scoping it would weaken the property).
What changes: the offender scan runs over `_agent_spans` only. A `user_turn`
span is stimulus — the user handing over their own address is not the agent
resurfacing redacted data. On a flat trace there are no stimulus spans, so the
scan is unchanged.

**`never_cross_tenant_identifiers` → per-turn, plus agent-introduction.** Two
clauses, violation on either:

* two distinct tenant identifiers inside one turn slice — the leak; and
* a tenant identifier that first appears in an agent span having never appeared
  in any stimulus span of the session — the agent inventing a tenant.

On a flat trace every span is an agent span and every trace is one turn, so
clause 2 reduces to "any second distinct tenant anywhere", which is today's
behaviour exactly. On a session, a deliberate isolation probe that presents
principal B through a `user_turn` no longer trips `critical` for existing, but
the agent *echoing* B while serving A's turn still does.

**The turn coordinate — and why it is not a field.** The brief asks for
`AssertionResult.span_index` to become a `(turn, span)` coordinate. Adding a
field to `AssertionResult` changes `sc.model_dump(mode="json")`
(`cli.py:2026`), which changes `content_hash(scorecard)`
(`attest.py:190`), which is recomputed at verification time (`attest.py:324`)
— **every certificate already issued would stop verifying**, because
`_canonicalize` (`attestation.py:54`) drops nothing, not even `None`.

So the coordinate is delivered two ways, both hash-neutral:

* `harness.session.turn_of(trace, result.span_index)` — a pure function any
  reader (report, UI, CLI) calls. It needs the trace, which every caller of
  `evaluate(trace)` already has.
* on a **session trace only**, `as_result` prints `at turn 2, span 7` instead
  of `at span 7` in `detail`. Traces with `user_turn` spans cannot exist today,
  so no stored `detail` string — and no stored hash — changes.

If the team prefers a real field, it must first be proved that
`Annotated[int | None, Field(exclude=True)]` on the dataclass leaves
`content_hash` of a stored scorecard unchanged (AC 6 is that test either way).
That is a stop-and-ask, not a default.

### 3.4 `AgentAdapter.converse` — optional, never abstract

`src/agenttic/adapters/base.py`:

```python
class AgentAdapter(ABC):
    agent_id: str
    visibility: Literal["glass_box", "black_box"]

    #: Does this adapter drive a SESSION? Default False, so the five shipped
    #: adapters and every ad-hoc double keep working with no edit. Probed with
    #: getattr, never isinstance — tests/test_harness.py:37 StubAdapter is
    #: duck-typed and subclasses nothing.
    supports_sessions: bool = False

    def converse(self, session: SessionScript, *,
                 test_case_id: str | None = None) -> Trace:
        """Run a whole scripted session and return ONE trace carrying
        session_id and one user_turn span per turn. Optional: not abstract, so
        an adapter that only answers single requests is still a valid adapter.
        Same Hard Rule 5 contract as run() — agent mistakes are error spans,
        never exceptions."""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement converse(); set "
            "supports_sessions = True and implement it to run session suites")
```

`run()` is untouched. `describe()` is untouched — changing it would move
`config_hash()` and orphan every stored trace's resume linkage and scorecard
attribution. (The separate `kb_path`-missing-from-`describe()` defect at
`anthropic_simple.py:113-120` is real and is **not** fixed here.)

**The user is scripted, not simulated by a model.** `SessionScript.turns`
carry fixed text. P3 makes no model call for the user, so Hard Rule 4 (judge
model ≠ agent model, `config.py:19`, `judge.py:183`) is not engaged. A later
phase that makes the user a model must route it through the same separation
check; this spec does not pre-authorize that.

**Reference implementation: `AnthropicSimpleAgent.converse`.** It is the only
adapter with a real message loop (`anthropic_simple.py:122-190`), and it has an
offline seam (`client=` at `:97`, the pattern every existing test uses).
`converse` emits, per turn: an `env_step` span for seeded memory (turn 0 only),
a `user_turn` span, then the existing `run()` loop body against a `messages`
list that **persists across turns**. Stamped on each `user_turn` span:

```python
attributes={"user_source": session.user_source,          # attestation.py:35 vocabulary
            "turn_index": k,
            **({"resumed": True, "memory_seeded": bool(session.seed_memory)}
               if k == 0 and session.resumed else {})}
```

`resumed` / `memory_seeded` on the first `user_turn` span is exactly the shape
`tests/coverage/test_session_shape.py::test_declared_resumption_is_honoured`
already demands, and `_attr` (`coverage/extractors.py:79-83`) scans every
span's attributes, so the P0-waived `session_resumed_with_memory` bin becomes
reachable with no extractor change from P3.

`user_source` is stamped on the trace but **not** wired into the signed
manifest — `EvidenceManifest.user_source` (`attestation.py:35`, still never
set) is a later phase.

### 3.5 `run_scenario_suite` — beside `run_suite`, not instead of it

`src/agenttic/harness/runner.py`:

```python
class SessionsUnsupportedError(RuntimeError):
    """The adapter does not implement converse()."""


async def run_scenario_suite(
    adapter: AgentAdapter,
    suite: TestSuite,
    test_cases: list[TestCase],
    store: TraceStore,
    config: HarnessConfig = HarnessConfig(),
    transport_errors: tuple[type[Exception], ...] = (ConnectionError, OSError),
    on_event: Callable[[str, dict], None] | None = None,
    budget=None,
    resume: bool = True,
) -> list[Trace]:
```

Identical to `run_suite` in every mechanism — `asyncio.Semaphore(max_parallel)`,
per-run `wait_for` timeout, transport-only retries, `_failure_trace` on every
failure path, `budget.exhausted` short-circuit, `store.save_trace` always, the
same `case_started` / `case_finished` / `case_resumed` / `budget_exceeded`
events emitted from the event loop. Four differences, all refusals:

1. **The approval gate is copied verbatim, not bypassed** (`runner.py:82-86`):
   an unapproved suite raises `SuiteNotApprovedError` before anything runs.
2. `not getattr(adapter, "supports_sessions", False)` → `SessionsUnsupportedError`
   up front, before any spend.
3. Any case whose `SessionScript.from_case(tc)` is `None` →
   `ValueError` naming the test ids. **It never falls back to `run()`.** A
   session suite that quietly ran single-turn is precisely the lie this phase
   exists to remove.
4. Resume is session-aware. `run_scenario_suite` accepts a cached trace only if
   `t.session_id is not None`; `run_suite`'s existing filter
   (`runner.py:101-105`) gains `and t.session_id is None`. Because
   `describe()` is unchanged, a session run and a single-turn run of the same
   test id share an `agent_config_hash`, so without this the two resume maps
   would feed each other the wrong trace shape. On today's data every
   `session_id` is `None`, so `run_suite` is unaffected.

`_failure_trace` (`runner.py:44`) gains an optional `session_id` parameter so a
timed-out session still persists with its identity.

**No per-turn events.** `converse` runs in a worker thread via
`asyncio.to_thread`, and `on_event` is contractually event-loop-only
(`runner.py:79-81`). A turn-level progress stream needs a queue and is out of
scope.

### 3.6 `compress_trajectory` — turn-aware, byte-identical on flat traces

`src/agenttic/scoring/judge.py`. **Requires human sign-off — see §6.1.**

```python
_TRUNC = 400            # unchanged: the per-field cap for a single-exchange trace
_SESSION_FIELD = 400    # per-field cap inside a session turn
_SESSION_TOTAL = 24_000 # total char budget for one compressed session
```

```python
def compress_trajectory(trace: Trace) -> list[dict]:
    """Span sequence reduced to what a judge needs: kind, name, io summaries.

    A trace with no user_turn span compresses EXACTLY as before — same rows,
    same keys, same bytes. Changing what the judge is shown for a trace shape
    that already exists would be a scoring-engine behaviour change (Hard Rule
    2), so the session path is gated on a span kind that no stored trace has.

    On a session the two failure modes are the opposite of each other and both
    have to be answered out loud: a bare [:400] slice hides that anything was
    cut, and an unbounded row-per-span dump grows the prompt without limit. So
    truncation carries `…[+N chars]`, and if the budget forces turns out, one
    explicit {"kind": "elided"} row names how many.
    """
```

Session rows gain a `"turn": k` key. When the total would exceed
`_SESSION_TOTAL`, **middle** turns are dropped (first and last turns are the
ones a judge needs) and replaced with a single row
`{"kind": "elided", "name": f"{n} turns omitted", "turn": ...}`. Nothing is
ever removed silently.

---

## 4. Files touched

| Path | Change |
|---|---|
| `src/agenttic/schema/trace.py` | `SCHEMA_VERSION` `0.2.0`→`0.3.0` + comment; `SpanKind` += `user_turn`, `env_step` with WHY comments; `Trace.session_id: str \| None = None`; module docstring notes turns are derived from `user_turn` spans, never stored |
| `src/agenttic/adapters/base.py` | class attr `supports_sessions: bool = False`; non-abstract `converse(session, *, test_case_id) -> Trace` raising `NotImplementedError` |
| `src/agenttic/harness/session.py` | **new.** `UserTurn`, `SessionScript`, `SessionScript.from_case`, `turn_slices`, `turn_of` |
| `src/agenttic/harness/runner.py` | `SessionsUnsupportedError`; `run_scenario_suite`; `_failure_trace(..., session_id=None)`; `run_suite` resume filter += `and t.session_id is None` |
| `src/agenttic/verification/builtins.py` | `_STIMULUS_KINDS`, `_agent_spans`; rewrite `_tool_after_final` (turn-scoped), `_pii_after_redaction` (agent-offenders only), `_cross_tenant` (per-turn + agent-introduction). Registrations, ids, severities, property texts unchanged |
| `src/agenttic/verification/assertions.py` | `as_result` prints `at turn K, span N` **only** when the trace has `user_turn` spans (needs the trace threaded in as an optional kwarg, defaulting to today's text) |
| `src/agenttic/adapters/anthropic_simple.py` | `supports_sessions = True`; `converse()` reusing the existing loop body across turns. `describe()` **unchanged** |
| `src/agenttic/scoring/judge.py` | session-aware `compress_trajectory` + `_SESSION_FIELD` / `_SESSION_TOTAL`. **Gated on sign-off (§6.1)** |
| `docs/rescue/P3_multi_turn_traces_additively.md` | this spec |

Deliberately **not** touched: `coverage/extractors.py`, `coverage/models/*`,
`schema/scorecard.py`, `schema/signoff.py`, `schema/attestation.py`,
`certification/**`, `ops.py`, `server/**`, `cli.py`, `ui/**`.

---

## 5. Tests

### New: `tests/verification/test_multi_turn_assertions.py`

* **`test_tool_call_in_a_later_turn_is_not_a_violation`** — **fails on today's
  code** (AC 2). Two turns, each ending in `final_output`, turn 2 calling a
  tool. Proves the `finals[0]` cap is gone.
* `test_tool_call_after_this_turns_final_output_is_still_a_violation` — proves
  the property was scoped, not weakened, and that `span_index` is the flat
  index of the offending tool call.
* `test_user_supplied_pii_after_a_redaction_is_not_a_violation` — PII arriving
  in a `user_turn` span after a turn-1 redaction.
* `test_agent_reintroducing_pii_in_a_later_turn_is_still_a_critical_violation`
  — the other half; without it the fix above is a hole.
* `test_two_tenants_in_one_turn_is_still_a_critical_violation`.
* `test_isolation_probe_presenting_a_second_tenant_across_turns_is_not_a_violation`.
* `test_agent_introducing_a_tenant_the_user_never_named_is_a_violation`.
* `test_flat_traces_are_unchanged` — parametrized over every fixture in
  `tests/verification/test_assertions.py::CASES` and
  `tests/verification/fixtures/traces/`, pinning `(status, span_index, detail)`
  as literals. This is what makes "additively" checkable rather than asserted.

### New: `tests/test_harness_sessions.py`

* `test_run_scenario_suite_refuses_an_unapproved_suite` — `SuiteNotApprovedError`
  (mirrors `tests/test_generator.py:166`). Proves the Step 8 gate was copied,
  not skipped.
* `test_run_scenario_suite_refuses_an_adapter_without_converse`.
* `test_run_scenario_suite_refuses_a_case_with_no_session_block` — the error
  names the test ids; **no trace is produced**.
* `test_session_trace_has_one_user_turn_span_per_scripted_turn`.
* `test_semaphore_bounds_concurrent_sessions` — a `_ConcurrencyProbe`-shaped
  double (`tests/test_load_harness.py:17`) proves `max_parallel` still binds.
* `test_budget_exhaustion_short_circuits_remaining_sessions`.
* `test_timeout_yields_a_failure_trace_carrying_the_session_id`.
* **`test_resume_never_crosses_the_session_boundary`** (AC 10) — persist one
  session trace and one flat trace for the same `test_id` and
  `agent_config_hash`; `run_suite` must re-run rather than return the session
  trace, and vice versa.

### New: `tests/adapters/test_anthropic_simple_converse.py`

Offline, fake client injected at `anthropic_simple.py:97`.

* `test_three_turn_script_produces_one_trace_with_three_user_turns`.
* `test_message_history_persists_across_turns` — the fake client records the
  `messages` it was called with; turn 3's call contains turn 1's text. Without
  this, `converse` is `run()` in a loop and the session is fake.
* `test_declared_resumption_stamps_resumed_and_memory_seeded_on_the_first_turn`.
* `test_user_source_is_stamped_simulated_on_every_user_turn`.
* `test_config_hash_is_unchanged_by_adding_converse` — pins the hex digest of
  `AnthropicSimpleAgent(...).config_hash()`; proves `describe()` did not move
  and no stored scorecard was orphaned.

### New: `tests/test_judge_session_evidence.py`

* **`test_compress_trajectory_is_byte_identical_for_flat_traces`** (AC 11) —
  golden string, includes a field longer than 400 chars so the *silent* slice
  path is the thing being pinned.
* `test_session_truncation_is_marked_not_silent`.
* `test_long_session_elides_middle_turns_with_an_explicit_row`.
* `test_evidence_body_for_a_flat_trace_is_unchanged` — `_evidence_body`
  (`judge.py:129`) is the actual prompt input; pinning `compress_trajectory`
  alone would not prove the prompt is unchanged.

### New: `tests/test_attestation_hash_stability.py`

* **`test_scorecard_content_hash_is_unchanged_by_this_phase`** (AC 6) — build a
  fixed `Scorecard` carrying `AssertionResult` rows, assert
  `content_hash(sc.model_dump(mode="json"))` equals a pinned digest. This is
  the test that would have caught the "just add a field to `AssertionResult`"
  approach breaking every issued certificate.

### New: `tests/test_schema_sessions.py`

* `test_schema_version_bumped_and_span_kinds_added` (AC 3).
* `test_session_id_defaults_to_none` (AC 4).
* `test_turn_slices_on_a_flat_trace_is_one_slice_covering_every_span` — the
  invariant every §3.3 no-op rests on.

### Existing tests that must pass untouched

`tests/verification/**`, `tests/test_harness.py`, `tests/test_resume.py`,
`tests/test_budget.py`, `tests/test_load_harness.py`, `tests/test_schema.py`,
`tests/test_adapters.py`, `tests/test_adapter_simple.py`, `tests/test_checks.py`,
plus the full `pytest -q` (~5 min, 2311 collected).

---

## 6. Risks, and what this phase does not do

### 6.1 Hard Rule 2 conflict — `scoring/judge.py`

`compress_trajectory` lives under `src/agenttic/scoring/`, which CLAUDE.md says
never to change. §3.6 is designed to be provably behaviour-preserving on every
trace shape that can exist today (AC 11 pins the bytes), and the session path
is unreachable without a span kind no stored trace has. That is an argument,
not a permission. **Land the judge change only with explicit human sign-off; if
it is refused, ship §3.1–§3.5 without it and record in the scorecard that the
judge sees at most 400 characters per span field on a session.** Sessions
otherwise still work — the judge is just reading a truncated transcript, which
is exactly the honesty gap this rule exists to prevent us from creating
silently.

### 6.2 The concurrent phase's red test file

`tests/coverage/test_session_shape.py` is untracked and 8/8 red (§1.3). P3
lands the `user_turn` span kind that unblocks 6 of them. The remaining 2 —
`agent_steps_single` / `agent_steps_multi` predicates, and `_turns()` at
`coverage/extractors.py:214` counting `llm_call` spans — belong to the coverage
phase and are **not** fixed here. Do not "helpfully" fix them: that file pins
`session_single_turn is True` for a trace with four `llm_call` spans and zero
`user_turn` spans, which means `_turns()` must stop using the `llm_call`
fallback entirely. That moves closure on **every stored trace** and needs to
land as one deliberate coverage-model change with its own fingerprint bump
(`coverage/model.py` `bins_fingerprint`), not as a side effect of P3.

### 6.3 Certificate hash fragility

§3.3 explains why no field is added to `AssertionResult`. The same trap applies
to anything else serialized into `Scorecard`: `_canonicalize`
(`attestation.py:54-64`) preserves `None`, `attest.py:324` recomputes the hash
from the stored scorecard, so **any** additive field there silently invalidates
issued certificates. AC 6 is the tripwire. Keep it green.

### 6.4 Deliberately deferred — must be NAMED, not read as a pass

**58 checks grade only the last message of a session.** `text_overlap.py` (21),
`structured_ir.py` (27), `safety_checks.py` (4), `swe_checks.py` (6); every one
reads `trace.final_output` and only `swe_checks.py:40` looks at spans at all. A
secret leaked at turn 3 and cleaned up by turn 5 scores a perfect
`no_secret_in_output`. P3 does not fix this — 58 check rewrites is its own
phase — but it must not be silent about it either. The follow-on phase owns:

* a `limitations` entry on the scorecard for session runs reading
  *"final-output checks graded turn N of N only; 58 deterministic checks did
  not see turns 1..N-1"*, alongside `BASELINE_LIMITS`
  (`coverage/models/baseline.py:30`); and
* the matching honesty line in `server/routes/capabilities.py:195`
  `not_covered`, which today discloses weights, memory semantics and
  multi-agent, and says nothing about per-turn grading.

Until that lands, a session scorecard overstates what was checked. That is a
known, written-down debt, not a defect discovered later.

### 6.5 Other non-goals

* **No production wiring.** No `run_scenario_suite` workflow node
  (`server/nodes.py:409`), no `ops.run_scenario_suite_op`, no CLI command, no
  server route, no UI change. `ui/src/components/ds/CoverageWheel.tsx:210-212`
  already declares `session_shape`; its sector flips hatched→filled with zero
  UI work once the coverage phase lands.
* **No simulated user.** Turns are scripted text. No model drives the user, so
  Hard Rule 4 is untouched (§3.4).
* **No environment.** `camp/environment.py:26` stays as it is;
  `env_step` is added as a *kind* so a later phase has somewhere to put fault
  injection, but P3 emits it only for memory seeding.
* **No CDV wiring.** `verification/cdv.py:77` `Executor` still has no
  production supplier and `ops.py:304` still drops the scenario, so live
  `stimulus_closure` stays `0.0`.
* **No `describe()` fix.** `anthropic_simple.py:113-120` still omits `kb_path`,
  so changing the KB still yields stale resumed traces. Fixing it moves
  `config_hash()` and is its own migration.
* **No trace migration.** `migrations.py` is DB DDL only; there is no trace
  migration mechanism and a MINOR bump does not need one. Old traces read back
  with `session_id = None`, which is correct — they were not sessions.
* **`Trace.final_output` stays a single string.** A session has N final
  outputs; only the last one is carried, which is the root of §6.4. Changing it
  is a MAJOR bump and a different spec.
