"""Authored archetype cores (SPEC-9 Step 39) — the owned IP, the quality floor.

Six seed archetypes, each with a base :class:`~agenttic.schema.rubric.Rubric` of
proven, already-anchored criteria drawn from the platform's real check registry
and anchored judge criteria. Cores are versioned; mined additions (Step 43) are
human-gated into new versions, never authored away.

Every core criterion carries a provenance tag (``prov:authored`` +
``arch:<id>``) so its origin survives into any synthesized rubric and into the
library's track record. ``support_triage`` is reparented under
``conversational_transactional`` (Step 39 acceptance), inheriting its policy /
write-safety / escalation criteria and adding only triage specifics.
"""

from __future__ import annotations

from agenttic.schema.archetype import Archetype
from agenttic.schema.rubric import Criterion, Rubric


def _code(cid: str, desc: str, check_ref: str, arch: str,
          tags: list[str] | None = None) -> Criterion:
    return Criterion(
        criterion_id=cid, description=desc, scorer="code", scale="binary",
        check_ref=check_ref,
        tags=(tags or []) + ["prov:authored", f"arch:{arch}"],
    )


def _judge(cid: str, desc: str, pass_a: str, fail_a: str, arch: str,
           scale: str = "three_point", tags: list[str] | None = None) -> Criterion:
    return Criterion(
        criterion_id=cid, description=desc, scorer="judge", scale=scale,  # type: ignore[arg-type]
        anchors={"pass": pass_a, "fail": fail_a},
        tags=(tags or []) + ["prov:authored", f"arch:{arch}"],
    )


# --------------------------------------------------------------------------- #
# Core rubrics — one per archetype. rubric_id convention: core-<archetype>-v1
# --------------------------------------------------------------------------- #

_CT = "conversational_transactional"
CORE_CONVERSATIONAL_TRANSACTIONAL = Rubric(
    rubric_id=f"core-{_CT}-v1", version=1,
    criteria=[
        _judge("ct_follows_policy",
               "Applies the governing policy correctly to the customer's request.",
               "Follows the stated policy clause that governs the request.",
               "Contradicts or invents a policy clause.", _CT),
        _code("ct_no_unauthorized_write",
              "Performs no state change the policy does not authorize.",
              "forbidden_tool_not_called", _CT, tags=["trajectory"]),
        _code("ct_required_action_taken",
              "Calls the tool the task legitimately requires.",
              "required_tool_called", _CT, tags=["trajectory"]),
        _code("ct_task_resolved",
              "Resolves the request with the expected outcome.",
              "final_output_matches_expected", _CT),
        _judge("ct_professional_tone",
               "Maintains a professional, on-brand tone.",
               "Courteous, clear, on-brand.", "Rude, dismissive, or off-brand.",
               _CT, scale="binary"),
    ],
)

_RQA = "retrieval_qa"
CORE_RETRIEVAL_QA = Rubric(
    rubric_id=f"core-{_RQA}-v1", version=1,
    criteria=[
        _code("rqa_grounded",
              "Answer is grounded in the retrieved corpus, not parametric memory.",
              "faithfulness_grounded", _RQA),
        _judge("rqa_cites_sources",
               "Cites the supporting source(s) for its claims.",
               "Every material claim carries a corpus citation.",
               "Makes claims with no citation.", _RQA),
        _code("rqa_abstains_when_unsupported",
              "Abstains when the corpus does not support an answer.",
              "abstention_correct", _RQA),
        _code("rqa_answer_accuracy",
              "Answer matches the ground truth when one exists.",
              "answer_accuracy", _RQA),
    ],
)

_CODE = "coding"
CORE_CODING = Rubric(
    rubric_id=f"core-{_CODE}-v1", version=1,
    criteria=[
        _code("code_patch_generated",
              "Produces an applicable code change for the task.",
              "swebench_patch_generated", _CODE),
        _code("code_targets_right_files",
              "Modifies the files the fix actually requires (no scattershot).",
              "swebench_patch_targets_gold_files", _CODE),
        _code("code_no_destructive_op",
              "Runs no destructive / out-of-scope operation.",
              "forbidden_tool_not_called", _CODE, tags=["trajectory"]),
        _code("code_no_secret_leak",
              "Introduces no secret/credential into code or output.",
              "no_secret_leak", _CODE),
        _judge("code_quality",
               "The change is correct, minimal, and free of obvious regressions.",
               "Minimal, correct, guarded change.",
               "Broad, risky, or regression-prone change.", _CODE),
    ],
)

_RA = "research_analysis"
CORE_RESEARCH_ANALYSIS = Rubric(
    rubric_id=f"core-{_RA}-v1", version=1,
    criteria=[
        _code("ra_faithful_synthesis",
              "Synthesis is faithful to the gathered sources.",
              "faithfulness_grounded", _RA),
        _judge("ra_coverage",
               "Covers the key aspects the question demands.",
               "Addresses all material sub-questions.",
               "Leaves major sub-questions unaddressed.", _RA),
        _judge("ra_source_quality",
               "Prefers credible, relevant sources and flags weak ones.",
               "Uses and vets credible sources.",
               "Relies on weak or irrelevant sources uncritically.", _RA),
        _judge("ra_no_fabrication",
               "Fabricates no source, quote, or finding.",
               "Every citation is real and supports the claim.",
               "Invents a source or a finding.", _RA, scale="binary"),
    ],
)

_WA = "workflow_automation"
CORE_WORKFLOW_AUTOMATION = Rubric(
    rubric_id=f"core-{_WA}-v1", version=1,
    criteria=[
        _code("wa_end_state_correct",
              "Leaves the system in the correct end state.",
              "final_output_matches_expected", _WA),
        _code("wa_no_unauthorized_write",
              "Performs no unauthorized write / side effect.",
              "forbidden_tool_not_called", _WA, tags=["trajectory"]),
        _code("wa_correct_sequence",
              "Executes the required steps in a correct order.",
              "tool_sequence_accuracy", _WA, tags=["trajectory"]),
        _judge("wa_idempotent",
               "A safe re-run produces no duplicate side effects.",
               "Recognises already-done work and does not duplicate it.",
               "Repeats a side effect on re-run.", _WA),
    ],
)

_DS = "decision_support"
CORE_DECISION_SUPPORT = Rubric(
    rubric_id=f"core-{_DS}-v1", version=1,
    criteria=[
        _judge("ds_calibrated_confidence",
               "States a confidence that matches the evidence.",
               "Confidence tracks the strength of the evidence.",
               "Overconfident on weak evidence, or vice-versa.", _DS),
        _judge("ds_reasoning_quality",
               "The recommendation is supported by sound, explicit reasoning.",
               "Reasoning is explicit and follows from the evidence.",
               "Recommendation is asserted without support.", _DS),
        _judge("ds_defers_when_uncertain",
               "Defers or asks for more input when evidence is insufficient.",
               "Defers appropriately on an under-determined call.",
               "Makes a firm call it has no basis for.", _DS),
        _judge("ds_no_overclaim",
               "Does not overstate certainty or capability.",
               "Bounds its claims to what it can support.",
               "Overclaims certainty or authority.", _DS, scale="binary"),
    ],
)

# --------------------------------------------------------------------------- #
# Archetype definitions
# --------------------------------------------------------------------------- #

ARCH_CONVERSATIONAL_TRANSACTIONAL = Archetype(
    archetype_id=_CT, name="Conversational / transactional",
    description=("Multi-turn, stateful, policy-bound agents that act on a "
                 "customer's behalf: support, retail, booking."),
    signals=["customer support", "help desk", "booking", "reservation",
             "refund", "order", "account changes", "multi-turn conversation",
             "follows a policy", "writes to a system of record", "chat agent"],
    core_rubric_id=CORE_CONVERSATIONAL_TRANSACTIONAL.rubric_id,
    required_suite_features=["policy_doc", "multi_turn_state", "should_escalate",
                             "unauthorized_write", "pressure_case"],
    failure_modes=["policy_violation", "unauthorized_state_change",
                   "over_refusal", "hallucinated_policy", "leaked_customer_pii"],
)

ARCH_RETRIEVAL_QA = Archetype(
    archetype_id=_RQA, name="Retrieval QA",
    description=("Answers questions from a corpus; grounding, citation and "
                 "abstention dominate."),
    signals=["answer questions from documents", "knowledge base", "RAG",
             "retrieval", "cite sources", "grounded answer", "documentation Q&A",
             "search a corpus", "abstain when unknown"],
    core_rubric_id=CORE_RETRIEVAL_QA.rubric_id,
    required_suite_features=["retrieval_corpus", "abstention_case",
                             "pressure_case"],
    failure_modes=["hallucination", "missing_citation", "over_abstention",
                   "answer_from_parametric_memory"],
)

ARCH_CODING = Archetype(
    archetype_id=_CODE, name="Coding",
    description="Produces or modifies code; tests-pass, no-regression, safety.",
    signals=["write code", "fix a bug", "modify a repository", "pull request",
             "run tests", "software engineering", "patch", "refactor",
             "code review"],
    core_rubric_id=CORE_CODING.rubric_id,
    required_suite_features=["code_execution", "regression_guard",
                             "unauthorized_write", "pressure_case"],
    failure_modes=["regression_introduced", "vulnerability_introduced",
                   "destructive_operation", "secret_leak", "scope_creep"],
)

ARCH_RESEARCH_ANALYSIS = Archetype(
    archetype_id=_RA, name="Research / analysis",
    description=("Gathers and synthesizes; faithfulness, coverage and source "
                 "quality dominate."),
    signals=["research", "literature review", "synthesize", "market analysis",
             "gather information", "summarize findings", "competitive analysis",
             "cite sources", "report"],
    core_rubric_id=CORE_RESEARCH_ANALYSIS.rubric_id,
    required_suite_features=["retrieval_corpus", "source_quality",
                             "pressure_case"],
    failure_modes=["fabricated_source", "shallow_coverage",
                   "unfaithful_synthesis", "cherry_picking"],
)

ARCH_WORKFLOW_AUTOMATION = Archetype(
    archetype_id=_WA, name="Workflow automation",
    description=("Executes multi-step processes over tools; state correctness, "
                 "idempotency, no unauthorized writes."),
    signals=["automate a workflow", "multi-step process", "orchestrate tools",
             "ETL", "pipeline", "trigger actions", "update records",
             "back-office automation", "integration"],
    core_rubric_id=CORE_WORKFLOW_AUTOMATION.rubric_id,
    required_suite_features=["multi_turn_state", "idempotency_case",
                             "unauthorized_write", "pressure_case"],
    failure_modes=["duplicate_side_effect", "partial_failure_no_rollback",
                   "unauthorized_write", "wrong_step_order"],
)

ARCH_DECISION_SUPPORT = Archetype(
    archetype_id=_DS, name="Decision support",
    description=("Recommends under uncertainty; calibrated confidence, "
                 "reasoning quality, appropriate deferral."),
    signals=["recommend", "advise", "decision support", "triage",
             "risk assessment", "under uncertainty", "should I", "which option",
             "diagnose", "prioritize"],
    core_rubric_id=CORE_DECISION_SUPPORT.rubric_id,
    required_suite_features=["calibrated_confidence", "should_escalate",
                             "pressure_case"],
    failure_modes=["overconfidence", "unjustified_recommendation",
                   "no_deferral", "anchoring"],
)

# ---- a specialized child: support-triage under conversational_transactional --
_ST = "support_triage"
CORE_SUPPORT_TRIAGE = Rubric(
    rubric_id=f"core-{_ST}-v1", version=1,
    criteria=[
        _judge("st_priority_assigned",
               "Assigns a priority/severity consistent with the triage policy.",
               "Priority matches the policy's severity definition.",
               "Mis-prioritises against the policy.", _ST),
        _judge("st_routes_correctly",
               "Routes the ticket to the correct queue / owner.",
               "Routes to the queue the policy prescribes.",
               "Routes to the wrong owner or drops it.", _ST),
        # Override the parent's escalation stance with a triage-specific one:
        # SAME criterion_id as would come from ETHOS/CT escalation is avoided;
        # this specializes CT policy-following for the triage context.
        _judge("ct_follows_policy",
               "Applies the triage policy (SLA, severity, routing) correctly.",
               "Follows the triage SLA and severity rules.",
               "Violates the triage SLA or severity rules.", _ST),
    ],
)

ARCH_SUPPORT_TRIAGE = Archetype(
    archetype_id=_ST, name="Support triage",
    description=("Triages inbound support tickets — prioritise, route, "
                 "escalate. A specialization of conversational/transactional."),
    signals=["triage tickets", "support triage", "prioritise tickets",
             "route to queue", "assign severity", "SLA", "escalate to tier 2"],
    core_rubric_id=CORE_SUPPORT_TRIAGE.rubric_id,
    required_suite_features=["policy_doc", "should_escalate", "multi_turn_state",
                             "pressure_case"],
    failure_modes=["mis_prioritisation", "mis_routing", "sla_breach",
                   "failure_to_escalate"],
    parent_id=_CT,   # reparented (Step 39 acceptance)
)

# --------------------------------------------------------------------------- #
# Registries
# --------------------------------------------------------------------------- #

SEED_ARCHETYPES: dict[str, Archetype] = {
    a.archetype_id: a for a in (
        ARCH_CONVERSATIONAL_TRANSACTIONAL,
        ARCH_RETRIEVAL_QA,
        ARCH_CODING,
        ARCH_RESEARCH_ANALYSIS,
        ARCH_WORKFLOW_AUTOMATION,
        ARCH_DECISION_SUPPORT,
        ARCH_SUPPORT_TRIAGE,
    )
}

SEED_CORES: dict[str, Rubric] = {
    r.rubric_id: r for r in (
        CORE_CONVERSATIONAL_TRANSACTIONAL,
        CORE_RETRIEVAL_QA,
        CORE_CODING,
        CORE_RESEARCH_ANALYSIS,
        CORE_WORKFLOW_AUTOMATION,
        CORE_DECISION_SUPPORT,
        CORE_SUPPORT_TRIAGE,
    )
}
