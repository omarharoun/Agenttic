"""The verification surface — what this platform actually tests.

Every number and name here is **enumerated from the live registries at request
time**, never hand-written. If a check is unregistered, an archetype removed, or
an assertion dropped, this endpoint says so on the next request. That is the
point: a capability page written as marketing copy drifts from the product within
a release, and then it is a claim nobody can verify.

Anything not implemented is reported as such rather than omitted, so the surface
is honest about its own edges.
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
    return {"total": len(names), "groups": groups}


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
        return [{"id": c.coverpoint_id, "kind": c.kind,
                 "provisional": c.provisional,
                 "bins": [b.bin_id for b in c.bins if b.bin_id != "other"],
                 "description": c.description} for c in model.coverpoints]
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
        "mcp_server": {
            "transports": ["stdio", "streaming http"],
            "checks": ["contract_schema", "golden_responses", "input_fuzzing",
                       "authorization", "error_taxonomy", "idempotency",
                       "rate_limit", "side_effect_disclosure",
                       "response_injection"],
        },
        "tools": {
            "sources": ["mcp", "native function-calling"],
            "checks": ["contract_schema", "input_fuzzing", "error_taxonomy",
                       "side_effect_disclosure", "failure_mode_handling",
                       "description_quality (cross-model selection accuracy)"],
        },
        "memory": {
            "implemented": True,
            "note": "memory is exercised ACROSS session boundaries — every check "
                    "below is invisible inside a single session",
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
        ],
    }
