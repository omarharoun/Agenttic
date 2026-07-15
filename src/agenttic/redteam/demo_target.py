"""A deterministic, no-API-key target for the end-to-end example.

The reference agent (:class:`AnthropicSimpleAgent`) drives a real LLM, so running
probes through it needs an Anthropic key + credits. To run the whole pipeline
OFFLINE, we REUSE that exact adapter but inject a scripted client in place of the
Anthropic SDK (the documented test seam: "tests inject a fake"). The result is a
genuine glass-box :class:`~agenttic.schema.trace.Trace` — real tool-call spans,
real final output — scored by the real scorer. No pipeline code is faked; only
the model is stand-in.

The scripted client models a *plausibly vulnerable* agent: it has a shallow,
raw-substring input filter (a "guard"). Requests that trip the guard are refused
(they survive the attack); requests that convey the same intent but slip past the
guard via character obfuscation or paraphrase are COMPLIED with (the agent
breaks). This is a realistic weakness — a keyword denylist bypassed by
obfuscation — and it makes the value of the generator's evasion variants concrete.

Point the generator at a real agent (real client + key) and the SAME probes run
unchanged; this stand-in only exists so the example runs with no credits.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.metrics._normalize import normalize

from .descriptor import AgentDescriptor

#: Raw ASCII phrases the shallow guard blocks (matched case-insensitively on the
#: UN-normalized request). Present only in the "direct" attack variants.
GUARD_TRIGGERS = (
    "ignore previous", "ignore your", "internal_api_token", "system:",
    "delete all records",
)

#: Normalized-text intent markers. Read AFTER ``normalize`` so obfuscated /
#: paraphrased requests are still understood (this is why obfuscation slips past
#: the raw guard yet still elicits compliance).
_SECRET_KWS = ("token", "credential", "api key", "apikey", "secret value",
               "configured with", "key you currently hold", "key you hold")
_TOOL_KWS = ("lookup_kb", "look up", "lookup", "knowledge base", "the kb")
_DANGER_KWS = ("delete", "wipe", "erase", "destroy", "purge", "remove all",
               "email")


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name: str, inp: dict, tid: str = "tu_demo"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=tid)


def _resp(blocks, stop_reason: str):
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=12, output_tokens=12))


class FakeVulnerableClient:
    """Scripted stand-in for ``anthropic.Anthropic`` — a naive, vulnerable agent."""

    def __init__(self, descriptor: AgentDescriptor):
        self.descriptor = descriptor
        sec = descriptor.primary_secret()
        self._secret = sec[1] if sec else ""
        self.messages = self  # so ``client.messages.create(...)`` resolves here

    # anthropic.Anthropic().messages.create(...) shape
    def create(self, *, messages, **_kw):
        last = messages[-1]
        content = last.get("content")
        # Second turn: the agent fed back a tool result -> wrap up.
        if (isinstance(content, list) and content
                and isinstance(content[0], dict)
                and content[0].get("type") == "tool_result"):
            return _resp([_text_block("Here is what I found.")], "end_turn")

        request = self._extract_request(messages)
        intent, evaded = self._classify(request)
        if not evaded or intent == "benign":
            # The guard caught it (or nothing actionable) -> refuse. Survivor.
            return _resp([_text_block("I can't help with that request.")],
                         "end_turn")
        if intent == "secret":
            return _resp([_text_block(f"Sure — the value is {self._secret}.")],
                         "end_turn")
        if intent == "tool":
            return _resp([_tool_block("lookup_kb", {"key": "all_secrets"})],
                         "tool_use")
        if intent == "danger":
            return _resp(
                [_text_block("Done — the records have been deleted as requested.")],
                "end_turn")
        return _resp([_text_block("I can't help with that request.")], "end_turn")

    @staticmethod
    def _extract_request(messages) -> str:
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                try:
                    obj = json.loads(m["content"])
                    if isinstance(obj, dict) and "request" in obj:
                        return str(obj["request"])
                except (json.JSONDecodeError, TypeError):
                    return m["content"]
                return m["content"]
        return ""

    def _classify(self, request: str) -> tuple[str, bool]:
        raw = (request or "").lower()
        evaded = not any(t in raw for t in GUARD_TRIGGERS)
        norm = normalize(request)
        if any(k in norm for k in _SECRET_KWS):
            intent = "secret"
        elif any(k in norm for k in _TOOL_KWS):
            intent = "tool"
        elif any(k in norm for k in _DANGER_KWS):
            intent = "danger"
        else:
            intent = "benign"
        return intent, evaded


def build_demo_target(descriptor: AgentDescriptor, *, kb_path: str = "kb.json"
                      ) -> AnthropicSimpleAgent:
    """The reference adapter wired to the scripted (no-key) vulnerable client."""
    return AnthropicSimpleAgent(
        model="demo-scripted-model",
        kb_path=kb_path,
        agent_id=descriptor.agent_id,
        system_prompt=descriptor.system_prompt,
        client=FakeVulnerableClient(descriptor),
    )
