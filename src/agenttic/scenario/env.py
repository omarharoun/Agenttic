"""The scenario environment — a gateway-guarded, stateful tool session (P1).

One class, :class:`ScenarioEnvironment`, and one method that matters:
:meth:`~ScenarioEnvironment.call`. It is the whole enforcement contract, in this
order, and the order is the point:

1. **Default-deny an unknown tool name.** It is never executed
   (``assistant/tools.py:239-241``).
2. **Ask the gateway, before the executor is reached.** A gateway consulted after
   the write has already landed is telemetry, not enforcement. If
   ``evaluate_tool_call`` itself raises, fail **closed**: nothing runs.
3. **Anything that is not ``allow`` does not execute.** ``deny``,
   ``require_approval``, ``transform``, ``terminate_session``, ``revoke_access``
   (``schema/enforcement.py:26-29``) all return ``BLOCKED_BY_HARNESS[...]``, the
   same error string the honeypot harness returns at ``redteam/honeypot.py:298``.
4. **Then the fault plan** (P4, ``scenario/faults.py``), if this scenario stages
   one for this call.
5. **Then run the executor**, which never raises.

**Why the fault fires AFTER the gateway, and not before** — the one design
decision in P4 that changes what a run means. Two reasons, both about not losing
a fact the run needed to record:

* A fault that pre-empted the gateway would ERASE an enforcement decision. The
  gateway's verdict on every call the agent attempted is the enforcement record;
  if an injected timeout could stop a ``deny`` from ever being evaluated, an
  agent that reached for a forbidden refund would read as merely unlucky, and
  which calls got hidden would depend on the seed.
* A denied call has ALREADY not executed. Injecting a timeout on top would
  report two different reasons for one non-execution, and a reader has no way to
  decide which one to believe.

So the gateway rules first and its verdict is always recorded; the fault is
consulted only on the ``allow`` path, where it stands in for the execution that
would otherwise have happened. The corollary is that ``PlannedFault.call_index``
counts calls the gateway ALLOWED — a call that never reached the world cannot be
the call that timed out.

``attributes["enforcement"]`` therefore has three values, not two:
``executed``, ``blocked``, and ``faulted`` — the call was allowed and the
environment failed it. ``faulted`` is neither of the others on purpose:
:attr:`ToolCall.executed` must stay false (nothing ran, the store did not move)
and :attr:`ToolCall.blocked` must stay false (the harness permitted this call;
crediting enforcement for a transport failure would overstate the harness).
``malformed_response`` is the exception and reads ``executed``, because it is:
the write landed and only the reply was corrupted.

Reads go through it too. An enforcement layer that only sees the dangerous calls
has already decided which calls are dangerous, using the classifier this phase
exists to stop relying on.

:meth:`ToolCall.as_span` is the ONE place that decides the span shape for a world
tool call — the declared risk class, the entity ids, and the enforcement stamp,
in the vocabulary ``redteam/honeypot.py:318-322`` already uses. A later adapter
phase reuses it rather than re-deriving it, which is how the two stay honest
about the same call.

Timestamps come from a fixed epoch plus a step counter, not from the wall clock.
A span here carries an ORDER, not a duration: nothing in this world measures
latency and it will not invent a figure for one.

**Known consequence, stated rather than hidden.** A span carries its tool's
declared risk class whether or not the call executed, because the declaration
describes the call that was *attempted* — so a blocked ``issue_refund`` still
reads as irreversible to ``verification/builtins.py`` and still trips
``mutating_irreversible``. That is defensible (the suite did exercise the
dangerous path) but it is a judgment the predicates make, not one the world
should quietly pre-empt by withholding what it knows. Both the ``enforcement``
stamp and ``Span.error`` are on the span, so a predicate that wants the other
reading has the evidence to take it. Changing the predicates is out of scope
here.

Re-exports :func:`~agenttic.scenario.world.seed_world` and the tool registry so
everything the P1 spec names lives at ``agenttic.scenario.env``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from agenttic.registry.sqlite_store import DuplicateVersionError
from agenttic.enforce.gateway import (
    EnforcementGateway, Session, compute_policy_hash)
from agenttic.schema.enforcement import Decision, EnforcementPolicy, Rule
from agenttic.schema.trace import Span
from agenttic.scenario.faults import (  # noqa: F401 — re-exported
    FAULT_ATTR, FaultPlan, FiredFault, PlannedFault, SkippedFault, apply_fault,
    plan_faults,
)
from agenttic.scenario.tools import (  # noqa: F401 — re-exported (see docstring)
    RETAIL_POLICY, RETAIL_TOOLS, RetailPolicy, ScenarioContext, ScenarioTool,
    execute, span_attributes, tool_schemas,
)
from agenttic.scenario.world import (  # noqa: F401 — re-exported
    Customer, Order, RetailStore, seed_world,
)

#: The world's zero. Every span is placed relative to it, so a scripted session
#: is reproducible down to its span timestamps.
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Action classes for this fixture's eight tools, so ``action_class_of``
#: (``enforce/lanes.py:35-41``) resolves ``write``/``read`` instead of
#: ``unknown`` and the per-class fail policy (``enforce/gateway.py:329-339``)
#: applies correctly.
#:
#: Supplied in CODE, never in ``config.yaml``: a fixture tool set's action
#: classes are not a model name, a threshold or a sample rate, and production
#: config must not grow entries for tools production does not have.
#:
#: ``escalate_to_human`` and ``confirm_with_customer`` are in neither list on
#: purpose — they are neither, and the honest resolution is ``unknown``, whose
#: fail policy is closed.
SCENARIO_ENFORCEMENT_CFG: dict = {
    "enforcement": {
        "action_classes": {
            "write": ["issue_refund", "cancel_order", "exchange_item",
                      "update_address"],
            "read": ["lookup_order", "get_customer"],
        },
    },
    "canaries": {"severity_on_trip": "S2"},
}


def install_scenario_enforcement(reg, agent_id: str, *, cfg: dict | None = None,
                                 rules=()) -> tuple[EnforcementGateway, Session]:
    """Stand up a real gateway session over this world. Returns ``(gateway,
    session)``.

    Mirrors ``redteam/honeypot.py:378-391``: save a hash-verified
    :class:`EnforcementPolicy` (carrying ``rules``), construct the gateway, start
    the session. The policy is passed to ``start_session`` explicitly rather than
    resolved from the registry, so a second agent's policy can never be the one
    that ends up serving.

    **Idempotent per (agent, ruleset), by construction.** ``save_policy`` is
    append-only and refuses a duplicate ``policy_id`` outright, so keying the id
    on the agent alone made the SECOND call for that agent raise — and every
    caller this exists for runs many scenarios against one agent: pass^k repeats
    the same suite, and the CDV loop realizes hundreds of scenarios per round.
    The ruleset digest in the id fixes both halves at once: the same rules resolve
    to the same stored policy and are reused, while a DIFFERENT ruleset gets its
    own id instead of silently colliding with, or overwriting, the first. A
    scenario that installs a deny rule can therefore never end up served by the
    permissive policy a previous scenario stored under the same name.
    """
    cfg = cfg or SCENARIO_ENFORCEMENT_CFG
    parsed = [r if isinstance(r, Rule) else Rule(**r) for r in (rules or ())]
    digest = hashlib.sha256(
        json.dumps([r.model_dump(mode="json") for r in parsed],
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
    pol = EnforcementPolicy(policy_id=f"scenario-{agent_id}-{digest}",
                            agent_id=agent_id, rules=parsed,
                            compiled_from=["scenario-world"])
    pol = pol.model_copy(update={"content_hash": compute_policy_hash(pol)})
    try:
        reg.save_policy(pol)
    except DuplicateVersionError:
        # Same agent, same rules, already installed. Serve the STORED policy
        # rather than the one just built: the gateway re-verifies the content
        # hash on every session (gateway.py:94) and would refuse a mismatch, so
        # reusing the stored object is the only reading that stays honest if the
        # two ever diverge.
        stored = reg.get_policy(pol.policy_id)
        if stored is not None:
            pol = stored
    gateway = EnforcementGateway(reg, cfg)
    session = gateway.start_session(agent_id, pol)
    return gateway, session


@dataclass(frozen=True)
class ToolCall:
    """One evaluated tool call: what was asked, what the gateway ruled, and what
    (if anything) happened."""

    name: str
    args: dict
    output: object | None
    error: str | None
    #: ``None`` only where no evaluation took place: an unknown tool name, which
    #: is refused before the gateway is reached, and an enforcement outage, which
    #: fails closed. Both are non-executions, so neither is an enforcement
    #: verdict to record.
    decision: Decision | None
    attributes: dict = field(default_factory=dict)
    start_time: datetime = EPOCH
    end_time: datetime = EPOCH

    @property
    def step(self) -> int:
        """This call's position in its session (1-based), read back off the
        deterministic clock rather than stored twice."""
        return int((self.start_time - EPOCH).total_seconds())

    @property
    def executed(self) -> bool:
        return self.attributes.get("enforcement") == "executed"

    @property
    def blocked(self) -> bool:
        return self.attributes.get("enforcement") == "blocked"

    @property
    def faulted(self) -> bool:
        """A fault was staged on this call and fired. True for the one kind that
        still executed (``malformed_response``) as well, because the agent was
        handed a corrupted reply either way — this asks what the environment did,
        where ``executed`` asks whether the world moved."""
        return self.attributes.get(FAULT_ATTR) is not None

    def as_span(self, *, span_id: str | None = None) -> Span:
        """The ONE place that decides the span shape for a world tool call.

        ``args`` are copied into ``Span.input`` so ``_entity_of``
        (``verification/builtins.py:77-83``) resolves ``order_id`` /
        ``customer_id`` and can match a write to the read that preceded it.

        The default ``span_id`` is the call's position in its session, not a
        uuid: a scripted session should replay to the same bytes, and a random id
        would be the one thing in this package that does not. Ids are therefore
        unique within a session — pass ``span_id`` explicitly if you are
        assembling one trace out of two.
        """
        out = self.output if isinstance(self.output, dict) else (
            {} if self.output is None else {"result": self.output})
        return Span(
            span_id=span_id or f"sp-{self.step:04d}",
            kind="tool_call", name=self.name,
            start_time=self.start_time, end_time=self.end_time,
            input=dict(self.args), output=out, error=self.error,
            attributes=dict(self.attributes))


class ScenarioEnvironment:
    """A stateful tool session over one :class:`RetailStore`.

    ``gateway`` and ``session_id`` are keyword-REQUIRED with no default. An
    optional gateway becomes an unused gateway, which is how ``signs_off`` became
    decorative.

    ``faults`` defaults to ``None``, meaning **derive the plan from the
    scenario** (:func:`~agenttic.scenario.faults.plan_faults`). That default is
    deliberate and is the difference between P4 and the hook it replaces: an
    injector nobody switches on is an unused field, which is the exact defect
    this rescue keeps finding. It changes nothing for a scenario that asked for
    ``all_ok`` — which is every scenario the existing suites realize — because a
    plan is only derived when the point requested a fault condition. Pass
    ``FaultPlan(())`` to state "no faults" explicitly.
    """

    def __init__(self, scenario, *, gateway: EnforcementGateway, session_id: str,
                 store: RetailStore | None = None,
                 faults: FaultPlan | None = None) -> None:
        # Resolve the session HERE: a mis-wired session fails loudly at
        # construction rather than silently mid-run, where the first symptom
        # would be a call that never reached enforcement.
        gateway.get_session(session_id)
        self.scenario = scenario
        self.gateway = gateway
        self.session_id = session_id
        self.store = store if store is not None else seed_world(scenario)
        # The scenario's own env_seed dict, by reference rather than by copy: it
        # is the ONLY source `confirm_with_customer` may answer from, and a stale
        # copy would let the environment answer from a world the scenario no
        # longer describes.
        self.ctx = ScenarioContext(
            notes={}, store=self.store,
            env_seed=getattr(scenario, "env_seed", None) or {})
        self.calls: list[ToolCall] = []
        self._baseline = self.store.snapshot()
        self._step = 0
        #: what will fail, and when — inspectable before the agent makes a move
        self.faults = plan_faults(scenario) if faults is None else faults
        self._fired: list[FiredFault] = []
        self._skipped: list[SkippedFault] = []
        #: calls per tool that the gateway ALLOWED — what `call_index` counts
        self._reached: dict[str, int] = {}

    # -- observation ---------------------------------------------------------

    def tool_schemas(self) -> list[dict]:
        return tool_schemas()

    def snapshot(self) -> dict:
        return self.store.snapshot()

    def state_diff(self) -> dict:
        """What this session changed, against the world as it was seeded. ``{}``
        before any successful write. The substrate a later phase compares against
        ``Expectation.goal_state_delta`` — P1 exposes it and computes no reward.
        """
        return self.store.diff(self._baseline)

    @property
    def interactions(self) -> list[dict]:
        """Escalations and confirmation requests — trajectory facts, deliberately
        NOT business records, so they never appear in :meth:`state_diff`."""
        return self.ctx.interactions

    @property
    def fired_faults(self) -> list[FiredFault]:
        """Faults that actually happened to this session. Never the plan."""
        return list(self._fired)

    @property
    def injected_failures(self) -> list[str]:
        """The kinds that FIRED, in the spelling ``RealizedScenario.
        injected_failures`` and the ``tool_condition`` bins share.

        This is the value that field may be filled from, and only this one. It
        is derived from events, so a plan aimed at a tool the agent never called
        contributes nothing — which is the whole point of the field's rescue:
        ``realize()`` used to write the REQUESTED condition here, and the
        coverage extractor reads the field as an authority on what HAPPENED.
        """
        return sorted({f.fault.kind for f in self._fired})

    def fault_report(self) -> dict:
        """Planned vs. fired vs. never reached, for a test or an operator."""
        return self.faults.report(self._fired, self._skipped)

    # -- the enforcement contract --------------------------------------------

    def call(self, name: str, args: dict | None = None) -> ToolCall:
        """Evaluate, then (only if allowed) execute. Never raises."""
        args = dict(args or {})
        start = self._tick()

        if name not in RETAIL_TOOLS:
            # Default-deny (step 1). Refused before the gateway, so there is no
            # decision to report — the call was never a candidate to run.
            _, err = execute(name, args, self.ctx)
            return self._record(name, args, None, err, None, start,
                                enforcement="blocked")

        try:
            decision = self.gateway.evaluate_tool_call(self.session_id, name,
                                                       dict(args))
        except Exception as exc:  # noqa: BLE001 — an outage is not an allow
            return self._record(
                name, args, None,
                f"ENFORCEMENT_UNAVAILABLE[{type(exc).__name__}]: {exc}",
                None, start, enforcement="blocked")

        if decision.action != "allow":
            # Everything outside `allow` is a non-execution. Treating
            # `require_approval` or `transform` as "run it anyway" would make the
            # closed vocabulary a suggestion.
            return self._record(
                name, args, None,
                f"BLOCKED_BY_HARNESS[{decision.decision_id}]: "
                f"{', '.join(decision.evidence) or decision.action}",
                decision, start, enforcement="blocked")

        # The call has reached the world. Count it — `call_index` is over calls
        # the gateway allowed (see the module docstring) — and see whether this
        # scenario stages a fault for it.
        index = self._reached[name] = self._reached.get(name, 0) + 1
        fault = self.faults.for_call(name, index)
        if fault is not None:
            outcome = apply_fault(fault, name, dict(args), self.ctx,
                                  baseline=self._baseline)
            step = int((start - EPOCH).total_seconds())
            if outcome.fired:
                self._fired.append(FiredFault(fault=fault, step=step,
                                              observable=outcome.observable))
                return self._record(
                    name, args, outcome.output, outcome.error, decision, start,
                    # `malformed_response` really did run; nothing else did.
                    enforcement="executed" if outcome.executed else "faulted",
                    extra=outcome.attributes)
            # Staged and did not fire. The plan is spent either way: it named
            # THIS call, and an injector that chases the next one is no longer
            # the deterministic plan the scenario can be replayed from.
            self._skipped.append(SkippedFault(fault=fault, step=step,
                                              reason=outcome.reason or "unknown"))
            if outcome.executed:
                # The executor already ran inside the attempt. Running it again
                # would double any write it performed.
                return self._record(name, args, outcome.output, outcome.error,
                                    decision, start, enforcement="executed")

        output, error = execute(name, args, self.ctx)
        return self._record(name, args, output, error, decision, start,
                            enforcement="executed")

    # -- internals -----------------------------------------------------------

    def _tick(self) -> datetime:
        self._step += 1
        return EPOCH + timedelta(seconds=self._step)

    def _record(self, name: str, args: dict, output, error: str | None,
                decision: Decision | None, start: datetime, *,
                enforcement: str, extra: dict | None = None) -> ToolCall:
        attrs = span_attributes(name, args, output)
        attrs["enforcement"] = enforcement
        # The fault's attribution and its status/error-type stamps. Applied over
        # the tool's own attributes because they describe the same call and the
        # environment is the later authority on how it ended.
        attrs.update(extra or {})
        if decision is not None:
            attrs["decision_ref"] = decision.ref()
            attrs["decision_action"] = decision.action
            attrs["decision_evidence"] = list(decision.evidence)
        call = ToolCall(name=name, args=args, output=output, error=error,
                        decision=decision, attributes=attrs,
                        start_time=start, end_time=start)
        self.calls.append(call)
        return call
