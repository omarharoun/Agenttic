"""The verification sign-off — what replaces the pass rate (SPEC-13 Step 64).

The deliverable stops being *"your agent scored 86%"* and becomes what a chip
gets before tape-out: **coverage model closed, assertions clean across N
generated scenarios, safety properties discharged over the authorization layer,
bug curve flat.**

Six legs plus provenance, every one of which can say "not run" rather than
quietly reading as success:

* **coverage** — closure per coverpoint and cross, unhit bins, waivers with
  reasons, `other`-bin drift
* **assertions** — total, violations, unexercised (vacuous), and
  evaluations that could not run at all (which block sign-off)
* **formal** — properties proven / counterexampled / unbounded / not attempted,
  each with its scope
* **convergence** — the bug-discovery curve and scenarios since the last new
  failure signature
* **regression** — pass^k on the directed suite of frozen historical bugs
* **envelope** — cost and latency
* **provenance** — the calibration state of every judge and classifier used

A pass rate is still reported, but **demoted to one line**, and when no coverage
model was present it renders `unscoped — no coverage model` (Hard Rule 56).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, Field

# One definition of the closure bar, config-backed. The literal used to be
# copied into this module, `coverage/model.py`, `coverage/collect.py` and both
# shipped models — five chances to disagree about what "closed" means, none of
# them the file an operator would edit.
from agenttic.coverage.targets import DEFAULT_CLOSURE_TARGET
from agenttic.schema.attestation import ScopeSummary, content_hash

LegStatus = Literal["populated", "not_run"]


class SignoffLike(Protocol):
    """What the signing path requires of any evidence sign-off.

    Two kinds of thing get certified and their evidence is not the same shape:
    an **agent run** is judged on trace coverage, assertions and formal proofs;
    a **component** (an MCP server, a memory store) has no traces at all and is
    judged on its own check battery. Both must be able to refuse, and both must
    be able to say what they cover — so the signing gate depends on this
    protocol rather than on either concrete type.
    """

    @property
    def signs_off(self) -> bool:
        """Deny-by-default. Evidence that did not run never reads as a pass."""
        ...

    def content_sha256(self) -> str:
        """Stable hash of the evidence, bound into the manifest."""
        ...

    def scope_summary(self) -> ScopeSummary:
        """What this evidence covers, for the face of the certificate."""
        ...

    def refusal_reasons(self) -> list[str]:
        """Why ``signs_off`` is False. Empty when it is True."""
        ...


class CoverageLeg(BaseModel):
    status: LegStatus = "not_run"
    model_ref: str = ""
    bins_fingerprint: str = ""
    trace_closure: float = 0.0
    stimulus_closure: float = 0.0
    #: fallback only — a populated leg carries the target the report measured
    #: against, which comes from config (Hard Rule 7). See coverage/targets.py.
    closure_target: float = DEFAULT_CLOSURE_TARGET
    closed: bool = False
    unhit_bins: list[str] = Field(default_factory=list)
    waived_bins: dict[str, str] = Field(default_factory=dict)   # bin -> reason
    other_drift: dict[str, float] = Field(default_factory=dict)
    illegal_hits: list[str] = Field(default_factory=list)
    provisional_coverpoints: list[str] = Field(default_factory=list)


class AssertionLeg(BaseModel):
    status: LegStatus = "not_run"
    assertion_set_ref: str = ""
    total: int = 0
    violations: int = 0
    unexercised: int = 0
    exercised_ratio: float = 0.0
    violated_properties: list[str] = Field(default_factory=list)
    unexercised_properties: list[str] = Field(default_factory=list)

    #: (trace x property) evaluations that could not run at all — a predicate
    #: that raised, or a trace the evaluator could not load. Disclosed in the
    #: same shape the coverage leg discloses non-results, and BLOCKING: the leg
    #: used to report `violations=0` over a silently reduced denominator, and
    #: `signs_off` bound on that zero, so an unrelated crash in one predicate
    #: turned a real violation into a signed pass. Defaults to 0 so every
    #: sign-off issued before this field existed re-validates and still signs.
    evaluations: int = 0
    evaluations_submitted: int = 0
    evaluation_failures: int = 0
    evaluation_failure_properties: list[str] = Field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.violations:
            return "FAIL"
        return "INCOMPLETE" if self.evaluation_failures else "PASS"


class FormalLeg(BaseModel):
    status: LegStatus = "not_run"
    proven: int = 0
    counterexample: int = 0
    unbounded: int = 0
    not_attempted: int = 0
    scope: str = "the tool-authorization layer"
    claims: list[str] = Field(default_factory=list)


class ConvergenceLeg(BaseModel):
    status: LegStatus = "not_run"
    scenarios_run: int = 0
    distinct_failure_signatures: int = 0
    scenarios_since_last_new_signature: int = 0
    curve_flattened: bool = False
    bug_curve: list[tuple[int, int]] = Field(default_factory=list)


class RegressionLeg(BaseModel):
    status: LegStatus = "not_run"
    frozen_cases: int = 0
    k: int = 1
    pass_hat_k: float = 0.0


class EnvelopeLeg(BaseModel):
    status: LegStatus = "not_run"
    mean_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    p95_latency_ms: float = 0.0
    closure_per_dollar: float = 0.0


class ProvenanceLeg(BaseModel):
    """The calibration state of everything that made a judgement. An
    uncalibrated judge inside a coverage model is exactly the false confidence
    this platform exists to prevent, so it is named here."""

    status: LegStatus = "not_run"
    judges: dict[str, str] = Field(default_factory=dict)        # id -> state
    classifiers: dict[str, str] = Field(default_factory=dict)   # id -> state
    harness_version: str = ""

    @property
    def any_provisional(self) -> bool:
        return any(v != "calibrated"
                   for v in {**self.judges, **self.classifiers}.values())


class ScoreboardLeg(BaseModel):
    """Observed behaviour compared against the DERIVED EXPECTATION.

    The missing UVM component. `stimulus/oracle.py:10` states it outright — "the
    abstract point plus the policy IS the reference model" — and the comparison
    already exists: `scenario/runner.py` `oracle_failures()` and
    `state_failures()` check `forbidden_tools`, `must_escalate` and
    `goal_state_delta` against what the run actually did, and
    `harness_executor.execute` gates `ExecutionResult.passed` on them.

    It just never reached the scoreboard. `ops.cdv_op` builds the Scorecard from
    the scoring engine's `RunScore`s and drops the oracle findings, so the
    reference model's verdict is absent from every scorecard, tier and
    certificate we issue. This leg carries it.

    Why here and not on `Scorecard`: `certification/attest.py:324` recomputes
    `content_hash(scorecard)` at verify time, so ANY field added to `Scorecard`
    — which embeds `RunScore` embeds `CriterionScore` — invalidates every
    certificate ever issued. `VerificationSignoff` is never recomputed there.

    `not_measured` is the load-bearing state, and it is NOT a pass. A stored
    suite runs no environment, so the state half of correctness cannot be
    observed for it; saying so is different from saying the agent behaved.
    """

    status: LegStatus = "not_run"
    #: Runs whose observed behaviour was compared against an expectation.
    compared: int = 0
    #: Runs with NO derivable expectation — no reference model applied. Never a
    #: pass: absence of a prediction is absence of evidence.
    not_measured: int = 0
    violations: int = 0
    #: Obligations whose antecedent never occurred (the expectation forbade
    #: nothing, required no escalation, ...). Vacuity, not compliance.
    unexercised: int = 0
    #: Comparisons that could not run — a malformed expectation, an environment
    #: that was asked for state and returned none. Blocking for the same reason
    #: `AssertionLeg.evaluation_failures` is: `violations == 0` must not be
    #: satisfiable by crashing.
    comparison_failures: int = 0
    violated_obligations: list[str] = Field(default_factory=list)
    unexercised_obligations: list[str] = Field(default_factory=list)
    #: Stated plainly, because a reader cannot discount what is not disclosed.
    scope_note: str = ""


class VerificationSignoff(BaseModel):
    """The headline of every report and certificate."""

    signoff_id: str
    agent_id: str
    agent_config_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    coverage: CoverageLeg = Field(default_factory=CoverageLeg)
    assertions: AssertionLeg = Field(default_factory=AssertionLeg)
    formal: FormalLeg = Field(default_factory=FormalLeg)
    convergence: ConvergenceLeg = Field(default_factory=ConvergenceLeg)
    regression: RegressionLeg = Field(default_factory=RegressionLeg)
    envelope: EnvelopeLeg = Field(default_factory=EnvelopeLeg)
    scoreboard: ScoreboardLeg = Field(default_factory=ScoreboardLeg)

    #: Which gate this sign-off was issued under. Bumped when `signs_off` gains
    #: a condition, so a stored sign-off keeps re-validating under the rule it
    #: was issued with rather than being retroactively failed by a rule that did
    #: not exist. v1: the scoreboard leg is REPORT-ONLY. v2: it blocks.
    gate_version: int = 1
    provenance: ProvenanceLeg = Field(default_factory=ProvenanceLeg)

    #: demoted to one line among several
    pass_rate: float | None = None

    #: the six legs the acceptance criteria require to be populated
    LEGS: ClassVar[tuple[str, ...]] = (
        "coverage", "assertions", "formal", "convergence", "regression",
        "envelope")

    @property
    def unscoped(self) -> bool:
        """A pass rate with no coverage model is an unscoped claim (HR56)."""
        return self.coverage.status != "populated"

    @property
    def pass_rate_label(self) -> str:
        if self.pass_rate is None:
            return "not measured"
        if self.unscoped:
            return f"{self.pass_rate:.0%} — unscoped (no coverage model)"
        return f"{self.pass_rate:.0%}"

    def populated_legs(self) -> list[str]:
        return [n for n in self.LEGS if getattr(self, n).status == "populated"]

    def missing_legs(self) -> list[str]:
        return [n for n in self.LEGS if getattr(self, n).status != "populated"]

    @property
    def complete(self) -> bool:
        return not self.missing_legs()

    @property
    def signs_off(self) -> bool:
        """The sign-off verdict: closure met, no assertion violations, every
        property actually evaluated, no formal counterexample, and no illegal-bin
        hit. Deny-by-default — a leg that did not run cannot contribute a pass.

        ``evaluation_failures == 0`` is the vacuity rule turned on the evaluator
        itself. Without it, ``violations == 0`` was satisfiable by CRASHING: one
        raising predicate wiped every result for that trace, the caller dropped
        it, and the leg reported zero violations over a denominator nobody could
        see. A property that could not be checked is not a property that held."""
        base = (self.coverage.status == "populated" and self.coverage.closed
                and self.assertions.status == "populated"
                and self.assertions.violations == 0
                and self.assertions.evaluation_failures == 0
                and self.formal.counterexample == 0
                and not self.coverage.illegal_hits)
        if self.gate_version < 2:
            # v1 ships the scoreboard leg REPORT-ONLY: it is computed, rolled up
            # and rendered, and it does not gate. Flipping it is a separate,
            # announced change, so a sign-off issued under v1 keeps meaning what
            # it meant when it was issued.
            return base
        return (base and self.scoreboard.status == "populated"
                and self.scoreboard.violations == 0
                and self.scoreboard.comparison_failures == 0)

    def content_sha256(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("created_at", None)
        return content_hash(data)

    def scope_summary(self) -> ScopeSummary:
        """The narrowing a reader needs in order to discount the claim."""
        return ScopeSummary(
            properties_total=self.assertions.total,
            properties_exercised=max(
                0, self.assertions.total - self.assertions.unexercised),
            trace_closure=self.coverage.trace_closure,
            closure_target=self.coverage.closure_target,
            closed=self.coverage.closed,
            violations=self.assertions.violations,
            unexercised_properties=list(self.assertions.unexercised_properties),
        )

    def _scoreboard_refusals(self) -> list[str]:
        """The scoreboard half of :meth:`refusal_reasons`, empty under v1.

        Mirrors :attr:`signs_off` exactly, including its version guard — naming
        a blocker that does not block would send the reader to fix the wrong
        thing, and staying silent about one that does is worse.
        """
        if self.gate_version < 2:
            return []
        why = []
        if self.scoreboard.status != "populated":
            why.append("the reference model was never compared against what the "
                       "run actually did, so correctness is unscoped")
            return why
        if self.scoreboard.violations:
            why.append(
                f"{self.scoreboard.violations} obligation(s) derived from the "
                "policy were violated: "
                + ", ".join(self.scoreboard.violated_obligations[:4]))
        if self.scoreboard.comparison_failures:
            why.append(
                f"{self.scoreboard.comparison_failures} comparison(s) could not "
                "run, so `violations == 0` is over a reduced denominator")
        return why

    def refusal_reasons(self) -> list[str]:
        """Why this sign-off is negative, in the terms the reader must act on.

        Mirrors :attr:`signs_off` condition for condition, and lists nothing
        else. Naming a leg that does not actually block the gate would send the
        reader off fixing the wrong thing — the other legs are reported as scope,
        not as blockers.
        """
        why: list[str] = []
        if self.coverage.status != "populated":
            why.append("coverage was never measured, so the claim is unscoped")
        elif not self.coverage.closed:
            why.append(
                f"coverage not closed: {self.coverage.trace_closure:.1%} against "
                f"a {self.coverage.closure_target:.0%} target"
                + (f" — unhit: {', '.join(self.coverage.unhit_bins[:5])}"
                   if self.coverage.unhit_bins else ""))
        if self.assertions.status != "populated":
            why.append("no properties were evaluated")
        if self.assertions.violations:
            why.append(
                f"{self.assertions.violations} property violation(s): "
                + "; ".join(self.assertions.violated_properties[:3]))
        if self.assertions.evaluation_failures:
            why.append(
                f"{self.assertions.evaluation_failures} of "
                f"{self.assertions.evaluations_submitted} property evaluation(s) "
                "could not run, so this battery is INCOMPLETE, not clean"
                + (": " + "; ".join(
                    self.assertions.evaluation_failure_properties[:3])
                   if self.assertions.evaluation_failure_properties else ""))
        if self.formal.counterexample:
            why.append(f"{self.formal.counterexample} formal counterexample(s)")
        if self.coverage.illegal_hits:
            why.append("illegal bin(s) hit: "
                       + ", ".join(self.coverage.illegal_hits[:5]))
        why += self._scoreboard_refusals()
        return why


class ComponentSignoff(BaseModel):
    """Evidence for a component — an MCP server, a memory store — which has no
    traces and therefore no trace coverage.

    Its battery of checks is the evidence. The same two rules apply as anywhere
    else in the platform: a check that **failed** blocks sign-off, and a critical
    check that was **skipped** blocks it too, because
    :attr:`CheckOutcome.passed` treats a skip as a pass and an unexercised check
    is not evidence of anything (Hard Rule 60, the vacuity rule).
    """

    signoff_id: str
    component_kind: Literal["mcp_server", "memory_store", "tool"]
    component_ref: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    #: check_id -> (score, critical, skipped, detail)
    checks: dict[str, tuple[float, bool, bool, str]] = Field(default_factory=dict)
    #: what this battery does NOT cover — required, and never blank
    scope_statement: str = ""

    @classmethod
    def from_outcomes(cls, *, signoff_id: str, component_kind: str,
                      component_ref: str, outcomes, scope_statement: str,
                      ) -> "ComponentSignoff":
        """Build from the ``CheckOutcome`` list a component suite already emits."""
        return cls(
            signoff_id=signoff_id,
            component_kind=component_kind,      # type: ignore[arg-type]
            component_ref=component_ref,
            checks={o.check_id: (o.score, o.critical, o.skipped, o.detail)
                    for o in outcomes},
            scope_statement=scope_statement)

    @property
    def failed(self) -> list[str]:
        return [cid for cid, (score, _c, skipped, _d) in self.checks.items()
                if not skipped and score < 1.0]

    @property
    def skipped_critical(self) -> list[str]:
        return [cid for cid, (_s, critical, skipped, _d) in self.checks.items()
                if critical and skipped]

    @property
    def exercised(self) -> list[str]:
        return [cid for cid, (_s, _c, skipped, _d) in self.checks.items()
                if not skipped]

    @property
    def signs_off(self) -> bool:
        """Deny-by-default: at least one check actually ran, none failed, and no
        critical check was skipped."""
        return (bool(self.exercised)
                and not self.failed
                and not self.skipped_critical
                and bool(self.scope_statement.strip()))

    def content_sha256(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("created_at", None)
        return content_hash(data)

    def scope_summary(self) -> ScopeSummary:
        """A component battery has no trace coverage, so closure is reported as
        met over the checks that ran — never inferred over checks that did not."""
        total = len(self.checks)
        ran = len(self.exercised)
        return ScopeSummary(
            properties_total=total,
            properties_exercised=ran,
            trace_closure=(ran / total) if total else 0.0,
            closure_target=1.0,
            closed=bool(total) and ran == total and not self.failed,
            violations=len(self.failed),
            unexercised_properties=sorted(
                cid for cid, (_s, _c, skipped, _d) in self.checks.items()
                if skipped),
        )

    def refusal_reasons(self) -> list[str]:
        why: list[str] = []
        if not self.exercised:
            why.append("no check in the battery actually ran")
        if self.failed:
            why.append(f"{len(self.failed)} check(s) failed: "
                       + ", ".join(sorted(self.failed)[:5]))
        if self.skipped_critical:
            why.append("critical check(s) skipped, which is not a pass: "
                       + ", ".join(sorted(self.skipped_critical)))
        if not self.scope_statement.strip():
            why.append("no scope_statement naming what is NOT covered")
        return why


def scoreboard_leg(findings_per_run, *, compared: int, not_measured: int = 0,
                   comparison_failures: int = 0, scope_note: str = "") -> ScoreboardLeg:
    """Roll the reference-model comparison up into its leg.

    ``findings_per_run`` is one iterable of failure signatures per compared run —
    exactly what `scenario/runner.py` `oracle_failures()` + `state_failures()`
    already return and `harness_executor` already gates on. Nothing new is
    derived here; the verdict that existed is simply carried somewhere it can be
    read.

    ``not_measured`` is runs with no derivable expectation. It is reported, never
    folded into a pass: a stored suite runs no environment, so the state half of
    correctness cannot be observed for it, and saying so is different from
    saying the agent behaved.
    """
    violated: list[str] = []
    for run in findings_per_run or []:
        for f in run or []:
            # `key()` first: FailureSignature is the real producer here and has
            # no `.signature`, so the fallback was rendering its dataclass repr
            # into the signed sign-off — 90 characters of Python syntax per
            # obligation, in the field a reader scans to see what was violated.
            key = getattr(f, "key", None)
            violated.append(key() if callable(key)
                            else (getattr(f, "signature", None) or str(f)))
    return ScoreboardLeg(
        status="populated" if (compared or not_measured) else "not_run",
        compared=compared, not_measured=not_measured,
        violations=len(violated), comparison_failures=comparison_failures,
        violated_obligations=sorted(set(violated))[:20],
        scope_note=scope_note or (
            f"{not_measured} run(s) had no derivable expectation and are NOT "
            "counted as passing" if not_measured else
            f"{compared} run(s) compared against the derived expectation"))


def build_signoff(
    *, signoff_id: str, agent_id: str, agent_config_hash: str = "",
    coverage_report=None, assertion_results=None, proof_results=None,
    cdv_result=None, regression=None, scorecard=None, provenance=None,
    scoreboard=None,
) -> VerificationSignoff:
    """Assemble a sign-off from the real artifacts. Any leg whose artifact is
    absent stays ``not_run`` — it never silently reads as a pass."""
    s = VerificationSignoff(signoff_id=signoff_id, agent_id=agent_id,
                            agent_config_hash=agent_config_hash)

    if scoreboard is not None:
        s.scoreboard = scoreboard

    if coverage_report is not None:
        cr = coverage_report
        s.coverage = CoverageLeg(
            status="populated", model_ref=cr.model_ref,
            bins_fingerprint=cr.bins_fingerprint,
            trace_closure=cr.trace_closure, stimulus_closure=cr.stimulus_closure,
            closure_target=cr.closure_target, closed=cr.closed,
            unhit_bins=[f"{cp.coverpoint_id}.{b}" for cp in cr.coverpoints.values()
                        for b in cp.unhit],
            # Every bin that left the closure denominator, with the reason —
            # bins waived on the model, and every bin of a coverpoint declared
            # not measurable. The sign-off is the signed artifact, so a hole it
            # does not name is a hole nobody downstream can see (Hard Rule 61).
            # Same implementation the report uses; they cannot disagree.
            waived_bins=cr.waived_bins(),
            other_drift=cr.other_drift(),
            illegal_hits=[f"{i.coverpoint_id}.{i.bin_id}" for i in cr.illegal_hits],
            provisional_coverpoints=cr.provisional_coverpoints)

    if assertion_results is not None:
        # Roll up PER PROPERTY, not per trace: N traces x M properties is N*M
        # results but only M properties. The same implementation the run path
        # uses, so the report and the sign-off can never disagree.
        from agenttic.verification.assertions import rollup_assertions
        summ = rollup_assertions(assertion_results)
        s.assertions = AssertionLeg(
            status="populated", total=summ["total"],
            violations=summ["violations"], unexercised=summ["unexercised"],
            exercised_ratio=summ["exercised_ratio"],
            evaluations=summ.get("evaluations", 0),
            evaluations_submitted=summ.get("evaluations_submitted", 0),
            evaluation_failures=summ.get("evaluation_failures", 0),
            evaluation_failure_properties=[
                f"{e['assertion_id']} ({e.get('traces', '')}) — {e['detail']}"
                for e in summ.get("evaluation_failure_properties", [])],
            violated_properties=[
                f"{v['assertion_id']} ({v.get('traces', '')}) — {v['detail']}"
                for v in summ["violated_properties"]],
            unexercised_properties=summ["unexercised_properties"])

    if proof_results is not None:
        counts = {k: sum(1 for r in proof_results if r.status == k)
                  for k in ("proven", "counterexample", "unbounded", "not_attempted")}
        s.formal = FormalLeg(status="populated", **counts,
                             claims=[r.claim() for r in proof_results])

    if cdv_result is not None:
        s.convergence = ConvergenceLeg(
            status="populated", scenarios_run=cdv_result.scenarios_run,
            distinct_failure_signatures=cdv_result.distinct_signatures,
            scenarios_since_last_new_signature=(
                cdv_result.scenarios_since_last_new_signature()),
            curve_flattened=cdv_result.curve_flattened(),
            bug_curve=list(cdv_result.bug_curve))
        s.envelope = EnvelopeLeg(
            status="populated", total_cost_usd=cdv_result.dollars_spent,
            mean_cost_usd=(cdv_result.dollars_spent / cdv_result.scenarios_run
                           if cdv_result.scenarios_run else 0.0),
            closure_per_dollar=cdv_result.closure_per_dollar)

    if regression is not None:
        s.regression = RegressionLeg(status="populated", **regression)

    if scorecard is not None:
        s.pass_rate = getattr(scorecard, "task_success_rate", None)
        # The guard used to be `p95 is not None`, which is true for every
        # scorecard ever built: `p95_latency_ms` is a float field defaulting to
        # 0.0. So a suite whose runs all died in the harness — those traces are
        # synthesized with `total_latency_ms=0.0` (harness/runner.py) — reported
        # `envelope: populated, p95 0ms`, and signoff_report.py printed it as a
        # measured performance envelope. Zero milliseconds is not a latency; it
        # is the absence of one, and this document is where an absence reported
        # as a measurement does the most damage.
        #
        # Only ever UPGRADE to populated here. The cdv branch above may already
        # have populated the leg from real cost evidence, and an untimed run is
        # no reason to throw that away.
        p95 = getattr(scorecard, "p95_latency_ms", None)
        runs = getattr(scorecard, "run_scores", None)
        timed = (bool(p95) if runs is None       # no per-run detail to inspect
                 else any((getattr(r, "latency_ms", 0.0) or 0.0) > 0.0
                          for r in runs))
        if p95 is not None and timed:
            s.envelope.p95_latency_ms = p95
            s.envelope.status = "populated"

    if provenance is not None:
        s.provenance = ProvenanceLeg(status="populated", **provenance)
    return s
