"""The verification surface — what this platform actually tests.

Every count, check name, assertion, coverpoint, archetype and formal property is
**enumerated from the live registry at request time**, never hand-written. If a
check is unregistered, an archetype removed, or an assertion dropped, this
endpoint says so on the next request. That is the point: a capability page
written as marketing copy drifts from the product within a release, and then it
is a claim nobody can verify.

Two things here are NOT enumerated, and are marked where they appear: the
MCP-server and toolset check lists (those batteries declare their ids inline in
``certification/mcp_suite.py`` / ``tool_suite.py`` rather than in a registry like
``MEMORY_CHECKS``, so this page transcribes them and a test pins the pair), and
the prose in ``not_covered`` / ``attestation.governing_rule``. Saying which is
which is the difference between an enumerated page and a page that only looks
enumerated.

``not_covered`` is the load-bearing half. Enumerating what exists is easy and
self-correcting; the edges that matter are the ones no registry can report,
because they are properties of the harness rather than entries in a table.
Anything not implemented is reported as such rather than omitted.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def _checks() -> dict:
    from agenttic.scoring.checks import CHECKS
    names = sorted(CHECKS)
    groups: dict[str, list[str]] = {}
    for n in names:
        if n.startswith(("ir_", "ordering_", "set_", "token_", "span_")):
            g = "retrieval & ranking"
        elif any(k in n for k in ("safety", "harmful", "injection", "secret",
                                  "pii", "profanity", "misuse", "system_prompt")):
            g = "safety"
        elif any(k in n for k in ("tool", "steps", "cost")):
            g = "tool use & budget"
        elif any(k in n for k in ("json", "schema", "format", "is_", "sql",
                                  "length", "word_count", "enum")):
            g = "format & structure"
        elif any(k in n for k in ("faithful", "answer", "abstention", "grounded")):
            g = "grounding & accuracy"
        else:
            g = "text similarity"
        groups.setdefault(g, []).append(n)
    return {"total": len(names), "groups": groups, "scope": _check_scope()}


def _check_scope() -> dict:
    """How much of a run each check actually reads.

    A check that reads ``trace.final_output`` and never touches ``trace.spans``
    grades the LAST message and nothing before it. On a single-turn trace that
    is the whole run and the distinction is invisible. On a session it is not:
    a secret disclosed at turn 3 and not repeated at turn 8 is scored clean by
    every one of them, which is a safety inversion of the same family as
    crediting an irreversible action on a confirmation that failed.

    Counted from the live registry by source introspection rather than written
    down, so the number falls on its own as checks are made turn-aware — a
    hardcoded figure here would rot into a false disclosure, which is the exact
    failure this page exists to avoid. If the source cannot be read (a zipped
    or bytecode-only install), the count is reported as unknown rather than
    guessed.
    """
    import inspect
    import re

    from agenttic.scoring.checks import CHECKS

    last_message_only, reads_spans, undetermined = [], [], []
    for name, fn in sorted(CHECKS.items()):
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            undetermined.append(name)
            continue
        if re.search(r"\.spans\b", src):
            reads_spans.append(name)
        elif "final_output" in src:
            last_message_only.append(name)
        else:
            undetermined.append(name)
    return {
        "last_message_only": len(last_message_only),
        "reads_the_whole_trace": len(reads_spans),
        "undetermined": len(undetermined),
        "note": "a check counted under last_message_only reads the final output "
                "and never the spans, so on a multi-turn session it grades the "
                "last message only — anything said or leaked at an earlier turn "
                "is outside what it can see",
    }


def _assertions() -> dict:
    from agenttic.verification.assertions import ASSERTIONS
    return {
        "total": len(ASSERTIONS),
        "items": [
            {"id": spec.assertion_id, "severity": spec.severity,
             "property": spec.property_text}
            for spec in sorted(ASSERTIONS.values(), key=lambda s: s.assertion_id)],
    }


def _coverage() -> dict:
    from agenttic.coverage.models.baseline import BASELINE_LIMITS, baseline_model
    from agenttic.coverage.models.conversational_transactional import seed_model
    base, fitted = baseline_model(), seed_model()

    def cps(model):
        """Project a coverpoint, including the parts that say it is NOT measured.

        Dropping ``measurable`` / ``not_measurable_reason`` / the per-bin waivers
        published ``session_shape`` as an ordinary measured dimension detecting
        "multi-turn, or resumed against prior memory" — in the same response
        whose ``not_covered`` says no second turn and no resumed session is ever
        exercised. A page can contradict itself in one payload if the projection
        is narrower than the model, so the projection is the model.

        ``bins`` stays a list of ids because it is rendered directly; a waived
        bin is still listed (hiding it would be the silent hole Hard Rule 61
        forbids) and named in ``waived_bins`` beside it. Every key is always
        present: a field that appears only in the interesting case is a field
        consumers forget to handle.
        """
        return [{"id": c.coverpoint_id, "kind": c.kind,
                 "provisional": c.provisional,
                 "bins": [b.bin_id for b in c.bins if b.bin_id != "other"],
                 "description": c.description,
                 "measurable": c.measurable,
                 "not_measurable_reason": c.not_measurable_reason,
                 # derived from `measurable` for a not-measurable coverpoint, but
                 # it is the consequence a reader is actually asking about: does
                 # this dimension move the headline closure figure?
                 "counts_toward_closure": c.required,
                 "waived_bins": {b.bin_id: b.reason for b in c.bins if b.waived},
                 } for c in model.coverpoints]
    return {
        "baseline": {"model": base.model_id, "limits": BASELINE_LIMITS,
                     "applies_to": "every run, automatically, with no model calls",
                     "coverpoints": cps(base),
                     "crosses": [x.cross_id for x in base.crosses]},
        "fitted_example": {"model": fitted.model_id,
                           "archetype": fitted.archetype_id,
                           "coverpoints": cps(fitted),
                           "crosses": [x.cross_id for x in fitted.crosses],
                           "provisional": fitted.provisional_coverpoints},
    }


def _formal() -> dict:
    from agenttic.verification.formal import SHIPPED, z3_available
    from agenttic.verification.formal.properties import DEFAULT_LIMIT
    props = [f() for f in SHIPPED]
    return {
        "total": len(props),
        "scope": "the deterministic tool-authorization guard layer",
        "limit": DEFAULT_LIMIT,
        "result_values": ["proven", "counterexample", "unbounded", "not_attempted"],
        "solver_available": z3_available(),
        "items": [{"id": p.property_id, "description": p.description}
                  for p in props],
    }


def _supply_chain() -> dict:
    from typing import get_args

    from agenttic.certification.catalog import EntryStatus
    from agenttic.certification.memory_suite import MEMORY_CHECKS
    return {
        # TRANSCRIBED, not enumerated: certify_mcp_server / certify_toolset build
        # their outcomes by calling check functions that carry their check_id
        # inline, so there is no MEMORY_CHECKS-style tuple to read. Until those
        # batteries declare themselves, these two lists are the one place on this
        # page that can go stale without anyone noticing — which is why they are
        # labelled here rather than left to look like the enumerated entries.
        "mcp_server": {
            "enumerated": False,
            "declared_in": "agenttic.certification.mcp_suite.certify_mcp_server",
            "transports": ["stdio", "streaming http"],
            "checks": ["contract_schema", "golden_responses", "input_fuzzing",
                       "authorization", "error_taxonomy", "idempotency",
                       "rate_limit", "side_effect_disclosure",
                       "response_injection"],
        },
        "tools": {
            "enumerated": False,
            "declared_in": "agenttic.certification.tool_suite.certify_toolset",
            "sources": ["mcp", "native function-calling"],
            "checks": ["contract_schema", "input_fuzzing", "error_taxonomy",
                       "side_effect_disclosure", "failure_mode_handling",
                       "description_quality (cross-model selection accuracy)"],
        },
        "memory": {
            "implemented": True,
            # The subject under test here is the STORE, driven directly across
            # simulated session boundaries. Naming that is the difference between
            # "this memory implementation isolates principals" (what we show) and
            # "this agent handles memory correctly" (what we do not) — no agent
            # runs inside this battery.
            "note": "the memory STORE is driven directly across session "
                    "boundaries — every check below is invisible inside a single "
                    "session, and none of them puts an agent in the loop",
            "subject": "a memory store, not an agent using one",
            "checks": [c["id"] for c in MEMORY_CHECKS],
            "questions": [{"id": c["id"], "critical": c["critical"],
                           "question": c["question"]} for c in MEMORY_CHECKS],
        },
        "catalog": {
            "implemented": True,
            "note": "the register of what is approved for use, and the rule for "
                    "how something enters and leaves it",
            "statuses": list(get_args(EntryStatus)),
            "promotion_gates": [
                "a signed evidence manifest that verifies",
                "evidence that has not expired",
                "evidence that has not been revoked",
                "a named approver and a written rationale",
                "for a challenger: a clean shadow comparison against the "
                "incumbent, judged per case rather than on the average",
            ],
            "conformance_findings": [
                "needs_reverification", "no_evidence", "evidence_unavailable",
                "evidence_mismatch", "evidence_expired", "evidence_revoked",
                "unregistered_dependency", "uncertified_dependency",
            ],
            "cascade": "retiring a component moves every dependent that was "
                       "certified with it to needs_reverification and suspends "
                       "its manifest",
        },
    }


def _archetypes() -> dict:
    from agenttic.rubric_engine.cores import SEED_ARCHETYPES
    return {
        "total": len(SEED_ARCHETYPES),
        "items": [{"id": a.archetype_id, "name": a.name,
                   "required_suite_features": a.required_suite_features,
                   "failure_modes": a.failure_modes}
                  for a in sorted(SEED_ARCHETYPES.values(),
                                  key=lambda a: a.archetype_id)],
    }


def _methodologies() -> dict:
    try:
        from agenttic.metrics.datasets import ADAPTERS
        ids = sorted(ADAPTERS)
    except Exception:  # noqa: BLE001
        ids = []
    return {"total": len(ids), "items": ids}


def _attestation() -> dict:
    from agenttic.certification.attest import DEFAULT_EXPIRY_DAYS
    return {
        "tiers": ["local_self_attested", "assurance"],
        "default_expiry_days": DEFAULT_EXPIRY_DAYS,
        "properties": ["signed evidence manifest", "CycloneDX agent BOM",
                       "bound to an exact agent_config_hash",
                       "expires", "revocable (drift suspends automatically)",
                       "signed append-only revocation list"],
        # NB: phrased to avoid the literal banned substrings. The claims guard is
        # deliberately blunt (plain substring match) and cannot tell a claim from
        # a prohibition of that claim — so the rule is worded without them rather
        # than the guard being loosened.
        "governing_rule": "sign the evidence, never the verdict — no artifact "
                          "makes an unbounded safety claim about an agent",
    }


@router.get("/capabilities")
def capabilities() -> dict:
    """What this platform tests, enumerated from the live registries."""
    return {
        "deterministic_checks": _checks(),
        "assertions": _assertions(),
        "coverage": _coverage(),
        "formal": _formal(),
        "supply_chain": _supply_chain(),
        "archetypes": _archetypes(),
        "methodologies": _methodologies(),
        "attestation": _attestation(),
        "not_covered": [
            "the model's internals — we verify the guard layer around it, never "
            "the weights",
            "memory SEMANTICS beyond the certified battery — we test isolation, "
            "deletion, contradiction, injection and capacity; we do not judge "
            "whether what a store chose to remember was worth remembering",
            "multi-agent interaction coverage",
            "anything a coverage model does not declare — unhit bins are reported, "
            "never assumed passed",
            # The three below are the shape of the harness itself, not gaps in a
            # registry. A case is one input dict handed to the agent once, so a
            # capability page that enumerates checks and archetypes without saying
            # what the agent was PUT THROUGH describes the measuring instrument and
            # not the experiment. These stay until a harness exists that can make
            # them false — and the first one has now been made NARROWER rather
            # than false: `agenttic cdv` runs an agent in a stateful retail world
            # (`agenttic.scenario`) that stages the five declared tool conditions
            # on the call a plan names. Everything a customer runs — a stored
            # suite through `run_standard` — still gets none of it, so the
            # disclosure stays and states which path it is about. Widening it to
            # "we simulate environments" on the strength of a CLI loop and eight
            # retail tools would be the over-claim this list exists to prevent.
            "a simulated environment on the standard run path — a suite case is "
            "one input dict handed to the agent once, with no world to act in and "
            "no fault injection: nothing on that path makes a service time out, "
            "return a 500, or refuse a write, and where coverage names a tool "
            "condition there it is reading what the trace or the scenario says "
            "the condition WAS. The `agenttic cdv` loop is the exception and its "
            "limits are its own: one offline retail world of eight declared "
            "tools, faults staged only on tools THIS platform executes (a "
            "black-box agent calling its own tools cannot be fault-injected), and "
            "a fault credits a coverage bin only where the injector stamped the "
            "call it failed",
            "a simulated user — a case is one input delivered as one message and "
            "the agent's reply ends the case. There is no counterparty to push "
            "back, supply a missing detail, change its mind, or ask a second "
            "question, so nothing on this surface is evidence about what the agent "
            "does after its first answer",
            "resumed sessions — every run starts with an empty context and ends "
            "with the agent's first final output. Nothing is carried across a "
            "session boundary by the harness, so multi-turn state and recall "
            "across sessions are untested here (the memory battery above tests a "
            "memory STORE directly, with no agent in the loop)",
            # This one is a limit of the CHECKS rather than of the harness, and
            # it is the one that gets WORSE as the harness improves: while a run
            # is one message the distinction cannot be observed, so the day a
            # session exists is the day this becomes a live hole. Stated now,
            # with the count read off the registry (`deterministic_checks.scope`)
            # rather than asserted here, so the two can never disagree.
            "harness enforcement for an agent whose tool loop we do not run — "
            "the honeypot battery works by planting a decoy tool in the list the "
            "MODEL sees and watching whether the framework blocks the call. That "
            "needs an adapter this platform executes. A black-box HTTP agent "
            "calls its own tools behind an endpoint and a managed agent runs "
            "server-side, so for both there is nowhere to plant bait and the "
            "question is not measurable here — the same limit as fault "
            "injection, and for the same reason. The battery refuses those "
            "adapters rather than substituting its own demo agent, and a battery "
            "run against that demo agent cannot be stored against a scorecard at "
            "all: its outcomes describe a fixture, not anybody's harness",
            "earlier turns of a session, for most deterministic checks — a check "
            "that reads the final output and never the spans grades the LAST "
            "message, so on a multi-turn trace a secret disclosed at turn 3 and "
            "not repeated at the end is scored clean by it. The count is in "
            "`deterministic_checks.scope`; the assertion layer and the coverage "
            "extractors read the whole trace and are not subject to this",
        ],
    }
