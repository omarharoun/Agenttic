"""Reference glass-box agent: Claude in a tool-use loop with two toy tools.

This is the platform's DUT for development: realistic enough to exercise the
whole pipeline (multi-step, tool calls, errors), simple enough to reason about.
Every LLM call and tool call becomes a Span; tool failures become error spans,
never crashes (Hard Rule 5).
"""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agenttic.adapters.base import AgentAdapter, EscalationRequired
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

SYSTEM_PROMPT = (
    "You are a precise assistant. Use the calculator tool for any arithmetic "
    "and the lookup_kb tool for any company facts. Answer concisely."
)

TOOLS = [
    {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression (+, -, *, /, **, parentheses).",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "lookup_kb",
        "description": "Look up a fact by key in the local knowledge base.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
]

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expression: str) -> float:
    """Arithmetic-only evaluator (no eval())."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"unsupported expression element: {ast.dump(node)}")
    return ev(ast.parse(expression, mode="eval"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sid() -> str:
    return uuid.uuid4().hex[:12]


class AnthropicSimpleAgent(AgentAdapter):
    """Claude with calculator + lookup_kb in a bounded tool-use loop."""

    visibility = "glass_box"

    def __init__(
        self,
        *,
        model: str,
        kb_path: str | Path,
        max_steps: int = 10,
        pricing_per_mtok: dict | None = None,
        client=None,
        agent_id: str = "anthropic-simple-ref",
        system_prompt: str | None = None,
        retry_policy=None,
        autonomy_policy: dict | None = None,
    ):
        if client is None:  # real client only constructed when not injected (tests inject a fake)
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        from agenttic.retry import RetryPolicy
        self.retry_policy = retry_policy or RetryPolicy()
        self.model = model
        self.kb_path = Path(kb_path)
        self.max_steps = max_steps
        self.pricing = pricing_per_mtok or {"input": 3.0, "output": 15.0}
        self.agent_id = agent_id
        # The DUT's task instructions ARE part of the configuration under
        # test — they flow into describe()/config_hash so a prompt tweak is
        # attributable across scorecards.
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        # HITL confidence-gated autonomy (Step 12). Shape (config `hitl_autonomy`):
        #   {"default": "auto"|"verify"|"human_required",
        #    "overrides": {tool_name: "auto"|"verify"|"human_required"}}
        # A tool resolving to "human_required" is not executed autonomously —
        # the agent raises EscalationRequired so the harness can consult a human.
        self.autonomy_policy = autonomy_policy or {}

    def _tool_policy(self, tool: str) -> str:
        """Resolve the autonomy level for ``tool`` (override wins over default)."""
        overrides = self.autonomy_policy.get("overrides") or {}
        return overrides.get(tool, self.autonomy_policy.get("default", "auto"))

    # -- AgentAdapter interface -------------------------------------------

    def _kb_fingerprint(self) -> str:
        """Content identity of the knowledge base ``lookup_kb`` reads.

        The KB is not a side input — it is half of what ``lookup_kb`` answers
        with, so two agents differing only in their KB are two different agents
        under test. It must therefore reach ``config_hash()``, which the harness
        uses to decide a case can be RESUMED from a stored trace
        (``harness/runner.py`` ``done`` map). Hash the CONTENT, not the path:
        the case that bites is a corrected ``kb.json`` written back to the same
        filename, where a path-keyed hash would silently serve the old traces.

        An unreadable KB is itself part of the configuration under test (every
        ``lookup_kb`` call will fail), so name the failure rather than collapsing
        every broken KB onto one hash — but keep it to the exception TYPE so the
        value stays deterministic and machine-independent (no paths, no errno
        text).

        ONE-TIME OPERATIONAL COST, stated rather than discovered: adding this key
        changes ``describe()`` and therefore ``config_hash()`` for every
        ``AnthropicSimpleAgent`` that has ever run, including ones whose KB never
        changed. ``config_hash`` is the resume key (``harness/runner.py`` matches
        ``t.agent_config_hash``) and part of the result-cache key, so the first
        run after this lands resumes nothing and re-executes every case at full
        cost. That is the correct trade — the alternative is the bug this exists
        to close, where a corrected ``kb.json`` written back to the same filename
        silently served the old traces — but it is a real bill on the next run and
        an operator should not have to infer it from a cache miss."""
        try:
            return hashlib.sha256(self.kb_path.read_bytes()).hexdigest()[:16]
        except OSError as exc:
            return f"unreadable:{type(exc).__name__}"

    def describe(self) -> dict:
        return {
            "adapter": "AnthropicSimpleAgent",
            "model": self.model,
            "system_prompt": self.system_prompt,
            "tools": [t["name"] for t in TOOLS],
            "max_steps": self.max_steps,
            # the tool's DATA, by content — see _kb_fingerprint
            "kb_sha256": self._kb_fingerprint(),
            # part of the config under test: a policy change is attributable.
            "autonomy_policy": self.autonomy_policy,
        }

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        spans: list[Span] = []
        # If a human already authorized this run (harness re-invoke after
        # escalation), don't re-escalate — the guidance is the authorization.
        human_authorized = "human_guidance" in test_input
        messages = [{"role": "user", "content": json.dumps(test_input)}]
        t_wall = time.monotonic()
        final_text = ""

        from agenttic.retry import with_retry
        for _ in range(self.max_steps):
            t0 = _now()
            try:
                resp = with_retry(lambda: self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=self.system_prompt,
                    tools=TOOLS,
                    messages=list(messages),
                ), self.retry_policy, op="agent")
            except Exception as exc:  # noqa: BLE001 — retries exhausted: persist partial work
                # Don't discard earlier steps (and their token cost): record an
                # error span and finish the trace with what we have.
                final_text = f"UPSTREAM_ERROR:{type(exc).__name__}: {exc}"
                spans.append(Span(
                    span_id=_sid(), kind="error", name="upstream_error",
                    start_time=t0, end_time=_now(), error=final_text))
                break
            tokens_in = getattr(resp.usage, "input_tokens", None)
            tokens_out = getattr(resp.usage, "output_tokens", None)
            try:  # observability counters (best-effort; never break a run)
                from agenttic.server.metrics import record_tokens
                record_tokens("agent", tokens_in, tokens_out)
            except Exception:  # noqa: BLE001
                pass
            spans.append(Span(
                span_id=_sid(), kind="llm_call", name=self.model,
                start_time=t0, end_time=_now(),
                input={"n_messages": len(messages)},
                output={"stop_reason": resp.stop_reason},
                tokens_in=tokens_in, tokens_out=tokens_out,
                cost_usd=self._cost(tokens_in, tokens_out),
            ))

            if resp.stop_reason != "tool_use":
                final_text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                )
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                # Confidence-gated autonomy: a human_required tool is NOT run
                # autonomously — escalate (a structured signal, not a failure),
                # carrying the spans so far so the harness preserves the work.
                if (not human_authorized
                        and self._tool_policy(block.name) == "human_required"):
                    raise EscalationRequired(
                        f"Authorize {block.name}?",
                        context={
                            "tool": block.name,
                            "tool_input": dict(block.input),
                            "test_case_id": test_case_id,
                            "policy": "human_required",
                        },
                        partial_trace_spans=spans,
                    )
                t1 = _now()
                output, error = self._exec_tool(block.name, dict(block.input))
                spans.append(Span(
                    span_id=_sid(), kind="tool_call", name=block.name,
                    start_time=t1, end_time=_now(),
                    input=dict(block.input),
                    output={"result": output} if error is None else {},
                    error=error,
                ))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output) if error is None else f"ERROR: {error}",
                    "is_error": error is not None,
                })
            messages.append({"role": "user", "content": results})
        else:
            final_text = "MAX_STEPS_EXCEEDED"
            spans.append(Span(
                span_id=_sid(), kind="error", name="max_steps_kill_switch",
                start_time=_now(), end_time=_now(),
                error=f"agent did not finish within {self.max_steps} steps",
            ))

        t2 = _now()
        spans.append(Span(
            span_id=_sid(), kind="final_output", name="final_output",
            start_time=t2, end_time=t2, output={"text": final_text},
        ))

        return Trace(
            trace_id=uuid.uuid4().hex,
            agent_id=self.agent_id,
            agent_config_hash=self.config_hash(),
            test_case_id=test_case_id,
            spans=spans,
            visibility=self.visibility,
            final_output=final_text,
            total_cost_usd=sum(s.cost_usd or 0.0 for s in spans),
            total_latency_ms=(time.monotonic() - t_wall) * 1000.0,
            total_steps=sum(1 for s in spans if s.kind in ("llm_call", "tool_call")),
            schema_version=SCHEMA_VERSION,
        )

    # -- internals ---------------------------------------------------------

    def _cost(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        if tokens_in is None or tokens_out is None:
            return None
        return (
            tokens_in * self.pricing["input"] + tokens_out * self.pricing["output"]
        ) / 1_000_000

    def _exec_tool(self, name: str, args: dict) -> tuple[object, str | None]:
        """Run a tool; return (output, error). Never raises."""
        try:
            if name == "calculator":
                return _safe_eval(args["expression"]), None
            if name == "lookup_kb":
                kb = json.loads(self.kb_path.read_text())
                key = args["key"]
                if key not in kb:
                    return None, f"key not found in KB: {key!r}"
                return kb[key], None
            return None, f"unknown tool: {name!r}"
        except Exception as exc:  # noqa: BLE001 — mistakes are data (Hard Rule 5)
            return None, f"{type(exc).__name__}: {exc}"
