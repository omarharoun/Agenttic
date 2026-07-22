"""SPEC-9 Step 40 — automatic classification acceptance tests (offline path)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from agenttic.rubric_engine.classify import (
    ClassifyInputs, classify, trace_shape_features)
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trace(*, tools=(), retrievals=0, turns=1) -> Trace:
    spans: list[Span] = []
    i = 0
    for _ in range(turns):
        spans.append(Span(span_id=f"l{i}", kind="llm_call", name="llm",
                          start_time=T0, end_time=T0 + timedelta(seconds=1)))
        i += 1
    for tool in tools:
        spans.append(Span(span_id=f"t{i}", kind="tool_call", name=tool,
                          start_time=T0, end_time=T0 + timedelta(seconds=1)))
        i += 1
    for _ in range(retrievals):
        spans.append(Span(span_id=f"r{i}", kind="retrieval", name="retrieve",
                          start_time=T0, end_time=T0 + timedelta(seconds=1)))
        i += 1
    spans.append(Span(span_id=f"f{i}", kind="final_output", name="final_output",
                      start_time=T0, end_time=T0 + timedelta(seconds=1)))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 spans=spans, visibility="glass_box", final_output="ok")


# --- pilot support agent ---------------------------------------------------

PILOT_SUPPORT = (
    "A customer support chat agent for an online store. It handles multi-turn "
    "conversations, looks up an order, processes a refund, updates the customer's "
    "account, and must follow the refund policy. It escalates to a human when the "
    "policy does not cover the case.")


def test_pilot_support_classifies_as_conversational_transactional():
    inputs = ClassifyInputs(
        agent_description=PILOT_SUPPORT,
        traces=[_trace(tools=["lookup_order", "process_refund", "update_account"],
                       turns=3)])
    matches = classify(inputs)
    assert matches[0].archetype_id == "conversational_transactional"
    # confirmed by trace shape (writes + multi-turn), not description alone
    assert "trace" in matches[0].source
    assert matches[0].confidence >= 0.5


def test_description_alone_still_classifies_support():
    # even with NO traces, the description keyword-matches CT above threshold
    matches = classify(ClassifyInputs(agent_description=PILOT_SUPPORT))
    assert matches[0].archetype_id == "conversational_transactional"
    assert matches[0].source == "keyword"


def test_retrieval_qa_fixture_picks_grounding_heavy_core():
    desc = ("A documentation Q&A agent. It answers questions from a knowledge "
            "base using retrieval (RAG), must cite sources for every claim, and "
            "abstains when the corpus does not support an answer.")
    inputs = ClassifyInputs(agent_description=desc,
                            traces=[_trace(retrievals=3, turns=1)])
    matches = classify(inputs)
    assert matches[0].archetype_id == "retrieval_qa"


def test_hybrid_composes_two_archetypes():
    # "a research assistant that books travel" -> research + transactional
    desc = ("A research assistant that gathers information and synthesizes a "
            "report, and also books travel: it makes a reservation and processes "
            "the booking for the user, following the travel policy.")
    matches = classify(ClassifyInputs(agent_description=desc))
    ids = {m.archetype_id for m in matches}
    assert "research_analysis" in ids
    assert "conversational_transactional" in ids
    # both cleared the threshold, so synthesis will compose both cores
    assert all(m.confidence >= 0.5 for m in matches)


def test_below_threshold_routes_to_custom():
    desc = "An agent that plays chess against the user and narrates each move."
    matches = classify(ClassifyInputs(agent_description=desc))
    assert len(matches) == 1
    assert matches[0].archetype_id == "custom"
    # it does NOT silently pick a wrong archetype
    assert matches[0].confidence < 0.5


def test_trace_shape_features_objective():
    feats = trace_shape_features(
        [_trace(tools=["get_order", "process_refund"], retrievals=1, turns=2)])
    assert feats["has_traces"] is True
    assert feats["writes"] == 1          # process_refund
    assert feats["reads"] == 1           # get_order
    assert feats["state_mutation"] is True
    assert feats["retrieval_calls"] >= 1
    assert feats["avg_turns"] == 2


def test_empty_traces_are_all_zero():
    feats = trace_shape_features([])
    assert feats["has_traces"] is False
    assert feats["writes"] == 0 and feats["retrieval_calls"] == 0


def test_trace_shape_can_confirm_over_weak_description():
    # a thin description but a clearly coding trace -> coding surfaces
    inputs = ClassifyInputs(
        agent_description="An agent that works on a repository to fix a bug.",
        traces=[_trace(tools=["git_apply_patch", "run_pytest"], turns=2)])
    matches = classify(inputs)
    assert "coding" in {m.archetype_id for m in matches}
