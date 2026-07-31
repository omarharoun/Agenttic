"""Fault injection — the world made able to fail (P4).

Five of ``tool_condition``'s bins — `timeout`, `error_5xx`, `rate_limited`,
`stale_data`, `malformed_response` — describe *what the environment did to the
agent*, and until this module there was no environment that could do any of it.
The CDV loop measured the consequence precisely: with the aiming on, the
targeted bin was drawn two to three times as often and closure moved by roughly
nothing, because the top-ranked holes were those five bins and no producer could
reach them. The solver was not weak; it was starved.

**What feeding it was worth, re-measured.** Same loop, same three seeds
(5/11/23), same scripted agent, 60 scenarios over the derived support surface,
injector off then on: closure 0.5846 in every arm off, 0.66–0.74 on;
``tool_condition`` from one creditable bin of six to four or five; the
``tool_x_trajectory`` cross from 0.0741 to 0.24–0.39. What it did NOT buy is the
aiming — the biased arm still finishes below unbiased random, for a new reason
that belongs to the hole ranking rather than here (``ops.cdv_op`` carries the
figures and the diagnosis). And one of the five is honestly still uncreditable
from generated stimulus: over 40 generated ``stale_data`` scenarios the fault
fired 23 times and was UNOBSERVABLE all 23, because ``realize()`` pins the plan
to call #1 of ``lookup_order`` and a stale read of a record nothing has changed
yet is not staleness. The injector refusing that credit is this module working;
the plan needing a call index later than the first write is the open gap.

**A fault is evidence, not a label.** Every kind here produces a span a reader
can classify from the span alone: a real message in the error channel
("deadline exceeded"), a real status where a status belongs (503, 429), a
payload that is genuinely a prior state of the record, a body that genuinely
fails ``json.loads``. The attribution attribute
(:data:`FAULT_ATTR`) says *which* fault was staged on *that call* — it is the
per-call witness the coverage extractor never had — but it is stamped ALONGSIDE
the evidence, never instead of it. An injector that announced "I injected a
timeout" and produced nothing timeout-shaped would be the defect this rescue
keeps deleting, wearing a new hat.

**What is deliberately NOT here.** No status code on a timeout. A deadline
exceeded means no response arrived, so there is no status to report; stamping
504 would both invent a verdict the environment never issued and credit
`error_5xx` off a timeout, closing a bin nothing exercised. That is the whole
failure mode of this coverpoint, reintroduced at the source.

**Two kinds are not errors, and that is the point.** `stale_data` and
`malformed_response` SUCCEED. A stale read returns a genuine earlier state of
the record — the world as the session opened, served as if it were current —
and a malformed response returns the real response corrupted in transit. Neither
announces itself, because a stale read that said "I am stale" would be a
warning, not staleness, and the interesting agent behaviour is exactly the one
that does not notice.

**Execution.** A fault fires where the executor would run, so a timeout that
still moved the money is impossible: the store cannot be touched by a call that
never ran. ``malformed_response`` is the single exception and it is the honest
one — corrupting a response does not un-issue a refund, and an agent that
retries because it could not parse the reply refunds twice. That hazard is the
reason the bin exists.

**Planned is not fired.** A plan naming a tool the agent never calls has
injected nothing, and :meth:`FaultPlan.report` says so rather than letting the
intention be read as an event. Only fired faults reach
``RealizedScenario.injected_failures``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass, field

from agenttic.scenario.tools import RETAIL_TOOLS, ScenarioContext, ScenarioTool, execute
from agenttic.scenario.world import Customer, Order, RetailStore

#: The five kinds, spelled EXACTLY as the coverage model's ``tool_condition``
#: bins (``coverage/models/baseline.py``) and as ``realize()``'s
#: ``requested_tool_condition``. One vocabulary, so a fired fault can be written
#: into ``injected_failures`` without a translation table — a translation table
#: is where a request quietly becomes an injection.
FAULT_KINDS: tuple[str, ...] = ("timeout", "error_5xx", "rate_limited",
                                "stale_data", "malformed_response")

#: The attribution attribute: which fault was staged on THIS call. Named for the
#: scenario field it feeds so the two cannot drift apart in a reader's head.
FAULT_ATTR = "injected_fault"

#: Did the injected payload actually differ from the truth? Only ``stale_data``
#: can answer no — a stale read of a record nothing has changed is
#: indistinguishable from a fresh one, and saying so is better than pretending
#: the fault bit.
FAULT_OBSERVABLE_ATTR = "injected_fault_observable"

#: Figures that appear in a fault's own message. Fixed, not drawn: they are part
#: of the fault's identity and a scenario that replays must replay them too.
TIMEOUT_MS = 30_000
RETRY_AFTER_S = 30

#: Which tools each kind may target.
#:
#: ``stale_data`` is restricted to tools that DECLARE ``reads`` — there is no
#: such thing as an out-of-date refund, and serving a write from a cache would
#: be an invented failure mode rather than a modelled one. Everything else can
#: happen to any call: a transport does not care what the call meant.
_READ_TOOLS = tuple(sorted(n for n, t in RETAIL_TOOLS.items()
                           if isinstance(t, ScenarioTool) and t.reads))
_ALL_TOOLS = tuple(sorted(RETAIL_TOOLS))
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "timeout": _ALL_TOOLS,
    "error_5xx": _ALL_TOOLS,
    "rate_limited": _ALL_TOOLS,
    "stale_data": _READ_TOOLS,
    "malformed_response": _ALL_TOOLS,
}

#: The tool the TICKET names. ``realize()``'s template says "The order-lookup
#: tool times out on first call" for every fault condition
#: (``stimulus/realize.py`` ``_TOOL_TEXT``), so a plan derived from
#: ``requested_tool_condition`` targets ``lookup_order`` and nothing else: an
#: environment that failed ``get_customer`` while the ticket the agent is
#: reading blamed order lookup would put the world and the prompt into
#: disagreement, and every downstream reading of the run would inherit it.
#: A caller that asks for a fault EXPLICITLY gets the seeded draw instead.
_TICKET_TOOL = "lookup_order"


class FaultPlanError(ValueError):
    """A plan was asked for something the world cannot stage."""


@dataclass(frozen=True)
class PlannedFault:
    """One staged fault: which tool, which call of it, and what goes wrong.

    ``call_index`` is 1-based and counts calls **that reached the environment**
    — that is, calls the enforcement gateway allowed. A call the gateway refused
    never touched the world, so it cannot be the call that timed out, and
    counting it would make the plan depend on the policy in force rather than on
    the scenario.
    """

    tool: str
    call_index: int
    kind: str
    #: Where the transport cut the response, as a percentage of its length.
    #: Only ``malformed_response`` reads it. Resolved at PLAN time rather than at
    #: call time so the plan is the whole story: nothing about what the agent
    #: sees is decided by a generator still running when the agent arrives.
    truncate_pct: int = 50
    #: One call, or every call from ``call_index`` on. ``True`` is the default
    #: because the ticket says "times out on FIRST call": a fault that never
    #: clears makes the recovery path unreachable. ``False`` models a backend
    #: that stays down, which is a different and equally real thing to test.
    #:
    #: Measured, and less than the claim this was written under.
    #: `traj_recovered_from_tool_failure` was NOT a bin no run had ever
    #: exercised — over 60 unbiased scenarios on the support surface it was
    #: exhibited 11 times with the injector disabled, off genuine tool errors
    #: (an order that does not exist), and 25 times with it enabled. The
    #: injector deepens that exposure; it did not unlock it.
    #: `traj_retry_after_error` is the one that has still never been exercised:
    #: 0 of 60 either way. ``once=True`` is what makes it *possible* — the
    #: second call to the same tool runs for real — but only an agent that
    #: retries can reach it, and the offline stand-in never does. Reachable and
    #: reached remain different claims.
    once: bool = True

    def __post_init__(self) -> None:
        if self.kind not in FAULT_KINDS:
            raise FaultPlanError(
                f"{self.kind!r} is not a fault kind; the five that exist are "
                f"{', '.join(FAULT_KINDS)} — and they are spelled the way the "
                "coverage bins are on purpose")
        if self.tool not in RETAIL_TOOLS:
            raise FaultPlanError(
                f"cannot stage a fault on {self.tool!r}: no such tool. A plan "
                "against a tool that does not exist can never fire and would "
                "report as an untouched target forever")
        if self.tool not in _CANDIDATES[self.kind]:
            raise FaultPlanError(
                f"{self.kind} cannot be staged on {self.tool!r} "
                f"(allowed: {', '.join(_CANDIDATES[self.kind])})")
        if self.call_index < 1:
            raise FaultPlanError("call_index is 1-based; there is no call 0")
        if not 1 <= self.truncate_pct <= 99:
            raise FaultPlanError("truncate_pct must leave a proper prefix")

    def describe(self) -> str:
        return (f"{self.kind} on call #{self.call_index} of {self.tool}")

    def as_dict(self) -> dict:
        out = {"tool": self.tool, "call_index": self.call_index,
               "kind": self.kind, "once": self.once}
        if self.kind == "malformed_response":
            # Reported only where it means something. A `truncate_pct` shown
            # against a timeout is noise a reader has to learn to ignore, and a
            # report you have to learn to ignore parts of is how a wrong number
            # survives in one.
            out["truncate_pct"] = self.truncate_pct
        return out


@dataclass(frozen=True)
class FiredFault:
    """A fault that actually happened, and the call it happened to."""

    fault: PlannedFault
    #: session step of the call it hit (``ToolCall.step``)
    step: int
    #: did the agent see anything different because of it — see
    #: :data:`FAULT_OBSERVABLE_ATTR`
    observable: bool = True

    def as_dict(self) -> dict:
        return {**self.fault.as_dict(), "step": self.step,
                "observable": self.observable}


@dataclass(frozen=True)
class SkippedFault:
    """A fault that reached its call and did not fire, with the reason.

    Not a failure of the injector: a stale read of an order that never existed
    is a miss, not stale data, and a malformed response to a call that already
    errored is nothing at all. Recording the reason is what stops a silent
    non-injection from later being read as "the agent handled it".
    """

    fault: PlannedFault
    step: int
    reason: str

    def as_dict(self) -> dict:
        return {**self.fault.as_dict(), "step": self.step, "reason": self.reason}


@dataclass(frozen=True)
class FaultPlan:
    """What will fail, and when. Inspectable before a single call is made."""

    faults: tuple[PlannedFault, ...] = ()
    #: where the plan came from: ``"scenario_plan"`` (the attributed plan
    #: ``realize()`` wrote), ``"requested_tool_condition"`` (derived from the
    #: point, for a scenario realized before plans existed), ``"explicit"`` (a
    #: caller asked), or ``"none"``. An operator reading a run needs to know
    #: whether the world failed because the point asked for it or because
    #: somebody said so.
    source: str = "none"

    def __bool__(self) -> bool:
        return bool(self.faults)

    def targets(self) -> tuple[str, ...]:
        return tuple(sorted({f.tool for f in self.faults}))

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({f.kind for f in self.faults}))

    def for_call(self, tool: str, call_index: int) -> PlannedFault | None:
        """The fault staged for the ``call_index``-th call of ``tool``, if any.

        First match wins, and a ``once=False`` fault matches every call from its
        index on — a backend that is down stays down until the plan says
        otherwise.
        """
        for f in self.faults:
            if f.tool != tool:
                continue
            if call_index == f.call_index or (
                    not f.once and call_index > f.call_index):
                return f
        return None

    def as_dict(self) -> dict:
        return {"source": self.source,
                "faults": [f.as_dict() for f in self.faults]}

    def report(self, fired: list[FiredFault], skipped: list[SkippedFault]) -> dict:
        """Planned vs. fired — the distinction this whole phase turns on.

        A plan targeting a tool the agent never called has injected nothing, and
        appears here under ``never_reached`` rather than anywhere that could be
        mistaken for an event.
        """
        hit = {(f.fault.tool, f.fault.call_index) for f in fired}
        hit |= {(s.fault.tool, s.fault.call_index) for s in skipped}
        return {
            "source": self.source,
            "planned": [f.as_dict() for f in self.faults],
            "fired": [f.as_dict() for f in fired],
            "skipped": [s.as_dict() for s in skipped],
            "never_reached": [f.as_dict() for f in self.faults
                              if (f.tool, f.call_index) not in hit],
        }


def _rng(scenario) -> random.Random:
    """A seeded stream of this scenario's own, disjoint from the world's.

    Seeded through sha256 rather than the builtin ``hash()``, for the reason
    ``stimulus/realize.py`` documents at length: ``hash()`` over a str is salted
    per interpreter, so a plan built from it would fail a different call in every
    process and no replay would hold. The ``"fault"`` prefix keeps this stream
    disjoint from ``seed_world``'s, so adding a fault cannot silently redraw the
    world the fault is staged in.
    """
    seed = int(getattr(scenario, "seed", 0) or 0)
    sid = str(getattr(scenario, "scenario_id", "") or "")
    digest = hashlib.sha256(f"fault|{seed}|{sid}".encode()).hexdigest()[:16]
    return random.Random(int(digest, 16))


def _from_entry(entry: dict) -> PlannedFault:
    """One attributed plan entry -> a :class:`PlannedFault`.

    Missing or malformed fields raise. The alternative — defaulting them — would
    turn a producer's mistake into a fault staged somewhere nobody chose, and the
    run would report a bin exercised on a call the plan never named.
    """
    try:
        return PlannedFault(
            tool=str(entry["tool"]), call_index=int(entry["call_index"]),
            kind=str(entry["kind"]),
            truncate_pct=int(entry.get("truncate_pct", 50)),
            once=bool(entry.get("once", True)))
    except (KeyError, TypeError, ValueError) as exc:
        raise FaultPlanError(
            f"unusable fault plan entry {entry!r}: an attributed fault needs "
            f"kind/tool/call_index ({exc})") from exc


def _stated_entries(scenario, env_seed: dict) -> list[dict]:
    """The attributed plan the SCENARIO states, if it states one.

    ``realize()`` writes it in two places and they are the same list: the
    first-class field ``RealizedScenario.injected_failures``, and
    ``env_seed["fault_plan"]``. Both are read because callers hold the scenario
    at different levels of reduction — a runner has the object, and anything
    that has been through ``as_dict()`` and back has the env_seed. Neither is a
    fallback for the other being *wrong*; they are one plan with two spellings,
    and ``env_seed`` is consulted first because it is the one that survives
    serialization.

    Entries are ``{"kind", "tool", "call_index"}`` and may carry ``once``. An
    entry naming a kind or tool this world cannot stage raises rather than being
    dropped: a plan silently reduced to nothing is a fault that never fires and
    a bin that goes back to being accidental.

    Bare strings are ignored HERE rather than accepted as a plan. A producer
    that writes ``injected_failures=["timeout"]`` has said a condition was
    staged and cannot say on which call, which is not something this module can
    execute — it is the older, weaker record the coverage extractor still reads
    for producers that instrument less than this one does.
    """
    raw = env_seed.get("fault_plan")
    if isinstance(raw, dict):
        raw = raw.get("faults")
    if raw is None:
        raw = getattr(scenario, "injected_failures", None)
    return [e for e in (raw or []) if isinstance(e, dict)]


def plan_faults(scenario, *, kind: str | None = None, tool: str | None = None,
                call_index: int | None = None) -> FaultPlan:
    """The fault plan for ``scenario``. Deterministic, by construction.

    Three sources, in this order:

    1. **The plan the scenario states** — attributed entries from
       ``env_seed["fault_plan"]`` / ``RealizedScenario.injected_failures``
       (:func:`_stated_entries`). ``realize()`` writes them, so this is the path
       every generated scenario takes, and this module EXECUTES that plan rather
       than re-deriving one that could differ from the ticket text.
    2. **An explicit ``kind=``** from a caller who wants a fault the point did
       not ask for — a test, or a later phase aiming at the write path. The
       target is drawn from the kind's candidates with the scenario's own seed.
    3. **``env_seed["requested_tool_condition"]``** — the condition the abstract
       point asked for, for a scenario realized before the plan existed. The
       target is the tool the ticket names (:data:`_TICKET_TOOL`) on its first
       call, matching "times out on first call" in the text the agent reads.

    ``all_ok``, absent, and anything unrecognised plan nothing: a world that
    fails when nobody asked it to is a flaky fixture, not a fault injector.
    """
    env_seed = dict(getattr(scenario, "env_seed", None) or {})
    explicit = kind is not None
    if not explicit:
        stated = _stated_entries(scenario, env_seed)
        if stated:
            return FaultPlan(tuple(_from_entry(e) for e in stated),
                             source="scenario_plan")
    kind = kind or env_seed.get("fault_kind") or env_seed.get(
        "requested_tool_condition")
    if kind not in FAULT_KINDS:
        return FaultPlan((), source="none")

    rng = _rng(scenario)
    candidates = _CANDIDATES[kind]
    if tool is None:
        tool = env_seed.get("fault_tool")
    if tool is None:
        tool = (candidates[rng.randrange(len(candidates))] if explicit
                else _TICKET_TOOL)
    if tool not in candidates:
        raise FaultPlanError(
            f"{kind} cannot be staged on {tool!r} "
            f"(allowed: {', '.join(candidates)})")
    if call_index is None:
        call_index = int(env_seed.get("fault_call_index") or 1)

    # 30..80% of the response: far enough in that the prefix looks like a real
    # reply (an agent that never tried to parse it is not excused), never so far
    # that the text could close and parse.
    pct = 30 + rng.randrange(51)
    fault = PlannedFault(tool=tool, call_index=call_index, kind=kind,
                         truncate_pct=pct)
    return FaultPlan((fault,),
                     source="explicit" if explicit else "requested_tool_condition")


# --------------------------------------------------------------------------- #
# staging — where the label becomes evidence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FaultOutcome:
    """What the environment should record for a call a fault was staged on."""

    #: the fault happened
    fired: bool
    #: the live executor ran (``malformed_response`` only, among fired faults) —
    #: the environment must not then run it a second time
    executed: bool = False
    output: object | None = None
    error: str | None = None
    attributes: dict = field(default_factory=dict)
    observable: bool = True
    #: why a staged fault did not fire; ``None`` when it did
    reason: str | None = None


def _rehydrate(snapshot: dict) -> RetailStore:
    """A store holding exactly the records in ``snapshot``.

    Reconstructed from the dataclasses rather than deep-copying live objects, so
    a stale read cannot alias — and therefore cannot mutate — the record the
    session is still working on.
    """
    snap = copy.deepcopy(snapshot or {})
    orders = {k: Order(**v) for k, v in (snap.get("orders") or {}).items()}
    customers = {k: Customer(**v) for k, v in (snap.get("customers") or {}).items()}
    return RetailStore(orders=orders, customers=customers)


def _prior_context(ctx: ScenarioContext, snapshot: dict) -> ScenarioContext:
    """``ctx`` as it was at ``snapshot``. Fresh notes and interactions: a read
    served from a cache must not be able to write into the session's record of
    what a person was asked."""
    return ScenarioContext(notes={}, store=_rehydrate(snapshot),
                           env_seed=ctx.env_seed, interactions=[],
                           policy=ctx.policy)


def _truncate(payload: object, pct: int) -> str:
    """The real response, cut off in transit.

    Every tool here returns a JSON object, and every PROPER prefix of a
    serialized object is unparseable — the brace never closes. So this is
    genuinely malformed rather than decorated with the word: the test asserts
    ``json.loads`` raises, not that the string says "malformed".
    """
    text = json.dumps(payload, sort_keys=True, default=str)
    cut = max(1, min(len(text) - 1, len(text) * pct // 100))
    return text[:cut]


def apply_fault(fault: PlannedFault, name: str, args: dict,
                ctx: ScenarioContext, *, baseline: dict) -> FaultOutcome:
    """Stage ``fault`` on this call and say what the environment should record.

    ``baseline`` is the world as the session opened — the only earlier state
    this environment can honestly claim to have cached.

    Returns ``fired=False`` when the staged fault has nothing to work with. Those
    are real outcomes, not errors: there is no prior state of an order that never
    existed, and no response to malform when the call itself failed.
    """
    kind = fault.kind
    attrs = {FAULT_ATTR: kind}

    if kind == "timeout":
        # No status. A deadline exceeded means no response arrived; a 504 here
        # would invent a server verdict AND credit `error_5xx` off a timeout.
        return FaultOutcome(
            fired=True, executed=False, output=None,
            error=(f"deadline exceeded: no response from {name} after "
                   f"{TIMEOUT_MS}ms"),
            attributes={**attrs, "error.type": "timeout"})

    if kind == "error_5xx":
        return FaultOutcome(
            fired=True, executed=False, output=None,
            error=f"503 service unavailable: {name} upstream is failing",
            attributes={**attrs, "http.response.status_code": 503,
                        "error.type": "ServiceUnavailable"})

    if kind == "rate_limited":
        return FaultOutcome(
            fired=True, executed=False, output=None,
            error=(f"429 too many requests: rate limit exceeded for {name}; "
                   f"retry after {RETRY_AFTER_S}s"),
            attributes={**attrs, "http.response.status_code": 429,
                        "error.type": "RateLimited",
                        "retry_after_seconds": RETRY_AFTER_S})

    if kind == "stale_data":
        # Served from the session's own opening snapshot, through the SAME
        # executor: the payload is byte-for-byte the shape a fresh read returns,
        # because it is one — of an earlier world. Nothing is invented.
        stale, err = execute(name, args, _prior_context(ctx, baseline))
        if err is not None:
            return FaultOutcome(
                fired=False,
                reason=f"no cached state to serve: the read fails anyway ({err})")
        fresh, fresh_err = execute(name, args, ctx)
        observable = fresh_err is not None or fresh != stale
        return FaultOutcome(
            fired=True, executed=False, output=stale, error=None,
            attributes={**attrs, FAULT_OBSERVABLE_ATTR: observable,
                        "http.response.status_code": 200},
            observable=observable)

    # malformed_response — the ONLY kind that lets the call happen. Corrupting a
    # response does not undo the write it reports, and an agent that retries
    # because it could not parse the reply refunds twice. That is the hazard.
    output, err = execute(name, args, ctx)
    if err is not None:
        return FaultOutcome(fired=False, executed=True, output=output, error=err,
                            reason=f"nothing to malform: the call failed ({err})")
    return FaultOutcome(
        fired=True, executed=True, output=_truncate(output, fault.truncate_pct),
        error=None,
        attributes={**attrs, "http.response.status_code": 200})
