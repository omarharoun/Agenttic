"""Coverage collection + hole analysis (SPEC-13 Step 59).

**Two numbers, never one** (anti-pattern §7.4 — coverage theater):

* **Stimulus coverage** — which bins were *requested* by the abstract point.
* **Trace coverage** — which bins the run actually *exhibited*.

Generating a ``tool_condition=timeout`` scenario where the timeout never fired is
a stimulus hit and a trace miss. **Closure is computed on trace coverage.**
Reporting only the stimulus number would let a generator claim credit for
corners it never actually reached.

Illegal-bin hits are FAILURES and never count toward closure. Unhit bins are
always listed (Hard Rule 61) — waiving one requires a reason recorded on the
model version.

**Three states, not two.** A bin is hit, unhit, or *not measurable* — and the
third is not a worse version of the second. An unhit bin is a finding a suite can
be told to fix; a coverpoint no producer in the system can feed cannot be fixed
by any suite, and reporting it at 0% would send a generator chasing a corner that
does not exist. Reporting it as *hit* is worse still: that is the over-report
this module now refuses. So a not-measurable coverpoint carries closure ``None``
(absence of a measurement, never the number zero), an empty ``unhit`` list, and
its bins move into the waived list with the reason attached — every artifact says
in words what it could not observe.

**And measurability is a fact about a RUN, not about a model.** Some producers
instrument a dimension and some do not — ``scenario/session.py`` emits
``user_turn`` spans and the stored-suite path emits none — so a coverpoint may
declare a ``measurable_when`` gate and have the question decided sample by
sample. Its closure is then computed over the samples that carried the
instrumentation, and the count of samples that did not is reported beside it,
because "3 of 40 runs were instrumented" and "40 of 40 were" are different facts
about the same number. A gate NO sample passes collapses to the paragraph above,
identically: closure ``None``, empty ``unhit``, bins waived with the reason.

**A run that never happened is not a sample.** :func:`collect` refuses any sample
whose trace carries an execution-failure marker (see :func:`nonresult_marker`) and
counts it instead. The harness synthesizes a Trace for a run the adapter could not
complete — one ``error`` span, no tool calls, ``final_output`` set to a marker
string like ``HARNESS_FAILURE:timeout``. Fed to the extractors that string is
non-empty, so it reads as an answer (``traj_direct_answer``), and the error text
reads as environment content (``data_condition``): a transport failure against a
dead endpoint credited `entity_not_found` because the message said "404 Not
Found". Those are the two mechanisms measured, and both are the same defect — a
bin credited without evidence that the thing happened. The exclusion is counted
and named, never silent: dropping runs quietly would make closure look better by
describing a smaller space, which is the failure mode this whole module exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable, Sequence

from agenttic.coverage.extractors import is_measurable as run_measurability
from agenttic.coverage.extractors import run_predicate
from agenttic.coverage.model import OTHER_BIN, Bin, Classifier, CoverageModel
from agenttic.coverage.targets import DEFAULT_CLOSURE_TARGET
from agenttic.schema.trace import Trace
from agenttic.scoring.engine import EXECUTION_FAILURE_PREFIXES

#: An injected classifier evaluator. M41 ships deterministic extraction only;
#: without one, classifier-backed bins are reported UNEVALUATED (never "missed"),
#: and their coverpoint renders PROVISIONAL.
ClassifyFn = Callable[[Classifier, Trace, dict | None], bool]


def _round(x: float | None) -> float | None:
    """``round`` that keeps "not measured" distinguishable from zero."""
    return None if x is None else round(x, 4)


def nonresult_marker(trace: Trace) -> str | None:
    """The execution-failure marker on a run that produced no result, else ``None``.

    ``EXECUTION_FAILURE_PREFIXES`` is *imported* from ``scoring.engine`` rather
    than restated here, and that is the point: scoring already excludes these
    traces (``nonresult_reason``, engine.py:102 — "the agent did not run; its
    error text is not an answer"), so coverage disagreeing about which runs count
    would put two subsystems on two different denominators for the same batch. A
    second copy of the tuple is exactly how they would drift apart. Importing a
    constant changes no scoring behaviour.

    Returns a short, bounded label — ``prefix:kind`` — for the exclusion tally:
    ``HARNESS_FAILURE:timeout``, ``UPSTREAM_ERROR:RateLimitError``,
    ``BLACKBOX_FAILURE:ConnectionError``. The second field is a harness failure
    kind or an exception class name on every path that writes one
    (``harness/runner.py:75``, ``adapters/anthropic_simple.py:166``,
    ``adapters/blackbox_http.py:249``), so the tally cannot blow up into one
    bucket per message; the message tail is deliberately dropped because it can
    carry a URL or a payload fragment and this label reaches a report.

    Note what this does NOT test. An ``error`` span is not the signal — an agent
    that hit a real tool error and recovered has error spans and a real answer,
    and that trace is precisely what ``traj_recovered_from_tool_failure`` exists
    to measure. Nor is an empty ``final_output``: an agent that genuinely returned
    nothing DID run, and its silence is a result. Only the marker is the signal,
    because only the infrastructure writes it, and it means one specific thing —
    this run did not complete, so nothing in the trace is a claim about behaviour.

    The conservative edge, stated rather than hidden: an ``UPSTREAM_ERROR`` trace
    can carry real ``tool_call`` spans from steps that did finish before the API
    gave out (the adapter keeps them on purpose, so the token cost is not lost).
    Those calls really happened, and excluding the trace forgoes them. That is
    deliberate: the case did not run, so its fragments are not a sample of it, and
    an under-report that is COUNTED AND NAMED in the report is the safe direction
    — where a bin credited from a partial run is not, because nothing downstream
    can tell it apart from a bin the suite actually reached.
    """
    fo = trace.final_output or ""
    if not fo.startswith(EXECUTION_FAILURE_PREFIXES):
        return None
    head = [p.strip() for p in fo.split(":", 2)[:2]]
    return ":".join(p for p in head if p)


@dataclass
class Sample:
    """One observation: a run, optionally with the abstract point that asked for
    it. ``requested`` maps coverpoint_id -> bin_id (the stimulus side)."""

    trace: Trace
    scenario: dict | None = None
    requested: dict[str, str] | None = None


@dataclass
class BinCoverage:
    bin_id: str
    trace_hits: int = 0          # the run EXHIBITED it
    #: trace hits restricted to samples the coverpoint could actually be MEASURED
    #: on (``Coverpoint.measurable_when``). Identical to ``trace_hits`` for an
    #: ungated coverpoint, and the two are incremented together there, so nothing
    #: reads differently for the coverpoints that were never gated.
    #:
    #: Two counters rather than one because the raw count is still data about the
    #: batch and is asserted on: `session_single_turn` firing on every trace ever
    #: written is the fact that motivated all of this, and hiding it would make
    #: the defect unobservable. What it must never become is closure.
    measurable_hits: int = 0
    stimulus_hits: int = 0       # the point REQUESTED it
    illegal: bool = False
    waived: bool = False
    provisional: bool = False
    unevaluated: bool = False    # classifier-backed with no evaluator supplied
    #: why this bin is out of the denominator, carried from the model so the
    #: reason travels with the number instead of staying in the source file.
    reason: str = ""

    @property
    def hit(self) -> bool:
        return self.trace_hits > 0


@dataclass
class CoverpointCoverage:
    coverpoint_id: str
    kind: str
    required: bool
    provisional: bool
    bins: dict[str, BinCoverage] = field(default_factory=dict)
    #: False when this batch carried none of the evidence this coverpoint reads.
    #: For an ungated coverpoint that is the model's own ``measurable``; for a
    #: gated one ``collect`` derives it, and it is True exactly when at least one
    #: sample passed the gate. Carried onto the coverage object because the model
    #: is not in the report and every artifact is built from the report — leaving
    #: the flag on the model was how the declaration failed to reach a single
    #: output.
    measurable: bool = True
    not_measurable_reason: str = ""
    #: The coverpoint declared a ``measurable_when`` gate, so measurability was
    #: decided sample by sample. Recorded rather than inferred from the counts
    #: below, because "gated and every sample passed" and "never gated" are
    #: different facts that both leave ``n_unmeasurable`` at zero — and because a
    #: hand-built ``CoverpointCoverage`` (no gate, no counts) must keep reading
    #: its raw hits.
    measurable_per_sample: bool = False
    #: samples this coverpoint COULD be read on, and samples it could not. The
    #: second is the disclosure the closure figure is meaningless without: "3 of
    #: 40 runs were instrumented" and "40 of 40 were" are different facts about
    #: the same number, and only one of them is worth acting on.
    n_measurable: int = 0
    n_unmeasurable: int = 0

    def exhibited(self, b: BinCoverage) -> int:
        """How many samples EXHIBITED this bin, on evidence this coverpoint could
        read.

        The one place the measurable subset is applied, so closure, ``unhit``,
        `other` drift and illegal hits cannot end up disagreeing about which hits
        counted. An ungated coverpoint reads the raw count — including one on a
        report a caller hand-built, which has no per-sample counters at all.
        """
        return b.measurable_hits if self.measurable_per_sample else b.trace_hits

    def uncountable_reason(self, b: BinCoverage) -> str:
        """Why this bin is OUTSIDE the closure denominator, in words — or ``""``
        when it is inside it.

        The single statement of the exclusion rule. :meth:`countable` is defined
        as "the bins this returns nothing for", so the two cannot drift, and
        every list that must agree with closure about which bins were measured
        (``unhit`` -> ``holes()``, ``divergence()``) asks the same question
        through the same method rather than restating a subset of the conditions.
        Restating a subset is exactly how ``divergence()`` came to emit
        ``requested 1, exhibited 0`` for classifier bins nothing had evaluated:
        it copied the ``measurable`` test and none of the other four.

        The reason is returned rather than a bool because the caller that needs
        the exclusion also needs to be able to SAY it. A bin dropped from a
        report without a reason is the absence-as-silence this module refuses in
        the other direction.
        """
        if not self.measurable:
            return (f"not measurable: {self.not_measurable_reason}"
                    if self.not_measurable_reason else "not measurable")
        if b.bin_id == OTHER_BIN:
            return "`other` is the unmodelled catch-all, not a corner to close"
        if b.illegal:
            return b.reason or ("illegal bin: exhibiting it is a FAILURE, so a "
                                "run that did not produce it is the correct "
                                "outcome and never a miss")
        if b.waived:
            return b.reason or "waived"
        if b.unevaluated:
            return ("classifier-backed bin and no evaluator was supplied — it "
                    "was NEVER EVALUATED, so nothing was measured to be absent")
        return ""

    def countable(self) -> list[BinCoverage]:
        """The bins the closure fraction is computed over.

        Empty for a not-measurable coverpoint: not one of its bins is in the
        denominator, because a bin can only be scored against evidence and there
        is none to score it against. Everything derived from ``countable()``
        follows from that — closure is ``None``, ``unhit`` is empty, and the bins
        appear in the waived list with the reason. A gated coverpoint no sample
        passed the gate on lands here by exactly the same route, because
        ``measurable`` is then False for the batch.
        """
        return [b for b in self.bins.values() if not self.uncountable_reason(b)]

    @property
    def trace_closure(self) -> float | None:
        """The fraction exhibited, or ``None`` when there was nothing to measure.

        ``None`` rather than ``0.0`` is the whole point: zero is a measurement —
        "we looked and the suite never got there" — and it reads as a gap a
        generator can be told to close. A dimension with no producer is not a gap
        in the suite, and callers formatting this must say so rather than print a
        percentage.

        For a gated coverpoint the fraction is computed over the samples that
        carried the instrumentation and over nothing else, so it is a real
        measurement of a real subset — which is why ``n_unmeasurable`` has to be
        read next to it. It is ``None`` again when that subset is empty.
        """
        if not self.measurable:
            return None
        c = self.countable()
        # `None`, not `0.0`, when the denominator is empty. `measurable` answers
        # "does a producer exist for this dimension"; it does NOT answer "was any
        # bin of it actually in the denominator", and the other four exclusions
        # (`other`, illegal, waived, and classifier-backed-with-no-evaluator) can
        # empty the list on a coverpoint whose flag says True. Both shipped
        # scenario producers collect with `classify=None`, so on a fitted model
        # every semantic bin is unevaluated and `intent`, `emotional_register`
        # and `policy_vector` each reported a hard 0.0 — "we looked and the suite
        # never got there" — over bins nothing had looked at. Three of eight
        # dimensions, each dragging the headline down for not having been
        # measured. See `not_measurable`, which now names them.
        return (sum(1 for b in c if self.exhibited(b)) / len(c)) if c else None

    @property
    def stimulus_closure(self) -> float | None:
        # Also None: asking for a corner nobody can observe being reached is not
        # a coverage claim either, in either direction.
        if not self.measurable:
            return None
        c = self.countable()
        # Same rule as `trace_closure`, and it has to be the same rule: two
        # numbers on one coverpoint disagreeing about whether anything was in the
        # denominator is how a reader ends up trusting the flattering one.
        return (sum(1 for b in c if b.stimulus_hits > 0) / len(c)) if c else None

    @property
    def unhit(self) -> list[str]:
        """Bins the runs never exhibited. Empty when the coverpoint is not
        measurable: you cannot have failed to exercise what you cannot observe,
        and listing these as gaps would put a hole no suite can close in front of
        an operator (and in front of the CDV solver, which would then aim at it
        forever).

        Read through :meth:`exhibited`, so it can never disagree with
        ``trace_closure`` about which bins counted: a bin hit only on samples
        this coverpoint could not be measured on is unhit in both.
        """
        return sorted(b.bin_id for b in self.countable()
                      if not self.exhibited(b))

    def waived_bins(self) -> dict[str, str]:
        """bin_id -> reason, for every bin excluded from the denominator by a
        named waiver. Hard Rule 61: a hole is never silent.

        A not-measurable coverpoint contributes ALL of its real bins here, under
        the coverpoint's reason, because that is exactly what happened to them —
        they left the denominator, and this is the only list that says so. A bin
        with its own waiver keeps its own reason: it is more specific.
        """
        out: dict[str, str] = {}
        for b in self.bins.values():
            if b.bin_id == OTHER_BIN or b.illegal:
                continue
            if b.waived:
                out[b.bin_id] = b.reason
            elif not self.measurable:
                out[b.bin_id] = f"not measurable: {self.not_measurable_reason}"
        return out

    @property
    def other_hits(self) -> int:
        """Samples that matched no modelled bin — counted over the measurable
        subset only.

        This is the distinction that made per-sample measurability necessary
        rather than merely tidy. ``other_drift`` reports a rising `other` count
        as *"the model is missing a dimension"*, and an uninstrumented run
        matching nothing is not that finding at all — it is *"this run carries no
        turn markers"*. Two different findings with two different owners, and
        counting the second as the first is exactly the conflation that made
        tightening `session_single_turn` to ``== 1`` the wrong repair.
        """
        b = self.bins.get(OTHER_BIN)
        return self.exhibited(b) if b else 0

    @property
    def illegal_hits(self) -> int:
        """Illegal-bin hits, over the measurable subset.

        An illegal hit is a failure, so under-reporting one is not a neutral
        choice — but a gate exists precisely because this coverpoint's predicates
        cannot be trusted on a run that lacks the instrumentation, and a failure
        credited from a predicate firing by default is not a failure the operator
        can act on. The sample is disclosed either way, in ``n_unmeasurable``.
        """
        return sum(self.exhibited(b) for b in self.bins.values() if b.illegal)


@dataclass
class CrossCoverage:
    cross_id: str
    coverpoints: list[str]
    target_combos: list[tuple[str, ...]] = field(default_factory=list)
    hit_combos: set[tuple[str, ...]] = field(default_factory=set)

    @property
    def closure(self) -> float:
        if not self.target_combos:
            return 0.0
        return len(set(self.target_combos) & self.hit_combos) / len(self.target_combos)

    @property
    def unhit(self) -> list[tuple[str, ...]]:
        return sorted(set(self.target_combos) - self.hit_combos)


@dataclass
class Hole:
    """An unexercised corner. ``unreachable`` is decided by a human waiver; at
    collection time every hole is merely *unexercised*."""

    kind: str                       # "bin" | "cross"
    where: str                      # coverpoint_id or cross_id
    what: str                       # bin_id or "a×b" combination
    required: bool = True
    rank: float = 0.0               # higher = more important to fill


@dataclass
class IllegalHit:
    coverpoint_id: str
    bin_id: str
    count: int


@dataclass
class CoverageReport:
    model_ref: str
    bins_fingerprint: str
    #: Samples actually MEASURED — the denominator of ``other_drift`` and the
    #: number ``headline()`` reports coverage "over". Non-results were never
    #: measured, so counting them here would understate drift and overstate how
    #: much evidence the closure figure rests on.
    n_samples: int
    coverpoints: dict[str, CoverpointCoverage] = field(default_factory=dict)
    crosses: dict[str, CrossCoverage] = field(default_factory=dict)
    illegal_hits: list[IllegalHit] = field(default_factory=list)
    closure_target: float = DEFAULT_CLOSURE_TARGET
    #: Samples refused by :func:`nonresult_marker` — runs the adapter could not
    #: complete. Carried on the report because the report is what every artifact
    #: is built from: a filter whose count lives only in the filtering function
    #: reaches no output, and an unreported exclusion is its own over-report (the
    #: remaining runs would look like the whole batch).
    n_nonresults: int = 0
    #: marker label -> how many samples carried it, so the exclusion is not just
    #: a count but a statement of WHAT failed. An operator reading "2 excluded"
    #: cannot act; "BLACKBOX_FAILURE:ConnectionError ×2" is a pointed finding.
    nonresult_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def n_submitted(self) -> int:
        """Samples handed to :func:`collect`, measured or not. Kept as a derived
        property so ``n_samples + n_nonresults`` cannot fall out of step with it."""
        return self.n_samples + self.n_nonresults

    def _scored(self) -> list[CoverpointCoverage]:
        """The coverpoints the headline is an average over.

        ``required`` AND ``measurable``. The model derives one from the other
        (``Coverpoint._validate`` forces ``required=False`` when a coverpoint is
        not measurable), so the second test is redundant *there* — but a
        ``CoverpointCoverage`` can be built without a model, and the headline is
        the number this whole change is about. Two tests, stated once, here.
        """
        return [c for c in self.coverpoints.values() if c.required and c.measurable]

    # -- the headline ------------------------------------------------------
    @property
    def trace_closure(self) -> float:
        """THE number. Computed on what runs exhibited, never on what was asked."""
        cps = self._scored()
        # None can only appear if a caller hand-built a CoverpointCoverage that
        # is required AND not measurable; dropping it from the numerator while
        # leaving it in the denominator would be a quiet penalty, so it leaves
        # both.
        vals = [v for v in
                ([c.trace_closure for c in cps]
                 + [x.closure for x in self.crosses.values()])
                if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def stimulus_closure(self) -> float:
        vals = [v for v in (c.stimulus_closure for c in self._scored())
                if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def not_measurable(self) -> dict[str, str]:
        """coverpoint_id -> reason, for every dimension this batch could not feed.

        The headline's companion. A closure figure that silently dropped a
        dimension would be a better-looking number describing a smaller space,
        which is the one thing this platform must never ship.

        A gated coverpoint appears here when NOT ONE sample passed its gate. That
        is the same statement as before per-sample measurability existed, made
        about a batch instead of about a build, and it is deliberately worded and
        rendered identically: for an uninstrumented batch nothing about this
        output moved.
        """
        out: dict[str, str] = {}
        for c in sorted(self.coverpoints.values(),
                        key=lambda c: c.coverpoint_id):
            if not c.measurable:
                out[c.coverpoint_id] = c.not_measurable_reason
            elif not c.countable():
                # Flagged measurable, and yet nothing of it was in the
                # denominator. Its closure is `None` for that reason, so it left
                # the headline — and a dimension that leaves the headline
                # silently is precisely the "better-looking number describing a
                # smaller space" this method exists to prevent. The reason comes
                # from `uncountable_reason`, the one statement of the exclusion
                # rule, so this list and the closure fraction can never disagree
                # about which bins were measured.
                why = sorted({c.uncountable_reason(b) for b in c.bins.values()})
                out[c.coverpoint_id] = (
                    "no bin of this dimension was in the denominator, so it is "
                    "outside the closure figure and was not measured either way: "
                    + "; ".join(w for w in why if w))
        return out

    def partial_measurability(self) -> dict[str, dict]:
        """coverpoint_id -> how much of the batch its closure was measured over.

        Reported only for a coverpoint that was measurable for SOME samples and
        not others, because that is the only case a reader cannot infer: a
        closure of 0.5 over 3 instrumented runs out of 40 and a closure of 0.5
        over all 40 are the same number and different facts, and one of them is
        worth acting on. A coverpoint measurable for every sample says nothing
        here (there is nothing to disclose) and one measurable for none is
        already named in :attr:`not_measurable`, where it belongs.
        """
        return {c.coverpoint_id: {
                    "measured_over": c.n_measurable,
                    "not_measurable_samples": c.n_unmeasurable,
                    "samples": self.n_samples,
                    "reason": c.not_measurable_reason}
                for c in sorted(self.coverpoints.values(),
                                key=lambda c: c.coverpoint_id)
                if c.measurable_per_sample and c.measurable
                and c.n_unmeasurable}

    def waived_bins(self) -> dict[str, str]:
        """``coverpoint.bin`` -> reason for every bin outside the denominator.

        One implementation, used by :meth:`as_dict` and by ``build_signoff`` — a
        report and a sign-off that computed this separately could disagree about
        which holes were declared, and the sign-off is the signed one.
        """
        return {f"{cp.coverpoint_id}.{b}": why
                for cp in self.coverpoints.values()
                for b, why in sorted(cp.waived_bins().items())}

    @property
    def closed(self) -> bool:
        return self.trace_closure >= self.closure_target and not self.illegal_hits

    @property
    def provisional_coverpoints(self) -> list[str]:
        return sorted(c.coverpoint_id for c in self.coverpoints.values() if c.provisional)

    def divergence(self) -> list[dict]:
        """Bins the stimulus REQUESTED but the trace never EXHIBITED — the
        generator asked for a corner it never actually reached.

        A not-measurable coverpoint contributes nothing here, for the same reason
        it contributes no holes: ``exhibited: 0`` is a measurement, and this list
        reads as *the generator asked and the run failed to deliver*. Nothing can
        deliver a dimension no producer emits, so the row would blame the
        generator for a gap in the instrumentation — and it is the one row that
        still prints a hard zero for a bin whose closure is deliberately
        ``None``. Today no stimulus space requests one (v2 deleted the
        session_shape dimension), so this is a guard, not a repair: a hand-built
        ``Sample(requested={"session_shape": "multi_turn"})`` produced exactly
        that row, and nothing stops the dimension coming back.

        For a gated coverpoint the row is emitted only once the batch made it
        measurable at all, and ``exhibited`` counts the measurable subset — the
        same subset closure was computed over, through the same method, so the
        list and the number cannot tell two stories. How much of the batch that
        subset was is in :meth:`partial_measurability`, and a reader weighing a
        divergence row on a gated dimension needs both.

        **And the coverpoint gate was only one of five exclusions.** This loop
        used to test ``cp.measurable`` and then iterate every bin, so a bin
        outside the denominator for any of the other four reasons — waived,
        illegal, `other`, or CLASSIFIER-BACKED WITH NO EVALUATOR — still printed
        a hard zero. The last one is not hypothetical: both shipped scenario
        producers collect with ``classify=None`` on purpose (scenario/runner.py,
        cli.py: "deterministic bins only, no model calls"), so on a fitted model
        EVERY semantic bin is ``unevaluated``, and a point requesting
        ``intent=complaint`` produced ``requested 1, exhibited 0`` for a bin
        nothing ever looked at. Worse than a lone false row: it sat in the same
        list, under the same glyph and the same counts, as a REAL divergence on a
        deterministic dimension, so a reader could not tell the finding from the
        fabrication. The rule is now asked once, of
        :meth:`CoverpointCoverage.uncountable_reason`, which is the same rule
        ``holes()`` has always used — the two lists disagreeing about which bins
        were measured was the bug in both rounds of this repair.

        A requested bin excluded here is NOT dropped: it moves to
        :meth:`unmeasured_requests` with the reason attached, because "we asked
        and the run did not deliver" and "we asked and nobody looked" are two
        findings with two different owners, and collapsing them into one string
        is the defect this module exists to prevent.
        """
        out = []
        for cp in self.coverpoints.values():
            for b in cp.countable():
                if b.stimulus_hits > 0 and cp.exhibited(b) == 0:
                    out.append({"coverpoint_id": cp.coverpoint_id, "bin_id": b.bin_id,
                                "requested": b.stimulus_hits, "exhibited": 0})
        return sorted(out, key=lambda d: (d["coverpoint_id"], d["bin_id"]))

    def unmeasured_requests(self) -> list[dict]:
        """Bins the stimulus REQUESTED that no measurement was ever taken of.

        The other half of :meth:`divergence`, and the reason removing those rows
        is a repair rather than a deletion. The generator really did aim at these
        corners; what did not happen is anybody looking. Saying nothing at all
        would trade an over-report for silence, and on this product silence about
        an unexercised check is the more expensive of the two mistakes.

        The row shape is deliberately NOT a divergence row: there is no
        ``exhibited`` key, because there is no such number — the absence of the
        field is the absence of the measurement — and there is a ``reason``,
        which a divergence row has no use for. A renderer that received both
        lists could not print one as the other by accident.
        """
        out = []
        for cp in self.coverpoints.values():
            for b in cp.bins.values():
                why = cp.uncountable_reason(b)
                if b.stimulus_hits > 0 and why:
                    out.append({"coverpoint_id": cp.coverpoint_id,
                                "bin_id": b.bin_id,
                                "requested": b.stimulus_hits, "reason": why})
        return sorted(out, key=lambda d: (d["coverpoint_id"], d["bin_id"]))

    def other_drift(self) -> dict[str, float]:
        """Share of samples landing in each `other` bin. A rising number is a
        finding: the model is missing a dimension.

        The denominator is the samples the coverpoint could be MEASURED on, not
        the batch, because those are the only samples whose `other` hits mean
        what this number claims. A gated coverpoint on a half-instrumented batch
        would otherwise report drift diluted by runs it never looked at — a
        smaller number describing a smaller space, which is the failure mode this
        module exists to prevent, pointing the other way.
        """
        out: dict[str, float] = {}
        for cp in self.coverpoints.values():
            n = cp.n_measurable if cp.measurable_per_sample else self.n_samples
            if cp.other_hits and n:
                out[cp.coverpoint_id] = round(cp.other_hits / n, 4)
        return out

    def holes(self) -> list[Hole]:
        """Unhit bins and cross combinations, ranked. NOTE: at M41 the rank is
        structural (required coverpoints and crosses first); ranking by the
        severity of the criteria that would have applied arrives with the CDV
        loop, which is where criteria are in scope.

        A not-measurable coverpoint contributes NO holes. A hole is a call to
        action — the CDV solver takes this list as its target set (verification/
        cdv.py) — and a bin no producer can feed is a corner no scenario can
        reach, so emitting it would point the solver at an unreachable target for
        the rest of the run. It is disclosed through ``not_measurable`` and the
        waived list instead, where it reads as a confession rather than a task.
        """
        out: list[Hole] = []
        for cp in self.coverpoints.values():
            if not cp.measurable:
                continue
            for b in cp.unhit:
                out.append(Hole("bin", cp.coverpoint_id, b, cp.required,
                                rank=2.0 if cp.required else 1.0))
        for x in self.crosses.values():
            for combo in x.unhit:
                out.append(Hole("cross", x.cross_id, "×".join(combo), True, rank=3.0))
        return sorted(out, key=lambda h: (-h.rank, h.where, h.what))

    def as_dict(self) -> dict:
        return {
            "model": self.model_ref,
            "bins_fingerprint": self.bins_fingerprint,
            "samples": self.n_samples,
            # The batch is only fully described by all three. `samples` alone
            # would let a run in which 8 of 10 cases died in transport read as a
            # clean 8-case run.
            "samples_submitted": self.n_submitted,
            "non_results": self.n_nonresults,
            "non_result_reasons": dict(sorted(self.nonresult_reasons.items())),
            "trace_closure": round(self.trace_closure, 4),
            "stimulus_closure": round(self.stimulus_closure, 4),
            "closure_target": self.closure_target,
            "closed": self.closed,
            "illegal_hits": [{"coverpoint_id": i.coverpoint_id, "bin_id": i.bin_id,
                              "count": i.count} for i in self.illegal_hits],
            "provisional_coverpoints": self.provisional_coverpoints,
            # The two disclosures that must travel with every number: which
            # dimensions could not be measured at all, and which bins left the
            # denominator by a named waiver (Hard Rule 61).
            "not_measurable": self.not_measurable,
            # ...and the third: a dimension measured over PART of the batch. It
            # is not "not measurable" (a number exists) and not fully measured
            # either, and printing the number without the fraction it rests on
            # would be the same undisclosed denominator as `samples` without
            # `non_results`, one dimension in.
            "partially_measurable": self.partial_measurability(),
            "waived_bins": self.waived_bins(),
            "other_drift": self.other_drift(),
            "stimulus_vs_trace_divergence": self.divergence(),
            # ...and the requests that never became a measurement at all. This
            # key exists so removing them from the line above is a MOVE and not
            # a deletion: the point aimed at these corners, and the only thing
            # that can be said about them is that nobody looked. Separate key,
            # separate shape (a `reason`, and no `exhibited`), so no consumer can
            # render one as the other.
            "stimulus_requested_not_measured": self.unmeasured_requests(),
            "coverpoints": {
                cp.coverpoint_id: {
                    # null, not 0 — a not-measurable coverpoint has no closure.
                    # JSON `null` survives the round-trip into every artifact and
                    # forces every renderer to decide what to print, which is the
                    # behaviour we want: a percentage here would be a claim.
                    "trace_closure": _round(cp.trace_closure),
                    "stimulus_closure": _round(cp.stimulus_closure),
                    "unhit": cp.unhit, "other_hits": cp.other_hits,
                    "provisional": cp.provisional,
                    "not_measurable": not cp.measurable,
                    "not_measurable_reason": cp.not_measurable_reason,
                    # The two numbers that say what the closure above rests on.
                    # Zero and zero for an ungated coverpoint, which is measured
                    # over every sample by construction and has nothing to
                    # confess; a gated one states both, and the second is never
                    # inferable from the first.
                    "samples_measured_over": cp.n_measurable,
                    "samples_not_measurable": cp.n_unmeasurable,
                } for cp in self.coverpoints.values()},
            "crosses": {x.cross_id: {"closure": round(x.closure, 4),
                                     "unhit": ["×".join(c) for c in x.unhit]}
                        for x in self.crosses.values()},
            "holes": [{"kind": h.kind, "where": h.where, "what": h.what}
                      for h in self.holes()],
        }

    def headline(self) -> str:
        prov = (f"  [PROVISIONAL: {', '.join(self.provisional_coverpoints)}]"
                if self.provisional_coverpoints else "")
        # The exclusion rides on the headline itself, not a footnote: this string
        # is what the CLI and the console print, and "closure over 8 samples" is
        # a different claim when 2 more were submitted and never ran.
        excluded = ""
        if self.n_nonresults:
            named = ", ".join(f"{k}×{v}" for k, v in
                              sorted(self.nonresult_reasons.items()))
            excluded = (f" [{self.n_nonresults} of {self.n_submitted} submitted "
                        f"NOT MEASURED — run did not complete: {named}]")
        # Same argument, one dimension in: a coverpoint scored over 3 of 40 runs
        # is a real number over a real subset, and the headline is where the
        # number is printed, so it is where the subset has to be printed too.
        partial = self.partial_measurability()
        if partial:
            names = ", ".join(f"{cp} on {d['measured_over']} of {d['samples']}"
                              for cp, d in sorted(partial.items()))
            excluded += f" [measured on part of the batch: {names}]"
        return (f"closure {self.trace_closure:.0%} of target {self.closure_target:.0%} "
                f"over {self.n_samples} samples "
                f"(stimulus {self.stimulus_closure:.0%}) — "
                f"{len(self.holes())} hole(s), {len(self.illegal_hits)} illegal hit(s)"
                + excluded + prov)


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #

def _bin_matches(b: Bin, trace: Trace, scenario: dict | None,
                 classify: ClassifyFn | None) -> bool | None:
    """True/False, or None when the bin cannot be evaluated (classifier-backed
    with no evaluator supplied)."""
    if b.predicate_ref:
        return run_predicate(b.predicate_ref, trace, scenario)
    if b.classifier is not None:
        if classify is None:
            return None
        return bool(classify(b.classifier, trace, scenario))
    return False        # the `other` bin is resolved by the caller


def collect(model: CoverageModel, samples: Sequence[Sample], *,
            classify: ClassifyFn | None = None) -> CoverageReport:
    """Extract coverage from samples. Deterministic bins need no model calls;
    classifier bins are only evaluated if an evaluator is injected.

    Samples whose trace is a NON-RESULT are refused here, before a single
    predicate sees them, and tallied onto the report (``n_nonresults`` /
    ``nonresult_reasons``). See :func:`nonresult_marker` for what qualifies.

    **Why the gate is here and not in the caller.** ``ops.verify_op`` could have
    filtered its list before building Samples, and that would have fixed the run
    path only. Three things argue for the collection entry point instead:

    * ``verification/cdv.py`` also calls ``collect`` — twice, once per round. The
      CDV solver takes ``report.holes()`` as its target set, so a bin credited by
      a run that died in transport is a hole the solver stops aiming at. A gate in
      ``verify_op`` protects the report and leaves the loop steering on fiction.
    * ``n_samples`` is the denominator of ``other_drift`` and the number the
      headline is stated "over". Only the function that decides what to measure
      can keep those honest; a caller filtering upstream would leave the report
      unable to say anything about what it dropped.
    * It is the same lesson as ``measurable``: the report is what every artifact
      is built from, so a fact the report does not carry reaches nothing. The
      count and the reasons live on the report, and therefore in ``as_dict()``,
      ``headline()``, and every consumer of them, for free.

    **Measurability is decided here too, per sample.** A coverpoint declaring a
    ``measurable_when`` gate is read only off the samples that carry the
    instrumentation it needs; the rest contribute nothing to it — not a hit, not
    a miss, not an `other` drift row — and are counted in ``n_unmeasurable`` so
    the closure figure never travels without the fraction of the batch it was
    computed over. Two properties are worth stating because they are what makes
    this a fix rather than a relabel: a gate no sample passes leaves the
    coverpoint reading exactly as a flatly not-measurable one, and a gate is
    never consulted for a coverpoint that does not declare one, so no ungated
    number moves by a bit.
    """
    model.validate_against_registry()
    measured: list[Sample] = []
    nonresults: dict[str, int] = {}
    for s in samples:
        marker = nonresult_marker(s.trace)
        if marker is None:
            measured.append(s)
        else:
            nonresults[marker] = nonresults.get(marker, 0) + 1
    # Everything below reads `measured`, never `samples`. The parameter is left
    # untouched on purpose: a reader has to be able to see, at the one line that
    # matters, that no refused sample reaches a predicate.

    report = CoverageReport(model_ref=model.ref(),
                            bins_fingerprint=model.bins_fingerprint(),
                            n_samples=len(measured),
                            closure_target=model.closure_target,
                            n_nonresults=sum(nonresults.values()),
                            nonresult_reasons=nonresults)

    for cp in model.coverpoints:
        # The declarations travel with the numbers. Leaving `measurable` on the
        # model was the whole defect: the report is what every artifact is built
        # from, so a flag the report does not carry reaches nothing.
        cov = CoverpointCoverage(cp.coverpoint_id, cp.kind, cp.required,
                                 cp.provisional, measurable=cp.measurable,
                                 not_measurable_reason=cp.not_measurable_reason,
                                 measurable_per_sample=cp.measurable_when is not None)
        for b in cp.bins:
            cov.bins[b.bin_id] = BinCoverage(
                bin_id=b.bin_id, illegal=b.illegal, waived=b.waived,
                provisional=b.provisional, reason=b.reason,
                unevaluated=(b.classifier is not None and classify is None))
        report.coverpoints[cp.coverpoint_id] = cov

    # per-sample extraction
    per_sample_hits: list[dict[str, set[str]]] = []
    for sample in measured:
        hits: dict[str, set[str]] = {}
        for cp in model.coverpoints:
            cov = report.coverpoints[cp.coverpoint_id]
            # Does THIS run carry the instrumentation this coverpoint reads? An
            # ungated coverpoint asks nothing and every sample counts, exactly as
            # before. A gated one asks once, before any bin is evaluated, so the
            # answer applies uniformly to every bin of the coverpoint — a bin
            # that fired on a run the coverpoint cannot be read on is recorded as
            # raw data and credited to nothing.
            readable = (run_measurability(cp.measurable_when, sample.trace,
                                          sample.scenario)
                        if cp.measurable_when else True)
            if cp.measurable_when:
                if readable:
                    cov.n_measurable += 1
                else:
                    cov.n_unmeasurable += 1
            matched: set[str] = set()
            for b in cp.bins:
                if b.bin_id == OTHER_BIN:
                    continue
                m = _bin_matches(b, sample.trace, sample.scenario, classify)
                if m:
                    matched.add(b.bin_id)
                    cov.bins[b.bin_id].trace_hits += 1
                    if readable:
                        cov.bins[b.bin_id].measurable_hits += 1
            if not matched:
                # exhaustive by construction: nothing matched -> `other`
                cov.bins[OTHER_BIN].trace_hits += 1
                if readable:
                    cov.bins[OTHER_BIN].measurable_hits += 1
                matched.add(OTHER_BIN)
            hits[cp.coverpoint_id] = matched

            # the stimulus side, recorded separately and never mixed in
            want = (sample.requested or {}).get(cp.coverpoint_id)
            if want and want in cov.bins:
                cov.bins[want].stimulus_hits += 1
        per_sample_hits.append(hits)

    # A gated coverpoint's `measurable` is a fact about THIS BATCH, so it is
    # derived once the batch has been read: True iff some sample carried the
    # instrumentation. `required` is derived with it and never apart from it —
    # the model enforces that identity (Coverpoint._validate) and splitting them
    # here would let a dimension report a closure figure the headline ignores,
    # which is a number nothing depends on. A gate no sample passed leaves both
    # False, which is bit-for-bit the state a flatly not-measurable coverpoint
    # has always been in.
    for cp in model.coverpoints:
        if cp.measurable_when is None:
            continue
        cov = report.coverpoints[cp.coverpoint_id]
        cov.measurable = cov.n_measurable > 0
        cov.required = cov.measurable

    # illegal bins are failures, excluded from closure by countable()
    for cp_id, cov in report.coverpoints.items():
        for b in cov.bins.values():
            n = cov.exhibited(b)
            if b.illegal and n:
                report.illegal_hits.append(IllegalHit(cp_id, b.bin_id, n))

    # Crosses. Raw per-bin hits are still recorded for a not-measurable
    # coverpoint (they are data about the trace, and dropping them would hide
    # the fact that `session_single_turn` fires on every trace) — they are simply
    # never turned into a closure figure. A cross that named a not-measurable
    # coverpoint WOULD launder them back into a number through this loop; no
    # shipped model declares one, and the honest fix if one is ever wanted is for
    # CoverageModel to refuse the cross rather than for this loop to guess.
    #
    # Per-sample measurability does NOT change that, and the loop below is why:
    # `per_sample_hits` is the raw matched set, unfiltered by any gate, because a
    # combination is a statement about one sample across several axes and this
    # loop has no per-axis notion of which samples were readable. Fixing a cross
    # would mean intersecting the measurable subsets of all its axes, which is
    # not built — so `CoverageModel` keeps refusing a cross that names a gated
    # axis, and this comment is where a future author is told what the loop would
    # have to learn first.
    for x in model.crosses:
        cc = CrossCoverage(x.cross_id, list(x.coverpoints))
        cps = [model.coverpoint(c) for c in x.coverpoints]
        legal_axes = [[b.bin_id for b in cp.countable_bins()] for cp in cps]  # type: ignore[union-attr]
        illegal = {tuple(combo[c] for c in x.coverpoints)
                   for combo in x.illegal_combinations
                   if all(c in combo for c in x.coverpoints)}
        if x.target == "all":
            cc.target_combos = [c for c in product(*legal_axes) if c not in illegal]
        else:
            cc.target_combos = [tuple(t[c] for c in x.coverpoints)
                                for t in x.target  # type: ignore[union-attr]
                                if tuple(t[c] for c in x.coverpoints) not in illegal]
        for hits in per_sample_hits:
            axes = [sorted(hits.get(c, set())) for c in x.coverpoints]
            for combo in product(*axes):
                if combo in illegal:
                    continue
                cc.hit_combos.add(combo)
        report.crosses[x.cross_id] = cc

    return report
