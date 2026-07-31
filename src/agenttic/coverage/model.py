"""The coverage model — defining what "tested" means (SPEC-13 Step 59).

A coverage model declares the space of situations an agent must be exercised in.
Closure over that model, not pass rate, is the headline (Hard Rule 56): "86%
passed" answers *what passed*; a coverage model answers ***what did we never
exercise?***

Four structural rules are enforced here rather than left to review, because each
is a way this build fails silently:

* **Bins are exhaustive.** Every coverpoint must carry an explicit ``other`` bin.
  A rising ``other`` count is itself a finding — the model is missing a dimension.
* **Deterministic coverpoints cannot be classifier-backed** (anti-pattern §7.5).
  Trajectory shape, tool condition, session shape and data condition are
  deterministic *by construction*; letting them take a classifier because
  predicates are more work would quietly make the whole model provisional.
* **Waiving a bin requires a named reason** (Hard Rule 61). Silent holes are
  forbidden; an unhit bin is always reported. The same rule governs declaring a
  whole coverpoint ``measurable=False``: a dimension no producer in the system
  can feed must say so, in words, rather than quietly scoring whatever its
  predicates happen to return. Measurability is a fact about a RUN, not about a
  model — a build can have one producer that instruments a dimension and another
  that does not — so a coverpoint may name a ``measurable_when`` gate and have
  the question decided per sample, with the count of samples it could not
  measure reported beside the closure it computed over the ones it could.
* **A cross may only name coverpoints that can produce a closure figure.**
  Excluding a not-measurable coverpoint from the headline and then crossing it
  with a measured one puts the same unfed bins straight back in as combinations,
  and a cross closure IS averaged into the headline. Refused, with the
  coverpoint's own reason quoted, rather than left for ``collect`` to
  special-case: that loop cannot tell a laundered combination from a real one.

Classifier-backed bins (``intent``, ``emotional_register``) inherit the SPEC-3
calibration discipline: they are PROVISIONAL until measured against humans.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agenttic.coverage.targets import DEFAULT_CLOSURE_TARGET

#: the catch-all bin every coverpoint must declare
OTHER_BIN = "other"

CoverpointKind = Literal["deterministic", "classifier"]

#: Coverpoints that are deterministic by construction — extracted from spans, so
#: a classifier here is always a mistake (anti-pattern §7.5).
DETERMINISTIC_BY_CONSTRUCTION = frozenset({
    "trajectory", "tool_condition", "session_shape", "data_condition",
    # agent_steps is a count of llm_call spans. It was the half of the old
    # session_shape that was actually being measured, and counting is not a
    # semantic judgement.
    "agent_steps",
    # action_risk is read straight off span attributes and tool names by the
    # same functions the assertion layer uses — a classifier here would make the
    # most safety-relevant coverpoint the least trustworthy one.
    "action_risk",
})


class Classifier(BaseModel):
    """An anchored judge prompt backing a semantic bin. Subject to the same
    calibration discipline as any judge criterion: PROVISIONAL until measured."""

    prompt: str
    anchors: dict = Field(default_factory=dict)   # must carry pass/fail examples
    calibrated: bool = False                      # Hard Rule 6 / SPEC-3
    alpha: float | None = None                    # agreement with humans, once measured

    @model_validator(mode="after")
    def _anchored(self) -> "Classifier":
        missing = {"pass", "fail"} - set(self.anchors)
        if missing:
            raise ValueError(
                f"classifier bins require pass/fail anchors; missing {sorted(missing)}")
        if self.calibrated and self.alpha is None:
            raise ValueError(
                "a classifier marked calibrated must record the measured alpha")
        return self

    @property
    def provisional(self) -> bool:
        return not self.calibrated


class Bin(BaseModel):
    """One value a coverpoint can take."""

    bin_id: str
    label: str = ""
    #: a registered deterministic predicate (see coverage.extractors)
    predicate_ref: str | None = None
    #: OR an anchored classifier. Never both.
    classifier: Classifier | None = None
    #: hitting an illegal bin is a FAILURE, never coverage
    illegal: bool = False
    #: excluded from closure — requires a named reason (Hard Rule 61)
    waived: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "Bin":
        if self.bin_id == OTHER_BIN:
            if self.predicate_ref or self.classifier:
                raise ValueError(
                    "the 'other' bin is the catch-all — it must declare neither a "
                    "predicate nor a classifier (it is hit when nothing else is)")
        else:
            if bool(self.predicate_ref) == bool(self.classifier):
                raise ValueError(
                    f"bin {self.bin_id}: declare exactly one of predicate_ref or "
                    "classifier")
        if self.waived and not self.reason.strip():
            raise ValueError(
                f"bin {self.bin_id}: waiving a bin requires a named reason "
                "(Hard Rule 61 — silent holes are forbidden)")
        if self.waived and self.illegal:
            raise ValueError(
                f"bin {self.bin_id}: a bin cannot be both illegal and waived — "
                "an illegal bin must never be hit, a waived one merely need not be")
        return self

    @property
    def provisional(self) -> bool:
        return self.classifier is not None and self.classifier.provisional


class Coverpoint(BaseModel):
    """One dimension of the space, with exhaustive bins."""

    coverpoint_id: str
    description: str = ""
    kind: CoverpointKind = "deterministic"
    bins: list[Bin]
    #: counts toward the headline closure figure. Derived, never set, when the
    #: coverpoint is not measurable — see below.
    required: bool = True
    #: False when this coverpoint cannot be read off an arbitrary sample. It is
    #: then reported as NOT MEASURED — neither scored 0% (which reads as "the
    #: suite never got there", a finding a generator could be told to fix) nor
    #: credited (which is the over-report this field exists to stop). An unhit
    #: bin is a finding; an unmeasurable coverpoint is a confession, and the two
    #: must not be rendered the same way.
    measurable: bool = True
    not_measurable_reason: str = ""
    #: A registered measurability gate (``coverage.extractors.MEASURABILITY``)
    #: deciding, PER SAMPLE, whether that run carries the instrumentation these
    #: bins read. Set it and ``measurable`` becomes the floor rather than the
    #: verdict: the coverpoint is not measurable for a sample that fails the
    #: gate, measurable for one that passes, and ``collect`` computes closure
    #: over the passing subset while reporting how many samples it could not
    #: measure. A coverpoint gated for a batch in which NO sample passes reads
    #: exactly as a flatly not-measurable one — same ``None`` closure, same
    #: waived bins, same named reason.
    #:
    #: Why this exists: ``measurable`` answers once for a whole model, and this
    #: build now has producers that instrument a dimension and producers that do
    #: not (``scenario/session.py`` emits ``user_turn``; the stored-suite path
    #: emits none). One flag has to be wrong about one of them — either a
    #: customer's ordinary run reports 0% closure on a dimension their harness
    #: cannot emit, or an instrumented session is credited from a predicate that
    #: never saw evidence. Measurability is a property of a RUN, so it is
    #: decided per run.
    measurable_when: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Coverpoint":
        if self.measurable_when is not None:
            if not self.measurable_when.strip():
                raise ValueError(
                    f"coverpoint {self.coverpoint_id}: measurable_when must name "
                    "a registered measurability gate, not an empty string")
            if self.measurable:
                # A gate exists precisely because an arbitrary sample cannot be
                # read. Allowing `measurable=True` alongside one would put the
                # coverpoint in the headline unconditionally and then quietly
                # narrow its denominator — a closure figure over a subset,
                # presented as one over the batch. The floor stays False and
                # `collect` raises it per batch, so the honest default is the one
                # that survives a caller who never looks at the gate.
                raise ValueError(
                    f"coverpoint {self.coverpoint_id}: a coverpoint whose "
                    "measurability is decided per sample is NOT measurable for "
                    "an arbitrary sample — declare measurable=False with the "
                    "reason a run that fails the gate cannot be read, and let "
                    "collect() raise it for the batch that passes")
        if not self.measurable:
            if not self.not_measurable_reason.strip():
                raise ValueError(
                    f"coverpoint {self.coverpoint_id}: declaring a coverpoint "
                    "not measurable requires a named reason — the same rule as "
                    "a waived bin (Hard Rule 61)")
            # `required` and `measurable` are one decision, not two: a dimension
            # no producer can feed cannot be required to close, and letting the
            # two drift is how a not-measurable coverpoint ends up either
            # dragging a headline it has no business being in or, worse, holding
            # a bin its predicate returns True for by default.
            #
            # A GATED coverpoint gets the same treatment here and for the same
            # reason — it is not measurable for an arbitrary sample — and
            # ``collect`` re-derives BOTH together for the batch it actually
            # measured, never one without the other. That keeps the identity
            # intact instead of turning the gate into an escape from it: a
            # coverpoint measurable for a batch is required for that batch, so
            # the mechanism cannot become a dimension that reports a number
            # nothing depends on.
            self.required = False
        elif self.not_measurable_reason.strip():
            raise ValueError(
                f"coverpoint {self.coverpoint_id}: a measurable coverpoint may "
                "not carry a not_measurable_reason — say which it is")
        ids = [b.bin_id for b in self.bins]
        if len(ids) != len(set(ids)):
            raise ValueError(f"coverpoint {self.coverpoint_id}: duplicate bin ids")
        if OTHER_BIN not in ids:
            raise ValueError(
                f"coverpoint {self.coverpoint_id}: bins must be exhaustive — an "
                f"explicit '{OTHER_BIN}' bin is mandatory so an unmodelled "
                "situation is visible instead of silently uncounted")
        if len(ids) < 2:
            raise ValueError(
                f"coverpoint {self.coverpoint_id}: needs at least one real bin "
                "besides 'other'")
        if self.kind == "deterministic":
            classy = [b.bin_id for b in self.bins if b.classifier is not None]
            if classy:
                raise ValueError(
                    f"coverpoint {self.coverpoint_id} is deterministic but bins "
                    f"{classy} are classifier-backed — deterministic coverpoints "
                    "are extracted from spans by construction (anti-pattern §7.5)")
        if (self.coverpoint_id in DETERMINISTIC_BY_CONSTRUCTION
                and self.kind != "deterministic"):
            raise ValueError(
                f"coverpoint {self.coverpoint_id} is deterministic by "
                "construction and may not be declared classifier-backed")
        return self

    @property
    def provisional(self) -> bool:
        """True if ANY bin is classifier-backed and not yet calibrated — the
        whole coverpoint's numbers render PROVISIONAL."""
        return any(b.provisional for b in self.bins)

    def bin(self, bin_id: str) -> Bin | None:
        return next((b for b in self.bins if b.bin_id == bin_id), None)

    def countable_bins(self) -> list[Bin]:
        """Bins that count toward closure: not illegal, not waived, not `other`.
        `other` is measured but is a finding, never a coverage target."""
        return [b for b in self.bins
                if not b.illegal and not b.waived and b.bin_id != OTHER_BIN]


class Cross(BaseModel):
    """A combination of coverpoints. Crosses are where the value lives: testing
    'angry customers' and 'refunds' separately proves nothing about angry
    customers demanding out-of-policy refunds during a tool outage."""

    cross_id: str
    coverpoints: list[str]
    #: combinations that must never be generated or counted, as {cp_id: bin_id}
    illegal_combinations: list[dict[str, str]] = Field(default_factory=list)
    #: "all" = the full legal product; or an explicit list of target combinations
    target: object = "all"

    @model_validator(mode="after")
    def _validate(self) -> "Cross":
        if len(self.coverpoints) < 2:
            raise ValueError(f"cross {self.cross_id}: needs at least two coverpoints")
        if len(set(self.coverpoints)) != len(self.coverpoints):
            raise ValueError(f"cross {self.cross_id}: duplicate coverpoints")
        if self.target != "all" and not isinstance(self.target, list):
            raise ValueError(
                f"cross {self.cross_id}: target must be 'all' or a list of "
                "combinations")
        return self


class CoverageModel(BaseModel):
    """A versioned, archetype-scoped declaration of what 'tested' means."""

    model_id: str
    version: int = 1
    archetype_id: str = ""
    coverpoints: list[Coverpoint]
    crosses: list[Cross] = Field(default_factory=list)
    #: fallback only — the shipped models resolve this from config (Hard Rule 7,
    #: config.yaml `coverage.closure_target`). See coverage/targets.py.
    closure_target: float = DEFAULT_CLOSURE_TARGET

    @model_validator(mode="after")
    def _validate(self) -> "CoverageModel":
        ids = [c.coverpoint_id for c in self.coverpoints]
        if len(ids) != len(set(ids)):
            raise ValueError(f"model {self.model_id}: duplicate coverpoint ids")
        if not (0.0 < self.closure_target <= 1.0):
            raise ValueError(
                f"model {self.model_id}: closure_target must be in (0, 1]")
        known = set(ids)
        for x in self.crosses:
            unknown = [c for c in x.coverpoints if c not in known]
            if unknown:
                raise ValueError(
                    f"cross {x.cross_id}: unknown coverpoints {unknown}")
            self._cross_axes_are_measurable(x)
            for combo in x.illegal_combinations:
                bad = {k: v for k, v in combo.items()
                       if k not in known
                       or self.coverpoint(k).bin(v) is None}  # type: ignore[union-attr]
                if bad:
                    raise ValueError(
                        f"cross {x.cross_id}: illegal_combination references "
                        f"unknown coverpoint/bin {bad}")
        return self

    def _cross_axes_are_measurable(self, x: Cross) -> None:
        """Refuse a cross whose axes cannot produce an honest closure figure.

        A cross closure is averaged into the headline alongside the coverpoint
        closures (``CoverageReport.trace_closure``), so an axis that cannot be
        measured does not merely make one cross meaningless — it moves THE
        number. Two ways that happens, and the first is an over-report:

        * **A not-measurable axis.** ``session_shape`` is not measurable for a
          run that emits no ``user_turn`` span, which is every run the stored
          suite path produces, and every one of its bins is therefore out of the
          denominator. Cross it with ``agent_steps`` and the same bins come
          straight back as combinations: ``session_single_turn`` is True on every
          trace that can exist (0 turns is <= 1), so the pair
          ``single_turn × multi_step`` counts as HIT and the cross reports 25%
          closure — a quarter of a dimension credited from a predicate that has
          never seen evidence. The coverpoint stays honest at ``None`` while the
          cross launders it. Worse, the three unhit combinations become holes at
          the top rank, which is the target list the CDV solver aims at for the
          rest of the run.

          A GATED axis (``measurable_when``) is refused by the same test and it
          is not an oversight. Per-sample measurability fixes the coverpoint's
          own number by scoring it over the samples that carry the
          instrumentation; it does nothing for a cross, because ``collect``
          builds combinations from the raw per-sample bin hits of every axis at
          once, and an uninstrumented sample still hits ``single_turn`` there.
          The laundering route is open exactly as before until a cross learns to
          intersect the measurable subsets of all of its axes, so the refusal
          stands and this paragraph is the record that it was considered.
        * **An axis with no countable bins** (every real bin illegal or waived).
          The product over an empty axis is empty, so the cross reports 0.0 —
          a measurement of nothing, dragging the headline down as if a suite had
          failed to reach something no suite can reach.

        Both are refused at construction, not filtered at collection: ``collect``
        sees only bin ids and cannot tell a laundered combination from a real one,
        and this class is where the other rules that fail silently are enforced.
        The two honest resolutions are named in the message — drop the axis, or
        build the producer and flip ``measurable`` (an approved fingerprint diff).
        """
        blind = [(c, self.coverpoint(c).not_measurable_reason)      # type: ignore[union-attr]
                 for c in x.coverpoints
                 if not self.coverpoint(c).measurable]              # type: ignore[union-attr]
        if blind:
            detail = "; ".join(f"{c} ({why})" for c, why in blind)
            raise ValueError(
                f"cross {x.cross_id}: coverpoint(s) "
                f"{[c for c, _ in blind]} are declared not measurable, and a "
                f"cross combination naming one is credited from a predicate no "
                f"producer feeds — the closure this cross would report goes "
                f"straight into the headline the coverpoint was excluded from. "
                f"Drop the axis, or make it measurable first. Reason(s): {detail}")
        empty = [c for c in x.coverpoints
                 if not self.coverpoint(c).countable_bins()]        # type: ignore[union-attr]
        if empty:
            raise ValueError(
                f"cross {x.cross_id}: coverpoint(s) {empty} have no countable "
                "bins (every real bin is illegal or waived), so the cross has an "
                "empty target set and would report 0.0 closure — a number "
                "measured over nothing, averaged into the headline")

    def coverpoint(self, cp_id: str) -> Coverpoint | None:
        return next((c for c in self.coverpoints if c.coverpoint_id == cp_id), None)

    @property
    def provisional_coverpoints(self) -> list[str]:
        """Coverpoints whose numbers must render PROVISIONAL (uncalibrated
        classifier bins)."""
        return [c.coverpoint_id for c in self.coverpoints if c.provisional]

    def bins_fingerprint(self) -> str:
        """A hash over every bin definition. Bins are versioned artifacts:
        widening or deleting a bin to hit the closure target (anti-pattern §7.7)
        changes this fingerprint, so it is a diff a human approves rather than a
        silent edit.

        ``measurable`` is in the payload for the same reason: flipping a
        coverpoint back into the headline once a producer for it exists is
        exactly as consequential as adding a bin, and must be as visible.

        ``measurable_when`` is in the payload for that same reason again — a gate
        IS that flip, made conditional — but it is written only when a model
        declares one. A key present on every coverpoint would rewrite every
        fingerprint in the registry on the day the field was added, invalidating
        stored scorecards to record a change none of those models made. So a
        model that declares no gate hashes byte-identically to the way it hashed
        before the field existed, and declaring a gate is a diff a human approves
        — the same bargain every other entry here strikes.

        Splitting `session_shape` into `agent_steps` + `session_shape` changes
        this fingerprint. That is intended and correct — closure figures either
        side of that split are measuring different things and were never
        comparable.
        """
        payload = [
            {"cp": c.coverpoint_id, "kind": c.kind, "measurable": c.measurable,
             **({"measurable_when": c.measurable_when}
                if c.measurable_when else {}),
             "bins": sorted(
                 [{"id": b.bin_id, "pred": b.predicate_ref,
                   "classifier": bool(b.classifier), "illegal": b.illegal,
                   "waived": b.waived} for b in c.bins],
                 key=lambda d: d["id"])}
            for c in sorted(self.coverpoints, key=lambda c: c.coverpoint_id)
        ]
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def ref(self) -> str:
        return f"coverage:{self.model_id}@v{self.version}"

    def validate_against_registry(self) -> None:
        """Fail loudly if a bin names a predicate that is not registered — never
        defer this to collection time (mirrors validate_rubric_checks).

        Gates are checked against the gate registry, not the predicate one. The
        two dicts hold callables of identical shape, so a model naming a bin
        predicate as its gate would run and return plausible booleans forever;
        checking each name against the registry its FIELD means is what makes
        that a startup error instead of a coverpoint whose measurability is
        decided by, say, whether the agent refused.
        """
        from agenttic.coverage.extractors import MEASURABILITY, PREDICATES
        missing = sorted({
            b.predicate_ref for c in self.coverpoints for b in c.bins
            if b.predicate_ref and b.predicate_ref not in PREDICATES})
        if missing:
            raise ValueError(
                f"coverage model {self.model_id} v{self.version} references "
                f"unregistered predicate(s): {missing}")
        no_gate = sorted({c.measurable_when for c in self.coverpoints
                          if c.measurable_when
                          and c.measurable_when not in MEASURABILITY})
        if no_gate:
            raise ValueError(
                f"coverage model {self.model_id} v{self.version} references "
                f"unregistered measurability gate(s): {no_gate} — a gate must "
                "be registered with @measurability, not @predicate")
