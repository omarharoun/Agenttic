"""Simulated user + conversation loop (SPEC-7 Step 30).

`UserSim` role-plays the user from a `UserScenario`. By default it is a
deterministic rule-driven user (known facts revealed only WHEN ASKED, hidden
facts only when the agent says a trigger phrase) — reproducible and dependency-
free for tests and CI. Given a `complete` seam it becomes an LLM user (the
production path; the simulator model must differ from the agent's — Hard Rule 32).

`run_conversation` drives agent-turn ↔ user-turn, each turn a span, bounded by
`max_turns`. The agent may call tools between user turns; escalation (Step 12)
is resolved mid-conversation and the agent resumed. Every trace it produces is
labelled ``user_source="simulated"`` (Hard Rule 31).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from agenttic.adapters.base import AgentAdapter, EscalationRequired, HumanChannel
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.schema.user_scenario import UserScenario


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(key: str) -> str:
    return key.replace("_", " ").lower()


class UserSim:
    """A simulated user. `complete(prompt) -> reply` makes it an LLM user;
    without it, a deterministic rule-driven user."""

    def __init__(self, scenario: UserScenario, complete: Callable[[str], str] | None = None):
        self.scenario = scenario
        self.complete = complete

    def opening(self) -> str:
        return self.scenario.goal

    def should_stop(self, agent_message: str) -> bool:
        low = agent_message.lower()
        return any(sc.lower() in low for sc in self.scenario.stop_conditions)

    def respond(self, agent_message: str, history: list[dict]) -> str:
        if self.complete is not None:
            return self.complete(self._prompt(agent_message, history))
        low = agent_message.lower()
        revealed: list[str] = []
        for key, val in self.scenario.known_facts.items():
            if key.lower() in low or _norm(key) in low:
                revealed.append(f"{key}: {val}")
        for key, spec in self.scenario.hidden_facts.items():
            trigger = str((spec or {}).get("reveal_when", "")).lower()
            if trigger and trigger in low:
                revealed.append(f"{key}: {(spec or {}).get('value')}")
        if revealed:
            return "; ".join(revealed) + "."
        return "I'm not sure — what exactly do you need from me?"

    def _prompt(self, agent_message: str, history: list[dict]) -> str:
        return (f"You are role-playing a user. Persona: {self.scenario.persona}. "
                f"Goal: {self.scenario.goal}. Temperament: {self.scenario.temperament}. "
                f"Reveal these only when asked: {self.scenario.known_facts}. "
                f"The agent just said: {agent_message!r}. Reply as the user in one turn.")


def _user_span(text: str) -> Span:
    now = _now()
    return Span(span_id=uuid.uuid4().hex[:12], kind="user_message", name="user",
                start_time=now, end_time=now, output={"text": text})


def run_conversation(agent: AgentAdapter, tc: TestCase, *,
                     user_complete: Callable[[str], str] | None = None,
                     human: HumanChannel | None = None) -> Trace:
    """Run a multi-turn conversation between `agent` and a simulated user built
    from ``tc.user_scenario``. Returns one conversation trace, always labelled
    simulated. Max-turns exhaustion yields a persisted failure trace, never a
    dropped run."""
    scenario = tc.user_scenario or UserScenario(goal=tc.task_description)
    user = UserSim(scenario, complete=user_complete)
    spans: list[Span] = []
    history: list[dict] = []
    escalated = False

    user_msg = user.opening()
    spans.append(_user_span(user_msg))
    history.append({"role": "user", "content": user_msg})

    final = ""
    done = False
    for _turn in range(scenario.max_turns):
        agent_input = {"conversation": list(history),
                       "task_description": tc.task_description, **tc.input}
        try:
            atrace = agent.run(agent_input, test_case_id=tc.test_id)
        except EscalationRequired as exc:
            escalated = True
            spans.extend(exc.partial_trace_spans)
            if human is None:
                final = "ESCALATED_UNRESOLVED"
                break
            guidance = human.respond(exc.question, exc.context)
            agent_input["human_guidance"] = guidance
            atrace = agent.run(agent_input, test_case_id=tc.test_id)

        spans.extend(atrace.spans)
        agent_msg = atrace.final_output
        history.append({"role": "assistant", "content": agent_msg})
        final = agent_msg
        if user.should_stop(agent_msg) or agent_msg.strip().endswith("DONE"):
            done = True
            break
        reply = user.respond(agent_msg, history)
        spans.append(_user_span(reply))
        history.append({"role": "user", "content": reply})
    else:
        # ran the full budget without the agent finishing (Hard Rule 5: persist it)
        now = _now()
        spans.append(Span(span_id=uuid.uuid4().hex[:12], kind="error",
                          name="max_turns_exhausted", start_time=now, end_time=now,
                          error=f"conversation did not conclude within {scenario.max_turns} turns"))
        final = "MAX_TURNS_EXHAUSTED"

    now = _now()
    spans.append(Span(span_id="final", kind="final_output", name="final_output",
                      start_time=now, end_time=now, output={"text": final}))
    # merged per-turn agent traces can repeat span_ids; re-id for uniqueness
    spans = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id=agent.agent_id,
        agent_config_hash=agent.config_hash(), test_case_id=tc.test_id, spans=spans,
        visibility=getattr(agent, "visibility", "glass_box"), final_output=final,
        total_cost_usd=sum(s.cost_usd or 0.0 for s in spans),
        total_steps=sum(1 for s in spans if s.kind in ("llm_call", "tool_call")),
        escalated=escalated, user_source="simulated", schema_version=SCHEMA_VERSION)


def agent_messages(trace: Trace) -> list[str]:
    """All agent-authored message texts in a conversation trace (for the
    communication check)."""
    out = []
    for s in trace.spans:
        if s.kind == "final_output" and isinstance(s.output, dict):
            t = s.output.get("text")
            if t:
                out.append(str(t))
    return out
