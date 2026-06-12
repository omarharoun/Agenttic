"""Reference glass-box agent: Claude in a tool-use loop with two toy tools.

This is the platform's DUT for development: realistic enough to exercise the
whole pipeline (multi-step, tool calls, errors), simple enough to reason about.
Every LLM call and tool call becomes a Span; tool failures become error spans,
never crashes (Hard Rule 5).
"""

from __future__ import annotations

import ast
import json
import operator
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ascore.adapters.base import AgentAdapter
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

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
    ):
        if client is None:  # real client only constructed when not injected (tests inject a fake)
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.kb_path = Path(kb_path)
        self.max_steps = max_steps
        self.pricing = pricing_per_mtok or {"input": 3.0, "output": 15.0}
        self.agent_id = agent_id

    # -- AgentAdapter interface -------------------------------------------

    def describe(self) -> dict:
        return {
            "adapter": "AnthropicSimpleAgent",
            "model": self.model,
            "system_prompt": SYSTEM_PROMPT,
            "tools": [t["name"] for t in TOOLS],
            "max_steps": self.max_steps,
        }

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        spans: list[Span] = []
        messages = [{"role": "user", "content": json.dumps(test_input)}]
        t_wall = time.monotonic()
        final_text = ""

        for _ in range(self.max_steps):
            t0 = _now()
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=list(messages),
            )
            tokens_in = getattr(resp.usage, "input_tokens", None)
            tokens_out = getattr(resp.usage, "output_tokens", None)
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
