# Changelog

## [3.0.0.0] - 2026-08-28 — the agent's words, checked against the same policy as its actions

> **Why a major.** Two breaking changes landed on `master` after the `v2.0.0`
> tag and were never released, so they go out here: the signing gate (`a932448`
> — a certificate can no longer outrun its evidence) and the closure correction
> (`53ea688` — published closure figures and both shipped models'
> `bins_fingerprint` move). Either alone forces the major; shipping them under a
> minor would tell anyone pinned `>=2.0,<3` that this was a safe upgrade. Their
> full release notes follow below, and the claim leg described here is simply
> the largest feature in the same release.

M45–M46 (SPEC-13 Steps 63b–63d). §63 asks whether the agent's *actions* can
violate policy. This asks whether its *words* are true about that same policy.
An agent can stay entirely inside its authorized tools and still tell a customer
"you don't need approval for that" when the policy requires it — nothing in the
action graph is wrong, and the lie is in the sentence.

`proof(claim)` is five-valued (valid / invalid / satisfiable / ambiguous /
impossible) where `proof(authorization)` is four-valued, and the two never share
a report row (**Hard Rule 72**): `1 counterexample · 1 invalid` on one line does
not tell a reader whether the agent *did* something illegal or *said* something
false, and those route to different owners. Output claims render as their own
section, `3b · OUTPUT CLAIMS`.

**Opt-in, and it says so.** `verify_op` runs on the normal path for every run and
promises zero model calls. Claim extraction needs a model to read prose, so it
runs only when the caller supplies both an extractor and the policy to check
against; otherwise the leg reads `not_run` and renders as `not checked` — which
is not the same as "no false claims found". A network-block test enforces the
promise.

**Report-only under gate v1**, following the scoreboard precedent. A verified
false claim does not block sign-off today. Flipping that is a separate, announced
change under a `gate_version` bump, so a sign-off issued under v1 keeps meaning
what it meant when it was issued.

### Soundness fix: a truncated search no longer returns a verdict

`claims.check_claim` answered "is this tool permitted" by counting how many
*reachable* states enable it, against a search capped at 200,000 states.
`_reachable` returned a bare `set` that could not say whether it had finished, so
the caller compared `enabled == len(states)` against the truncated set — both
sides shrink together, which means truncation could never make the test fail,
only make it confident. Measured on the shipped fixture: at every cap from 0
upward the old code returned VALID, including where it had explored one of three
reachable states.

`_reachable` now returns `(states, complete)`, and `check_claim` returns
AMBIGUOUS rather than a verdict when the search was cut short — in either
direction, since `enabled == 0` was equally unsound on a partial set. `prove` has
always done this (`unbounded`, never `proven`, at the cap) and both now share
`DEFAULT_MAX_STATES`. Two adjacent holes closed with it: `graph.unbounded` was
unchecked in the claim path, and the AMBIGUOUS sentence said "could not be
soundly translated" for outcomes that were not translation failures.

### `extraction_failures` — unchecked is not clean

If extraction fails, that output was not checked. Returning an empty claim list
would render as a clean row and silently shrink the denominator, so
`ClaimExtractionError` propagates and is counted outside the five buckets and
printed above them — the same shape as `unexercised`, `not_measurable`,
`non_results` and `evaluation_failures`.

### Also

- `verification/claim_extract.py` is the only module in `verification` that may
  touch a model, mirroring `stimulus/realize.py`. The client is injected, never
  constructed, so the whole path is exercisable offline.
- The extractor makes a fresh call per invocation and caches nothing. `translate`
  samples it `n` times and treats unanimity as confidence; a memoized extractor
  would return one opinion `n` times and manufacture that unanimity.
- 50 new tests. Full suite 4457 passing, 8 skipped, green. The four
  `test_release_ladder` failures previously recorded here as pre-existing were
  fixed on `master` (`861df4e`) and no longer reproduce. Eight CLI tests remain
  coupled to colour: with `FORCE_COLOR` set they fail on unmodified `master`
  too, and pass with `NO_COLOR=1`.

### Fixed at ship time

- An enforcement policy that failed to compile left the claims leg `not_run` —
  the same reading as claim checking never having been switched on. A policy
  that does not compile checks nothing, so it is now recorded per trace in
  `extraction_failures` and renders as NOT CHECKED. This was the one path that
  had escaped the rule the rest of this entry is built on.
- The `NOT CHECKED` line no longer says "extraction failed", since a failed
  policy compile is not a failed extraction. The cause is carried per entry.

### Fixed — a clipped extraction no longer blames its own JSON

`model_extractor` capped the response at 2000 tokens. The input is an agent's
whole final message and the output is one object per policy claim in it, so a
chatty agent produces both a long prompt and a long answer — and 2000 clipped
it. The clipped JSON then failed to parse and was reported as "no parseable
claim list", which named the wrong cause: nothing was malformed, the reply
never finished. It failed safe (NOT CHECKED, never clean) but it failed
silently, on exactly the outputs most likely to carry a claim worth catching.

The ceiling is now 16000, and `stop_reason` is read BEFORE the content: a
`max_tokens` stop reports the ceiling it hit and says the output is unchecked;
a `refusal` says the extractor declined. A response carrying no `stop_reason`
at all still reads normally.

### Fixed — the frontend builds again

`npm run build` had not worked: it calls `lint:tokens`, which was referenced
but never defined, and behind that two pages carried orphan JSX closing tags
that produced all 38 `tsc` errors. A file that cannot parse cannot be
typechecked, so those two also hid 87 further type errors across the project —
including a whole block of scenario-run types and API methods that the Step
17.2 typed-layer move deleted from `api.ts` without adding to `api/types.ts`,
while the page using them kept importing them.

Repaired end to end: the tags, the missing script, the lost types and methods,
dropped imports across six files, a `verdictScope` helper that was called but
never written, and the 14 remaining `no-explicit-any` violations of the Step
17.2 gate. `/engine` is lazy-loaded as its route comment always said it must
be, which takes the landing bundle from 134.5 to 113.5 KB gz.

Four defects the newly-running tests then exposed: `Onboarding` threw wherever
`localStorage` is absent, the `existing-suite` template still ended with the
removed live-monitor step, one page made an unbounded "is safe" claim, and both
route checkers let the 404 catch-all answer for every path — which had made the
dead-link guard incapable of failing.

`npm run verify` is green: 512 tests, clean lint, clean typecheck.

### Added at ship time

- `ui` now has the `verify` script `CLAUDE.md` has always documented
  (`npm run lint && tsc --noEmit && vitest run`). It was referenced but never
  defined, so the documented UI gate could not run.

## Unreleased — closure stops counting what nobody measured (NUMBERS MOVE)

> **Release note.** This changes published closure figures and both shipped
> models' `bins_fingerprint`. It ships alone, and it is announced, because a
> correction that makes our own numbers look better is the one kind of change a
> verification product cannot make quietly. **No stored scorecard is re-scored:**
> old scorecards keep their fingerprint and their figure, and the two
> fingerprints below are how a reader tells which rule produced a number.
>
> | model | fingerprint before | after |
> |---|---|---|
> | `cov-baseline-deterministic@v3` | `b604cd60b902b62d` | `cf22f3789194673f` |
> | `cov-conversational_transactional@v3` | — | `e66444ac33aa1d79` |

Two corrections to the same rule — *measure what was measured, disclose what was
not* — landing together because shipping only the first would be a restatement
in our own favour.

### 1. A dimension nobody evaluated is no longer a dimension scored at zero

`CoverpointCoverage.trace_closure` returned a hard `0.0` whenever its denominator
was empty but its `measurable` flag said True. Those are different questions:
`measurable` asks *does a producer exist for this dimension*, and it does not ask
*was any bin of it actually in the denominator*. Four other exclusions —
`other`, illegal, waived, and **classifier-backed with no evaluator supplied** —
can empty the list underneath a flag that says True.

That last one is not hypothetical. Both shipped scenario producers collect with
`classify=None` deliberately, so on a fitted model **every** semantic bin is
unevaluated. `intent`, `emotional_register` and `policy_vector` each reported
`0.0` — which reads as *"we looked and the suite never got there"* — over bins
nothing had looked at, and each dragged the headline down for not having been
measured. Three of eight dimensions.

They now report `None` and leave the headline, and — this is the half that makes
it a correction rather than a flattering edit — **they are named in
`not_measurable` with the reason**, through the same channel that has always
carried the headline's companion, so the scorecard, the MCP tools and the console
disclose them without a line of new wiring. A closure figure that silently
dropped a dimension would be a better-looking number describing a smaller space.

Measured, 12 real offline runs against the fitted model:

```
headline trace_closure  0.2709 -> 0.3522   (+8.1 points)
left the denominator:   intent, emotional_register, policy_vector
```

A real zero is still a zero: a dimension whose bins WERE countable and simply
never exhibited still reports `0.0`, because that is a gap a generator can be
told to close.

### 2. `session_shape` is now measured on the runs that instrument it

`measurable` is declared per MODEL, and turn instrumentation is a fact about a
RUN: `scenario/session.py` stamps a `user_turn` span before every delivery and
the stored-suite path stamps none, so one flag was guaranteed to be wrong about
one of them. The per-sample mechanism existed and no coverpoint used it.

`session_shape` now declares `measurable_when="session_turns_instrumented"`. The
floor stays `measurable=False` with its reason — an arbitrary sample still cannot
be read for turn shape — and `collect()` raises it for the batch that carried the
instrumentation. Measured, 10 runs each way:

```
single-shot batch   session_shape  not measurable, n_unmeasurable=10, named in not_measurable
multi-turn batch    session_shape  measurable, closure 1.0, n_measurable=10, drops out of not_measurable
                    headline 0.3314 -> 0.3922
```

`session_single_turn` is still `<= 1`, still True at zero turns, and still not
tightened to `== 1`: with neither bin firing the trace lands in `other`, whose
drift reads as *"the model is missing a dimension"* when the model is fine and the
run is uninstrumented. What keeps that True out of a closure figure is now the
gate, and the flag beneath it. `extractors._single`'s docstring asserted this
gate existed for two releases while nothing referenced it; the claim has been made
true rather than deleted, and a test pins that exactly one shipped coverpoint
declares a gate.

### Also: the harness battery has a caller (SPEC-13 P7), off by default

`redteam/honeypot.py` has always separated `resisted` (a fact about the MODEL)
from `attempted_blocked` (a fact about the HARNESS), and `report_op` has always
rendered a stored battery — but nothing ran one against a real agent, so the
distinction lived in dev tooling and never reached a scorecard.
`run_and_score_op` now runs it when `harness.honeypot_battery` says so,
discovering the decoy surface from the agent's own `describe()` rather than a
hand-written descriptor.

**Off by default, and silence is the honest off-state.** It drives real probes
against a real agent and spends money on every run it is enabled for. When it
does not run, no battery is stored and the report carries no harness section —
deliberately not a synthesised NOT MEASURED one, which would have to invent a
posture and a decoy list and would read as *"we tested the harness and it was
inconclusive"* when nothing tested it. `NOT MEASURED` is the verdict for a battery
that RAN and reached the enforcement path zero times; *"we did not run one"* is a
sign-off question, not something a renderer can infer from a missing row.

It never costs the run: the scorecard is aggregated and stored before the battery
starts, an adapter it cannot instrument is logged as a fact about harness coverage
rather than raised, and the failure handlers read the scorecard id defensively so
the handler cannot raise the exception it exists to contain.

## Unreleased — the signing gate: a certificate can no longer outrun its evidence (BREAKING)

> **Release note.** This is a breaking change (`sign_manifest`/`build_manifest`
> signatures and issuance behaviour), so it warrants a **major** bump to 3.0.0 —
> not a minor one. Version numbers are deliberately left untouched here: the two
> adapters pin `agenttic` exactly, so `agenttic`, `agenttic-langgraph` and
> `agenttic-openai-agents` must be bumped and published together, and PyPI is
> still on 1.0.1 (the 2.0.0 artifacts were built but never uploaded). Cutting the
> version is a release decision, not part of this change.


`VerificationSignoff.signs_off` was already correct and deny-by-default. Its only
consumer was a **renderer** — so a run could print `DOES NOT SIGN OFF` and mint a
signed, publicly verifiable, *graded* certificate from the same scorecard. The
verdict and the signature were unconnected code paths. They are now the same path.

### What changed

- **`sign_manifest()` refuses.** It is the single chokepoint every signing path
  reaches the key through, and it now raises `SignoffRefused` unless the sign-off
  is positive and its hash matches the manifest. No `force=` escape hatch.
- **`issue_certificate()` refuses.** The public graded certificate is gated on the
  same evidence. A grade is not issued over unclosed coverage or an outstanding
  property violation.
- **Sign-offs are built and persisted.** `verify_op()` assembles the sign-off from
  the artifacts it already holds; `aggregate_op()` stores it on the scorecard
  (`Scorecard.signoff`). Certification works from a stored scorecard and never
  holds the traces, so this had to survive the round-trip.
- **Scope travels on the artifact.** A signed `ScopeSummary` (properties
  exercised / total, closure, violations, unexercised property names) is carried
  inside the signed payload and rendered on `/certified/:id`. Tampering with it
  breaks the signature.
- **Components get their own contract.** `ComponentSignoff` covers MCP servers
  and memory stores, which have no traces. A **skipped** critical check does not
  count as a pass, even though `CheckOutcome.passed` returns `True` for a skip.
- **`/scan` now issues a signed SCAN REPORT, not a certificate.** A ~14-probe
  lexical screen cannot close a 95% coverage target and was never meant to.
  Reports are signed (integrity, not endorsement), carry `artifact:
  "scan_report"` and `certified: false` inside the signed body, and say on their
  face that they are not certificates.

### Coding agents: a tool-use hook and an MCP server

`agenttic certify` drives agents through adapters, and a coding agent (Claude
Code, Cursor, an SWE agent) has none — it runs on its own, in a real repo. Two new
surfaces make it verifiable.

**`agenttic hook claude-code`** — a `PostToolUse` hook. `agenttic hook install`
prints the settings snippet; `agenttic hook verify` reports closure and violated
properties over the captured session with no config and no database, so it works
in any repo. Spans are OTLP-shaped, so the existing importer reads them unchanged.

The reason a hook is necessary rather than convenient: for a coding agent **`Bash`
means anything.** `ls` and `rm -rf /` are the same tool with the same span name, so
tool-level instrumentation carries no risk information at all. The command string
is the only place the answer exists and the hook is the only place that sees it.
`hooks/command_risk.classify()` reads it:

| verdict | examples |
|---|---|
| irreversible | `rm -rf`, `git push --force`, `git reset --hard`, `git branch -D`, `DROP TABLE`, `kubectl delete`, `twine upload`, `terraform destroy` |
| mutating, recoverable | `git commit`, `pip install`, `sed -i`, `mkdir`, `echo > f` |
| read-only | `ls`, `git status`, `grep`, `pytest`, `ruff` |
| **unknown** | `python -c`, `curl`, `npm run`, `make`, any unrecognised binary |

Three rules keep it honest:

- **Silence is never a read-only credit.** An ambiguous command omits the
  attribute entirely rather than emitting `mutating: false`, which the ingest
  fidelity guard would record as an explicit "does not mutate".
- **The worst segment decides**, and every segment is checked — `make deploy &&
  curl …` reports *mutating with an unclassifiable segment*, so a caller still
  confirms. An earlier benign match cannot mask a later opaque one.
- **The command is never recorded** (a shell line can carry a token), but a
  `sha256` fingerprint is — without it every `Bash` span keys identically and
  `never_repeated_identical_tool_call` fires on four *different* commands.

**`agenttic mcp`** — an MCP stdio server (hand-rolled JSON-RPC, no SDK dependency,
matching the MCP *client* that probes other servers). A subcommand rather than a
second console script, so the one-entry-point invariant left over from removing
the `ascore` alias stays intact; register it as
`{"command": "agenttic", "args": ["mcp"]}`. Three tools:
`classify_command` (ask **before** running: is this irreversible? — the only tool
here that prevents a defect rather than reporting one), `verify_session`, and
`what_is_untested`.

### Closure over production traffic

Suite closure stalls near 20% and never closes, because nobody authors 95% of a
situation space — `timeout`, `rate_limited`, `escalated_to_human`,
`entity_not_found`, `mutating_irreversible` happen in production and almost never
in a test suite. The OTel ingest was already importing production traces
(`source="otel_ingest"`, `mode="live"`) and **never verifying them**.

`agenttic ingest verify-traffic --agent <id>` now measures the same coverage model
and the same safety properties over that population. Deterministic, zero model
calls. A claim of *closed over N days of real traffic* is stronger than *closed
over 40 authored cases*, and it makes ingested telemetry into evidence rather than
just storage.

**With an honesty guard, because this is easy to get wrong.** Ingested spans come
from someone else's instrumentation and usually carry no mutation semantics.
`is_write` falls back to tool-name hints, so an uninstrumented `process_request`
would be silently credited to `action_risk.read_only` — a coverage credit for a
question nobody answered. Every tool span is therefore classified by confidence:

| confidence | meaning |
|---|---|
| `explicit` | the producer instrumented `mutating` / `irreversible` (including an explicit `false` — a stated "no" is evidence) |
| `inferred` | no attribute; the tool *name* matched a hint |
| `unknown` | no attribute, no hint. **Never a read-only credit.** |

Results report `action_risk_trustable` plus the specific uninstrumented tool names
to fix, and warn when closure rests on instrumentation that cannot support it.

### Verification is now a component of the harness

There were **three** certification paths, not two, and the third bypassed
verification entirely. `agenttic certify` → `run_matrix` → `run_standard` held
every trace and threw them away after scoring, so it produced a Tier A/B/C with
no trace closure, no `action_risk`, no assertions and no sign-off — while the
certificate path refused the same agent on the same evidence. Two verdicts over
one agent, with nothing reconciling them.

`run_standard` now accumulates traces across every suite × k and runs
`verify_run()` over them. It is deterministic and makes **zero model calls**, so
it runs on the normal path rather than being something a caller must remember to
ask for. Both harness entry points (`run_standard`, `run_standard_op`) get it from
the one change.

`decide()` takes the result and maps it onto the existing cap machinery:

| evidence | effect |
|---|---|
| **critical** property violated | floor breach → **Tier C** |
| non-critical property violated | cap → Tier B |
| coverage not closed | cap → Tier B, naming the number |
| properties never exercised | named in the dossier, **caps nothing** |
| verification did not run | cap → Tier B (absence is not a pass) |

Unexercised properties deliberately cap nothing — capping on them would punish an
honest report of its own limits. Certificate issuance still **refuses** outright;
tiers are graded and capped. One evidence base, two appropriate outcomes.

`Dossier.verification` carries the whole block, inside `content_sha256` so it is
tamper-evident. As with the manifest, post-v2 optional fields are dropped from
`hashable_content()` when unset, so **every dossier already persisted still
verifies offline** and the hash chain is intact.

### Coverage model v2 — closure numbers are NOT comparable with v1

The baseline and seed models gain an **`action_risk`** coverpoint: `read_only`,
`mutating_reversible`, `mutating_irreversible` (unconfirmed), and
`irreversible_confirmed`. Both models are bumped to **version 2** and
`bins_fingerprint()` changes, so the discontinuity cannot pass silently.

Why it was added: coverage recorded what the environment did *to* the agent and
never what the agent did *to the world*. Measured against deepeval, inspect_ai,
promptfoo and langsmith on the same traces, adding a case that moved money
irreversibly and tripped a CRITICAL assertion moved baseline closure by **exactly
zero** (16.6% → 16.6%). It now moves (18.0% → 22.2%) and names the untested risk
paths. Expect closure to read **lower** than under v1 — the model asks a question
it previously did not.

### Migration

- **Scorecards recorded before this release carry no sign-off and cannot be
  certified.** Re-run the suite and certify the new scorecard. Already-issued
  certificates are unaffected and continue to verify — post-v1 optional manifest
  fields are excluded from the hash when unset precisely so historical digests
  are unchanged.
- **Expect refusals.** Against real production data (0/32 scorecards closed, mean
  closure 20.4%) no agent-run certificate issues until coverage closes. That is
  the intended behaviour, not a regression: use the coverage report's named holes
  and the CDV loop to close them.

## 2.0.0 — the `ascore` name is gone (BREAKING)

`ascore` was the pre-rename name of this project. The compatibility layer that
kept it working has been removed. Everything below is a rename, not a behaviour
change — but several of them are things your deployment supplies, so read the
migration before upgrading.

### Migration — do this BEFORE upgrading

1. **Environment variables.** Every `ASCORE_*` variable is now `AGENTTIC_*`, and
   the fallback is gone: `ASCORE_*` names are **no longer read at all**. Rename
   the keys in your `.env` (values are unchanged):

   ```bash
   sed -i -E 's/^ASCORE_/AGENTTIC_/' .env
   ```

   This matters most for `ASCORE_CERT_SIGNING_KEY` / `ASCORE_PASSPORT_SIGNING_KEY`:
   without them, signing **fails closed**. Do the rename before deploying, not
   after. Verify with your published key id — it must not change.

2. **The `ascore` command is gone.** Use `agenttic`. Update any CI step, cron
   entry, systemd unit, or Dockerfile `CMD` that invokes `ascore`.

3. **Prometheus metrics renamed** `ascore_* → agenttic_*` (e.g.
   `ascore_http_requests_total` → `agenttic_http_requests_total`). Update
   dashboards, recording rules and alerts.

4. **Everyone is logged out once.** The session cookie, CSRF cookie and the
   browser token key were renamed, so existing sessions are not recognised. No
   action needed; users log in again.

5. **New SQLite installs use `agenttic.db`.** An existing `ascore.db` is still
   opened automatically when no `agenttic.db` is present, so no data is
   orphaned — but if you back up by filename, note that
   `scripts/backup.sh` now globs both.

### Removed
- `ASCORE_*` environment-variable support and the `_env.py` shim that provided it.
- The `ascore` console script and its `_ascore_alias` entry point.

### Changed
- Session/CSRF cookies, the Redis event key, Prometheus metric names, the logger
  name, the OpenTelemetry tracer name and browser storage keys all carry the
  `agenttic` name.
- The shipped `paths.registry_db` default is `agenttic.db`.
- `agenttic init` no longer scaffolds a config that mentions `ascore` — this was
  the most visible leak, since it is the first file a new user reads.

### Fixed
- **`scripts/backup.sh` could silently back up nothing.** It globbed
  `ascore*.db`; with the new default filename that matched no files while still
  reporting success. It now globs both names.
- **`scripts/restore.sh` restored to the wrong path**, defaulting to `ascore.db`
  — a file the app no longer opens, so the data appeared lost.
- `examples/certify_demo.sh` invoked the removed `ascore` command.

### Kept deliberately
`ascore` still appears where it names a live object rather than the project: the
Postgres role/database and the `ascore-data` volume (renaming those is an
`ALTER ROLE`/volume migration, not a text edit), and the `ascore.db` fallback that
prevents orphaning an existing registry.

### Note
`agenttic-langgraph` and `agenttic-openai-agents` are released as 2.0.0 in
lock-step, because they pin `agenttic` exactly. Upgrade all three together.


## Unreleased — Coverage-driven verification (SPEC-13)

### M44 — Sign-off + vPlan (Step 64)

What replaces the pass rate. The deliverable stops being *"your agent scored 86%"*
and becomes what a chip gets before tape-out.

#### Added
- **`VerificationSignoff`** (`schema/signoff.py`) — six legs plus provenance:
  coverage, assertions, formal, convergence, regression pass^k, envelope, and the
  calibration state of every judge and classifier used. **Every leg can say
  "not run"** rather than quietly reading as success, and the sign-off verdict is
  deny-by-default: a leg that did not run cannot contribute a pass.
- **`verification/vplan.py`** — traceability: requirement → coverpoint(s) →
  assertion(s) → criteria → results. **Requirements with nothing mapped to them
  are flagged UNTESTED, loudly** — and "mapped but unexercised" is reported
  separately, because the two have different fixes (write a test vs. run more
  stimulus).
- **`reporting/signoff_report.py`** — headline order **closure → assertions →
  formal → convergence → regression → envelope**, with the pass rate demoted to
  one line. A pass rate with no coverage model renders
  `unscoped — no coverage model` (Hard Rule 56). The renderer reuses the formal
  layer's honesty guard and refuses to emit an unqualified claim.
- The SPEC-12 certificate now carries `signoff_sha256`, so a certificate backed by
  a verification sign-off names it — and tampering with the sign-off breaks the
  manifest hash.

### M43 — Formal verification of the authorization layer (Step 63)

*For all inputs*, not *for 200 test cases* — over the one part of the system where
that claim is honest.

#### Added
- **`agenttic.verification.formal`** — the tool-authorization guard layer as a
  finite state machine `(permission, confirmation, entity, tenant, availability)`,
  extracted from the compiled `EnforcementPolicy` (SPEC-2): `deny` removes an
  edge, `require_approval` makes it need explicit confirmation,
  `terminate_session`/`revoke_access` move to the revoked state.
- A small **property language** — no tool without confirmation, no write from an
  unauthenticated state, no write without a prior read, no cross-tenant exposure,
  no tool after revocation. Each property carries its **scope and limit
  sentences**, so there is no API that renders the claim without them.
- **Four-valued discharge** — `proven` / `counterexample(path)` / `unbounded` /
  `not_attempted`. Exhaustive reachability over a finite guard layer is a
  decision procedure and yields `proven`; a **bounded** z3 check can refute but
  **never** returns `proven`; an incomplete search, an unbounded domain, or a
  missing solver each report themselves honestly. Silence is never read as safety.
- `render_report` is the only renderer and **refuses to emit an artifact** that
  makes an unqualified claim or mentions a proof without its limit.
- `z3-solver` added as the optional `agenttic[formal]` extra — the base install
  stays lean, and without it the z3 method reports `not_attempted`.

#### New hard rules
- **62.** Formal claims state their scope — the guard layer, not the model — in
  the same sentence as the claim. No artifact says an agent is "proven safe".
  (The shared banned-claims list was hardened with the singular/adjectival
  variants it was missing.)

### M42 — Constrained-random stimulus + the CDV loop (Steps 60–61)

Replace the fixed suite with a declared **scenario space** generated from, and a
loop that closes coverage instead of counting passes.

#### Added
- **`agenttic.stimulus.space`** — the solver stage: **pure, seeded code that must
  never import a model client** (enforced by an AST test plus a network-disabled
  10,000-point sample). Dimensions aligned 1:1 with coverpoints, per-value
  weights, and `Implies` / `Requires` / `Illegal` constraints. Includes
  **constraint propagation** (`narrow_domains`), which is what lets targeting
  reach a corner that exists only as a rare conjunction instead of timing out.
- **`agenttic.stimulus.realize`** — the *only* module here that touches a model.
  Model id, temperature and seed are pinned and the realized scenario is stored
  **verbatim**; with no client it realizes deterministically from a template, so
  the whole loop runs in CI without keys.
- **`agenttic.stimulus.oracle`** — the derived oracle: **a rule table, not a model
  call**. `intent=refund ∧ data_condition=entity_not_found` ⇒ `should_grant=False`,
  `must_convey=["...not found"]`, `forbidden_tools=["issue_refund"]`. Every
  derivation records which rules fired, so an expectation is auditable. Tone and
  clarity stay anchored judge criteria — they are never derived here.
- **`agenttic.verification.cdv`** — `run_until_closure()`: generate → run →
  extract coverage → rank holes → **bias the next batch at the holes** → repeat.
  Plus the **bug-discovery curve** over distinct failure signatures
  `(criterion_id, failure_mode, trajectory_bin)` with a convergence read, and
  frozen failing scenarios **proposed** into the directed regression suite through
  the human gate. Hard budget stops cleanly and reports partial closure with
  `closure_per_dollar`.
- Seed scenario space for `conversational_transactional`, aligned to the coverage
  model minus `trajectory` — trajectory is an *output* of a run, never an input
  you can ask for.

#### New hard rules
- **57.** Every generated scenario is reproducible from its seed plus the
  scenario-space version; the realized scenario is stored verbatim. Replay refuses
  when the space fingerprint has changed rather than silently producing different
  text.
- **58.** Expected outcomes are **derived** from the abstract point and the policy
  document, never guessed after the run.
- **63.** Failing generated scenarios become directed regression tests through the
  normal human gate — never auto-added.

### M41 — Coverage model (Step 59)

State **what was never exercised**, using traces you already have. A fixed suite
answers "what passed?"; a coverage model answers the question that decides
sign-off. Deterministic-first: trajectory, tool condition, session shape and data
condition are extracted from spans with zero model calls.

#### Added
- **`agenttic.coverage`** — `Bin` / `Coverpoint` / `Cross` / `CoverageModel` with
  validation that makes the silent-failure modes impossible: bins must be
  exhaustive (an explicit `other` bin is mandatory), a bin declares exactly one of
  a predicate or a classifier, illegal bins are declared, and **waiving a bin
  requires a named reason**.
- **Deterministic extractors** (`coverage/extractors.py`), a `@predicate` registry
  mirroring `@check`: 9 trajectory shapes (including `retry_after_error`,
  `recovered_from_tool_failure`, `escalated_to_human`, `max_steps_hit`,
  `budget_exceeded` — whether the recovery path was exercised at all), 6 tool
  conditions, 3 session shapes, 5 data conditions. Pure functions, no network.
- **Collection + hole analysis** (`coverage/collect.py`) reporting **two numbers,
  never one**: *stimulus* coverage (which bins were requested) and *trace*
  coverage (which the run actually exhibited). **Closure is computed on trace
  coverage**; a bin requested but never exhibited is reported as divergence.
  Plus ranked holes, `other`-bin drift, and illegal-bin hits.
- **Seed model** for `conversational_transactional` — 7 coverpoints and 4 required
  crosses (`intent × policy_vector` at "all", etc.). The four deterministic
  coverpoints carry the model's weight; `intent`, `emotional_register` and
  `policy_vector` are classifier-backed and render **PROVISIONAL** until measured
  against humans (SPEC-3 discipline).
- **Versioned registry artifact**: `save_coverage_model` / `get_coverage_model` /
  `list_coverage_models`, append-only, storing a `bins_fingerprint` — so widening
  or deleting a bin to hit the closure target changes the fingerprint and is a
  diff a human approves, never a silent edit.

#### New hard rules
- **56.** Coverage closure, not pass rate, is the headline. A pass rate reported
  without a coverage model is an unscoped claim.
- **61.** Unhit bins are always reported. Waiving one requires a named reason
  recorded on the model version. Silent holes are forbidden.

### M40 — Assertions (Step 62)

Continuous properties monitored on **every** trace — including runs that pass
every criterion, and sampled live production traffic. This is a parallel
verification path: it does not change the scoring engine or the Step 14
promotion gate.

#### Added
- **`agenttic.verification`** — an assertion registry mirroring the `@check`
  pattern, plus vacuity-aware temporal helpers over the span sequence
  (`never`, `always`, `precedes`, `within`, `eventually`). Pure functions: no
  model calls, no network, safe to run continuously.
- **Built-in assertion library** (8 properties, severity-mapped): no write
  without a prior read of the same entity; no tool call after the final output;
  no PII after a redaction step; no secret or credential in any output span; no
  identical tool call repeated beyond a limit; every irreversible action
  preceded by explicit confirmation; every escalation preceded by an uncertainty
  signal (where instrumented); no two tenant identifiers in one trace.
- **`AssertionSet`** (`schema/assertion_set.py`) — the *versioned registry
  artifact* pinning which properties a run monitored, stored append-only
  (`save_assertion_set` / `get_assertion_set` / `list_assertion_sets`). The
  implementations are code; the set in force is evidence, so dropping a property
  is a version bump a human approves, never a silent edit.
- **Scorecard**: a separate `assertions` block with `verification_status`,
  `assertion_violations`, and `assertions_unexercised`. Assertion results never
  enter criterion scores, the weighted mean, or `task_success_rate`.
- **Live path**: `LiveMonitor.assert_trace()` evaluates assertions on 100% of
  ingested production traces (not just the judge-sampled fraction) with zero
  judge calls.

#### New hard rules
- **59.** Assertions run on every trace — batch and live — including traces that
  pass every criterion. A violation is a failure regardless of scores: a run
  scoring 1.0 on every criterion while violating a property reports **FAIL**,
  with the property named.
- **60.** Unexercised assertions are reported as `unexercised`, never as passed.
  An assertion whose antecedent never occurred is not evidence of correctness.

## v1.0.0 — Distribution & plug-and-play (SPEC-8)

The first version a stranger can adopt: `pip install agenttic`, add one line, and
get a signed safety grade in under a minute. This is the distribution layer over
SPECs 1–7 — packaging, ergonomics, auto-detection, and docs. Scoring,
certification, and enforcement are unchanged.

### Added
- **Public umbrella package `agenttic`** (`src/agenttic/`): the supported,
  semver'd surface re-exporting the stable API from internal `ascore.*` —
  `trace`, `instrument`, `session`, `certify`, `verify`, and the canonical
  `Trace`/`Span` run type. `__all__` is enforced by a test; nothing else leaks
  (Hard Rule 36). Ships `py.typed`.
- **Packaging + extras**: the distribution is `agenttic` (one wheel, two
  packages — public `agenttic` + internal `ascore`); base install pulls **no
  framework SDK** and imports none (Hard Rule 37). Optional extras
  `agenttic[langgraph]`, `agenttic[openai]`, `agenttic[otel]`, `agenttic[all]`
  pull the matching adapter distributions (`agenttic-langgraph`,
  `agenttic-openai-agents`), which keep their own pyproject. `agenttic` console
  command added alongside back-compat `ascore`.
- **Auto-detecting `trace()`** (`agenttic._detect`): inspects an object's public
  shape and dispatches — LangGraph graph → langgraph adapter, OpenAI Agents agent
  → that adapter, any other callable → a generic OTel wrapper — without the caller
  naming the framework. Duck-typed detection (no framework import to detect);
  adapters loaded behind `try/except ImportError`. Behavior-identical (Hard Rule
  38); target from `target=`/env/`distribution.target` config; opt-in non-blocking
  `enforce` posture (Rules 31/35). No target ⇒ a logged no-op, never a phone-home.
- **`@instrument` + `session()`** (`agenttic._decorator`): wrap any custom
  `query -> response` function (or code block) into a canonical run. Unobservable
  tool calls yield a **partial** trajectory with a logged reason — never a
  fabricated one (Hard Rule 39).
- **`agenttic init`**: scaffold a runnable quickstart (config + reference `kb.json`
  + sample + steps) that certifies the reference agent with no edits and no API
  key. **`agenttic doctor`**: verify zero-touch setup — validate a captured span
  stream and/or probe a target `/v1/traces` endpoint, with actionable failures.
- **Docs**: `docs/QUICKSTART.md` (finish-line promise, every command
  test-executed), `docs/integrations/` (zero-touch OTel config per framework:
  CrewAI, LangGraph, LlamaIndex, OpenAI Agents, generic OTLP, each honest about
  captured-vs-not / NOT ASSESSED).
- **Release tooling**: `scripts/release/pypi.sh` builds all distributions, runs
  `twine check --strict`, and dry-runs to TestPyPI (the credentialed upload is a
  guarded human step). `scripts/quickstart_check.sh` + a CI job prove the
  fresh-venv install → certify → verify path runs unattended under a minute.

### Notes
- No scoring/certification/enforcement behavior changed. `import agenttic` pulls
  no framework SDK. See `docs/SPEC2_DEVIATIONS.md` for the distribution-model and
  rename deviation notes.

## v0.8.0-enforce-ramp — Progressive enforcement ramp (SPEC-7 M21)

The trust ladder from unknown-vendor to inline-trusted: a per-agent enforcement
mode layered on the SPEC-4 gateway, so a customer sees a clean shadow run before
anything blocks.

### Added
- **Enforcement ramp** (`src/agenttic/enforce/ramp.py`): a strictly-ordered
  per-agent mode — `observe` → `shadow` → `enforce_reads` → `enforce_all`.
  Shadow computes the decision the gateway *would* make and logs the would-be
  block, but lets everything through; enforce_reads blocks only read-class;
  enforce_all blocks all. Mode changes are append-only, actor-stamped events;
  advancing is deliberate, stepping down to observe is always allowed (safety
  valve). A mode change never touches the compiled policy — it can only choose
  how much of it binds, never loosen it (Hard Rule 35).
- **Shadow report** (`ramped_evaluate`, `shadow_report`): what would have been
  blocked, projected block rate, and false-positive candidates. Marking a shadow
  would-be block benign feeds the SPEC-4 hardening loop (a hardening candidate +
  checker-eval case), so false positives are tuned down before enforcement is
  enabled.
- **Surfaces**: CLI `ascore enforce mode <agent> [mode]` and
  `ascore enforce shadow-report <agent>`; API `GET`/`POST /api/enforce/mode`,
  `GET /api/enforce/shadow-report`, `POST /api/enforce/shadow-report/false-positive`;
  a `ramp` section on the enforcement dashboard (current mode + would-be blocks).

## v0.7.0-integrate — Production integration: OTel ingest, adapters, CI gate, self-host/air-gap (SPEC-7 M18–M20)

Agenttic goes to where production already is: the CI that gates merges, the
frameworks agents are built in, the OTel bus enterprises already run, and the
private networks regulated data can't leave.

### Added
- **CI safety gate** (`.github/actions/agent-safety/`): a composite GitHub Action
  (+ hermetic container entry) that runs the safety battery via `ascore` and
  posts a PR status check + summary. Per-dimension deltas vs the base branch and
  **regression gating** fail the merge when a dimension erodes even if the letter
  grade holds. Fully offline/self-contained (mock provider, no hosted account).
- **OTel-GenAI ingest** (`src/agenttic/ingest/`): an OTLP/HTTP `POST /v1/traces`
  receiver + `ascore ingest otel <file>` batch importer. Spans following the
  GenAI semantic conventions map to `Trace` (tools + I/O hashes, tokens,
  `agent_config_hash` preserved) and enforcement spans to `Decision`. Provenance
  `source="otel_ingest"`; stored `mode="live"` so ingested traces are
  structurally excluded from batch certification scorecards (SPEC-1 Step 9
  invariant). Incomplete spans degrade gracefully. Round-trip documented in
  `docs/OTEL_INTEROP.md`. `Trace.source` added (SCHEMA_VERSION 0.2.0).
- **Framework adapters** (`adapters/`): thin `agenttic-langgraph` (public
  `BaseCallbackHandler`) and `agenttic-openai-agents` (public `RunHooks`)
  packages — `trace(agent)` emits GenAI spans, behavior-identical, public-API
  only. Optional `enforce=` routes through the gateway at the ramp's non-blocking
  shadow default and fails loud without a compiled policy. Authoring guide in
  `adapters/README.md`.
- **Self-hosted / VPC / air-gapped** (`deploy/`): one-command Docker Compose
  stack (BYO-Postgres), a Helm chart (secrets, JWKS, ingress, resource docs), and
  a hard no-egress air-gap mode. A startup egress self-check refuses to boot
  naming any capability that would require outbound network; egress-only features
  are flagged unavailable, never silently degraded. `ascore airgap check`,
  `docs/SELF_HOSTING.md`, `docs/AIRGAP.md` (with a data-residency statement).

### Notes
- Observability before enforcement, always: ingest and adapters observe and
  never block. Progressive inline enforcement (the ramp) lands in M21.

## v0.6.0-passport — Passport + receipts + verifier SDK + risk feed (M16–M17)

Real Ed25519 (via the `cryptography` library — never hand-rolled).

### Added
- **Passport** (`schema/passport.py`, `passport/keys.py`, `passport/issuer.py`):
  short-lived Ed25519-signed credentials bound to the latest certification
  evidence; JWKS at `/.well-known/agenttic-jwks.json`; key rotation with overlap;
  private keys held in memory only (grep-tested never to land in
  registry/logs/events/exports). Revoked/stale certification cannot carry a live
  passport; status is checked separately from signature (revocation beats a valid
  signature). Migration v22.
- **Signed action receipts** (`passport/receipts.py`): bind a passport to one
  logged allow-decision (no receipt without a logged allow); hashes not payloads
  by default (opt-in content is redaction-checked); delegation chains resolve to
  the human principal with every hop's policy hash.
- **Verifier SDK** — Python (`verify/`) + JS (`verify/js/`), offline against a
  fetched JWKS with distinct named errors (Tampered/Expired/Revoked/UnknownKey);
  cross-implementation golden-fixture parity. `Agent-Passport` header + example
  relying-party server (accepts valid, rejects revoked).
- **Risk feed** (`feeds/risk_api.py`): authenticated aggregate signal
  (tier+status, posture, incident+SLA counts, block/approval/canary rates,
  oversight health, passport validity) — no traces/payloads/PII; agrees with
  independent SDK verification. **Webhooks** on tier_change / revocation /
  incident_s1_s2 / stage_demotion (SSRF-checked delivery).

## v0.5.0-staged — Staged release ladder + canaries + oversight (M14–M15)

### Added
- **Staged release ladder** (`schema/release.py`, `release/ladder.py`): ordered
  stages internal→vetted→limited→ga, cohorts, stage-gated access (above-stage
  calls denied with origin=stage_gate), compiler stage dimension (GA
  stricter-or-equal, tighten-only). Registry migration v20.
- **Evidence-gated promotion** (`release/promotion.py`): criteria-checked
  (observation hours, incident ceiling, tier prereq), one stage at a time, forced
  promotion impossible, append-only PromotionRecord + recompile; open S1/S2
  auto-demotes immediately.
- **Honeypot canaries** (`enforce/canaries.py`): per-agent versioned decoy tools,
  planted credentials, tripwire domains; Lane-1 trip ⇒ deny + S1 incident naming
  canary id + call ref; zero false positives; scorecard-separation invariant;
  rotation preserves append-only trip history. Migration v21.
- **Oversight analytics** (`oversight/analytics.py`): approval latency, approval
  rate, override-of-deny, post-approval incident attribution, rubber-stamp
  indicator (aggregate process health). Config toggle: sustained rubber-stamp
  tightens posture (second approver + raised sampling) — indicator-only when off.
- **Interactive RL oversight loop** (opt-in addendum, `enforce/interactive_oversight.py`):
  live review of borderline decisions + a Thompson contextual bandit that
  auto-tightens on feedback but only ever *proposes* loosening (gated by an
  explicit, logged human confirmation). `ascore oversight watch|confirm`.

## v0.4.0-enforce — Enforcement gateway + policy compiler (M11–M13)

An inline enforcement gateway compiled from certification evidence: hash-verified
policy load → Lane 1 (deterministic) → Lane 2 (classifiers) → append-only log →
Lane 3 (async judge). Nothing enforces without a logged decision.

### Added
- **Enforcement contracts** (`schema/enforcement.py`): Rule (closed action vocab),
  EnforcementPolicy (content-hashed), Decision, single append-only
  EnforcementEvent, ApprovalRequest. Registry migration v19.
- **Gateway** (`enforce/gateway.py`): session model, hash-verified policy load
  (refusal-on-mismatch is itself an event), pipeline, in-process + HTTP proxy
  (`/api/enforce/*`) with identical event shape.
- **Lane 1** — allow/deny lists, action classes, arg matchers, egress allowlist
  (SSRF reuse), rate ceilings; deny evidence names rule + pattern.
- **Lane 2** — injection screen on results (quarantine, original preserved) +
  secret/PII redaction on outbound args. Per-class fail policy (write ⇒ closed,
  read ⇒ open + fail_open logged) with hard timeout.
- **Policy compiler** (`enforce/compiler.py`): pure, config-driven; tier posture,
  caps → rule templates, autonomy scaling, staleness, incident pressure; every
  rule's origin names its mapping; byte-identical determinism; tighten-only
  overrides; recompilation on evidence change (certify + revoke wired).
- **Lane 3 async judge**: sampled verdicts retro-tag, open incidents, enqueue
  hardening, terminate/revoke — never inline. **Approvals**: park → resolve with
  PAT identity → expiry follows class fail policy; resolutions become measured
  card evidence. Hardening/checker-eval feedback loop.
- **Dashboard** metrics + FP button, **event export** (JSON + OTel-GenAI),
  **self-security** (chain-to-dossier provenance, secret redaction in exports,
  tenancy isolation, no self-exemption), public "enforced under policy <hash>".

## v0.3.0-cards — Agent cards + autonomy (M9–M10)

Provenance-tracked agent cards on the AI Agent Index taxonomy, autonomy
classification, and the Index Catalog.

### Added
- **Agent card schema** (`schema/agent_card.py`): FieldStatus trichotomy
  (value_present / none_found / confirmed_none / not_applicable), provenance
  computed from refs (measured/documented/attested), append-only versioned cards.
- **Field registry** generated deterministically from the vendored **2025 AI Agent
  Index** (CC BY 4.0) — six categories, never hand-transcribed.
- **Autofill** from Agenttic evidence (models, action space, benchmarks, incidents,
  monitoring, certification) — every field measured with resolvable refs.
- **Autonomy classifier** (L1–L5, conservative, None when unclassifiable) and a
  **covered-agent detector** (True/False/None with evidence).
- **Autonomy-scaled tiers**: frontier levels (L4/L5) add required domains + tighten
  floors; covered agent without a card ⇒ `undocumented_covered_agent` cap.
- **Index interop**: import (documented, cited, Catalog-only, no scores) + export
  (JSON/CSV, round-trip-validated). Imported agents excluded from leaderboards.
- **Public** `GET /cards/{agent_id}` (provenance classes distinct) + `GET /catalog`;
  per-category completeness. `ascore cards autofill|show|annotate`.
- Registry migration v18 (agent_cards, append-only).
- `docs/ATTRIBUTION.md`, `data/vendor/ai-agent-index/` (CC BY 4.0).

## v0.2.0-cert — Certification track (SPEC-2 → M8)

The certification track: verifiable, hash-chained evidence dossiers plus the
incident lifecycle. Honest by construction — NOT ASSESSED domains never estimated,
provisional judge caps at Tier B, elicitation inconsistency (sandbagging) disclosed.

### Added
- **Certification schema** (`schema/certification.py`): `CertificationProfile`,
  `TierDecision` (evidence-mandatory), `Attestation`, `DomainCoverage`
  (assessed_real/assessed_seed/not_assessed), hash-chained `Dossier`.
- **Incident schema** (`schema/incident.py`): S1–S4, tz/DST-safe SLA clock,
  append-only lifecycle, regulator-facing `export()`.
- **Deterministic hashing** (`certification/hashing.py`) — offline-reproducible
  dossier content hashes (sorted keys, UTF-8).
- **Profiles**: capability-domain tags, fail-loud pinned resolution, coverage
  computation, seeded `cert-agent-safety-v1`, `ascore profiles list|show`.
- **Elicitation**: neutral/strong matrix (distinct config hashes), paired-bootstrap
  gap analysis with INCONSISTENT flagging (sandbagging probe), persisted summaries.
- **Tier engine** (`certification/tiers.py`): pure, config-driven A/B/C decision;
  A unreachable under a provisional judge.
- **Certify pipeline + CLI/API**: `ascore certify` (+ `--renew`, `--mock`),
  `ascore dossier verify|revoke|show`; `POST /api/certify` (async job),
  `GET /api/dossiers[/{id}][/report.pdf]`, public `GET /certification/{id}`.
- **Renderers**: dossier md/pdf/json/inspect (NOT ASSESSED visually distinct).
- **Evaluator role**: independent attestation computed from tenancy, evaluator
  isolation, PAT-revocation abort, BYO-key billing + ceilings.
- **Incidents**: FSM over events, drift/tagged/manual triggers, SLA clocks,
  `ascore incidents …`, `/api/incidents`, regulatory crosswalks.
- **Staleness engine**: computed current/stale/revoked status surfaced on
  dossiers, leaderboard badges, and the public verify page.
- Registry migration v16–v17 (certification + incident + elicitation tables,
  append-only).

### Docs
- `AGENTTIC-MASTER-PLAYBOOK.md`, `docs/SPEC2_BASELINE.md`,
  `docs/SPEC2_DEVIATIONS.md`, `docs/SPEC_INDEX.md`, `docs/INCIDENT_CROSSWALK.md`,
  `docs/REGULATORY_CROSSWALK.md`, `examples/certify_demo.sh`.
